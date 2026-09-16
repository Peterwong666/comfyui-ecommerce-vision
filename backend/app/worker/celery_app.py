"""Celery 应用（P2-08 建立骨架，P6 填充任务）。

ADR-004：Celery + Redis。Celery 相对 RQ/Dramatiq 的优势是
支持优先级队列与成熟的重试策略，这两点本项目都需要
（优先级队列 FR-3.4；重试策略 FR-4.4）。
"""

from __future__ import annotations

from celery import Celery
from kombu import Queue

from app.core.config import settings

celery_app = Celery(
    "comfyui_platform",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=["app.worker.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="Asia/Shanghai",
    enable_utc=True,
    # 任务确认策略：worker 崩溃时任务重回队列，配合 T14 僵尸恢复形成双保险
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    # 预取 1：GPU 并发固定为 1（NFR-2），预取多个只会让任务堆在单个 worker 内存里
    worker_prefetch_multiplier=1,
    task_track_started=True,
    result_expires=3600 * 24 * 7,
    # 长任务：出图可能几十秒到几分钟，软限要留够
    task_soft_time_limit=settings.task_timeout_seconds + 60,
    task_time_limit=settings.task_timeout_seconds + 120,
)

# 优先级队列。数字越小越紧急（与 Task.priority 语义一致）。
# GPU 并发 = 1，所以队列的作用不是并行，而是**决定下一个跑谁**。
celery_app.conf.task_queues = (
    Queue("gen_high"),
    Queue("gen_default"),
    Queue("gen_low"),
    Queue("maintenance"),  # 清理、成本统计等非实时任务
)
celery_app.conf.task_default_queue = "gen_default"

# 定时任务（P9/P10 用）
celery_app.conf.beat_schedule = {}
