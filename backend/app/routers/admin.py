"""管理接口：全部需要 ADMIN_TOKEN（Bearer 或 X-Admin-Token）。"""
from __future__ import annotations

import logging
import shutil
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from ..core import store
from ..core.config import get_settings
from ..core.security import require_admin
from ..core.store import TASK_ID_RE, dir_size, log_buffer, queue_snapshot
from ..models.schemas import AdminConfigUpdate
from ..services.inference import safe_delete_task_files
from .public import public_task_view

log = logging.getLogger("yue2-studio")

router = APIRouter(prefix="/api/admin", dependencies=[Depends(require_admin)])


@router.get("/health")
def admin_health():
    """管理视角的健康详情（含模型错误原文，公开 healthz 不暴露）。"""
    s = get_settings()
    return {
        "model_loaded": store.model_loaded,
        "model_repo": s.model_repo,
        "model_device": s.model_device,
        "model_error": store.model_error,
        "worker_alive": store.worker_alive(),
        "queue_pending": store.task_queue.qsize(),
        "history_count": len(store.get_all_history()),
    }


@router.get("/queue")
def admin_queue():
    queued, pending, running = queue_snapshot()
    return {
        "pending_count": len(pending),
        "running": public_task_view(running) if running else None,
        "pending": [public_task_view(t) for t in pending],
        "max_queue": get_settings().max_queue,
    }


@router.get("/tasks")
def admin_tasks(status: Optional[str] = Query(default=None), limit: int = Query(default=50, ge=1, le=200)):
    items = list(store.tasks.values())
    if status:
        items = [t for t in items if t.get("status") == status]
    # pending/running 优先，同状态内新的在前（运维先看最新）
    pri = {"running": 0, "pending": 1, "failed": 2, "succeeded": 3}
    items.sort(key=lambda t: t.get("created_at", ""), reverse=True)
    items.sort(key=lambda t: pri.get(t.get("status"), 9))
    return {"total": len(items), "items": [public_task_view(t) for t in items[:limit]]}


@router.post("/tasks/{task_id}/cancel")
def admin_cancel_task(task_id: str):
    if not TASK_ID_RE.match(task_id):
        raise HTTPException(status_code=400, detail="非法 task_id")
    task = store.tasks.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    if task.get("status") == "running":
        raise HTTPException(status_code=409, detail="任务正在 GPU 上执行，无法取消（只能等待完成）")
    if task.get("status") in ("succeeded", "failed"):
        raise HTTPException(status_code=409, detail=f"任务已结束（{task.get('status')}），无需取消")
    task["cancel_requested"] = True
    log.info("🛑 管理员取消排队任务 [%s]", task_id)
    return {"status": "cancel_requested", "task_id": task_id}


@router.delete("/history/{task_id}")
def admin_delete_history(task_id: str):
    if not TASK_ID_RE.match(task_id):
        raise HTTPException(status_code=400, detail="非法 task_id")
    removed = store.remove_history_record(task_id)
    store.tasks.pop(task_id, None)
    safe_delete_task_files(task_id)
    store.save_pending_snapshot()
    if removed is None:
        raise HTTPException(status_code=404, detail="记录不存在")
    log.info("🗑 管理员删除历史 [%s]", task_id)
    return {"status": "deleted", "task_id": task_id}


@router.get("/disk")
def admin_disk():
    s = get_settings()
    audio_count = len(list(s.audio_dir.glob("*.flac"))) if s.audio_dir.exists() else 0
    info = {
        "output_dir": str(s.output_dir),
        "audio_count": audio_count,
        "audio_bytes": dir_size(s.audio_dir) if s.audio_dir.exists() else 0,
        "artifacts_bytes": dir_size(s.artifacts_root) if s.artifacts_root.exists() else 0,
        "history_count": len(store.get_all_history()),
        "queue_pending": store.task_queue.qsize(),
    }
    try:
        du = shutil.disk_usage(s.output_dir)
        info.update({"disk_total": du.total, "disk_used": du.used, "disk_free": du.free})
    except OSError:
        pass
    return info


@router.get("/logs")
def admin_logs(tail: int = Query(default=200, ge=1, le=500)):
    return {"total_buffered": len(log_buffer), "items": list(log_buffer)[-tail:]}


@router.get("/config")
def admin_get_config():
    s = get_settings()
    return {"config": s.public_config(), "cors_origins": s.cors_origins}


@router.put("/config")
def admin_update_config(payload: AdminConfigUpdate):
    # 运行时热更新（重启后以环境变量为准）
    s = get_settings()
    updated: dict = {}
    if payload.max_queue is not None:
        s.max_queue = payload.max_queue
        updated["max_queue"] = s.max_queue
    if payload.log_level:
        level = payload.log_level.upper()
        logging.getLogger().setLevel(getattr(logging, level, logging.INFO))
        logging.getLogger("yue2-studio").setLevel(getattr(logging, level, logging.INFO))
        s.log_level = level
        updated["log_level"] = level
    log.info("⚙️ 管理员更新配置 %s", updated)
    return {"updated": updated, "config": s.public_config()}
