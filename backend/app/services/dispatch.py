"""任务派发（在 API 与 Worker 之间解耦）。

为什么单独一层：API 进程**不应该**依赖 Celery 的具体任务实现，
否则 P6 调整 worker 结构时会牵动 API。这里只负责「把 task_id 送进队列」。

失败语义：broker 不可用时**不抛异常**，任务保持 `queued` 状态。
这样 broker 恢复后，T14 的僵尸扫描 / 队列表扫描可以把它重新投递，
用户不会看到「提交失败」—— 因为任务确实已经落库了。
"""

from __future__ import annotations

from app.core.logging import bind_task, get_logger

log = get_logger(__name__)

# 优先级 → 队列名。数字越小越紧急（1 最高）。
_QUEUE_BY_PRIORITY = {
    1: "gen_high",
    2: "gen_high",
    3: "gen_high",
    4: "gen_default",
    5: "gen_default",
    6: "gen_default",
    7: "gen_low",
    8: "gen_low",
    9: "gen_low",
}


def queue_for_priority(priority: int) -> str:
    return _QUEUE_BY_PRIORITY.get(priority, "gen_default")


def enqueue_task(task_id: int, priority: int = 5) -> bool:
    """把任务投入队列。返回是否投递成功。

    P6 会实现 `app.worker.tasks.execute_task`；在那之前导入失败是预期的，
    所以这里用 try/except 包住，并降级为「仅记录日志」。
    """
    try:
        from app.worker.tasks import execute_task
    except ImportError:
        log.info(
            "dispatch.skipped (worker 未就绪，P6 实现)",
            extra=bind_task(task_id=task_id, priority=priority),
        )
        return False

    try:
        execute_task.apply_async(
            args=[task_id], queue=queue_for_priority(priority)
        )
        log.info(
            "dispatch.enqueued",
            extra=bind_task(task_id=task_id, queue=queue_for_priority(priority)),
        )
        return True
    except Exception as exc:  # broker 不可用不应让提交失败
        log.warning(
            "dispatch.broker_unavailable err=%s",
            str(exc)[:200],
            extra=bind_task(task_id=task_id),
        )
        return False
