"""Pydantic 模型：public + admin 共用，长度上限读配置保证前后一致。"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

from ..core.config import get_settings


def _s() -> object:
    return get_settings()


class GenerateRequest(BaseModel):
    title: Optional[str] = Field(default="未命名歌曲", max_length=100)
    style: str = Field(min_length=1, max_length=2000)
    lyrics: str = Field(min_length=1, max_length=10000)
    cot: Literal["full", "none"] = "full"
    seed: Optional[int] = Field(default=None, ge=0, le=2**31 - 1)


class TaskCreateResponse(BaseModel):
    task_id: str
    status: str
    queue_position: int
    seed: int


class AdminConfigUpdate(BaseModel):
    max_queue: Optional[int] = Field(default=None, ge=1, le=100)
    log_level: Optional[str] = Field(default=None, max_length=10)
