"""集中配置：全部可用环境变量覆盖，方便单机 / compose / 公网部署。"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_DIR = Path(__file__).resolve().parents[2]


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
        self.max_style_len: int = int(_getenv("YUE2_MAX_STYLE", "2000"))
        self.max_lyrics_len: int = int(_getenv("YUE2_MAX_LYRICS", "10000"))
        self.max_title_len: int = 100
        self.log_level: str = _getenv("LOG_LEVEL", "INFO").upper()
        # 管理口令：为空表示禁用 /api/admin/*（默认禁用最安全）
        self.admin_token: str = _getenv("ADMIN_TOKEN", "")
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
