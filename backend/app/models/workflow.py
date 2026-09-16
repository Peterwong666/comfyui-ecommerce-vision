"""工作流与模板（FR-6.1 / FR-6.3 / FR-6.4）。

设计约束（ADR-004 约束 2）：
**工作流以 API 格式 JSON 存库，参数通过占位符注入，前端表单由 param_schema 自动生成。**
这是「新增工作流不改后端与前端代码」（FR-6.4）的实现方式，也是低门槛（C10）的基础。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.task import Task


class Workflow(Base, TimestampMixin):
    """标准化工作流，**版本化**（FR-6.3：改坏要能退回去）。

    版本策略：同 name 多 version 并存，`is_active` 标记当前生效版本。
    回滚 = 把旧版本的 is_active 置 True。
    """

    __tablename__ = "workflows"
    __table_args__ = (
        UniqueConstraint("name", "version", name="uq_workflows_name_version"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    # 稳定标识，如 "t2i_v1" / "i2i_v1" / "upscale_v1" / "inpaint_v1" /
    # "style_transfer_v1" / "batch_v1"（对应 PRD FR-6.2 的六条工作流）
    name: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)

    # ComfyUI API 格式工作流 JSON（"86" 这类字符串 key 的节点图）
    definition: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    # 参数 Schema：驱动前端动态表单（FR-2.3 / P7-02）。
    # 结构见 docs/prd/PRD_v1.md §3 M2 FR-2.3；示例：
    # {"fields":[{"key":"steps","type":"int","min":1,"max":100,"default":25,
    #             "label":"步数","visible":true,"group":"basic"}]}
    param_schema: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    # 节点参数注入映射：{"steps": ["3", "inputs", "steps"], ...}
    # key 为 param_schema 的 key，value 为 ComfyUI 节点图里的路径
    param_bindings: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)

    # 评测分与良品率（FR-7.2 看板数据来源）
    eval_score: Mapped[float | None] = mapped_column(Float)
    good_rate: Mapped[float | None] = mapped_column(Float)

    templates: Mapped[list[Template]] = relationship(back_populates="workflow")
    tasks: Mapped[list[Task]] = relationship(back_populates="workflow")

    def __repr__(self) -> str:
        return f"<Workflow {self.name}@v{self.version} active={self.is_active}>"


class Template(Base, TimestampMixin):
    """场景模板 = 工作流 + 预设参数 + 效果示例图（FR-6.1）。

    这是**最关键转化点**（persona 旅程 3→4）：模板必须一眼看懂效果，
    所以 example_asset_id 不是可选项，是决定用户会不会继续的因素。
    """

    __tablename__ = "templates"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    name: Mapped[str] = mapped_column(String(128), nullable=False)
    category: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text)

    workflow_id: Mapped[int] = mapped_column(
        ForeignKey("workflows.id", ondelete="RESTRICT"), nullable=False, index=True
    )

    # 预设参数包（FR-2.4 / FR-3.2），一键套用
    preset_params: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    # 效果示例图，模板列表页展示用（FR-2.2）
    example_asset_id: Mapped[int | None] = mapped_column(
        ForeignKey("assets.id", ondelete="SET NULL")
    )

    usage_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rating: Mapped[float | None] = mapped_column(Float)  # 用户评分，用于排序
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)

    workflow: Mapped[Workflow] = relationship(back_populates="templates")
    tasks: Mapped[list[Task]] = relationship(back_populates="template")

    def __repr__(self) -> str:
        return f"<Template {self.name!r} category={self.category}>"
