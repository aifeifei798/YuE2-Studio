"""公开接口测试：校验、入队全链路、限流、已删除接口的兼容性。"""
from __future__ import annotations

from backend.app.core.config import get_settings
from backend.app.core import store
from .conftest import wait_status


def test_healthz_ok_and_worker_alive(client):
    h = client.get("/healthz").json()
    assert h["model_loaded"] is True
    assert h["worker_alive"] is True
    assert h["status"] == "ok"
    assert "model_error" not in h  # 敏感信息不得公开


def test_generate_validation_422(client):
    r = client.post("/api/generate", json={"title": "t", "style": "", "lyrics": "", "cot": "full", "seed": None})
    assert r.status_code == 422
    r = client.post("/api/generate", json={"title": "t", "style": "pop", "lyrics": "la", "cot": "evil", "seed": None})
    assert r.status_code == 422


def test_generate_full_flow(client):
    r = client.post(
        "/api/generate",
        json={"title": "单测之歌", "style": "City Pop", "lyrics": "[Verse]\nhello", "cot": "full", "seed": 7},
    )
    assert r.status_code == 202
    tid = r.json()["task_id"]

    t = wait_status(client, tid)
    assert t["status"] == "succeeded"
    rec = t["record"]
    assert rec["audio_url"] == f"/audio/{tid}.flac"

    # 音频可下载，且无临时文件残留
    a = client.get(rec["audio_url"])
    assert a.status_code == 200 and len(a.content) > 0
    assert not list(get_settings().audio_dir.glob("*.tmp"))

    # 任务完成后排队快照最终应为空（worker 落盘与状态更新有微秒级竞态，轮询确认）
    import time

    deadline = time.time() + 5
    while store.load_pending_snapshot() != [] and time.time() < deadline:
        time.sleep(0.1)
    assert store.load_pending_snapshot() == []

    # history.json 不得经 /audio 泄露
    assert client.get("/audio/history.json").status_code == 404
    assert client.get("/audio/pending.json").status_code == 404


def test_task_id_validation(client):
    assert client.get("/api/tasks/evil-id").status_code == 400
    assert client.get("/api/tasks/00000000").status_code == 404


def test_public_delete_gone(client):
    # 公开删除已收归管理端：老路径不再存在
    assert client.delete("/api/history/12345678").status_code in (404, 405)


def test_rate_limit(client):
    s = get_settings()
    old = s.submit_per_hour
    s.submit_per_hour = 2
    try:
        payload = {"title": "t", "style": "pop", "lyrics": "la", "cot": "full", "seed": 1}
        assert client.post("/api/generate", json=payload).status_code == 202
        assert client.post("/api/generate", json=payload).status_code == 202
        r = client.post("/api/generate", json=payload)
        assert r.status_code == 429
    finally:
        s.submit_per_hour = old
