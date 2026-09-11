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
import time
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
# worker 心跳（monotonic 秒）：看门狗与 healthz 据此判断 worker 是否存活
worker_heartbeat: float = 0.0


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


def _pending_file() -> Path:
    return get_settings().output_dir / "pending.json"


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


# ----------------- 排队快照：重启不丢 pending 任务 -----------------
_PENDING_FIELDS = ("task_id", "title", "style", "lyrics", "cot", "seed", "created_at", "client")


def save_pending_snapshot() -> None:
    """把当前排队中的任务落盘（只含 JSON 安全字段）。"""
    try:
        queued: List[str] = list(task_queue._queue)  # type: ignore[attr-defined]
    except Exception:
        queued = []
    snapshot = [
        {k: tasks[tid].get(k) for k in _PENDING_FIELDS}
        for tid in queued
        if tid in tasks
    ]
    try:
        pf = _pending_file()
        pf.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(dir=str(pf.parent), prefix="pending.", suffix=".tmp")
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
    for d in (s.audio_dir, s.output_dir):
        if not d.exists():
            continue
        for tmp in d.glob("*.tmp"):
            try:
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
