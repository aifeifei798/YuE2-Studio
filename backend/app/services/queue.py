"""单 GPU worker：串行消费队列，支持取消排队中的任务。"""
from __future__ import annotations

import asyncio
import logging

from ..core import store
from .inference import cleanup_task_files, run_generation

log = logging.getLogger("yue2-studio")


async def worker_loop() -> None:
    log.info("GPU worker 启动，串行消费队列")
    while True:
        task_id = await store.task_queue.get()
        task = store.tasks.get(task_id)
        if task is None:
            store.task_queue.task_done()
            continue
        # 被管理端取消的排队任务直接跳过执行
        if task.get("cancel_requested"):
            task["status"] = "failed"
            task["error"] = "任务已被管理员取消"
            task["finished_at"] = store.now_str()
            log.info("⏭ 跳过已取消任务 [%s]", task_id)
            store.task_queue.task_done()
            continue
        task["status"] = "running"
        task["started_at"] = store.now_str()
        log.info("🎵 开始生成 [%s] 歌名=%s Seed=%s", task_id, task["title"], task["seed"])
        try:
            record = await asyncio.to_thread(run_generation, task)
            task["status"] = "succeeded"
            task["finished_at"] = store.now_str()
            task["record"] = record
            store.add_history_record(record)
            log.info("✅ 生成成功 [%s]", task_id)
        except Exception as e:
            log.exception("❌ 生成失败 [%s]", task_id)
            task["status"] = "failed"
            task["finished_at"] = store.now_str()
            task["error"] = (str(e)[:500] or "生成失败")
            cleanup_task_files(task_id)
        finally:
            store.task_queue.task_done()
