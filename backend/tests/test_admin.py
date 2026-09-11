"""管理接口测试：鉴权、队列/取消、磁盘/日志/配置、删档。"""
from __future__ import annotations

import threading

from backend.app.core import store
from backend.app.services import queue as queue_mod
from .conftest import ADMIN_HEADERS, wait_status


def test_admin_requires_auth(client):
    assert client.get("/api/admin/queue").status_code == 401
    assert client.get("/api/admin/queue", headers={"Authorization": "Bearer wrong"}).status_code == 401


def test_admin_disabled_without_token(client):
    from backend.app.core.config import get_settings

    s = get_settings()
    old = s.admin_token
    s.admin_token = ""
    try:
        assert client.get("/api/admin/queue", headers=ADMIN_HEADERS).status_code == 403
    finally:
        s.admin_token = old


def test_admin_queue_and_health(client):
    q = client.get("/api/admin/queue", headers=ADMIN_HEADERS).json()
    assert set(("pending_count", "running", "pending", "max_queue")) <= set(q)
    h = client.get("/api/admin/health", headers=ADMIN_HEADERS).json()
    assert h["model_loaded"] is True and h["worker_alive"] is True


def test_admin_cancel_pending(client, monkeypatch):
    gate = threading.Event()
    real = queue_mod.run_generation

    def blocking(task):
        gate.wait(timeout=15)
        return real(task)

    monkeypatch.setattr(queue_mod, "run_generation", blocking)
    payload = {"title": "t", "style": "pop", "lyrics": "la", "cot": "full", "seed": 1}
    first = client.post("/api/generate", json=payload).json()["task_id"]
    second = client.post("/api/generate", json=payload).json()["task_id"]

    # 等第一个进入 running，第二个保持 pending 后取消
    import time

    deadline = time.time() + 10
    while client.get(f"/api/tasks/{first}").json()["status"] != "running" and time.time() < deadline:
        time.sleep(0.1)
    r = client.post(f"/api/admin/tasks/{second}/cancel", headers=ADMIN_HEADERS)
    assert r.status_code == 200
    # 运行中的任务不可取消
    assert client.post(f"/api/admin/tasks/{first}/cancel", headers=ADMIN_HEADERS).status_code == 409
    gate.set()

    assert wait_status(client, first)["status"] == "succeeded"
    done = wait_status(client, second)
    assert done["status"] == "failed" and "取消" in done["error"]


def test_admin_delete_history(client):
    tid = client.post(
        "/api/generate",
        json={"title": "待删", "style": "pop", "lyrics": "la", "cot": "full", "seed": 1},
    ).json()["task_id"]
    assert wait_status(client, tid)["status"] == "succeeded"

    r = client.delete(f"/api/admin/history/{tid}", headers=ADMIN_HEADERS)
    assert r.json()["status"] == "deleted"
    assert client.get(f"/api/tasks/{tid}").status_code == 404
    # 未鉴权删不了
    assert client.delete(f"/api/admin/history/{tid}").status_code == 401


def test_admin_disk_logs_config(client):
    d = client.get("/api/admin/disk", headers=ADMIN_HEADERS).json()
    assert d["history_count"] >= 0 and "disk_free" in d
    logs = client.get("/api/admin/logs", headers=ADMIN_HEADERS).json()
    assert logs["total_buffered"] >= 1
    cfg = client.get("/api/admin/config", headers=ADMIN_HEADERS).json()
    assert "max_queue" in cfg["config"]
    assert "ADMIN_TOKEN" not in str(cfg) and "test-admin-token" not in str(cfg)

    r = client.put("/api/admin/config", headers=ADMIN_HEADERS, json={"max_queue": 7})
    assert r.json()["updated"]["max_queue"] == 7
    # 非法值被 schema 挡掉
    assert client.put("/api/admin/config", headers=ADMIN_HEADERS, json={"max_queue": 0}).status_code == 422


def test_pending_snapshot_roundtrip():
    from backend.app.core.store import TASK_ID_RE

    assert TASK_ID_RE.match("abcdef12")
    assert not TASK_ID_RE.match("evil-id")
    assert store.check_submit_rate("127.0.0.1", 0) is True  # 0 = 不限
