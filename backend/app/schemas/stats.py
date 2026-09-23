"""Pydantic Schema：质量看板（P8-05）。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class WorkflowQuality(BaseModel):
    """单个工作流的质量指标。"""

    workflow_id: int
    workflow_name: str
    display_name: str
    total_outputs: int = Field(description="产物总数（kind=output, 未删除）")
    adopted_count: int = Field(description="已采纳数")
    yield_rate: float | None = Field(description="良品率（adopted / total），无产物时为 None")
    eval_score: float | None = Field(description="画质评分（4 维度均值），未评时为 None")
    avg_duration_ms: float | None = Field(description="平均耗时（ms），无完成任务时为 None")
    avg_gpu_seconds: float | None = Field(description="平均 GPU 耗时（s）")
    success_rate: float | None = Field(description="任务成功率（succeeded / total_tasks）")


class TaskStats(BaseModel):
    """任务统计总览。"""

    total: int
    succeeded: int
    failed: int
    canceled: int
    queued: int
    running: int
    success_rate: float | None = Field(description="成功率")
    avg_duration_ms: float | None = Field(description="已完成任务平均耗时")
    avg_gpu_seconds: float | None = Field(description="平均 GPU 耗时")
    total_gpu_seconds: float = Field(description="累计 GPU 耗时（秒）")


class DefectStats(BaseModel):
    """缺陷统计。"""

    defect_type: str
    count: int
    hit_count: int


class QualityDashboard(BaseModel):
    """质量看板总响应。"""

    overview: QualityOverview
    by_workflow: list[WorkflowQuality]
    task_stats: TaskStats
    top_defects: list[DefectStats] = Field(description="缺陷知识库 hit_count 排行")


class QualityOverview(BaseModel):
    """总览指标。"""

    total_outputs: int = Field(description="全部产物数")
    total_adopted: int = Field(description="全部已采纳数")
    yield_rate: float | None = Field(description="总良品率")
    total_tasks: int
    total_events: int = Field(description="总事件数")
    adopted_events: int = Field(description="image_adopted 事件数")
    downloaded_events: int = Field(description="image_downloaded 事件数（北极星）")
