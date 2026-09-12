"""history_db 单测：老 JSON 迁移、损坏自愈、搜索分页（不经过 HTTP）。"""
from __future__ import annotations

import json
import sqlite3

from backend.app.core import history_db


def _legacy_records():
    # 老文件 newest-first
    return [
        {
            "task_id": "bbbbbbbb",
            "title": "新歌",
            "style": "pop",
            "lyrics": "la",
            "seed": 2,
            "cot": "full",
            "audio_url": "/audio/bbbbbbbb.flac",
            "created_at": "2026-09-02 10:00:00",
        },
        {
            "task_id": "aaaaaaaa",
            "title": "旧歌",
            "style": "rock",
            "lyrics": "lo",
            "seed": 1,
            "cot": "none",
            "audio_url": "/audio/aaaaaaaa.flac",
            "created_at": "2026-09-01 10:00:00",
        },
    ]


def test_migrate_from_json(tmp_path):
    db = tmp_path / "history.db"
    src = tmp_path / "history.json"
    src.write_text(json.dumps(_legacy_records(), ensure_ascii=False), encoding="utf-8")

    assert history_db.migrate_from_json(db, src) == 2
    # 老文件改名保留
    assert not src.exists() and src.with_name("history.json.migrated").exists()
    # 展示顺序最新在前
    total, items = history_db.query_history("", limit=10, offset=0, path=db)
    assert total == 2 and [i["task_id"] for i in items] == ["bbbbbbbb", "aaaaaaaa"]
    # 幂等：库非空后再次调用返回 0
    assert history_db.migrate_from_json(db, src) == 0


def test_search_and_crud(tmp_path):
    db = tmp_path / "history.db"
    history_db.init_db(db)
    for rec in _legacy_records():
        history_db.add_history_record(rec, db)

    total, items = history_db.query_history("新歌", path=db)
    assert total == 1 and items[0]["task_id"] == "bbbbbbbb"
    total, _ = history_db.query_history("1", path=db)  # seed 搜索
    assert total == 1
    # LIKE 通配符不得被当成通配符
    total, _ = history_db.query_history("%", path=db)
    assert total == 0

    assert history_db.get_history_record("aaaaaaaa", db)["title"] == "旧歌"
    assert history_db.get_history_record("00000000", db) is None
    assert history_db.count_history(db) == 2
    removed = history_db.remove_history_record("aaaaaaaa", db)
    assert removed is not None and removed["task_id"] == "aaaaaaaa"
    assert history_db.count_history(db) == 1
    assert history_db.remove_history_record("aaaaaaaa", db) is None


def test_corrupt_db_rebuilds(tmp_path):
    db = tmp_path / "history.db"
    db.write_bytes(b"not a sqlite file")
    history_db.init_db(db)  # 不得抛异常，应备份重建
    assert history_db.count_history(db) == 0
    assert list(tmp_path.glob("history.db.bak.*"))
    history_db.add_history_record(_legacy_records()[0], db)
    assert history_db.count_history(db) == 1
