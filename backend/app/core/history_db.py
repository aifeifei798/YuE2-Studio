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
import hashlib
import hmac
import json
import logging
import secrets
import shutil
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .config import get_settings

log = logging.getLogger("yue2-studio")

COLUMNS = ("task_id", "title", "style", "lyrics", "seed", "cot", "audio_url", "created_at", "created_ts", "owner")

_KEY_COLUMNS = ("id", "name", "key_hash", "key_prefix", "quota_total", "used_count", "enabled", "note", "created_at", "last_used_at")

_SCHEMA = """CREATE TABLE IF NOT EXISTS records (
  task_id TEXT PRIMARY KEY,
  title TEXT NOT NULL DEFAULT '',
  style TEXT NOT NULL DEFAULT '',
  lyrics TEXT NOT NULL DEFAULT '',
  seed INTEGER NOT NULL DEFAULT 0,
  cot TEXT NOT NULL DEFAULT 'full',
  audio_url TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT '',
  created_ts REAL NOT NULL DEFAULT 0,
  owner TEXT DEFAULT NULL
)"""

_KEYS_SCHEMA = """CREATE TABLE IF NOT EXISTS api_keys (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE,
  key_hash TEXT NOT NULL UNIQUE,
  key_prefix TEXT NOT NULL DEFAULT '',
  quota_total INTEGER NOT NULL DEFAULT 0,
  used_count INTEGER NOT NULL DEFAULT 0,
  enabled INTEGER NOT NULL DEFAULT 1,
  note TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT '',
  last_used_at TEXT NOT NULL DEFAULT ''
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


def _now_str() -> str:
    return datetime.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")


def _ensure_schema(db: Path) -> None:
    con = _connect(db)
    try:
        with con:
            con.execute(_SCHEMA)
            con.execute(_KEYS_SCHEMA)
            con.execute("PRAGMA journal_mode=WAL")
            # 存量库补列（新库 CREATE 已包含，老库 ALTER）
            cols = {r[1] for r in con.execute("PRAGMA table_info(records)").fetchall()}
            if "owner" not in cols:
                con.execute("ALTER TABLE records ADD COLUMN owner TEXT DEFAULT NULL")
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
    owner = rec.get("owner")
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
        "owner": str(owner) if owner else None,
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
            """INSERT INTO records (task_id,title,style,lyrics,seed,cot,audio_url,created_at,created_ts,owner)
               VALUES (:task_id,:title,:style,:lyrics,:seed,:cot,:audio_url,:created_at,:created_ts,:owner)
               ON CONFLICT(task_id) DO UPDATE SET
                 title=excluded.title, style=excluded.style, lyrics=excluded.lyrics,
                 seed=excluded.seed, cot=excluded.cot, audio_url=excluded.audio_url,
                 created_at=excluded.created_at, created_ts=excluded.created_ts, owner=excluded.owner""",
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


def clear_keys(path: Optional[Path] = None) -> None:
    """清空 Key 表（仅测试隔离用）。"""
    with _lock, _connect(path) as con:
        con.execute("DELETE FROM api_keys")
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
    keyword: str = "",
    limit: int = 200,
    offset: int = 0,
    path: Optional[Path] = None,
    owner: Optional[str] = None,
) -> Tuple[int, List[Dict[str, Any]]]:
    """返回 (总数, 当页记录 newest-first)。搜索匹配标题或 seed；owner 只看某人的歌。"""
    kw = keyword.strip()
    conds: List[str] = []
    args: List[Any] = []
    if owner is not None:
        conds.append("owner IS ?")
        args.append(owner)
    if kw:
        like = f"%{_escape_like(kw)}%"
        # SQLite 默认 LIKE 对 ASCII 大小写不敏感；中文不受影响
        conds.append("(title LIKE ? ESCAPE '\\' OR CAST(seed AS TEXT) LIKE ? ESCAPE '\\')")
        args.extend([like, like])
    where = f"WHERE {' AND '.join(conds)}" if conds else ""
    with _connect(path) as con:
        total = int(con.execute(f"SELECT COUNT(*) FROM records {where}", args).fetchone()[0])
        rows = con.execute(
            f"SELECT * FROM records {where} ORDER BY rowid DESC LIMIT ? OFFSET ?",
            (*args, limit, offset),
        ).fetchall()
        return total, [_row_to_dict(r) for r in rows]


# ----------------- API Key：库里只存哈希，明文只在创建时返回一次 -----------------

def _hash_key(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def _key_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    return {c: row[c] for c in _KEY_COLUMNS if c != "key_hash"}


def create_api_key(
    name: str, quota_total: int = 0, note: str = "", path: Optional[Path] = None
) -> Tuple[Dict[str, Any], str]:
    """创建 key。返回 (公开信息, 明文secret)。重名抛 ValueError。"""
    secret = "sk-" + secrets.token_hex(16)
    rec = {
        "name": name,
        "key_hash": _hash_key(secret),
        "key_prefix": secret[:10],
        "quota_total": max(0, quota_total),
        "note": note,
        "created_at": _now_str(),
    }
    with _lock, _connect(path) as con:
        try:
            cur = con.execute(
                """INSERT INTO api_keys (name,key_hash,key_prefix,quota_total,note,created_at)
                   VALUES (:name,:key_hash,:key_prefix,:quota_total,:note,:created_at)""",
                rec,
            )
            con.commit()
        except sqlite3.IntegrityError:
            raise ValueError("用户名已存在")
        row = con.execute("SELECT * FROM api_keys WHERE id=?", (cur.lastrowid,)).fetchone()
        return _key_to_dict(row), secret


def verify_api_key(name: str, secret: str, path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """校验用户名+key。返回行（含内部字段供调用方判断 enabled/配额），失败返回 None。"""
    with _connect(path) as con:
        row = con.execute("SELECT * FROM api_keys WHERE name=?", (name,)).fetchone()
        if row is None:
            return None
        if not hmac.compare_digest(row["key_hash"], _hash_key(secret)):
            return None
        return dict(row)


def list_keys(path: Optional[Path] = None) -> List[Dict[str, Any]]:
    with _connect(path) as con:
        rows = con.execute("SELECT * FROM api_keys ORDER BY id ASC").fetchall()
        return [_key_to_dict(r) for r in rows]


def update_key(
    key_id: int,
    quota_total: Optional[int] = None,
    enabled: Optional[bool] = None,
    note: Optional[str] = None,
    path: Optional[Path] = None,
) -> Optional[Dict[str, Any]]:
    sets: List[str] = []
    args: Dict[str, Any] = {"id": key_id}
    if quota_total is not None:
        sets.append("quota_total=:quota_total")
        args["quota_total"] = max(0, quota_total)
    if enabled is not None:
        sets.append("enabled=:enabled")
        args["enabled"] = 1 if enabled else 0
    if note is not None:
        sets.append("note=:note")
        args["note"] = note
    if not sets:
        return get_key(key_id, path)
    with _lock, _connect(path) as con:
        con.execute(f"UPDATE api_keys SET {', '.join(sets)} WHERE id=:id", args)
        con.commit()
        row = con.execute("SELECT * FROM api_keys WHERE id=?", (key_id,)).fetchone()
        return _key_to_dict(row) if row is not None else None


def get_key(key_id: int, path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    with _connect(path) as con:
        row = con.execute("SELECT * FROM api_keys WHERE id=?", (key_id,)).fetchone()
        return _key_to_dict(row) if row is not None else None


def get_key_by_name(name: str, path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    with _connect(path) as con:
        row = con.execute("SELECT * FROM api_keys WHERE name=?", (name,)).fetchone()
        return _key_to_dict(row) if row is not None else None


def delete_key(key_id: int, path: Optional[Path] = None) -> bool:
    with _lock, _connect(path) as con:
        cur = con.execute("DELETE FROM api_keys WHERE id=?", (key_id,))
        con.commit()
        return cur.rowcount > 0


def increment_used(key_id: int, path: Optional[Path] = None) -> None:
    """成功生成一首后计数（失败/取消不计）。"""
    with _lock, _connect(path) as con:
        con.execute(
            "UPDATE api_keys SET used_count=used_count+1, last_used_at=? WHERE id=?",
            (_now_str(), key_id),
        )
        con.commit()
