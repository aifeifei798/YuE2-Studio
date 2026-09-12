"""历史库：SQLite 持久化（替代 history.json 全量读写）。

设计要点：
- 每次操作短连接（`timeout=10`），写操作经模块内锁串行化；
  单进程单 worker 下足够，未来多副本需换外部 DB。
- 排序一律 `rowid DESC`（最新在前），与老 history.json 的 insert(0) 语义一致。
- 首次启动若存在老 `history.json` 且库为空，自动一次性导入并改名
  `history.json.migrated`（原文件保留可查）。
- 库损坏（DatabaseError）时备份为 `history.db.bak.<时间>` 后重建，绝不丢启动。
"""
from __future__ import annotations

import datetime
import json
import logging
import shutil
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .config import get_settings

log = logging.getLogger("yue2-studio")

COLUMNS = ("task_id", "title", "style", "lyrics", "seed", "cot", "audio_url", "created_at", "created_ts")

_SCHEMA = """CREATE TABLE IF NOT EXISTS records (
  task_id TEXT PRIMARY KEY,
  title TEXT NOT NULL DEFAULT '',
  style TEXT NOT NULL DEFAULT '',
  lyrics TEXT NOT NULL DEFAULT '',
  seed INTEGER NOT NULL DEFAULT 0,
  cot TEXT NOT NULL DEFAULT 'full',
  audio_url TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT '',
  created_ts REAL NOT NULL DEFAULT 0
)"""

_lock = threading.Lock()


def _db_path(path: Optional[Path] = None) -> Path:
    return path or get_settings().output_dir / "history.db"


def _connect(path: Optional[Path] = None) -> sqlite3.Connection:
    db = _db_path(path)
    db.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(db), timeout=10)
    con.row_factory = sqlite3.Row
    return con


def _backup_corrupt(db: Path, reason: str) -> None:
    try:
        ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = db.with_suffix(f".db.bak.{ts}")
        # 改名（而非复制）：原位置必须清空，否则重连还是同一个坏库
        shutil.move(str(db), str(backup))
        log.warning("历史库损坏（%s），已备份到 %s 并重建", reason, backup)
    except Exception:
        log.exception("备份损坏的历史库失败")


def _ensure_schema(db: Path) -> None:
    con = _connect(db)
    try:
        with con:
            con.execute(_SCHEMA)
            con.execute("PRAGMA journal_mode=WAL")
    finally:
        con.close()


def init_db(path: Optional[Path] = None) -> None:
    """建表 + 开 WAL + 触发老 JSON 一次性迁移。启动时调用一次。"""
    db = _db_path(path)
    try:
        _ensure_schema(db)
    except sqlite3.DatabaseError as e:
        _backup_corrupt(db, str(e)[:100])
        _ensure_schema(db)
    migrate_from_json(db)


def migrate_from_json(db: Optional[Path] = None, json_path: Optional[Path] = None) -> int:
    """老 history.json → SQLite，一次性。返回导入条数（0 表示无需迁移）。"""
    db = _db_path(db)
    src = json_path or get_settings().output_dir / "history.json"
    if not src.exists() or count_history(db) > 0:
        return 0
    _ensure_schema(db)
    try:
        data = json.loads(src.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            return 0
    except Exception:
        log.exception("老 history.json 解析失败，跳过迁移")
        return 0
    # 老文件 newest-first；倒序插入使 rowid 顺序与展示顺序一致
    n = 0
    for rec in reversed(data):
        try:
            add_history_record(_coerce(rec), db)
            n += 1
        except Exception:
            log.warning("跳过一条无法迁移的历史记录", exc_info=True)
    try:
        src.rename(src.with_name("history.json.migrated"))
        log.info("已迁移 %d 条历史到 SQLite，老文件改名 history.json.migrated", n)
    except OSError:
        log.exception("老 history.json 改名失败（数据已入库，不影响使用）")
    return n


def _coerce(rec: Dict[str, Any]) -> Dict[str, Any]:
    ts: float = 0
    try:
        ts = datetime.datetime.strptime(str(rec.get("created_at", "")), "%Y-%m-%d %H:%M:%S").timestamp()
    except (ValueError, TypeError):
        ts = 0
    return {
        "task_id": str(rec.get("task_id", "")),
        "title": str(rec.get("title", "")),
        "style": str(rec.get("style", "")),
        "lyrics": str(rec.get("lyrics", "")),
        "seed": int(rec.get("seed") or 0),
        "cot": str(rec.get("cot") or "full"),
        "audio_url": str(rec.get("audio_url", "")),
        "created_at": str(rec.get("created_at", "")),
        "created_ts": ts,
    }


def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    return {c: row[c] for c in COLUMNS}


def add_history_record(record: Dict[str, Any], path: Optional[Path] = None) -> None:
    rec = _coerce(record)
    if not rec["task_id"]:
        return
    if not rec["created_ts"]:
        rec["created_ts"] = time.time()
    with _lock, _connect(path) as con:
        con.execute(
            """INSERT INTO records (task_id,title,style,lyrics,seed,cot,audio_url,created_at,created_ts)
               VALUES (:task_id,:title,:style,:lyrics,:seed,:cot,:audio_url,:created_at,:created_ts)
               ON CONFLICT(task_id) DO UPDATE SET
                 title=excluded.title, style=excluded.style, lyrics=excluded.lyrics,
                 seed=excluded.seed, cot=excluded.cot, audio_url=excluded.audio_url,
                 created_at=excluded.created_at, created_ts=excluded.created_ts""",
            rec,
        )
        con.commit()


def remove_history_record(task_id: str, path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    with _lock, _connect(path) as con:
        row = con.execute("SELECT * FROM records WHERE task_id=?", (task_id,)).fetchone()
        if row is None:
            return None
        con.execute("DELETE FROM records WHERE task_id=?", (task_id,))
        con.commit()
        return _row_to_dict(row)


def clear_all(path: Optional[Path] = None) -> None:
    """清空全表（仅测试隔离用）。"""
    with _lock, _connect(path) as con:
        con.execute("DELETE FROM records")
        con.commit()


def get_history_record(task_id: str, path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    with _connect(path) as con:
        row = con.execute("SELECT * FROM records WHERE task_id=?", (task_id,)).fetchone()
        return _row_to_dict(row) if row is not None else None


def count_history(path: Optional[Path] = None) -> int:
    try:
        with _connect(path) as con:
            return int(con.execute("SELECT COUNT(*) FROM records").fetchone()[0])
    except sqlite3.DatabaseError:
        return 0


def _escape_like(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def query_history(
    keyword: str = "", limit: int = 200, offset: int = 0, path: Optional[Path] = None
) -> Tuple[int, List[Dict[str, Any]]]:
    """返回 (总数, 当页记录 newest-first)。搜索匹配标题或 seed。"""
    kw = keyword.strip()
    with _connect(path) as con:
        if kw:
            like = f"%{_escape_like(kw)}%"
            # SQLite 默认 LIKE 对 ASCII 大小写不敏感；中文不受影响
            where = "WHERE title LIKE ? ESCAPE '\\' OR CAST(seed AS TEXT) LIKE ? ESCAPE '\\'"
            args: tuple = (like, like)
        else:
            where, args = "", ()
        total = int(con.execute(f"SELECT COUNT(*) FROM records {where}", args).fetchone()[0])
        rows = con.execute(
            f"SELECT * FROM records {where} ORDER BY rowid DESC LIMIT ? OFFSET ?",
            (*args, limit, offset),
        ).fetchall()
        return total, [_row_to_dict(r) for r in rows]
