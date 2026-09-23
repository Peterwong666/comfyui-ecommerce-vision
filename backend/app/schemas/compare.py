"""Pydantic Schema：A/B 对比（P8-07）。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class CompareVariant(BaseModel):
    """对比变体：同一 seed 下的一组参数。"""

    label: str = Field(description="变体标签（如 'SDXL steps=30'）")
    params: dict[str, Any] = Field(description="覆盖参数（合并到工作流默认值之上）")
    prompt: str | None = Field(default=None, description="覆盖正向提示词")
    negative_prompt: str | None = Field(default=None, description="覆盖负向提示词")


class CompareSubmitIn(BaseModel):
    """提交 A/B 对比请求。"""

    workflow_name: str = Field(description="工作流名称")
    seed: int = Field(description="统一 seed（-1 表示随机，所有变体共享）")
    base_params: dict[str, Any] = Field(default_factory=dict, description="基础参数（所有变体共享）")
    base_prompt: str | None = Field(default=None, description="基础正向提示词")
    base_negative_prompt: str | None = Field(default=None, description="基础负向提示词")
    variants: list[CompareVariant] = Field(
        min_length=2, max_length=6, description="对比变体（2-6 个）"
    )


class CompareVariantResult(BaseModel):
    """单个变体的结果。"""

    label: str
    params: dict[str, Any]
    task_id: int | None = Field(description="关联任务 ID，未执行时为 None")
    asset_id: int | None = Field(default=None, description="产物 ID，未完成时为 None")
    status: str = Field(description="任务状态")
    duration_ms: int | None = None
    gpu_seconds: float | None = None


class CompareGroupOut(BaseModel):
    """对比组响应。"""

    id: str = Field(description="对比组 ID（客户端生成的 UUID）")
    workflow_name: str
    seed: int
    created_at: datetime
    variants: list[CompareVariantResult]
