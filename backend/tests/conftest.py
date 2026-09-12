"""pytest 基础设施：stub yue2（重依赖 dev 环境没有）+ 隔离输出目录。

运行：仓库根执行 `python -m pytest backend/tests -q`
"""
from __future__ import annotations

import os
import sys
import tempfile
import types
from pathlib import Path

import pytest

# ---- 必须在 import backend 之前准备好 env 与 stub ----
_OUT = Path(tempfile.mkdtemp(prefix="yue2-test-"))
os.environ["YUE2_OUTPUT_DIR"] = str(_OUT)
os.environ["ADMIN_TOKEN"] = "test-admin-token"
os.environ["YUE2_SUBMIT_PER_HOUR"] = "100"
os.environ["CORS_ORIGINS"] = "http://127.0.0.1:8000,http://localhost:8000"
# 生产默认强制登录；测试里匿名用例多，此处显式关闭（单测 test_require_api_key_* 覆盖开启逻辑）
os.environ["REQUIRE_API_KEY"] = "false"

_fake = types.ModuleType("yue2")


class DummySong:
    def save(self, p):
        # 与真实 yue2 pipeline.save 一致：只接受 .flac/.wav 后缀
        assert str(p).lower().endswith((".flac", ".wav")), f"非法音频后缀: {p}"
        Path(p).write_bytes(b"FAKEFLAC")

    def save_artifacts(self, d):
        Path(d).mkdir(parents=True, exist_ok=True)
        (Path(d) / "a.txt").write_text("ok")


class DummyPipe:
    @classmethod
    def from_pretrained(cls, repo, device="cuda"):
        return cls()

    def __call__(self, style, lyrics, cot="full", seed=0):
        assert style and lyrics
        return DummySong()


_fake.YuE2Pipeline = DummyPipe
sys.modules["yue2"] = _fake

from backend.app.core.config import get_settings, reload_settings  # noqa: E402
from backend.app.core import store  # noqa: E402

reload_settings()

ADMIN_HEADERS = {"Authorization": "Bearer test-admin-token"}


@pytest.fixture(scope="session")
def client():
    """全 session 共用一个 lifespan。

    注意：asyncio.Queue 会绑定首次触碰它的 event loop，
    每个用例开关一次 TestClient 会触发新 loop，导致‘bound to a
    different event loop’。生产环境 uvicorn 只有一个 loop，
    测试侧用单 client + 逐用例清理状态来模拟。
    """
    from fastapi.testclient import TestClient
    from backend.app.main import app

    with TestClient(app) as c:
        yield c


def _reset_state():
    import time

    # 先排空在途任务：上个用例提交了但没等完成的任务会在后台落库，
    # 不等它做完就清库会漏到下个用例（表现为 total 莫名多 1）
    deadline = time.time() + 15
    while time.time() < deadline:
        if store.task_queue.qsize() == 0 and not any(
            t.get("status") in ("pending", "running") for t in store.tasks.values()
        ):
            break
        time.sleep(0.05)
    store.tasks.clear()
    while True:
        try:
            store.task_queue.get_nowait()
            store.task_queue.task_done()
        except Exception:
            break
    store.submit_hits.clear()
    out = get_settings().output_dir
    for name in ("history.json", "history.json.migrated", "pending.json"):
        try:
            (out / name).unlink()
        except OSError:
            pass
    # 历史库只清数据不清文件：删库文件会导致表丢失（建表仅 lifespan 跑一次）
    from backend.app.core import history_db

    try:
        history_db.clear_all()
        history_db.clear_keys()
    except Exception:
        pass
    reload_settings()


@pytest.fixture(autouse=True)
def _isolate():
    yield
    _reset_state()


def wait_status(client, task_id: str, timeout: float = 15.0) -> dict:
    import time

    deadline = time.time() + timeout
    while time.time() < deadline:
        t = client.get(f"/api/tasks/{task_id}").json()
        if t["status"] in ("succeeded", "failed"):
            return t
        time.sleep(0.2)
    raise AssertionError(f"任务 {task_id} 超时未结束")
