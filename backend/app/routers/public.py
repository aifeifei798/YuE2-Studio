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
from .auth import parse_key_header

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
            queued = store.queued_ids()
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
        "history_count": store.count_history(),
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
    # Key 鉴权（可选）：带 X-API-Key 则校验并记配额；REQUIRE_API_KEY 开启时匿名直接 401
    key_row = None
    parsed = parse_key_header(request)
    if parsed is not None:
        name, secret = parsed
        key_row = store.verify_api_key(name, secret)
        if key_row is None:
            raise HTTPException(status_code=401, detail="用户名或 Key 错误")
        if not key_row["enabled"]:
            raise HTTPException(status_code=403, detail="该 Key 已被管理员停用")
    elif s.require_api_key:
        raise HTTPException(status_code=401, detail="本站点要求登录后才能生成（用户名 + Key）")
    if key_row is not None and key_row["quota_total"] > 0:
        used = key_row["used_count"] + store.count_active_by_key(key_row["id"])
        if used >= key_row["quota_total"]:
            raise HTTPException(
                status_code=429,
                detail=f"该 Key 配额已用完（{key_row['used_count']}/{key_row['quota_total']} 首），请联系管理员",
            )
    # 每 Key 并存上限：防单个用户一次提交几十个塞满全局队列（0=不限）
    if key_row is not None and s.max_pending_per_key > 0:
        active_key = store.count_active_by_key(key_row["id"])
        if active_key >= s.max_pending_per_key:
            raise HTTPException(
                status_code=429,
                detail=f"该用户已有 {active_key} 个进行中任务（上限 {s.max_pending_per_key} 个），请等待完成后再提交",
            )
    # 已登录（Key）用户按 Key 配额限流，不再叠加 IP 并存限制；
    # 匿名提交仍按 IP 限流（无账号体系下 IP 即用户）
    if key_row is None and s.max_pending_per_ip > 0:
        active = store.count_active_by_ip(ip)
        if active >= s.max_pending_per_ip:
            raise HTTPException(
                status_code=429,
                detail=f"该 IP 已有 {active} 个进行中任务（上限 {s.max_pending_per_ip} 个），请等待完成后再提交",
            )
    # 放最后：只有前面全部通过才计入小时限流，配额/IP 挡掉的不消耗次数
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
        "key_id": key_row["id"] if key_row is not None else None,
        "owner": key_row["name"] if key_row is not None else None,
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


@router.get("/api/config")
def public_config():
    """前端动态取长度上限/开关，避免前后端硬编码两头漂移（无敏感字段）。"""
    s = get_settings()
    return {
        "max_title_len": s.max_title_len,
        "max_style_len": s.max_style_len,
        "max_lyrics_len": s.max_lyrics_len,
        "require_api_key": s.require_api_key,
        "max_queue": s.max_queue,
        "max_pending_per_key": s.max_pending_per_key,
    }


@router.get("/api/tasks/{task_id}")
def fetch_task(task_id: str):
    if not TASK_ID_RE.match(task_id):
        raise HTTPException(status_code=400, detail="非法 task_id")
    task = store.tasks.get(task_id)
    if task is None:
        # 重启后内存丢失但库还在：DB 兜底（无需全量加载）
        rec = store.get_history_record(task_id)
        if rec is not None:
            return {"task_id": task_id, "status": "succeeded", "record": rec}
        raise HTTPException(status_code=404, detail="任务不存在")
    return public_task_view(task)


@router.get("/api/history")
def fetch_history(
    limit: Optional[int] = Query(default=None, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    q: str = Query(default="", max_length=100),
):
    keyword = q.strip()
    total, items = store.query_history(keyword, limit if limit is not None else 200, offset)
    # 兼容老前端：不带 limit 时直接返回数组
    if limit is None:
        return items
    return {"total": total, "offset": offset, "limit": limit, "items": items}
