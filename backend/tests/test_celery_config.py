"""Celery 配置的不变量（P6-02）。

配置类的问题有一个共同特征：**错了不会报错，只会在运行时表现成"平台很怪"** ——
优先级队列被删掉只是"高优先级任务没有插队"，beat 指向不存在的任务只是定时兜底
永远不生效，并发没锁死只是偶尔 OOM。所以这些不变量值得用测试钉住。

⚠️ 本文件 import 了 `app.worker.tasks` 并**不是**多余的：任务是靠模块 import 时的
装饰器注册的，生产环境由 `include=["app.worker.tasks"]` 触发；测试里必须显式 import，
否则 `celery_app.tasks` 里只有 celery 自带的几个任务（下面 `test_tasks_module_is_included`
就是钉这一点的）。
"""

from __future__ import annotations

import pytest

from app.core.config import settings
from app.services.dispatch import queue_for_priority
from app.worker import tasks as worker_tasks
from app.worker.celery_app import celery_app


def test_tasks_module_is_included() -> None:
    """`include` 是任务能被注册的**唯一**生产保障。

    漏了它的表现是：worker 起来了、队列也在收消息，但每个任务都报
    "Received unregistered task" —— 而 beat 里的兜底任务会**静默不执行**。
    """
    assert "app.worker.tasks" in celery_app.conf.include


def test_gpu_concurrency_is_one() -> None:
    """NFR-2：GPU 并发恒为 1。两重保证都要在（配置 + Redis 独占位）。"""
    assert celery_app.conf.worker_concurrency == 1
    # 预取 1：否则任务会堆在单个 worker 内存里，取消也取消不掉
    assert celery_app.conf.worker_prefetch_multiplier == 1


def test_priority_queues_exist() -> None:
    names = {q.name for q in celery_app.conf.task_queues}
    assert {"gen_high", "gen_default", "gen_low", "maintenance"} <= names
    assert celery_app.conf.task_default_queue == "gen_default"


@pytest.mark.parametrize("priority", [1, 2, 3, 4, 5, 6, 7, 8, 9])
def test_every_priority_maps_to_a_declared_queue(priority: int) -> None:
    """每个优先级都必须落到一个**已声明**的队列上。

    否则投递到一个不存在的队列时，kombu 会直接创建它 —— 而没有任何 worker
    监听它，任务会静静地躺在那里（表现是"提交了但一直不跑"）。
    """
    declared = {q.name for q in celery_app.conf.task_queues}
    assert queue_for_priority(priority) in declared


def test_priority_ordering_is_high_default_low() -> None:
    """数字越小越紧急，且三段区间不能重叠或越界。"""
    assert queue_for_priority(1) == "gen_high"
    assert queue_for_priority(3) == "gen_high"
    assert queue_for_priority(5) == "gen_default"
    assert queue_for_priority(7) == "gen_low"
    assert queue_for_priority(9) == "gen_low"
    assert queue_for_priority(settings.default_priority) == "gen_default"


def test_beat_tasks_are_registered() -> None:
    """beat 条目必须指向**已注册**的任务。

    指向不存在的任务时 Celery 不会报错，只是那个定时任务永远不执行 ——
    于是"兜底扫描"形同不存在，直到有一天任务真的卡住了才被发现。
    """
    schedule = celery_app.conf.beat_schedule
    assert schedule, "beat 兜底任务不能为空（AC-5.1 依赖它）"
    for name, entry in schedule.items():
        assert entry["task"] in celery_app.tasks, f"beat 条目 {name} 指向未注册的任务"


def test_core_tasks_are_registered() -> None:
    for task in (
        worker_tasks.execute_task,
        worker_tasks.requeue_orphans,
        worker_tasks.recover_zombies,
    ):
        assert task.name in celery_app.tasks


def test_acks_late_is_enabled() -> None:
    """`acks_late` + `reject_on_worker_lost`：worker 崩了任务要能重回队列，
    与 T14 心跳超时恢复形成双保险。"""
    assert celery_app.conf.task_acks_late is True
    assert celery_app.conf.task_reject_on_worker_lost is True


def test_time_limits_exceed_task_timeout() -> None:
    """Celery 的硬限必须**大于**业务超时。

    反过来的话，业务超时还没来得及走"可重试"路径，任务就先被 Celery 硬杀了 ——
    状态会停在 running，只能等心跳超时兜底，多绕一大圈。
    """
    assert celery_app.conf.task_soft_time_limit > settings.task_timeout_seconds
    assert celery_app.conf.task_time_limit > celery_app.conf.task_soft_time_limit


def test_broker_outage_fails_fast() -> None:
    """broker / 结果后端不可达时必须**快速**放弃。

    `dispatch.enqueue_task` 就在 `POST /tasks` 的返回路径上；默认参数下 Celery
    会阻塞约 19 秒（实测）才放弃，直接把 NFR-1 的提交响应预算击穿。
    这里锁住"重试次数很少"，防止有人把参数改回默认值。
    """
    result_retry = celery_app.conf.result_backend_transport_options["retry_policy"]
    assert result_retry["max_retries"] <= 1
    assert result_retry["interval_max"] <= 1
    assert celery_app.conf.task_publish_retry_policy["max_retries"] <= 1
