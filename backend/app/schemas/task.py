"""Pydantic Schema：任务与批量（P2-08）。

边界值全部取 `settings`，确保「PRD §6.2 的边界值」与「代码校验」不会各说各话。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.config import settings


class TaskSubmitIn(BaseModel):
    """单次生成提交（FR-2.5）。"""

    workflow_name: str = Field(description="如 t2i_v1")
    template_id: int | None = None

    # 提示词的**唯一顶层通道**。与 params["prompt"] 同时传时，以此为准（契约 §3.4）。
    prompt: str | None = Field(
        default=None,
        max_length=settings.max_prompt_length,
        description="正向提示词；同时出现在 params['prompt'] 时本条优先",
    )
    negative_prompt: str | None = Field(default=None, max_length=settings.max_prompt_length)

    params: dict[str, Any] = Field(default_factory=dict)
    upload_asset_ids: list[int] = Field(default_factory=list, description="参考图/商品图")

    steps: int | None = Field(default=None, ge=settings.min_steps, le=settings.max_steps)
    cfg: float | None = Field(default=None, ge=settings.min_cfg, le=settings.max_cfg)
    seed: int | None = None

    # 幂等键（EX-6）：客户端可传，用于避免重复提交
    idempotency_key: str | None = Field(default=None, max_length=128)

    @field_validator("prompt")
    @classmethod
    def _check_prompt(cls, v: str | None) -> str | None:
        if v is not None and len(v) < settings.min_prompt_length:
            raise ValueError(f"提示词长度至少 {settings.min_prompt_length}")
        return v


class BatchSubmitIn(BaseModel):
    """批量提交（FR-3.4）。"""

    workflow_name: str
    template_id: int | None = None
    name: str | None = Field(default=None, max_length=255)

    # SKU → 素材 ID 列表。多 SKU × 多图（FR-3.1）
    sku_assets: dict[str, list[int]] = Field(default_factory=dict)

    common_params: dict[str, Any] = Field(default_factory=dict)
    # 每个 SKU 出几张
    images_per_sku: int = Field(default=1, ge=1, le=20)

    idempotency_key: str | None = Field(default=None, max_length=128)

    @field_validator("sku_assets")
    @classmethod
    def _check_sku_count(cls, v: dict[str, list[int]]) -> dict[str, list[int]]:
        """校验 **SKU 数**。

        ⚠️ 这里只管 SKU 维度；「总张数」由 `submit_batch` 用 `max_batch_size` 校验。
        契约 §5 #6 之前两者同名 `max_batch_size`，导致同一个名字在两处表达两个意思
        （SKU 数 vs 总张数），已拆成 `max_sku_count` / `max_batch_size`。
        """
        if not v:
            raise ValueError("sku_assets 不能为空")
        if len(v) > settings.max_sku_count:
            raise ValueError(f"SKU 数不能超过 {settings.max_sku_count}")
        return v

    def total_images(self) -> int:
        return len(self.sku_assets) * self.images_per_sku


class TaskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    batch_id: int | None
    idx: int
    status: str
    workflow_id: int
    template_id: int | None
    params: dict[str, Any]
    seed: int | None

    retry_count: int
    error_type: str | None
    error_message: str | None

    started_at: datetime | None
    finished_at: datetime | None
    wait_seconds: float | None
    duration_ms: int | None
    gpu_seconds: float | None
    cost_yuan: float | None = None

    created_at: datetime

    # 是否可手动重试（FR-4.4）。**显式字段**，值取自 ORM 的 `Task.can_retry`。
    # 不能写成 `@property`：Pydantic v2 不序列化 property，前端实际拿不到该字段
    # （契约 §5 #4 记录的就是这个坑）。
    can_retry: bool = False


class BatchOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str | None
    status: str
    workflow_id: int
    template_id: int | None
    total_count: int
    succeeded_count: int
    failed_count: int
    canceled_count: int
    progress: float
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime


class TaskEstimateOut(BaseModel):
    """提交前预估（FR-3.3）：张数 / 预计耗时 / 预计成本 / 并发。"""

    total_images: int
    estimated_seconds: float
    estimated_cost_yuan: float | None
    gpu_concurrency: int = 1
    queue_depth: int | None = None
    estimated_wait_seconds: float | None = None


class CancelOut(BaseModel):
    task_id: int
    status: str
    message: str
