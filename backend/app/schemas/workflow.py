"""Pydantic Schema：工作流与模板（P2-08）。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class WorkflowOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    version: int
    display_name: str
    description: str | None
    is_active: bool
    eval_score: float | None
    good_rate: float | None


class WorkflowDetailOut(WorkflowOut):
    """含 param_schema —— 前端据此渲染动态表单（FR-2.3 / P7-02）。"""

    param_schema: dict[str, Any]
    # 注意：不返回 definition（ComfyUI 节点图），避免暴露内部实现与模型路径


class TemplateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    category: str
    description: str | None
    workflow_id: int
    preset_params: dict[str, Any]
    example_asset_id: int | None
    usage_count: int
    rating: float | None
    sort_order: int


class ModelRegistryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    kind: str
    version: str
    license: str | None
    is_commercial_ok: bool
    license_note: str | None
    eval_score: float | None
    good_rate: float | None
    is_active: bool
    is_default: bool


class TemplateCreateIn(BaseModel):
    """管理员新增模板（FR-6.5）。"""

    name: str = Field(max_length=128)
    category: str = Field(max_length=64)
    description: str | None = None
    workflow_id: int
    preset_params: dict[str, Any] = Field(default_factory=dict)
    example_asset_id: int | None = None
    sort_order: int = 0
