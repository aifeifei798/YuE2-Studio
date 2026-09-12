"""Pydantic 模型：长度上限运行时取配置（支持 reload_settings 后即时生效）。"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

from ..core.config import get_settings

# 用户名鉴权走 `X-API-Key: 用户名:secret`，冒号是分隔符、空白易误输，一律禁止
_USERNAME_PATTERN = r"^[^:\s]+$"


class GenerateRequest(BaseModel):
    title: Optional[str] = Field(default="未命名歌曲", max_length=500)
    style: str = Field(min_length=1, max_length=20000)
    lyrics: str = Field(min_length=1, max_length=20000)
    cot: Literal["full", "none"] = "full"
    seed: Optional[int] = Field(default=None, ge=0, le=2**31 - 1)

    @field_validator("title", "style", "lyrics")
    @classmethod
    def _check_runtime_limits(cls, v: Optional[str], info) -> Optional[str]:
        if v is None:
            return v
        s = get_settings()
        limits = {"title": s.max_title_len, "style": s.max_style_len, "lyrics": s.max_lyrics_len}
        limit = limits.get(info.field_name, 0)
        if limit and len(v) > limit:
            raise ValueError(f"{info.field_name} 超过长度上限（{limit}）")
        return v


class TaskCreateResponse(BaseModel):
    task_id: str
    status: str
    queue_position: int
    seed: int


class AdminConfigUpdate(BaseModel):
    max_queue: Optional[int] = Field(default=None, ge=1, le=100)
    submit_per_hour: Optional[int] = Field(default=None, ge=0, le=10000)
    max_pending_per_ip: Optional[int] = Field(default=None, ge=0, le=100)
    max_pending_per_key: Optional[int] = Field(default=None, ge=0, le=100)
    require_api_key: Optional[bool] = None
    log_level: Optional[str] = Field(default=None, max_length=10)


class KeyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=32, pattern=_USERNAME_PATTERN)
    quota_total: int = Field(default=0, ge=0, le=100000)
    note: str = Field(default="", max_length=200)


class KeyUpdate(BaseModel):
    quota_total: Optional[int] = Field(default=None, ge=0, le=100000)
    enabled: Optional[bool] = None
    note: Optional[str] = Field(default=None, max_length=200)


class KeyLogin(BaseModel):
    username: str = Field(min_length=1, max_length=32, pattern=_USERNAME_PATTERN)
    key: str = Field(min_length=1, max_length=100)
