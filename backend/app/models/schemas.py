"""Pydantic 模型：长度上限取自配置（进程启动时求值，改 env 需重启）。"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

from ..core.config import get_settings

_cfg = get_settings()


class GenerateRequest(BaseModel):
    title: Optional[str] = Field(default="未命名歌曲", max_length=_cfg.max_title_len)
    style: str = Field(min_length=1, max_length=_cfg.max_style_len)
    lyrics: str = Field(min_length=1, max_length=_cfg.max_lyrics_len)
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
