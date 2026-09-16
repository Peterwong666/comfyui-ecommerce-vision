"""埋点事件表（P1-20 埋点方案的落地）。

12 个核心事件定义见 `docs/prd/tracking_plan.md` §3。
用 JSONB 存差异化属性：12 个事件属性各不相同，
若为每个事件建一张表会产生 12 张表和 12 套查询逻辑，得不偿失。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, Index, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class EventName:
    """12 个核心事件名。见 tracking_plan.md §3。"""

    # 用户与准入
    USER_REGISTER = "user_register"  # E01
    USER_LOGIN = "user_login"  # E02
    # 生成主链路
    IMAGE_UPLOADED = "image_uploaded"  # E03
    TEMPLATE_SELECTED = "template_selected"  # E04 ★ 关键转化点
    PARAM_CHANGED = "param_changed"  # E05
    TASK_SUBMITTED = "task_submitted"  # E06
    # 任务执行
    TASK_STARTED = "task_started"  # E07
    TASK_RETRY = "task_retry"  # E08
    TASK_FINISHED = "task_finished"  # E09 ★ 最重要（5 个指标依赖它）
    # 结果消费
    IMAGE_ADOPTED = "image_adopted"  # E10 ★ 良品率分子
    IMAGE_DOWNLOADED = "image_downloaded"  # E11 ★ 北极星分子
    IMAGE_REGENERATED = "image_regenerated"  # E12

    @classmethod
    def all(cls) -> tuple[str, ...]:
        return (
            cls.USER_REGISTER,
            cls.USER_LOGIN,
            cls.IMAGE_UPLOADED,
            cls.TEMPLATE_SELECTED,
            cls.PARAM_CHANGED,
            cls.TASK_SUBMITTED,
            cls.TASK_STARTED,
            cls.TASK_RETRY,
            cls.TASK_FINISHED,
            cls.IMAGE_ADOPTED,
            cls.IMAGE_DOWNLOADED,
            cls.IMAGE_REGENERATED,
        )


class Event(Base):
    """事件表。

    注意：**没有 TimestampMixin** —— 事件表按 ts 分区/清理，
    updated_at 无意义且会增大写入量（埋点是高频写入路径）。
    """

    __tablename__ = "events"
    # 注：ts 列已用 index=True 生成 ix_events_ts，这里只放复合索引，避免重复建同名索引
    __table_args__ = (
        Index("ix_events_name_ts", "event_name", "ts"),
        Index("ix_events_task", "task_id"),
        Index("ix_events_user_ts", "user_id", "ts"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1, index=True)

    event_name: Mapped[str] = mapped_column(String(64), nullable=False)
    # 幂等去重：关键事件（如 task_finished）重复上报时靠它识别
    event_id: Mapped[str | None] = mapped_column(String(64), unique=True)

    user_id: Mapped[int | None] = mapped_column(BigInteger)
    task_id: Mapped[int | None] = mapped_column(BigInteger)
    batch_id: Mapped[int | None] = mapped_column(BigInteger)
    session_id: Mapped[str | None] = mapped_column(String(64))

    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )
    app_version: Mapped[str | None] = mapped_column(String(32))
    source: Mapped[str | None] = mapped_column(String(32))  # web / api / worker

    # 差异化属性。**不上传商品图内容、不存提示词全文**（tracking_plan §1.2 反面清单）
    props: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    def __repr__(self) -> str:
        return f"<Event {self.event_name} ts={self.ts} task={self.task_id}>"


class AuditLog(Base):
    """审计日志（FR-1.4 / NFR-4）：登录/提交/删除留痕。"""

    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1, index=True)

    user_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    target_type: Mapped[str | None] = mapped_column(String(64))
    target_id: Mapped[str | None] = mapped_column(String(64))

    ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(Text)
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )

    def __repr__(self) -> str:
        return f"<AuditLog {self.action} user={self.user_id}>"
