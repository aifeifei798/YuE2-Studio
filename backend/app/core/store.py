"""共享运行时状态：内存任务表 + GPU 队列 + 历史文件原子读写 + 内存日志环。"""
from __future__ import annotations

import asyncio
import collections
import datetime
import json
import logging
import os
import re
import shutil
import tempfile
import threading
from pathlib import Path
from typing import Any, Deque, Dict, List, Tuple

from .config import get_settings

log = logging.getLogger("yue2-studio")

TASK_ID_RE = re.compile(r"^[0-9a-f]{8}$")
SAFE_FILENAME_RE = re.compile(r"^[0-9a-f]{8}\.flac$")

# GPU 任务队列（单 worker 串行消费，多 worker/多副本会失效，见 README）
task_queue: asyncio.Queue[str] = asyncio.Queue()
tasks: Dict[str, Dict[str, Any]] = {}
history_lock = threading.Lock()
worker_task: Any = None

# 模型句柄（延迟加载，缺依赖时服务仍可启动并报 503）
pipe: Any = None
model_loaded: bool = False
model_error: str = ""

# 内存日志环，供 /api/admin/logs 拉取（容器内看日志最省事的方式）
log_buffer: Deque[str] = collections.deque(maxlen=500)


class RingBufferHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            log_buffer.append(self.format(record))
        except Exception:
            pass


def now_str() -> str:
    return datetime.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")


def _history_file() -> Path:
    return get_settings().history_file


def _backup_corrupt_history() -> None:
    try:
        hf = _history_file()
        ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = hf.with_suffix(f".json.bak.{ts}")
        shutil.copyfile(hf, backup)
        log.warning("history.json 损坏，已备份到 %s", backup)
    except Exception:
        log.exception("备份损坏的 history.json 失败")


def get_all_history() -> List[Dict[str, Any]]:
    hf = _history_file()
    if not hf.exists():
        return []
    try:
        data = json.loads(hf.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        _backup_corrupt_history()
        return []


def _write_history_atomic(history: List[Dict[str, Any]]) -> None:
    hf = _history_file()
    hf.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=str(hf.parent), prefix="history.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, hf)
    finally:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass


def add_history_record(record: Dict[str, Any]) -> None:
    with history_lock:
        history = get_all_history()
        history = [h for h in history if h.get("task_id") != record.get("task_id")]
        history.insert(0, record)
        _write_history_atomic(history)


def remove_history_record(task_id: str) -> Dict[str, Any] | None:
    with history_lock:
        history = get_all_history()
        kept = [h for h in history if h.get("task_id") != task_id]
        removed = next((h for h in history if h.get("task_id") == task_id), None)
        if removed is not None:
            _write_history_atomic(kept)
        return removed


def migrate_legacy_flat_outputs() -> None:
    """兼容老版本 outputs/*.flac 扁平存放 -> outputs/audio/*.flac。"""
    s = get_settings()
    try:
        s.output_dir.mkdir(parents=True, exist_ok=True)
        s.audio_dir.mkdir(parents=True, exist_ok=True)
        s.artifacts_root.mkdir(parents=True, exist_ok=True)
        for flac in s.output_dir.glob("*.flac"):
            if SAFE_FILENAME_RE.match(flac.name):
                dest = s.audio_dir / flac.name
                if not dest.exists():
                    shutil.move(str(flac), str(dest))
                    log.info("迁移老音频 %s -> %s", flac.name, dest)
    except Exception:
        log.exception("迁移老 outputs 目录失败")


def queue_snapshot() -> Tuple[List[str], List[Dict[str, Any]], Dict[str, Any] | None]:
    """返回 (排队task_id列表, pending任务, running任务)。"""
    try:
        queued: List[str] = list(task_queue._queue)  # type: ignore[attr-defined]
    except Exception:
        queued = []
    pending = [tasks[tid] for tid in queued if tid in tasks]
    running = next((t for t in tasks.values() if t.get("status") == "running"), None)
    return queued, pending, running


def dir_size(path: Path) -> int:
    total = 0
    try:
        for p in path.rglob("*"):
            try:
                if p.is_file():
                    total += p.stat().st_size
            except OSError:
                continue
    except OSError:
        pass
    return total
