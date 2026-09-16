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
    include=["app.worker.tasks", "app.worker.maintenance"],
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
    # GPU 并发 = 1 的第二重保证（第一重是 concurrency.GpuSlot 的 Redis 独占位）。
    # 这里写明是让"默认行为"也正确：漏传 --concurrency 时不会变成按核数并发。
    worker_concurrency=1,
)

# ---------- 不可达时必须**快速**失败，而不是慢慢等 ----------
#
# 背景（实测）：broker/结果后端不可达时，默认参数下的 `apply_async` 会阻塞
# **19.3 秒**才放弃（实测值），报的是
# "Retry limit exceeded while trying to reconnect to the Celery result store backend"。
# 而 `dispatch.enqueue_task` 就在 `POST /tasks` 的返回路径上 ——
# 于是"broker 挂掉"会把提交接口从 <200ms 拖成 19s（NFR-1 直接被击穿），
# 批量提交 1000 张更是灾难。
#
# 设计意图本来就是"broker 不可用时不让提交失败，任务留在 queued 等兜底扫描补投"
# （见 `services/dispatch.py`），这个意图只有在**快速失败**时才成立。
celery_app.conf.update(
    # 结果后端的重试是 19 秒的真凶（默认 max_retries=20 / interval_step=1s）
    result_backend_transport_options={
        "retry_policy": {
            "max_retries": 1,
            "interval_start": 0,
            "interval_step": 0.1,
            "interval_max": 0.3,
        },
        "socket_connect_timeout": 2,
        "socket_timeout": 2,
    },
    broker_transport_options={"socket_connect_timeout": 2, "socket_timeout": 2},
    redis_socket_connect_timeout=2,
    redis_socket_timeout=2,
    # 发布本身最多重试 1 次 —— 重试对"服务没起来"这种情况没有帮助，
    # 只会把 API 的延迟堆上去；真正的兜底是 beat 里的 requeue_orphans。
    task_publish_retry=True,
    task_publish_retry_policy={
        "max_retries": 1,
        "interval_start": 0,
        "interval_step": 0.1,
        "interval_max": 0.3,
    },
    # worker 启动时不要无限重连（由部署层负责拉起与重启）
    broker_connection_retry_on_startup=False,
    broker_connection_max_retries=1,
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

# 定时兜底（P6-02 / AC-5.1「无任务永久停留非终态」）。
# 两个任务互补：一个救"没被消费的排队任务"，一个救"Worker 挂掉留下的僵尸"。
celery_app.conf.beat_schedule = {
    "requeue-orphans": {
        "task": "app.worker.tasks.requeue_orphans",
        "schedule": 60.0,
        "options": {"queue": "maintenance"},
    },
    "recover-zombies": {
        "task": "app.worker.tasks.recover_zombies",
        "schedule": 60.0,
        "options": {"queue": "maintenance"},
    },
    # 过期素材/产物的物理清理（P6-10）。**每 6 小时**，与上面两条 60 秒级的兜底
    # 刻意拉开量级，理由：
    #
    # 1. **它不是活性问题**。上面两条兜底保的是 AC-5.1「无任务永久停留非终态」——
    #    停 60 秒就能被用户看见（"我的图怎么不跑了"），所以必须分钟级。
    #    清理晚几个小时执行，用户和指标都不会有任何感觉：保留期本身就是"天"级的。
    # 2. **但也不能拖到一天一次**。数据盘是这台机器上最紧的资源（EX-4 disk_full，
    #    配置里的 disk_min_free_gb），而清理是**唯一**能把空间还回来的手段。
    #    6 小时让「实际保留期」的偏差最多 6 小时，并且积压时的排空速度是
    #    每天 4 × cleanup_batch_limit（500）= 2000 条，而不是 500 条。
    # 3. **频率越高越好也是错的**。worker 的 concurrency 是 1（NFR-2），
    #    这个任务和出图任务共用同一个 worker 进程 —— 每分钟扫一遍库既白烧 DB
    #    连接，又和出图主链路抢那个唯一的执行位。
    #
    # 结果：6 小时是「空间能及时还回来」与「不去干扰出图」之间的折中。
    "cleanup-assets": {
        "task": "app.worker.maintenance.cleanup_assets",
        "schedule": 21600.0,  # 6 小时
        "options": {"queue": "maintenance"},
    },
}
