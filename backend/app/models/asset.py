"""素材与产物（FR-2.1 / FR-5.1~5.5 / FR-11 数据隔离）。

`is_adopted` 是**良品率的唯一数据来源**（PRD §5 指标定义：良品率 = 采纳数 / succeeded 图数），
因此它必须落在数据库而不是前端状态里。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import BigInteger, Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin
from app.models.enums import AssetKind

if TYPE_CHECKING:
    from app.models.task import Task
    from app.models.user import User


class Asset(Base, TimestampMixin):
    __tablename__ = "assets"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1, index=True)

    # 数据隔离（FR-1.3）：用户只能看到自己的素材与产物
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    task_id: Mapped[int | None] = mapped_column(
        ForeignKey("tasks.id", ondelete="SET NULL"), index=True
    )

    kind: Mapped[AssetKind] = mapped_column(String(16), nullable=False, index=True)

    # 对象存储 key（MinIO）。路径由 ID 生成，禁止用户传入路径（NFR-4 路径遍历防护）。
    object_key: Mapped[str] = mapped_column(String(512), nullable=False, unique=True)
    mime_type: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)

    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    original_name: Mapped[str | None] = mapped_column(String(255))

    # 用户侧状态
    is_adopted: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, index=True
    )  # 采纳 → 良品率分子
    is_favorite: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_deleted: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, index=True
    )  # 软删除（FR-5.3）

    # 可复现性元数据（FR-5.4 / C1）：
    # {"seed":..., "workflow":"t2i_v1@v3", "model_sha256":"...", "params":{...}}
    # 客户追单时靠它复现同一张图。
    meta: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    user: Mapped[User] = relationship(back_populates="assets")
    task: Mapped[Task | None] = relationship(back_populates="assets")

    def __repr__(self) -> str:
        return f"<Asset id={self.id} kind={self.kind} adopted={self.is_adopted}>"


class PromptLibrary(Base, TimestampMixin):
    """提示词库（JD-4 要求 ≥200 条带标签）。

    同时为 V2 的 LLM 提示词助手（E4）预留结构化存储。
    """

    __tablename__ = "prompt_library"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1, index=True)

    title: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[str] = mapped_column(String(64), nullable=False, index=True)  # 服饰/3C/家居...
    positive: Mapped[str] = mapped_column(Text, nullable=False)
    negative: Mapped[str | None] = mapped_column(Text)

    tags: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    usage_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    def __repr__(self) -> str:
        return f"<PromptLibrary {self.title!r} category={self.category}>"


class DefectKnowledge(Base, TimestampMixin):
    """缺陷知识库（JD-9 / FR-7.3，要求 ≥30 条）。

    结构固定为「瑕疵类型 → 成因 → 解法 → 参数」，这是 JD 原文要求的沉淀形式。
    """

    __tablename__ = "defect_knowledge"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    # 手部畸变 / 文字模糊 / 结构漂移 / 过曝 / 材质失真 ...
    defect_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    symptom: Mapped[str] = mapped_column(Text, nullable=False)  # 现象描述
    cause: Mapped[str] = mapped_column(Text, nullable=False)  # 成因
    solution: Mapped[str] = mapped_column(Text, nullable=False)  # 解法
    recommended_params: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )  # 推荐的参数调整

    related_asset_id: Mapped[int | None] = mapped_column(
        ForeignKey("assets.id", ondelete="SET NULL")
    )
    hit_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    def __repr__(self) -> str:
        return f"<DefectKnowledge {self.defect_type!r}>"
