"""状态机单元测试。

覆盖 `docs/prd/PRD_v1.md` §5.6 的验收标准 AC-5.1~AC-5.4。
这是全项目最重要的测试文件 —— 状态机错了，界面显示、良品率、
成本核算会同时错，而且很难在集成测试里发现。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.enums import BatchStatus, ErrorType, TaskStatus
from app.models.task import Batch, Task
from app.services import state_machine as sm


def _make_task(db: Session, user, workflow, **kw) -> Task:  # type: ignore[no-untyped-def]
    task = sm.create_task(
        db,
        user_id=user.id,
        workflow_id=workflow.id,
        params=kw.pop("params", {"steps": 25}),
        **kw,
    )
    db.commit()
    return task


# --- T1/T2/T5 正常路径 ---------------------------------------------------------


def test_create_task_starts_pending(db: Session, user, workflow) -> None:
    """T1：提交后为 pending。"""
    task = _make_task(db, user, workflow)
    assert task.status == TaskStatus.PENDING.value


def test_happy_path_pending_to_succeeded(db: Session, user, workflow) -> None:
    """T2→T5→T6：完整成功路径，且记录排队时长与 GPU 秒。"""
    task = _make_task(db, user, workflow)
    sm.enqueue(db, task)
    assert task.status == TaskStatus.QUEUED.value
    assert task.queued_at is not None

    sm.mark_running(db, task)
    assert task.status == TaskStatus.RUNNING.value
    assert task.heartbeat_at is not None

    sm.mark_succeeded(db, task, duration_ms=12000, gpu_seconds=12.0, gpu_mem_peak_mb=9000)
    assert task.status == TaskStatus.SUCCEEDED.value
    assert task.duration_ms == 12000
    assert task.gpu_seconds == 12.0
    assert task.finished_at is not None


# --- 非法迁移必须报错（fail fast） ---------------------------------------------


def test_illegal_transition_raises(db: Session, user, workflow) -> None:
    """pending 不能直接到 succeeded —— 必须经过 queued/running。"""
    task = _make_task(db, user, workflow)
    with pytest.raises(sm.InvalidTransition):
        sm.mark_succeeded(db, task)


def test_terminal_state_cannot_transition_again(db: Session, user, workflow) -> None:
    """成功是终态，不允许再迁移（AC-5.3 的隐性要求：不会重复产出）。"""
    task = _make_task(db, user, workflow)
    sm.enqueue(db, task)
    sm.mark_running(db, task)
    sm.mark_succeeded(db, task)
    with pytest.raises(sm.InvalidTransition):
        sm.mark_succeeded(db, task)


def test_pending_cannot_run_directly(db: Session, user, workflow) -> None:
    task = _make_task(db, user, workflow)
    with pytest.raises(sm.InvalidTransition):
        sm.mark_running(db, task)


# --- T7/T8/T9 重试（PRD §5.4 的核心设计） -------------------------------------


def test_oom_is_retried(db: Session, user, workflow) -> None:
    """OOM 是可重试错误：降级后可能成功，不该直接判死（AC-6.1）。"""
    task = _make_task(db, user, workflow)
    sm.enqueue(db, task)
    sm.mark_running(db, task)

    should_retry = sm.mark_retrying(db, task, ErrorType.OOM, "CUDA out of memory")
    assert should_retry is True
    assert task.status == TaskStatus.RETRYING.value
    assert task.retry_count == 1

    sm.enqueue(db, task)
    assert task.status == TaskStatus.QUEUED.value


def test_retry_exhausted_becomes_failed(db: Session, user, workflow) -> None:
    """T9：重试超过上限 → failed。"""
    task = _make_task(db, user, workflow)
    for i in range(settings.task_max_retries):
        sm.enqueue(db, task)
        sm.mark_running(db, task)
        assert sm.mark_retrying(db, task, ErrorType.OOM, "oom") is True
        assert task.retry_count == i + 1

    # 再失败一次就耗尽
    sm.enqueue(db, task)
    sm.mark_running(db, task)
    assert sm.mark_retrying(db, task, ErrorType.OOM, "oom") is False
    assert task.status == TaskStatus.FAILED.value
    assert task.finished_at is not None


def test_invalid_param_is_not_retried(db: Session, user, workflow) -> None:
    """致命错误（非法参数）不重试。

    这是 PRD §5.4 T7 vs T11 的核心区分：重试一万次还是错，
    若当成可重试会让队列被必然失败的任务堵死。
    """
    task = _make_task(db, user, workflow)
    sm.enqueue(db, task)
    sm.mark_running(db, task)

    should_retry = sm.mark_retrying(db, task, ErrorType.INVALID_PARAM, "参数越界")
    assert should_retry is False
    assert task.status == TaskStatus.FAILED.value


def test_model_missing_is_not_retried(db: Session, user, workflow) -> None:
    """EX-9：模型缺失是致命错误，重试无意义。"""
    task = _make_task(db, user, workflow)
    sm.enqueue(db, task)
    sm.mark_running(db, task)
    assert sm.mark_retrying(db, task, ErrorType.MODEL_MISSING, "模型不存在") is False
    assert task.status == TaskStatus.FAILED.value


def test_error_type_retryable_classification() -> None:
    """错误分类表本身要正确，否则上面所有重试逻辑都是错的。"""
    assert ErrorType.OOM.is_retryable
    assert ErrorType.TIMEOUT.is_retryable
    assert ErrorType.WORKER_CRASH.is_retryable
    assert not ErrorType.INVALID_PARAM.is_retryable
    assert not ErrorType.MODEL_MISSING.is_retryable
    assert not ErrorType.QUOTA_EXCEEDED.is_retryable


# --- T10/T3/T4 取消 ------------------------------------------------------------


def test_cancel_from_pending(db: Session, user, workflow) -> None:
    """T3。"""
    task = _make_task(db, user, workflow)
    sm.mark_canceled(db, task)
    assert task.status == TaskStatus.CANCELED.value


def test_cancel_from_queued(db: Session, user, workflow) -> None:
    """T4。"""
    task = _make_task(db, user, workflow)
    sm.enqueue(db, task)
    sm.mark_canceled(db, task)
    assert task.status == TaskStatus.CANCELED.value


def test_cancel_from_running(db: Session, user, workflow) -> None:
    """T10：执行中取消（AC-5.2 要求在步间隙生效，这里只验证状态迁移）。"""
    task = _make_task(db, user, workflow)
    sm.enqueue(db, task)
    sm.mark_running(db, task)
    sm.mark_canceled(db, task)
    assert task.status == TaskStatus.CANCELED.value


def test_cancellable_set_matches_prd() -> None:
    """PRD §5.2：只有 4 个非终态可取消。"""
    assert TaskStatus.cancellable() == {
        TaskStatus.PENDING,
        TaskStatus.QUEUED,
        TaskStatus.RUNNING,
        TaskStatus.RETRYING,
    }
    assert TaskStatus.SUCCEEDED not in TaskStatus.cancellable()
    assert TaskStatus.FAILED not in TaskStatus.cancellable()


# --- T13 重试（断点续跑） ------------------------------------------------------


def test_reset_for_retry_preserves_history(db: Session, user, workflow) -> None:
    """T13：手动重试回到 pending。

    **保留** retry_count 与 error_type 是刻意的 ——
    排障需要知道它之前失败过几次、为什么失败。
    """
    task = _make_task(db, user, workflow)
    sm.enqueue(db, task)
    sm.mark_running(db, task)
    sm.mark_failed(db, task, ErrorType.MODEL_MISSING, "模型缺失")

    sm.reset_for_retry(db, task)
    assert task.status == TaskStatus.PENDING.value
    assert task.error_type == ErrorType.MODEL_MISSING.value  # 保留
    assert task.started_at is None
    assert task.finished_at is None


def test_reset_for_retry_from_canceled(db: Session, user, workflow) -> None:
    task = _make_task(db, user, workflow)
    sm.mark_canceled(db, task)
    sm.reset_for_retry(db, task)
    assert task.status == TaskStatus.PENDING.value


def test_cannot_retry_from_succeeded(db: Session, user, workflow) -> None:
    """已成功的不能重试 —— 否则会重复产出（AC-5.3）。"""
    task = _make_task(db, user, workflow)
    sm.enqueue(db, task)
    sm.mark_running(db, task)
    sm.mark_succeeded(db, task)
    with pytest.raises(sm.InvalidTransition):
        sm.reset_for_retry(db, task)


# --- EX-6 幂等 -----------------------------------------------------------------


def test_duplicate_submit_returns_same_task(db: Session, user, workflow) -> None:
    """EX-6 / AC-6.3：同用户同幂等键重复提交返回同一个任务。"""
    t1 = sm.create_task(
        db,
        user_id=user.id,
        workflow_id=workflow.id,
        params={},
        idempotency_key="abc123",
    )
    db.commit()
    t2 = sm.create_task(
        db,
        user_id=user.id,
        workflow_id=workflow.id,
        params={},
        idempotency_key="abc123",
    )
    db.commit()
    assert t1.id == t2.id


def test_different_idempotency_keys_create_new_tasks(db: Session, user, workflow) -> None:
    t1 = sm.create_task(
        db, user_id=user.id, workflow_id=workflow.id, params={}, idempotency_key="k1"
    )
    db.commit()
    t2 = sm.create_task(
        db, user_id=user.id, workflow_id=workflow.id, params={}, idempotency_key="k2"
    )
    db.commit()
    assert t1.id != t2.id


def test_no_idempotency_key_always_creates_new(db: Session, user, workflow) -> None:
    """不带幂等键时不生效去重（绝大多数场景）。"""
    t1 = sm.create_task(db, user_id=user.id, workflow_id=workflow.id, params={})
    db.commit()
    t2 = sm.create_task(db, user_id=user.id, workflow_id=workflow.id, params={})
    db.commit()
    assert t1.id != t2.id


# --- T14 / EX-7 僵尸恢复 -------------------------------------------------------


def test_recover_zombie_tasks(db: Session, user, workflow) -> None:
    """Worker 被 kill -9 后，靠心跳超时被动检测（AC-6.4）。"""
    task = _make_task(db, user, workflow)
    sm.enqueue(db, task)
    sm.mark_running(db, task)

    # 伪造过期心跳（超过 heartbeat timeout）
    task.heartbeat_at = datetime.now(timezone.utc) - timedelta(
        seconds=settings.task_heartbeat_timeout_seconds + 60
    )
    db.commit()

    recovered = sm.recover_zombie_tasks(db)
    db.commit()

    assert recovered == 1
    db.refresh(task)
    assert task.status == TaskStatus.QUEUED.value  # 回到队列而非直接失败
    assert task.error_type == ErrorType.WORKER_CRASH.value
    assert task.retry_count == 1


def test_recover_ignores_fresh_heartbeat(db: Session, user, workflow) -> None:
    """心跳新鲜的运行中任务不能被误判为僵尸。"""
    task = _make_task(db, user, workflow)
    sm.enqueue(db, task)
    sm.mark_running(db, task)  # 心跳 = now
    db.commit()

    assert sm.recover_zombie_tasks(db) == 0
    db.refresh(task)
    assert task.status == TaskStatus.RUNNING.value


def test_recover_ignores_non_running(db: Session, user, workflow) -> None:
    """只有 running 才可能是僵尸。"""
    _make_task(db, user, workflow)  # pending，无心跳
    db.commit()
    assert sm.recover_zombie_tasks(db) == 0


# --- AC-5.1 无任务永久停留非终态 -----------------------------------------------


def test_find_timed_out(db: Session, user, workflow) -> None:
    """EX-2：识别执行超时的任务。"""
    task = _make_task(db, user, workflow)
    sm.enqueue(db, task)
    sm.mark_running(db, task)
    task.started_at = datetime.now(timezone.utc) - timedelta(
        seconds=settings.task_timeout_seconds + 10
    )
    db.commit()

    timed_out = sm.find_timed_out(db)
    assert len(timed_out) == 1
    assert timed_out[0].id == task.id


def test_find_timed_out_excludes_fresh(db: Session, user, workflow) -> None:
    task = _make_task(db, user, workflow)
    sm.enqueue(db, task)
    sm.mark_running(db, task)
    db.commit()
    assert sm.find_timed_out(db) == []


# --- T12 父任务聚合（AC-5.4） --------------------------------------------------


def _make_batch(db: Session, user, workflow, total: int) -> Batch:  # type: ignore[no-untyped-def]
    batch = Batch(
        user_id=user.id,
        workflow_id=workflow.id,
        status=BatchStatus.PENDING.value,
        total_count=total,
    )
    db.add(batch)
    db.flush()
    return batch


def _add_child(db: Session, batch: Batch, idx: int, user, workflow) -> Task:  # type: ignore[no-untyped-def]
    return sm.create_task(
        db,
        user_id=user.id,
        workflow_id=workflow.id,
        batch_id=batch.id,
        idx=idx,
        params={},
    )


def test_batch_all_succeeded(db: Session, user, workflow) -> None:
    batch = _make_batch(db, user, workflow, 2)
    for i in (1, 2):
        t = _add_child(db, batch, i, user, workflow)
        sm.enqueue(db, t)
        sm.mark_running(db, t)
        sm.mark_succeeded(db, t)
    db.commit()
    db.refresh(batch)
    assert batch.status == BatchStatus.SUCCEEDED.value
    assert batch.succeeded_count == 2
    assert batch.progress == 1.0


def test_batch_partial(db: Session, user, workflow) -> None:
    """核心场景：500 张成功 497 失败 3 → partial。

    既不能标 succeeded（掩盖问题），也不能标 failed（用户以为全废）。
    """
    batch = _make_batch(db, user, workflow, 2)
    ok = _add_child(db, batch, 1, user, workflow)
    sm.enqueue(db, ok)
    sm.mark_running(db, ok)
    sm.mark_succeeded(db, ok)

    bad = _add_child(db, batch, 2, user, workflow)
    sm.enqueue(db, bad)
    sm.mark_running(db, bad)
    sm.mark_failed(db, bad, ErrorType.MODEL_MISSING, "x")
    db.commit()

    db.refresh(batch)
    assert batch.status == BatchStatus.PARTIAL.value
    assert batch.succeeded_count == 1
    assert batch.failed_count == 1


def test_batch_all_failed(db: Session, user, workflow) -> None:
    batch = _make_batch(db, user, workflow, 2)
    for i in (1, 2):
        t = _add_child(db, batch, i, user, workflow)
        sm.enqueue(db, t)
        sm.mark_running(db, t)
        sm.mark_failed(db, t, ErrorType.MODEL_MISSING, "x")
    db.commit()
    db.refresh(batch)
    assert batch.status == BatchStatus.FAILED.value


def test_batch_running_while_children_pending(db: Session, user, workflow) -> None:
    batch = _make_batch(db, user, workflow, 3)
    t = _add_child(db, batch, 1, user, workflow)
    sm.enqueue(db, t)
    sm.mark_running(db, t)
    sm.mark_succeeded(db, t)
    _add_child(db, batch, 2, user, workflow)  # 仍 pending
    db.commit()
    db.refresh(batch)
    assert batch.status == BatchStatus.RUNNING.value
    assert batch.total_count == 3
    assert batch.progress == pytest.approx(1 / 3)


def test_batch_all_canceled(db: Session, user, workflow) -> None:
    batch = _make_batch(db, user, workflow, 2)
    for i in (1, 2):
        t = _add_child(db, batch, i, user, workflow)
        sm.mark_canceled(db, t)
    db.commit()
    db.refresh(batch)
    assert batch.status == BatchStatus.CANCELED.value


def test_derive_status_is_pure() -> None:
    """derive_status 必须是纯函数，便于覆盖全部分支（AC-5.4）。"""
    b = Batch(total_count=4, succeeded_count=4, failed_count=0, canceled_count=0)
    assert b.derive_status() == BatchStatus.SUCCEEDED

    b = Batch(total_count=4, succeeded_count=1, failed_count=3, canceled_count=0)
    assert b.derive_status() == BatchStatus.PARTIAL

    b = Batch(total_count=4, succeeded_count=0, failed_count=4, canceled_count=0)
    assert b.derive_status() == BatchStatus.FAILED

    b = Batch(total_count=4, succeeded_count=4, failed_count=0, canceled_count=0)
    b.total_count = 5
    assert b.derive_status() == BatchStatus.RUNNING
