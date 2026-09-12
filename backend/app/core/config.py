"""集中配置：全部可用环境变量覆盖，方便单机 / compose / 公网部署。

加载顺序（后者优先）：默认值 < 仓库根 `.env` < 真实环境变量。
本地直接 `uvicorn` 运行时自动读 `.env`；compose 经 `env_file` 注入的
真实环境变量不受 `.env` 覆盖。
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_DIR = Path(__file__).resolve().parents[2]

try:
    from dotenv import load_dotenv

    # override=False：已存在的真实环境变量优先，绝不覆盖
    load_dotenv(REPO_ROOT / ".env", override=False)
except ImportError:  # 极简环境没装 dotenv 时退化为纯环境变量
    pass


def _getenv(name: str, default: str) -> str:
    return os.getenv(name, default)


class Settings:
    def __init__(self) -> None:
        self.model_repo: str = _getenv("YUE2_MODEL_REPO", "m-a-p/YuE2-3B")
        self.model_device: str = _getenv("YUE2_DEVICE", "cuda")
        # 默认输出到仓库根 outputs/，compose 里会被 volume 覆盖
        self.output_dir: Path = Path(_getenv("YUE2_OUTPUT_DIR", str(REPO_ROOT / "outputs")))
        self.port: int = int(_getenv("YUE2_PORT", "8000"))
        self.max_queue: int = int(_getenv("YUE2_MAX_QUEUE", "10"))
        self.submit_per_hour: int = int(_getenv("YUE2_SUBMIT_PER_HOUR", "20"))
        # 每 IP 最大并存任务数（pending+running），0 = 不限（仅匿名提交走此限制）
        self.max_pending_per_ip: int = int(_getenv("YUE2_MAX_PENDING_PER_IP", "2"))
        # 每 Key 最大并存任务数（pending+running），0 = 不限（防单个用户塞满全局队列）
        self.max_pending_per_key: int = int(_getenv("YUE2_MAX_PENDING_PER_KEY", "2"))
        self.max_style_len: int = int(_getenv("YUE2_MAX_STYLE", "2000"))
        self.max_lyrics_len: int = int(_getenv("YUE2_MAX_LYRICS", "10000"))
        self.max_title_len: int = int(_getenv("YUE2_MAX_TITLE", "100"))
        self.log_level: str = _getenv("LOG_LEVEL", "INFO").upper()
        # 管理口令：为空表示禁用 /api/admin/*（默认禁用最安全）
        self.admin_token: str = _getenv("ADMIN_TOKEN", "")
        # 为 true 时生成接口必须带有效 API Key（防公网滥用），默认强制登录
        self.require_api_key: bool = _getenv("REQUIRE_API_KEY", "true").strip().lower() in ("1", "true", "yes", "on")
        raw = _getenv("CORS_ORIGINS", "http://127.0.0.1:8000,http://localhost:8000")
        self.cors_origins: list[str] = [o.strip() for o in raw.split(",") if o.strip()]
        self.allow_credentials: bool = "*" not in self.cors_origins

    @property
    def audio_dir(self) -> Path:
        return self.output_dir / "audio"

    @property
    def artifacts_root(self) -> Path:
        return self.output_dir / "artifacts"

    @property
    def history_file(self) -> Path:
        return self.output_dir / "history.json"

    @property
    def web_dist(self) -> Path:
        return REPO_ROOT / "web" / "dist"

    def public_config(self) -> dict:
        """可安全暴露给前端/管理页的配置（绝不含 token）。"""
        return {
            "model_repo": self.model_repo,
            "model_device": self.model_device,
            "max_queue": self.max_queue,
            "submit_per_hour": self.submit_per_hour,
            "max_pending_per_ip": self.max_pending_per_ip,
            "max_pending_per_key": self.max_pending_per_key,
            "require_api_key": self.require_api_key,
            "max_style_len": self.max_style_len,
            "max_lyrics_len": self.max_lyrics_len,
            "max_title_len": self.max_title_len,
            "admin_enabled": bool(self.admin_token),
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def reload_settings() -> Settings:
    get_settings.cache_clear()
    return get_settings()
