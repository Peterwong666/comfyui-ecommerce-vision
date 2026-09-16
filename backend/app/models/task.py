"""任务与批量任务（P2-08）。

**这是全项目最核心的数据模型** —— 它承载 `docs/prd/PRD_v1.md` §5 的完整状态机。

关键设计（均可在 PRD 找到依据）：
1. 子任务粒度 = 一张图，以支持「断点续跑精确到张」（FR-3.5）
2. `heartbeat_at` 用于僵尸任务检测（T14 / EX-7）—— Worker 可能被 kill -9，
   没有机会执行任何清理代码，只能靠心跳超时被动检测
3. `idempotency_key` 用于重复提交幂等（EX-6）
4. `gpu_seconds` 用于单张成本模型（FR-7.5）—— **不能用挂钟时间**，
   否则会把零成本的排队时间算成 GPU 成本
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin
from app.models.enums import BatchStatus, ErrorType, TaskStatus

if TYPE_CHECKING:
    from app.models.asset import Asset
    from app.models.user import User
    from app.models.workflow import Template, Workflow


class Batch(Base, TimestampMixin):
    """批量任务（父任务）。状态由子任务聚合推导，见 PRD §5.3。"""

    __tablename__ = "batches"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1, index=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    name: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[BatchStatus] = mapped_column(
        String(16), nullable=False, default=BatchStatus.PENDING.value, index=True
    )

    workflow_id: Mapped[int] = mapped_column(
        ForeignKey("workflows.id", ondelete="RESTRICT"), nullable=False
    )
    template_id: Mapped[int | None] = mapped_column(
        ForeignKey("templates.id", ondelete="SET NULL")
    )

    # 公共参数（所有子任务共享），子任务可有自己的覆盖
    common_params: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    # 计数聚合，避免每次列表页都 COUNT 子任务（列表页性能，NFR-1）
    total_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    succeeded_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    canceled_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped[User] = relationship(back_populates="batches")
    tasks: Mapped[list[Task]] = relationship(
        back_populates="batch", cascade="all, delete-orphan", order_by="Task.idx"
    )

    @property
    def progress(self) -> float:
        """完成度 0~1，用于任务中心进度条（FR-4.1）。"""
        if self.total_count <= 0:
            return 0.0
        done = self.succeeded_count + self.failed_count + self.canceled_count
        return min(done / self.total_count, 1.0)

    def derive_status(self) -> BatchStatus:
        """按 PRD §5.3 聚合规则推导父任务状态。

        这是纯函数（不落库），落库由 `recompute()` 负责，
        便于单元测试覆盖全部分支（验收项 AC-5.4）。
        """
        if self.total_count <= 0:
            return BatchStatus.PENDING
        finished = self.succeeded_count + self.failed_count + self.canceled_count
        if finished < self.total_count:
            return BatchStatus.RUNNING
        # 全部到达终态，按构成决定
        if self.canceled_count == self.total_count:
            return BatchStatus.CANCELED
        if self.failed_count == self.total_count:
            return BatchStatus.FAILED
        if self.failed_count > 0 or self.canceled_count > 0:
            return BatchStatus.PARTIAL
        return BatchStatus.SUCCEEDED

    def __repr__(self) -> str:
        return f"<Batch id={self.id} status={self.status} {self.succeeded_count}/{self.total_count}>"


class Task(Base, TimestampMixin):
    """单张图任务（子任务）。状态机见 PRD §5.4 的 T1~T14。"""

    __tablename__ = "tasks"
    __table_args__ = (
        # 幂等键去重（EX-6）：同一用户重复提交返回已有 task_id。
        # 用 partial index（仅约束非空行），因为绝大多数任务没有幂等键。
        Index(
            "uq_tasks_idempotency",
            "user_id",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
        ),
        CheckConstraint("retry_count >= 0", name="retry_count_non_negative"),
        Index("ix_tasks_status_created", "status", "created_at"),
        Index("ix_tasks_batch_status", "batch_id", "status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1, index=True)

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    batch_id: Mapped[int | None] = mapped_column(
        ForeignKey("batches.id", ondelete="CASCADE"), index=True
    )
    idx: Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # 批内序号，便于定位

    workflow_id: Mapped[int] = mapped_column(
        ForeignKey("workflows.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    template_id: Mapped[int | None] = mapped_column(
        ForeignKey("templates.id", ondelete="SET NULL")
    )

    status: Mapped[TaskStatus] = mapped_column(
        String(16), nullable=False, default=TaskStatus.PENDING.value, index=True
    )
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=5, index=True)

    # ---------- 输入 ----------
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    prompt: Mapped[str | None] = mapped_column(Text)
    negative_prompt: Mapped[str | None] = mapped_column(Text)
    seed: Mapped[int | None] = mapped_column(BigInteger)

    # 幂等键（EX-6）。同一用户 + 同参数短时间重复提交 → 返回已有任务。
    idempotency_key: Mapped[str | None] = mapped_column(String(128))

    # ---------- 执行与观测 ----------
    # ComfyUI 的 prompt_id，串起全链路 trace（FR-4.5）
    engine_prompt_id: Mapped[str | None] = mapped_column(String(64), index=True)

    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_type: Mapped[str | None] = mapped_column(String(32), index=True)
    error_message: Mapped[str | None] = mapped_column(Text)

    # 状态机时间戳
    queued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # 僵尸任务检测（T14 / EX-7）。Worker 被 kill -9 时无任何清理机会，
    # 只能靠心跳超时被动判定，不能依赖进程退出钩子。
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)

    # ---------- 度量 ----------
    # 排队时长与执行时长必须分开：单卡串行时排队可能远长于执行，
    # 混在一起会让 P95 指标失真，也会让成本核算错误。
    wait_seconds: Mapped[float | None] = mapped_column(Float)  # 排队时长
    duration_ms: Mapped[int | None] = mapped_column(Integer)  # 执行时长
    gpu_seconds: Mapped[float | None] = mapped_column(Float)  # 计费用（FR-7.5）
    gpu_mem_peak_mb: Mapped[float | None] = mapped_column(Float)  # 显存水位

    # 降级记录（EX-1 OOM 自动降级）
    degrade_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    degrade_actions: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)

    user: Mapped[User] = relationship(back_populates="tasks")
    batch: Mapped[Batch | None] = relationship(back_populates="tasks")
    workflow: Mapped[Workflow] = relationship(back_populates="tasks")
    template: Mapped[Template | None] = relationship(back_populates="tasks")
    assets: Mapped[list[Asset]] = relationship(back_populates="task")

    # ---------- 状态机辅助 ----------

    @property
    def error(self) -> ErrorType | None:
        return ErrorType(self.error_type) if self.error_type else None

    @property
    def can_retry(self) -> bool:
        """是否可重试。区分瞬时/致命错误是 PRD §5.4 T7 vs T11 的设计要点。"""
        if self.status not in (TaskStatus.FAILED.value, TaskStatus.RETRYING.value):
            return False
        err = self.error
        if err is None:
            return True
        return err.is_retryable

    @property
    def cost_yuan(self) -> float | None:
        """单张成本（FR-7.5）。用 GPU 秒而非挂钟时间，见模块 docstring 第 4 点。"""
        from app.core.config import settings

        if self.gpu_seconds is None or settings.gpu_cost_per_hour <= 0:
            return None
        return self.gpu_seconds * settings.gpu_cost_per_second

    def __repr__(self) -> str:
        return f"<Task id={self.id} status={self.status} retry={self.retry_count}>"
