"""管理鉴权：v1 只用 ADMIN_TOKEN（Bearer），后续可升级 JWT/API Key。"""
from __future__ import annotations

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from ..core.config import get_settings

_bearer = HTTPBearer(auto_error=False)


def require_admin(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> None:
    settings = get_settings()
    if not settings.admin_token:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="管理接口未启用（请设置 ADMIN_TOKEN 后重启）",
        )
    token = ""
    if creds is not None and creds.scheme.lower() == "bearer":
        token = creds.credentials or ""
    if not token:
        # 兼容部分反代/旧客户端用 X-Admin-Token 头
        token = request.headers.get("x-admin-token", "")
    if token != settings.admin_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="管理鉴权失败",
        )
