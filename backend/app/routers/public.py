"""公开接口：Studio 客户端使用，保持向后兼容。"""
from __future__ import annotations

import random
import uuid

from fastapi import APIRouter, HTTPException, Query, Request
from typing import Optional

from ..core import store
from ..core.config import get_settings
from ..core.store import TASK_ID_RE
from ..models.schemas import GenerateRequest, TaskCreateResponse

router = APIRouter()


def client_ip(request: Request) -> str:
    # 反代后取 X-Forwarded-For 首段（nginx 已透传），直连取对端地址
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        return fwd.split(",")[0].strip() or "unknown"
    return request.client.host if request.client else "unknown"


def public_task_view(task: dict) -> dict:
    view: dict = {
        "task_id": task["task_id"],
        "status": task["status"],
        "title": task.get("title"),
        "created_at": task.get("created_at"),
        "started_at": task.get("started_at"),
        "finished_at": task.get("finished_at"),
    }
    if task["status"] == "succeeded" and "record" in task:
        view["record"] = task["record"]
    if task["status"] == "failed":
        view["error"] = task.get("error", "生成失败")
    if task["status"] in ("pending", "running"):
        try:
            queued = list(store.task_queue._queue)  # type: ignore[attr-defined]
            view["queue_position"] = queued.index(task["task_id"]) + 1 if task["task_id"] in queued else 0
        except Exception:
            view["queue_position"] = 0
    return view


@router.get("/healthz")
def healthz():
    s = get_settings()
    alive = store.worker_alive()
    return {
        "status": "ok" if (store.model_loaded and alive) else "degraded",
        "model_loaded": store.model_loaded,
        "model_repo": s.model_repo,
        "queue_pending": store.task_queue.qsize(),
        "history_count": len(store.get_all_history()),
        "worker_alive": alive,
    }


@router.post("/api/generate", status_code=202, response_model=TaskCreateResponse)
async def generate_music(req: GenerateRequest, request: Request):
    s = get_settings()
    # 运行时可调 max_queue：读最新 settings
    if not store.model_loaded:
        raise HTTPException(status_code=503, detail="模型正在加载或加载失败，请稍后重试")
    if store.task_queue.qsize() >= s.max_queue:
        raise HTTPException(status_code=429, detail=f"当前排队任务已满（{s.max_queue}），请稍后再试")
    ip = client_ip(request)
    if not store.check_submit_rate(ip, s.submit_per_hour):
        raise HTTPException(
            status_code=429,
            detail=f"提交过于频繁（每 IP 每小时限 {s.submit_per_hour} 次），请稍后再试",
        )

    title = req.title.strip() if req.title and req.title.strip() else "未命名歌曲"
    actual_seed = req.seed if req.seed is not None else random.randint(1, 2**31 - 1)
    task_id = uuid.uuid4().hex[:8]
    task: dict = {
        "task_id": task_id,
        "title": title,
        "style": req.style,
        "lyrics": req.lyrics,
        "cot": req.cot,
        "seed": actual_seed,
        "status": "pending",
        "created_at": store.now_str(),
        "client": ip,
    }
    store.tasks[task_id] = task
    await store.task_queue.put(task_id)
    store.save_pending_snapshot()
    return {
        "task_id": task_id,
        "status": "pending",
        "queue_position": store.task_queue.qsize(),
        "seed": actual_seed,
    }


@router.get("/api/tasks/{task_id}")
def fetch_task(task_id: str):
    if not TASK_ID_RE.match(task_id):
        raise HTTPException(status_code=400, detail="非法 task_id")
    task = store.tasks.get(task_id)
    if task is None:
        for rec in store.get_all_history():
            if rec.get("task_id") == task_id:
                return {"task_id": task_id, "status": "succeeded", "record": rec}
        raise HTTPException(status_code=404, detail="任务不存在")
    return public_task_view(task)


@router.get("/api/history")
def fetch_history(
    limit: Optional[int] = Query(default=None, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    q: str = Query(default="", max_length=100),
):
    history = store.get_all_history()
    keyword = q.strip().lower()
    if keyword:
        history = [
            h
            for h in history
            if keyword in str(h.get("title", "")).lower() or keyword in str(h.get("seed", ""))
        ]
    total = len(history)
    if limit is None:
        return history[:200]
    return {"total": total, "offset": offset, "limit": limit, "items": history[offset : offset + limit]}
