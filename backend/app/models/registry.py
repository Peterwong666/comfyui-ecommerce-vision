"""模型 registry（FR-9.1 / C9 合规）。

**这是商用红线的守门人**。许可矩阵见 `docs/sop/license_matrix.md`：
模型权重许可千差万别（Apache-2.0 可商用 / Non-Commercial 禁用 / 未知禁用），
一旦用了不可商用的模型，整个产品不合法。

已知在本项目中被禁用的：InsightFace 权重（Non-Commercial）、
FLUX.1-dev / FLUX.2-dev（Non-Commercial）。
可商用：SDXL（OpenRAIL）、FLUX.2 klein 4B（Apache-2.0）。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import BigInteger, Boolean, Float, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin
from app.models.enums import ModelKind


class ModelRegistry(Base, TimestampMixin):
    """内部标准化模型库（JD-4：筛选、测试、适配、版本归档）。"""

    __tablename__ = "model_registry"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    kind: Mapped[ModelKind] = mapped_column(String(32), nullable=False, index=True)
    version: Mapped[str] = mapped_column(String(64), nullable=False, default="1")

    # 数据盘上的绝对路径（软链进 ComfyUI 的 models 目录）。
    # 配置外置：不写死在代码里，可随部署环境变化（NFR-6）。
    file_path: Mapped[str | None] = mapped_column(String(512))
    file_size: Mapped[int | None] = mapped_column(BigInteger)
    sha256: Mapped[str | None] = mapped_column(String(64), index=True)

    # ---------- 许可（商用红线） ----------
    license: Mapped[str | None] = mapped_column(String(128))
    license_url: Mapped[str | None] = mapped_column(String(512))
    # 是否可商用。未知一律 False（C9：未知即不用）。
    is_commercial_ok: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, index=True
    )
    license_note: Mapped[str | None] = mapped_column(Text)

    # ---------- 评测与质量 ----------
    eval_score: Mapped[float | None] = mapped_column(Float)
    good_rate: Mapped[float | None] = mapped_column(Float)
    eval_notes: Mapped[str | None] = mapped_column(Text)

    # ---------- 状态 ----------
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    enable_notes: Mapped[str | None] = mapped_column(Text)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    extra: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    def __repr__(self) -> str:
        return (
            f"<ModelRegistry {self.name} kind={self.kind} "
            f"commercial={self.is_commercial_ok} active={self.is_active}>"
        )
