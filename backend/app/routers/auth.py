"""用户侧 Key 鉴权：登录校验、配额自查、只看我的历史。

凭证格式：请求头 `X-API-Key: <用户名>:<secret>`（secret 即建 key 时返回一次的明文）。
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from ..core import store
from ..models.schemas import KeyLogin

router = APIRouter()


def parse_key_header(request: Request) -> Optional[tuple[str, str]]:
    raw = request.headers.get("x-api-key", "")
    if not raw or ":" not in raw:
        return None
    name, secret = raw.split(":", 1)
    name, secret = name.strip(), secret.strip()
    if not name or not secret:
        return None
    return name, secret


def require_key(request: Request) -> dict:
    """有效且启用的 key 才放行；区分 401（错）与 403（停用）。"""
    parsed = parse_key_header(request)
    if parsed is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="请先登录（用户名 + Key）")
    name, secret = parsed
    row = store.verify_api_key(name, secret)
    if row is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户名或 Key 错误")
    if not row["enabled"]:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="该 Key 已被管理员停用")
    return row


def key_quota_view(row: dict) -> dict:
    quota = row["quota_total"]
    return {
        "name": row["name"],
        "quota_total": quota,
        "quota_used": row["used_count"],
        # 配额语义：成功生成的歌曲数；排队中/失败不计
        "quota_left": None if quota <= 0 else max(0, quota - row["used_count"]),
        "enabled": bool(row["enabled"]),
        "created_at": row["created_at"],
        "last_used_at": row["last_used_at"],
    }


@router.post("/api/auth/login")
def login(payload: KeyLogin):
    row = store.verify_api_key(payload.username.strip(), payload.key.strip())
    if row is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户名或 Key 错误")
    if not row["enabled"]:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="该 Key 已被管理员停用")
    return {"ok": True, **key_quota_view(row)}


@router.get("/api/auth/me")
def me(key: dict = Depends(require_key)):
    fresh = store.get_key_by_name(key["name"]) or key
    return key_quota_view(fresh)


@router.get("/api/auth/history")
def my_history(
    limit: int = Query(default=20, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    q: str = Query(default="", max_length=100),
    key: dict = Depends(require_key),
):
    total, items = store.query_history(q.strip(), limit, offset, owner=key["name"])
    return {"total": total, "offset": offset, "limit": limit, "items": items}
