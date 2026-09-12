"""API Key 体系测试：发 key、登录、配额、归属、停用、强制登录模式。"""
from __future__ import annotations

import threading
import time

from backend.app.core import store
from backend.app.services import queue as queue_mod
from .conftest import ADMIN_HEADERS, wait_status


def _make_key(client, name="alice", quota=0, note=""):
    r = client.post(
        "/api/admin/keys",
        headers=ADMIN_HEADERS,
        json={"name": name, "quota_total": quota, "note": note},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["api_key"].startswith("sk-")
    assert "key_hash" not in str(body)
    return body


def _kh(name, secret):
    return {"X-API-Key": f"{name}:{secret}"}


def test_key_crud_and_login(client):
    created = _make_key(client, "alice", quota=5, note="测试")
    secret = created["api_key"]

    # 列表不泄露哈希与明文
    items = client.get("/api/admin/keys", headers=ADMIN_HEADERS).json()["items"]
    alice = next(x for x in items if x["name"] == "alice")
    assert alice["quota_total"] == 5 and alice["used_count"] == 0
    assert alice["key_prefix"] == secret[:10]
    assert alice["created_at"] and alice["note"] == "测试"

    # 重名拒绝
    r = client.post("/api/admin/keys", headers=ADMIN_HEADERS, json={"name": "alice"})
    assert r.status_code == 409

    # 登录成功返回配额视图
    me = client.post("/api/auth/login", json={"username": "alice", "key": secret}).json()
    assert me["ok"] is True and me["quota_total"] == 5 and me["quota_left"] == 5
    # 错误凭证
    assert client.post("/api/auth/login", json={"username": "alice", "key": "sk-wrong"}).status_code == 401
    assert client.post("/api/auth/login", json={"username": "nobody", "key": secret}).status_code == 401

    # 改配额 + 停用
    kid = alice["id"]
    assert client.patch(f"/api/admin/keys/{kid}", headers=ADMIN_HEADERS, json={"quota_total": 3}).json()["quota_total"] == 3
    assert client.patch(f"/api/admin/keys/{kid}", headers=ADMIN_HEADERS, json={"enabled": False}).json()["enabled"] == 0
    assert client.post("/api/auth/login", json={"username": "alice", "key": secret}).status_code == 403
    # 停用后生成也被拒
    r = client.post("/api/generate", headers=_kh("alice", secret), json={"title": "t", "style": "s", "lyrics": "l"})
    assert r.status_code == 403

    # 删除
    assert client.delete(f"/api/admin/keys/{kid}", headers=ADMIN_HEADERS).json()["status"] == "deleted"
    assert client.delete(f"/api/admin/keys/{kid}", headers=ADMIN_HEADERS).status_code == 404


def test_quota_enforced_and_counted_on_success(client, monkeypatch):
    gate = threading.Event()
    real = queue_mod.run_generation

    def blocking(task):
        gate.wait(timeout=15)
        return real(task)

    monkeypatch.setattr(queue_mod, "run_generation", blocking)
    from backend.app.core.config import get_settings

    s = get_settings()
    old_ip_quota, old_rate = s.max_pending_per_ip, s.submit_per_hour
    s.max_pending_per_ip = 0  # 本用例只看 key 配额，IP 配额先不限
    try:
        created = _make_key(client, "bob", quota=1)
        h = _kh("bob", created["api_key"])
        payload = {"title": "t", "style": "pop", "lyrics": "la", "cot": "full", "seed": 1}

        first = client.post("/api/generate", headers=h, json=payload).json()["task_id"]
        deadline = time.time() + 10
        while client.get(f"/api/tasks/{first}").json()["status"] != "running" and time.time() < deadline:
            time.sleep(0.1)
        # 配额1首且已有1首在途 → 第二首被拒
        r = client.post("/api/generate", headers=h, json=payload)
        assert r.status_code == 429
        assert "配额" in r.json()["detail"]
        gate.set()
        assert wait_status(client, first)["status"] == "succeeded"

        # 成功计1次，用完后即使空闲也拒
        me = client.get("/api/auth/me", headers=h).json()
        assert me["quota_used"] == 1 and me["quota_left"] == 0
        r = client.post("/api/generate", headers=h, json=payload)
        assert r.status_code == 429
    finally:
        s.max_pending_per_ip = old_ip_quota
        s.submit_per_hour = old_rate
        gate.set()


def test_failed_task_does_not_consume_quota(client, monkeypatch):
    def boom(task):
        raise RuntimeError("boom")

    monkeypatch.setattr(queue_mod, "run_generation", boom)
    created = _make_key(client, "carol", quota=1)
    h = _kh("carol", created["api_key"])
    tid = client.post("/api/generate", headers=h, json={"title": "t", "style": "s", "lyrics": "l"}).json()["task_id"]
    assert wait_status(client, tid)["status"] == "failed"
    assert client.get("/api/auth/me", headers=h).json()["quota_used"] == 0


def test_owner_attribution_and_my_history(client):
    a = _make_key(client, "dave", quota=10)
    b = _make_key(client, "erin", quota=10)
    ha, hb = _kh("dave", a["api_key"]), _kh("erin", b["api_key"])
    payload = {"title": "归属歌", "style": "s", "lyrics": "l", "cot": "full", "seed": 1}

    ta = client.post("/api/generate", headers=ha, json=payload).json()["task_id"]
    assert wait_status(client, ta)["status"] == "succeeded"
    # 匿名提交一首对照
    tu = client.post("/api/generate", json=payload).json()["task_id"]
    assert wait_status(client, tu)["status"] == "succeeded"

    rec = client.get(f"/api/tasks/{ta}").json()["record"]
    assert rec["owner"] == "dave"

    mine = client.get("/api/auth/history", headers=ha, params={"limit": 20}).json()
    assert mine["total"] == 1 and mine["items"][0]["task_id"] == ta
    other = client.get("/api/auth/history", headers=hb, params={"limit": 20}).json()
    assert other["total"] == 0
    # 未登录查个人历史 → 401
    assert client.get("/api/auth/history").status_code == 401

    # 管理员按 key 查历史
    kid = next(x["id"] for x in client.get("/api/admin/keys", headers=ADMIN_HEADERS).json()["items"] if x["name"] == "dave")
    hist = client.get(f"/api/admin/keys/{kid}/history", headers=ADMIN_HEADERS).json()
    assert hist["total"] == 1 and hist["name"] == "dave"


def test_require_api_key_mode(client):
    from backend.app.core.config import get_settings

    s = get_settings()
    old = s.require_api_key
    s.require_api_key = True
    try:
        payload = {"title": "t", "style": "s", "lyrics": "l", "cot": "full", "seed": 1}
        assert client.post("/api/generate", json=payload).status_code == 401
        created = _make_key(client, "frank", quota=0)
        h = _kh("frank", created["api_key"])
        tid = client.post("/api/generate", headers=h, json=payload).json()["task_id"]
        assert wait_status(client, tid)["status"] == "succeeded"
    finally:
        s.require_api_key = old


def test_require_api_key_default_true(monkeypatch):
    """生产默认强制登录：不设 env 时即为 true。"""
    import os

    from backend.app.core.config import reload_settings

    monkeypatch.delenv("REQUIRE_API_KEY", raising=False)
    assert os.getenv("REQUIRE_API_KEY") is None
    assert reload_settings().require_api_key is True


def test_key_pending_limit(client, monkeypatch):
    """每 Key 并存上限：1 个在途时第二个被拒（配额不限，只看并存数）。"""
    gate = threading.Event()
    real = queue_mod.run_generation

    def blocking(task):
        gate.wait(timeout=15)
        return real(task)

    monkeypatch.setattr(queue_mod, "run_generation", blocking)
    from backend.app.core.config import get_settings

    s = get_settings()
    old_key_quota = s.max_pending_per_key
    s.max_pending_per_key = 1
    try:
        created = _make_key(client, "gail", quota=0)
        h = _kh("gail", created["api_key"])
        payload = {"title": "t", "style": "pop", "lyrics": "la", "cot": "full", "seed": 1}

        first = client.post("/api/generate", headers=h, json=payload).json()["task_id"]
        deadline = time.time() + 10
        while client.get(f"/api/tasks/{first}").json()["status"] != "running" and time.time() < deadline:
            time.sleep(0.1)
        r = client.post("/api/generate", headers=h, json=payload)
        assert r.status_code == 429
        assert "进行中" in r.json()["detail"]
        gate.set()
        assert wait_status(client, first)["status"] == "succeeded"
        # 空闲后可再次提交
        tid2 = client.post("/api/generate", headers=h, json=payload).json()["task_id"]
        assert wait_status(client, tid2)["status"] == "succeeded"
    finally:
        s.max_pending_per_key = old_key_quota
        gate.set()


def test_reset_key(client):
    """换 Key：旧 Key 立即失效，新 Key 可用，配额已用数与历史保留。"""
    created = _make_key(client, "hank", quota=10)
    old_secret = created["api_key"]
    h_old = _kh("hank", old_secret)
    payload = {"title": "换key歌", "style": "s", "lyrics": "l", "cot": "full", "seed": 1}

    tid = client.post("/api/generate", headers=h_old, json=payload).json()["task_id"]
    assert wait_status(client, tid)["status"] == "succeeded"

    kid = next(x["id"] for x in client.get("/api/admin/keys", headers=ADMIN_HEADERS).json()["items"] if x["name"] == "hank")
    r = client.post(f"/api/admin/keys/{kid}/reset", headers=ADMIN_HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert body["api_key"].startswith("sk-") and body["api_key"] != old_secret
    assert body["key_prefix"] == body["api_key"][:10]
    assert "key_hash" not in str(body)

    # 旧 Key 登录/生成都被拒
    assert client.post("/api/auth/login", json={"username": "hank", "key": old_secret}).status_code == 401
    assert client.post("/api/generate", headers=h_old, json=payload).status_code == 401
    # 新 Key 好用，且已用配额保留
    h_new = _kh("hank", body["api_key"])
    me = client.post("/api/auth/login", json={"username": "hank", "key": body["api_key"]}).json()
    assert me["ok"] is True and me["quota_used"] == 1
    mine = client.get("/api/auth/history", headers=h_new, params={"limit": 20}).json()
    assert mine["total"] == 1 and mine["items"][0]["task_id"] == tid
    # 不存在的 Key 返回 404
    assert client.post("/api/admin/keys/999999/reset", headers=ADMIN_HEADERS).status_code == 404
    # 未鉴权换不了
    assert client.post(f"/api/admin/keys/{kid}/reset").status_code == 401


def test_owner_column_migrates_on_old_db(tmp_path):
    import sqlite3

    from backend.app.core import history_db

    db = tmp_path / "history.db"
    con = sqlite3.connect(str(db))
    # 模拟 SQLite 迁移前的老库：没有 owner 列、没有 keys 表
    con.execute(
        """CREATE TABLE records (
             task_id TEXT PRIMARY KEY, title TEXT, style TEXT, lyrics TEXT,
             seed INTEGER, cot TEXT, audio_url TEXT,
             created_at TEXT, created_ts REAL)"""
    )
    con.execute("INSERT INTO records VALUES ('aaaaaaaa','旧歌','r','l',1,'full','/audio/aaaaaaaa.flac','2026-01-01 00:00:00',0)")
    con.commit()
    con.close()

    history_db.init_db(db)
    rec = history_db.get_history_record("aaaaaaaa", db)
    assert rec is not None and rec["owner"] is None
    row, secret = history_db.create_api_key("gina", 2, path=db)
    assert secret.startswith("sk-") and row["quota_total"] == 2
    assert history_db.verify_api_key("gina", secret, db) is not None
    assert history_db.verify_api_key("gina", "sk-wrong", db) is None
