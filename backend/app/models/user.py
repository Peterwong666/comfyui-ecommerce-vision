"""用户与配额（FR-1.1 / FR-1.2 / FR-9.2）。"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, Boolean, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin
from app.models.enums import UserRole

if TYPE_CHECKING:
    from app.models.asset import Asset
    from app.models.task import Batch, Task


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    # ADR-004 约束 4：预留 tenant_id 与 role 供 V2 多租户使用。
    # V1 单租户：tenant_id 固定为 1，不做筛选逻辑，但字段先留好。
    tenant_id: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1, index=True)

    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[UserRole] = mapped_column(
        String(16), nullable=False, default=UserRole.USER.value
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # 配额（FR-1.2）。内测期由管理员手工配置（FR-9.2）。
    quota_total: Mapped[int] = mapped_column(Integer, nullable=False, default=50)
    quota_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    tasks: Mapped[list[Task]] = relationship(back_populates="user", cascade="all, delete-orphan")
    batches: Mapped[list[Batch]] = relationship(back_populates="user", cascade="all, delete-orphan")
    assets: Mapped[list[Asset]] = relationship(back_populates="user", cascade="all, delete-orphan")

    @property
    def quota_remaining(self) -> int:
        return max(self.quota_total - self.quota_used, 0)

    @property
    def quota_exhausted(self) -> bool:
        """对应 PRD EX-12：配额耗尽时拒绝提交新任务。"""
        return self.quota_remaining <= 0

    def __repr__(self) -> str:
        return f"<User id={self.id} email={self.email!r} role={self.role}>"
