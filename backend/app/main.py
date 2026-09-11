"""FastAPI 装配：lifespan / CORS / 静态 / 路由。"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .core.config import get_settings
from .core.store import (
    TASK_ID_RE,
    RingBufferHandler,
    get_all_history,
    migrate_legacy_flat_outputs,
    tasks,
)
from .routers import admin as admin_router
from .routers import public as public_router
from .services.inference import load_model_blocking
from .services.queue import worker_loop
from .core import store as store_module

log = logging.getLogger("yue2-studio")


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    # 日志：控制台 + 内存环（供管理页拉取）
    handler = RingBufferHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)-7s | %(message)s"))
    logging.getLogger().addHandler(handler)
    logging.getLogger("yue2-studio").addHandler(handler)

    migrate_legacy_flat_outputs()
    for rec in get_all_history():
        tid = rec.get("task_id")
        if isinstance(tid, str) and TASK_ID_RE.match(tid) and tid not in tasks:
            tasks[tid] = {"task_id": tid, "status": "succeeded", "record": rec, **rec}
    await asyncio.to_thread(load_model_blocking)
    store_module.worker_task = asyncio.create_task(worker_loop())
    yield
    if store_module.worker_task:
        store_module.worker_task.cancel()


def create_app() -> FastAPI:
    s = get_settings()
    logging.basicConfig(
        level=getattr(logging, s.log_level, logging.INFO),
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    )
    app = FastAPI(title="YuE2 Music Studio", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=s.cors_origins,
        allow_credentials=s.allow_credentials,
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["Content-Type", "Authorization", "X-Admin-Token"],
    )
    app.include_router(public_router.router)
    app.include_router(admin_router.router)

    # 音频只暴露子目录
    s.output_dir.mkdir(parents=True, exist_ok=True)
    s.audio_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/audio", StaticFiles(directory=str(s.audio_dir)), name="audio")

    # 前端：唯一来源 web/dist（Vite 构建产物）。未构建则明确报错，不再回退老单文件。
    dist = s.web_dist
    dist_index = dist / "index.html"
    if dist.exists():
        assets = dist / "assets"
        if assets.exists():
            app.mount("/assets", StaticFiles(directory=str(assets)), name="assets")

    @app.get("/", include_in_schema=False)
    def read_root():
        if dist_index.exists():
            return FileResponse(str(dist_index))
        raise HTTPException(status_code=500, detail="前端缺失：请先在 web/ 执行 npm run build")

    @app.get("/admin/{full_path:path}", include_in_schema=False)
    def admin_spa(full_path: str):
        # SPA 回退：/admin/* 都返回同一 index，保证刷新不 404
        if dist_index.exists():
            return FileResponse(str(dist_index))
        raise HTTPException(status_code=404, detail="管理前端未构建")

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=get_settings().port, workers=1)
