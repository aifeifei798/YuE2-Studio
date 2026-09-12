"""共享运行时状态：内存任务表 + GPU 队列 + 排队快照 + 内存日志环。

历史记录持久化见 `history_db.py`（SQLite），此处仅做兼容重导出。
"""
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
import time
from pathlib import Path
from typing import Any, Deque, Dict, List, Tuple

from .config import get_settings
from .history_db import (
    add_history_record,
    count_history,
    create_api_key,
    delete_key,
    get_history_record,
    get_key,
    get_key_by_name,
    clear_keys,
    increment_used,
    init_db,
    list_keys,
    migrate_from_json,
    query_history,
    remove_history_record,
    update_key,
    regenerate_key_secret,
    verify_api_key,
)

__all__ = [
    "TASK_ID_RE",
    "SAFE_FILENAME_RE",
    "task_queue",
    "tasks",
    "history_lock",
    "worker_task",
    "worker_heartbeat",
    "beat",
    "worker_alive",
    "pipe",
    "model_loaded",
    "model_error",
    "log_buffer",
    "RingBufferHandler",
    "submit_hits",
    "check_submit_rate",
    "count_active_by_ip",
    "count_active_by_key",
    "now_str",
    "add_history_record",
    "remove_history_record",
    "get_history_record",
    "count_history",
    "query_history",
    "init_db",
    "migrate_from_json",
    "create_api_key",
    "verify_api_key",
    "list_keys",
    "get_key",
    "get_key_by_name",
    "clear_keys",
    "update_key",
    "delete_key",
    "regenerate_key_secret",
    "increment_used",
    "save_pending_snapshot",
    "load_pending_snapshot",
    "cleanup_tmp_files",
    "migrate_legacy_flat_outputs",
    "queue_snapshot",
    "queued_ids",
    "prune_tasks",
    "dir_size",
]

log = logging.getLogger("yue2-studio")

TASK_ID_RE = re.compile(r"^[0-9a-f]{8}$")
SAFE_FILENAME_RE = re.compile(r"^[0-9a-f]{8}\.flac$")

# GPU 任务队列（单 worker 串行消费，多 worker/多副本会失效，见 README）
task_queue: asyncio.Queue[str] = asyncio.Queue()
tasks: Dict[str, Dict[str, Any]] = {}
history_lock = threading.Lock()
worker_task: Any = None
# worker 心跳（monotonic 秒）：看门狗与 healthz 据此判断 worker 是否存活
worker_heartbeat: float = 0.0

# 内存任务表上限：只保留最近完成的 N 条，已完成超限即淘汰（DB 仍是全量，fetch 走 DB 兜底）
MAX_TASKS_IN_MEMORY = 500


def queued_ids() -> List[str]:
    """排队中的 task_id（按入队顺序）。封装 asyncio.Queue 私有成员，调用方勿直读 _queue。"""
    try:
        return list(task_queue._queue)  # type: ignore[attr-defined]
    except Exception:
        return []


def prune_tasks() -> None:
    """淘汰已结束的老任务，防止 tasks 字典无限增长。pending/running 永不淘汰。"""
    if len(tasks) <= MAX_TASKS_IN_MEMORY:
        return
    done = [tid for tid, t in tasks.items() if t.get("status") in ("succeeded", "failed")]
    if not done:
        return
    # 按完成/创建时间从老到新排，删到上限为止
    done.sort(key=lambda tid: (tasks[tid].get("finished_at", ""), tasks[tid].get("created_at", "")))
    for tid in done[: len(tasks) - MAX_TASKS_IN_MEMORY]:
        tasks.pop(tid, None)


def beat() -> None:
    global worker_heartbeat
    worker_heartbeat = time.monotonic()


def worker_alive(timeout_sec: float = 60.0) -> bool:
    return worker_heartbeat > 0 and (time.monotonic() - worker_heartbeat) < timeout_sec

# 模型句柄（延迟加载，缺依赖时服务仍可启动并报 503）
pipe: Any = None
model_loaded: bool = False
model_error: str = ""

# 内存日志环，供 /api/admin/logs 拉取（容器内看日志最省事的方式）
log_buffer: Deque[str] = collections.deque(maxlen=500)


# 提交限流：ip -> 最近 1 小时内的提交时间戳（monotonic 秒）
submit_hits: Dict[str, Deque[float]] = {}


def check_submit_rate(ip: str, per_hour: int) -> bool:
    """返回 True 表示允许本次提交；超限返回 False。per_hour<=0 表示不限。"""
    if per_hour <= 0:
        return True
    now = time.monotonic()
    window = now - 3600.0
    hits = submit_hits.get(ip)
    if hits is None:
        hits = collections.deque()
        submit_hits[ip] = hits
    while hits and hits[0] < window:
        hits.popleft()
    if len(hits) >= per_hour:
        return False
    hits.append(now)
    # 顺手回收长期不活跃的 ip，防止字典无限增长
    if len(submit_hits) > 10000:
        for k in [k for k, v in submit_hits.items() if not v or v[-1] < window]:
            del submit_hits[k]
    return True


def count_active_by_ip(ip: str) -> int:
    """该 IP 当前并存任务数（pending + running）。无账号体系下 IP 即用户。"""
    return sum(
        1
        for t in tasks.values()
        if t.get("client") == ip and t.get("status") in ("pending", "running")
    )


def count_active_by_key(key_id: int) -> int:
    """该 Key 当前并存任务数（pending + running）。"""
    return sum(
        1
        for t in tasks.values()
        if t.get("key_id") == key_id and t.get("status") in ("pending", "running")
    )


class RingBufferHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            log_buffer.append(self.format(record))
        except Exception:
            pass


def now_str() -> str:
    return datetime.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")


def _pending_file() -> Path:
    return get_settings().output_dir / "pending.json"


# ----------------- 排队快照：重启不丢 pending 任务 -----------------
_PENDING_FIELDS = ("task_id", "title", "style", "lyrics", "cot", "seed", "created_at", "client", "key_id", "owner")


def save_pending_snapshot() -> None:
    """把当前排队中的任务落盘（只含 JSON 安全字段）。"""
    queued: List[str] = queued_ids()
    snapshot = [
        {k: tasks[tid].get(k) for k in _PENDING_FIELDS}
        for tid in queued
        if tid in tasks
    ]
    try:
        pf = _pending_file()
        pf.parent.mkdir(parents=True, exist_ok=True)
        # 后缀不用 .tmp：cleanup_tmp_files 会清 *.tmp，启动时序一变就会误删快照
        fd, tmp_path = tempfile.mkstemp(dir=str(pf.parent), prefix="pending.", suffix=".snapwriting")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(snapshot, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, pf)
        finally:
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except OSError:
                pass
    except Exception:
        log.exception("排队快照写入失败")


def load_pending_snapshot() -> List[Dict[str, Any]]:
    pf = _pending_file()
    if not pf.exists():
        return []
    try:
        data = json.loads(pf.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            return []
        return [d for d in data if isinstance(d, dict) and TASK_ID_RE.match(str(d.get("task_id", "")))]
    except Exception:
        log.exception("排队快照读取失败，已忽略")
        return []


def cleanup_tmp_files() -> None:
    """清掉上次崩溃残留的 *.tmp 音频/快照临时文件。"""
    s = get_settings()
    # 精确匹配：只清音频半成品与快照半成品，不碰其它 .tmp
    # （*.flac.tmp 是历史版本的残留模式，一并兼容清理）
    audio_patterns = ("*.flac.tmp", "*.writing.flac")
    if s.audio_dir.exists():
        for pat in audio_patterns:
            for tmp in s.audio_dir.glob(pat):
                try:
                    tmp.unlink()
                    log.info("清理残留临时文件 %s", tmp.name)
                except OSError:
                    pass
    if s.output_dir.exists():
        leftovers = (
            list(s.output_dir.glob("pending.*.snapwriting"))
            + list(s.output_dir.glob("*.tmp"))
            + list(s.output_dir.glob("*.writing.flac"))
        )
        for tmp in leftovers:
            try:
                # 快照正式文件 pending.json 永不删除
                if tmp.name == "pending.json":
                    continue
                tmp.unlink()
                log.info("清理残留临时文件 %s", tmp.name)
            except OSError:
                pass


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
    queued: List[str] = queued_ids()
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
