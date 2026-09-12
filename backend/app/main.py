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
    RingBufferHandler,
    cleanup_tmp_files,
    init_db,
    load_pending_snapshot,
    migrate_legacy_flat_outputs,
    task_queue,
    tasks,
)
from .routers import admin as admin_router
from .routers import auth as auth_router
from .routers import public as public_router
from .services.inference import load_model_blocking
from .services.queue import worker_loop
from .core import store as store_module

log = logging.getLogger("yue2-studio")


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    # 日志：控制台 + 内存环（供管理页拉取）；只挂 yue2-studio，不挂 root，
    # 否则 uvicorn access log 会冲掉 500 条业务日志；
    # lifespan 多次执行（如测试）也不得重复挂载，否则日志翻倍
    studio_logger = logging.getLogger("yue2-studio")
    handler = next((h for h in studio_logger.handlers if isinstance(h, RingBufferHandler)), None)
    if handler is None:
        handler = RingBufferHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)-7s | %(message)s"))
        # 只收本应用日志，不过滤掉 uvicorn 也行但会淹没管理页
        handler.addFilter(lambda r: r.name == "yue2-studio" or r.name.startswith("yue2-studio."))
        studio_logger.addHandler(handler)
    # 显式定级：basicConfig 在 root 已有 handler 时是空操作（如 pytest/uvicorn 接管），
    # 不显式 setLevel 会导致 INFO 日志到不了内存环
    logging.getLogger("yue2-studio").setLevel(getattr(logging, s.log_level, logging.INFO))

    migrate_legacy_flat_outputs()
    cleanup_tmp_files()
    init_db()  # 建表 + 老 history.json 一次性迁移
    # 注意：历史不再全量预载入内存；重启后的查询走 fetch_task 的 DB 兜底
    # 恢复上次未消费完的排队任务（运行中任务因进程结束已中断，不恢复）
    restored = 0
    for item in load_pending_snapshot():
        tid = item["task_id"]
        if tid in tasks:
            continue
        tasks[tid] = {**item, "status": "pending", "restored": True}
        task_queue.put_nowait(tid)
        restored += 1
    if restored:
        log.info("从排队快照恢复 %d 个任务", restored)
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
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Content-Type", "Authorization", "X-Admin-Token", "X-API-Key"],
    )
    app.include_router(public_router.router)
    app.include_router(auth_router.router)
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
        # 分离部署的 api 容器本就没有前端：404 + 明确提示，别用 500 误触发监控
        raise HTTPException(status_code=404, detail="前端缺失：api 容器不内置前端（请访问 web 服务）；all-in-one 请先在 web/ 执行 npm run build")

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=get_settings().port, workers=1)
