"""质量看板接口（P8-05 · FR-7.2）。"""

from __future__ import annotations

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from app.api.deps import CurrentUser, DbSession
from app.models.asset import Asset, DefectKnowledge
from app.models.event import Event
from app.models.task import Task
from app.models.workflow import Workflow
from app.schemas.stats import (
    DefectStats,
    QualityDashboard,
    QualityOverview,
    TaskStats,
    WorkflowQuality,
)

router = APIRouter(tags=["stats"])


@router.get("/stats/quality", response_model=QualityDashboard, summary="质量看板（P8-05）")
def get_quality_dashboard(
    _: CurrentUser,
    db: DbSession,
    limit_defects: int = Query(default=10, ge=1, le=50, description="缺陷排行显示条数"),
) -> QualityDashboard:
    """聚合产物采纳、任务执行、缺陷知识库数据，提供质量看板视图。"""

    # ── 1. 产物总览 ──
    output_filter = (
        Asset.kind == "output",
        Asset.is_deleted.is_(False),
    )

    total_outputs = int(
        db.scalar(select(func.count()).select_from(Asset).where(*output_filter)) or 0
    )
    total_adopted = int(
        db.scalar(
            select(func.count())
            .select_from(Asset)
            .where(*output_filter, Asset.is_adopted.is_(True))
        )
        or 0
    )
    yield_rate = (total_adopted / total_outputs) if total_outputs > 0 else None

    # ── 2. 事件统计 ──
    adopted_events = int(
        db.scalar(
            select(func.count())
            .select_from(Event)
            .where(Event.event_name == "image_adopted")
        )
        or 0
    )
    downloaded_events = int(
        db.scalar(
            select(func.count())
            .select_from(Event)
            .where(Event.event_name == "image_downloaded")
        )
        or 0
    )
    total_events = int(db.scalar(select(func.count()).select_from(Event)) or 0)

    # ── 3. 任务统计 ──
    status_counts = dict(
        db.execute(
            select(Task.status, func.count()).group_by(Task.status)
        ).all()
    )
    task_total = sum(status_counts.values())
    task_succeeded = status_counts.get("succeeded", 0)
    task_failed = status_counts.get("failed", 0)
    task_canceled = status_counts.get("canceled", 0)
    task_queued = status_counts.get("queued", 0)
    task_running = status_counts.get("running", 0)
    task_success_rate = (task_succeeded / task_total) if task_total > 0 else None

    # 平均耗时（只算已完成的）
    avg_duration = db.scalar(
        select(func.avg(Task.duration_ms)).where(
            Task.status == "succeeded", Task.duration_ms.isnot(None)
        )
    )
    avg_gpu = db.scalar(
        select(func.avg(Task.gpu_seconds)).where(
            Task.status == "succeeded", Task.gpu_seconds.isnot(None)
        )
    )
    total_gpu = float(
        db.scalar(
            select(func.coalesce(func.sum(Task.gpu_seconds), 0)).where(
                Task.status == "succeeded"
            )
        )
        or 0
    )

    task_stats = TaskStats(
        total=task_total,
        succeeded=task_succeeded,
        failed=task_failed,
        canceled=task_canceled,
        queued=task_queued,
        running=task_running,
        success_rate=task_success_rate,
        avg_duration_ms=float(avg_duration) if avg_duration else None,
        avg_gpu_seconds=float(avg_gpu) if avg_gpu else None,
        total_gpu_seconds=total_gpu,
    )

    # ── 4. 按工作流维度 ──
    workflows = list(
        db.scalars(
            select(Workflow).where(Workflow.is_active.is_(True)).order_by(Workflow.name)
        ).all()
    )

    by_workflow: list[WorkflowQuality] = []
    for wf in workflows:
        wf_outputs = int(
            db.scalar(
                select(func.count())
                .select_from(Asset)
                .where(*output_filter, Asset.task_id.isnot(None))
                .join(Task, Asset.task_id == Task.id)
                .where(Task.workflow_id == wf.id)
            )
            or 0
        )
        wf_adopted = int(
            db.scalar(
                select(func.count())
                .select_from(Asset)
                .where(
                    *output_filter,
                    Asset.is_adopted.is_(True),
                    Asset.task_id.isnot(None),
                )
                .join(Task, Asset.task_id == Task.id)
                .where(Task.workflow_id == wf.id)
            )
            or 0
        )
        wf_yield = (wf_adopted / wf_outputs) if wf_outputs > 0 else None

        # 该工作流的任务统计
        wf_task_total = int(
            db.scalar(
                select(func.count()).select_from(Task).where(Task.workflow_id == wf.id)
            )
            or 0
        )
        wf_task_succeeded = int(
            db.scalar(
                select(func.count())
                .select_from(Task)
                .where(Task.workflow_id == wf.id, Task.status == "succeeded")
            )
            or 0
        )
        wf_success_rate = (
            (wf_task_succeeded / wf_task_total) if wf_task_total > 0 else None
        )

        wf_avg_dur = db.scalar(
            select(func.avg(Task.duration_ms)).where(
                Task.workflow_id == wf.id,
                Task.status == "succeeded",
                Task.duration_ms.isnot(None),
            )
        )
        wf_avg_gpu = db.scalar(
            select(func.avg(Task.gpu_seconds)).where(
                Task.workflow_id == wf.id,
                Task.status == "succeeded",
                Task.gpu_seconds.isnot(None),
            )
        )

        by_workflow.append(
            WorkflowQuality(
                workflow_id=wf.id,
                workflow_name=wf.name,
                display_name=wf.display_name,
                total_outputs=wf_outputs,
                adopted_count=wf_adopted,
                yield_rate=wf_yield,
                eval_score=wf.eval_score,
                avg_duration_ms=float(wf_avg_dur) if wf_avg_dur else None,
                avg_gpu_seconds=float(wf_avg_gpu) if wf_avg_gpu else None,
                success_rate=wf_success_rate,
            )
        )

    # ── 5. 缺陷排行 ──
    defect_rows = list(
        db.scalars(
            select(DefectKnowledge)
            .order_by(DefectKnowledge.hit_count.desc())
            .limit(limit_defects)
        ).all()
    )
    top_defects = [
        DefectStats(
            defect_type=d.defect_type,
            count=1,  # 每条缺陷知识库条目计 1
            hit_count=d.hit_count,
        )
        for d in defect_rows
    ]

    # ── 组装响应 ──
    overview = QualityOverview(
        total_outputs=total_outputs,
        total_adopted=total_adopted,
        yield_rate=yield_rate,
        total_tasks=task_total,
        total_events=total_events,
        adopted_events=adopted_events,
        downloaded_events=downloaded_events,
    )

    return QualityDashboard(
        overview=overview,
        by_workflow=by_workflow,
        task_stats=task_stats,
        top_defects=top_defects,
    )
