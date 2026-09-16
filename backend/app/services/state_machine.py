"""任务状态机（`docs/prd/PRD_v1.md` §5 的代码实现）。

**这是全项目业务逻辑的核心**。所有状态变更必须走这里，不允许在别处
直接 `task.status = ...` —— 否则终态校验、心跳维护、父任务聚合、埋点触发
会被绕过，PRD §5.6 的 5 条验收标准（AC-5.1~5.5）就无从保证。

迁移编号与 PRD §5.4 表格一一对应。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import bind_task, get_logger
from app.models.enums import BatchStatus, ErrorType, TaskStatus
from app.models.task import Batch, Task

log = get_logger(__name__)


class InvalidTransition(Exception):
    """非法状态迁移。代表代码 bug 或并发写入，必须暴露而不是静默忽略。"""

    def __init__(self, task_id: int, frm: str, to: str):
        super().__init__(f"任务 {task_id} 不允许 {frm} → {to}")
        self.task_id, self.frm, self.to = task_id, frm, to


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime | None) -> datetime | None:
    """统一成带 UTC 时区的 datetime。

    有些驱动**不保存时区**（SQLite 就是），`DateTime(timezone=True)` 读回来是 naive 的；
    直接与 `datetime.now(timezone.utc)` 相减会 `TypeError`。而任务从库里读出来
    再计算排队时长正是 worker 的常规路径 —— 所以这里必须兜住。

    按契约 §2.1「数据库存 UTC」的约定补时区，而不是猜本地时区。
    """
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


# 允许的迁移表，直接映射 PRD §5.4 的 T1~T14。
# 显式枚举而非「判断目标状态」，是为了让非法迁移立刻报错（fail fast）。
_ALLOWED: dict[str, frozenset[str]] = {
    TaskStatus.PENDING.value: frozenset({TaskStatus.QUEUED.value, TaskStatus.CANCELED.value}),
    TaskStatus.QUEUED.value: frozenset(
        {TaskStatus.RUNNING.value, TaskStatus.CANCELED.value}
    ),
    TaskStatus.RUNNING.value: frozenset(
        {
            TaskStatus.SUCCEEDED.value,
            TaskStatus.RETRYING.value,
            TaskStatus.FAILED.value,  # T11 致命错误
            TaskStatus.CANCELED.value,  # T10
            TaskStatus.QUEUED.value,  # T14 Worker 崩溃后恢复
        }
    ),
    TaskStatus.RETRYING.value: frozenset(
        {TaskStatus.QUEUED.value, TaskStatus.FAILED.value, TaskStatus.CANCELED.value}
    ),
    # 终态：只允许「重试」回到 pending（T13）
    TaskStatus.FAILED.value: frozenset({TaskStatus.PENDING.value}),
    TaskStatus.CANCELED.value: frozenset({TaskStatus.PENDING.value}),
    TaskStatus.SUCCEEDED.value: frozenset(),
}


def _apply(task: Task, to: TaskStatus) -> None:
    frm = task.status
    allowed = _ALLOWED.get(frm, frozenset())
    if to.value not in allowed:
        raise InvalidTransition(task.id, frm, to.value)
    task.status = to.value


# ---------------------------------------------------------------- 提交与入队


def create_task(db: Session, **kwargs: object) -> Task:
    """T1：用户提交 → pending。

    幂等（EX-6）：若同一用户带同一 idempotency_key 已提交过，直接返回已有任务，
    避免用户重复点击产生双倍扣费与双倍出图。
    """
    key = kwargs.get("idempotency_key")
    user_id = kwargs.get("user_id")
    if key and user_id:
        existing = db.scalar(
            select(Task).where(Task.user_id == user_id, Task.idempotency_key == key)
        )
        if existing is not None:
            log.info("task.duplicate_rejected", extra=bind_task(task_id=existing.id))
            return existing

    task = Task(**kwargs)  # type: ignore[arg-type]
    task.status = TaskStatus.PENDING.value
    db.add(task)
    db.flush()
    log.info("task.created", extra=bind_task(task_id=task.id, user_id=user_id))
    return task


def enqueue(db: Session, task: Task) -> None:
    """T2 / T8：pending|retrying → queued。"""
    _apply(task, TaskStatus.QUEUED)
    task.queued_at = _now()
    db.flush()


def mark_running(db: Session, task: Task) -> None:
    """T5：queued → running。记录排队时长（用于 E07 埋点与 P95 分析）。"""
    _apply(task, TaskStatus.RUNNING)
    now = _now()
    task.started_at = now
    task.heartbeat_at = now
    queued_at = _as_utc(task.queued_at)
    if queued_at is not None:
        task.wait_seconds = (now - queued_at).total_seconds()
    log.info(
        "task.started",
        extra=bind_task(task_id=task.id, wait_s=task.wait_seconds),
    )


def heartbeat(db: Session, task: Task) -> None:
    """刷新心跳。僵尸任务检测（T14）依赖它。"""
    task.heartbeat_at = _now()
    db.flush()


# ---------------------------------------------------------------- 成功与失败


def mark_succeeded(
    db: Session,
    task: Task,
    *,
    duration_ms: int | None = None,
    gpu_seconds: float | None = None,
    gpu_mem_peak_mb: float | None = None,
    engine_prompt_id: str | None = None,
) -> None:
    """T6：running → succeeded。

    `gpu_seconds` 必须由执行层提供 —— 不能用挂钟时间代替，
    否则会把排队的零成本时间算进单张成本（FR-7.5）。
    """
    _apply(task, TaskStatus.SUCCEEDED)
    task.finished_at = _now()
    if duration_ms is not None:
        task.duration_ms = duration_ms
    if gpu_seconds is not None:
        task.gpu_seconds = gpu_seconds
    if gpu_mem_peak_mb is not None:
        task.gpu_mem_peak_mb = gpu_mem_peak_mb
    if engine_prompt_id:
        task.engine_prompt_id = engine_prompt_id
    db.flush()
    _recompute_batch(db, task)
    log.info(
        "task.succeeded",
        extra=bind_task(task_id=task.id, duration_ms=task.duration_ms),
    )


def mark_retrying(db: Session, task: Task, error: ErrorType, message: str) -> bool:
    """T7：running → retrying。

    返回 True 表示**进入重试**，False 表示**重试耗尽转 failed**（T9）。

    这里体现 PRD §5.4 的核心设计：区分瞬时错误与致命错误。
    OOM 值得重试（降级后可能成功），非法参数重试一万次还是错。
    """
    _apply(task, TaskStatus.RETRYING)
    task.error_type = error.value
    task.error_message = (message or "")[:2000]
    task.retry_count += 1

    if error.is_retryable and task.retry_count <= settings.task_max_retries:
        log.warning(
            "task.retry_scheduled",
            extra=bind_task(
                task_id=task.id, error_type=error.value, retry=task.retry_count
            ),
        )
        return True

    # T9：重试耗尽，或错误本身不可重试 → 直接失败
    _apply(task, TaskStatus.FAILED)
    task.finished_at = _now()
    db.flush()
    _recompute_batch(db, task)
    log.error(
        "task.failed",
        extra=bind_task(task_id=task.id, error_type=error.value, retry=task.retry_count),
    )
    return False


def mark_failed(db: Session, task: Task, error: ErrorType, message: str) -> None:
    """T11：running → failed，**不重试**（致命错误）。

    与 mark_retrying 分开，是为了让「明确致命」的场景不经过 retrying，
    避免在任务中心显示一次无意义的重试。
    """
    _apply(task, TaskStatus.FAILED)
    task.error_type = error.value
    task.error_message = (message or "")[:2000]
    task.finished_at = _now()
    db.flush()
    _recompute_batch(db, task)
    log.error("task.failed_fatal", extra=bind_task(task_id=task.id, error_type=error.value))


def mark_canceled(db: Session, task: Task) -> None:
    """T3 / T4 / T10：任意可取消状态 → canceled。

    执行中的取消应在**采样步间隙**生效（AC-5.2），不在采样中途硬杀进程，
    否则可能留下损坏的显存/临时文件。
    """
    _apply(task, TaskStatus.CANCELED)
    task.finished_at = _now()
    db.flush()
    _recompute_batch(db, task)
    log.info("task.canceled", extra=bind_task(task_id=task.id))


def reset_for_retry(db: Session, task: Task) -> None:
    """T13：failed|canceled → pending，供用户手动重试（FR-4.4 / 断点续跑）。

    故意**保留** retry_count 与 error_type —— 排障需要知道它之前失败过几次。
    """
    _apply(task, TaskStatus.PENDING)
    task.started_at = None
    task.finished_at = None
    task.heartbeat_at = None
    task.engine_prompt_id = None
    db.flush()
    log.info("task.reset_for_retry", extra=bind_task(task_id=task.id))


# ---------------------------------------------------------------- 父任务聚合


def _recompute_batch(db: Session, task: Task) -> None:
    """T12：子任务状态变更 → 重算父任务状态与计数（PRD §5.3）。"""
    if task.batch_id is None:
        return
    batch = db.get(Batch, task.batch_id)
    if batch is None:
        return
    recompute_batch_counts(db, batch)


def recompute_batch_counts(db: Session, batch: Batch) -> None:
    """从子任务重新聚合计数与状态。

    用 SQL 聚合而非内存累加：内存累加在并发/重试场景下极易算错，
    而这里的数据要支撑良品率与进度条，不能有偏差。
    """
    rows = db.execute(
        select(Task.status, func.count())
        .where(Task.batch_id == batch.id)
        .group_by(Task.status)
    ).all()

    counts = {status: int(n) for status, n in rows}
    batch.succeeded_count = counts.get(TaskStatus.SUCCEEDED.value, 0)
    batch.failed_count = counts.get(TaskStatus.FAILED.value, 0)
    batch.canceled_count = counts.get(TaskStatus.CANCELED.value, 0)

    new_status = batch.derive_status()
    if batch.status != new_status.value:
        batch.status = new_status.value
        if new_status in BatchStatus.terminal() and batch.finished_at is None:
            batch.finished_at = _now()
        log.info(
            "batch.status_changed",
            extra=bind_task(batch_id=batch.id, status=new_status.value),
        )
    db.flush()


# ---------------------------------------------------------------- 恢复与清理


def find_zombie_tasks(db: Session) -> list[Task]:
    """T14 / EX-7：找出心跳超时的运行中任务。

    与 `recover_zombie_tasks` 分开，是为了让调用方**先拿到 id 再恢复**：
    恢复只是改状态，任务还要被**重新投递**才会真的继续跑（否则它会静静地
    停在 queued 直到兜底扫描发现它）。worker 的 `recover_zombies` 就是这么用的。
    """
    deadline = _now() - timedelta(seconds=settings.task_heartbeat_timeout_seconds)
    return list(
        db.scalars(
            select(Task).where(
                Task.status == TaskStatus.RUNNING.value,
                (Task.heartbeat_at.is_(None)) | (Task.heartbeat_at < deadline),
            )
        ).all()
    )


def recover_zombie_tasks(db: Session) -> int:
    """T14 / EX-7：Worker 崩溃后恢复。

    Worker 可能被 `kill -9` 或被 OOM Killer 杀掉，**没有机会执行任何清理代码**。
    所以不能依赖「进程退出时清理」，只能靠**心跳超时被动检测**。
    这正是 PRD §5 把 `heartbeat_at` 单列出来的原因。

    返回恢复的任务数。
    """
    zombies = find_zombie_tasks(db)

    for task in zombies:
        # 僵尸恢复走 RUNNING → QUEUED（T14），而不是直接 failed，
        # 因为任务本身大概率没问题，是载体（Worker）挂了。
        task.status = TaskStatus.QUEUED.value
        task.retry_count += 1
        task.error_type = ErrorType.WORKER_CRASH.value
        task.error_message = "Worker 心跳超时，任务已自动重新入队"
        log.warning("task.recovered", extra=bind_task(task_id=task.id))

    if zombies:
        db.flush()
    return len(zombies)


def find_timed_out(db: Session) -> list[Task]:
    """EX-2：找出执行超时的任务，交由执行层中断。"""
    deadline = _now() - timedelta(seconds=settings.task_timeout_seconds)
    return list(
        db.scalars(
            select(Task).where(
                Task.status == TaskStatus.RUNNING.value,
                Task.started_at.is_not(None),
                Task.started_at < deadline,
            )
        ).all()
    )
