"""GPU 推理：同步阻塞函数，调用方必须用 asyncio.to_thread 包裹。"""
from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path
from typing import Any, Dict

from ..core import store
from ..core.config import get_settings

log = logging.getLogger("yue2-studio")


def load_model_blocking() -> None:
    s = get_settings()
    log.info("⏳ 正在加载 YuE2 模型 %s (device=%s)，请稍候...", s.model_repo, s.model_device)
    try:
        from yue2 import YuE2Pipeline  # 延迟导入，缺依赖时服务仍可启动并报 503

        store.pipe = YuE2Pipeline.from_pretrained(s.model_repo, device=s.model_device)
        store.model_loaded = True
        store.model_error = ""
        log.info("✅ 模型加载成功，服务就绪")
    except Exception as e:
        store.pipe = None
        store.model_loaded = False
        store.model_error = str(e)[:1000]
        log.error("❌ 模型加载失败: %s", e)


def run_generation(task: Dict[str, Any]) -> Dict[str, Any]:
    if store.pipe is None:
        raise RuntimeError("模型尚未加载成功，请稍后重试或联系管理员查看日志")
    s = get_settings()
    task_id: str = task["task_id"]
    file_path = s.audio_dir / f"{task_id}.flac"
    artifacts_dir = s.artifacts_root / task_id

    song = store.pipe(
        style=task["style"],
        lyrics=task["lyrics"],
        cot=task["cot"],
        seed=task["seed"],
    )
    s.audio_dir.mkdir(parents=True, exist_ok=True)
    # 先写临时文件再原子改名：播放器永远不会读到半截写入中的 flac。
    # tmp 必须以 .flac 结尾（yue2 save() 按 path.suffix 校验后缀），
    # 且放在非静态的 output_dir，写一半的文件不会被 /audio 暴露出去；
    # 同卷内 os.replace 保证原子性
    tmp_path = s.output_dir / f"{task_id}.writing.flac"
    song.save(str(tmp_path))
    os.replace(tmp_path, file_path)
    try:
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        song.save_artifacts(str(artifacts_dir))
    except Exception:
        log.warning("task %s save_artifacts 失败", task_id, exc_info=True)

    return {
        "task_id": task_id,
        "title": task["title"],
        "style": task["style"],
        "lyrics": task["lyrics"],
        "seed": task["seed"],
        "cot": task["cot"],
        "audio_url": f"/audio/{task_id}.flac",
        "created_at": store.now_str(),
    }


def cleanup_task_files(task_id: str) -> None:
    s = get_settings()
    try:
        writing = s.output_dir / f"{task_id}.writing.flac"
        if writing.exists():
            writing.unlink()
        flac = s.audio_dir / f"{task_id}.flac"
        if flac.exists():
            flac.unlink()
        artifacts = s.artifacts_root / task_id
        if artifacts.exists():
            shutil.rmtree(artifacts, ignore_errors=True)
        legacy = s.output_dir / f"{task_id}_artifacts"
        if legacy.exists():
            shutil.rmtree(legacy, ignore_errors=True)
    except Exception:
        log.exception("清理任务文件失败: %s", task_id)


def safe_delete_task_files(task_id: str) -> None:
    """防路径穿越的删除（仅允许 8 位 hex）。"""
    from ..core.store import TASK_ID_RE

    if not TASK_ID_RE.match(task_id):
        return
    s = get_settings()
    try:
        flac = (s.audio_dir / f"{task_id}.flac").resolve()
        if flac.is_relative_to(s.audio_dir.resolve()) and flac.exists():
            flac.unlink()
        artifacts = (s.artifacts_root / task_id).resolve()
        if artifacts.is_relative_to(s.artifacts_root.resolve()) and artifacts.exists():
            shutil.rmtree(artifacts, ignore_errors=True)
        legacy = (s.output_dir / f"{task_id}_artifacts").resolve()
        if legacy.is_relative_to(s.output_dir.resolve()) and legacy.exists():
            shutil.rmtree(legacy, ignore_errors=True)
    except Exception:
        log.exception("删除任务文件失败: %s", task_id)
