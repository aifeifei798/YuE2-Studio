"""单 GPU worker：串行消费队列，支持取消排队中的任务。"""
from __future__ import annotations

import asyncio
import logging

from ..core import store
from .inference import cleanup_task_files, run_generation

log = logging.getLogger("yue2-studio")


async def worker_loop() -> None:
    log.info("GPU worker 启动，串行消费队列")
    store.beat()
    while True:
        try:
            await _consume_one()
        except asyncio.CancelledError:
            raise
        except Exception:
            # 看门狗：任何意外异常都不能带走 worker，否则队列静默卡死；
            # 退避 1s，避免瞬间失败时空转打爆 CPU/日志
            log.exception("worker 迭代异常，已捕获并继续")
            try:
                await asyncio.sleep(1)
            except asyncio.CancelledError:
                raise
        finally:
            store.beat()
            store.save_pending_snapshot()


async def _consume_one() -> None:
    task_id = await store.task_queue.get()
    try:
        task = store.tasks.get(task_id)
        if task is None:
            return
        # 被管理端取消的排队任务直接跳过执行
        if task.get("cancel_requested"):
            task["status"] = "failed"
            task["error"] = "任务已被管理员取消"
            task["finished_at"] = store.now_str()
            log.info("⏭ 跳过已取消任务 [%s]", task_id)
            return
        task["status"] = "running"
        task["started_at"] = store.now_str()
        log.info("🎵 开始生成 [%s] 歌名=%s Seed=%s", task_id, task["title"], task["seed"])
        try:
            record = await asyncio.to_thread(run_generation, task)
            task["record"] = record
            # 归属 + 配额：只有成功生成的才计入 key 用量（失败/取消不计）
            if task.get("owner"):
                record["owner"] = task["owner"]
            if task.get("key_id") is not None:
                store.increment_used(task["key_id"])
            store.add_history_record(record)
            # 先落盘再标成功：轮询到 succeeded 即代表可查（否则客户端有竞态）
            task["status"] = "succeeded"
            task["finished_at"] = store.now_str()
            store.save_pending_snapshot()
            log.info("✅ 生成成功 [%s]", task_id)
        except Exception as e:
            log.exception("❌ 生成失败 [%s]", task_id)
            task["status"] = "failed"
            task["finished_at"] = store.now_str()
            task["error"] = (str(e)[:500] or "生成失败")
            cleanup_task_files(task_id)
            store.save_pending_snapshot()
    finally:
        # 无论成败/取消/异常，计数器必须归还，否则队列统计永久泄漏
        store.task_queue.task_done()
