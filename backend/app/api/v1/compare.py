"""A/B 对比接口（P8-07）。

同 seed 下对比模型 / 参数 / ControlNet 权重的差异。
V1 实现：对比组存储在内存中（单实例、非持久化），
任务提交复用现有 tasks 接口。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession
from app.models.task import Task
from app.models.workflow import Workflow
from app.schemas.compare import (
    CompareGroupOut,
    CompareSubmitIn,
    CompareVariantResult,
)

router = APIRouter(tags=["compare"])

# V1 内存存储（单实例，重启丢失）
_compare_groups: dict[str, dict[str, Any]] = {}


@router.post(
    "/compare",
    response_model=CompareGroupOut,
    status_code=status.HTTP_202_ACCEPTED,
    summary="提交 A/B 对比（P8-07）",
)
def submit_compare(
    body: CompareSubmitIn,
    user: CurrentUser,
    db: DbSession,
) -> CompareGroupOut:
    """提交多个变体任务（同 seed、不同参数），返回对比组。

    ⚠️ V1 不直接提交任务到队列 —— 只创建对比组定义。
    实际执行需要 GPU，由用户在 GPU 可用时手动触发。
    对比组存储在内存中，重启后丢失。
    """
    # 验证工作流存在
    wf = db.scalar(
        select(Workflow).where(
            Workflow.name == body.workflow_name, Workflow.is_active.is_(True)
        )
    )
    if not wf:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"工作流 '{body.workflow_name}' 不存在或未启用",
        )

    group_id = uuid.uuid4().hex[:12]
    now = datetime.now(timezone.utc)

    variants = []
    for v in body.variants:
        variants.append(
            CompareVariantResult(
                label=v.label,
                params={**body.base_params, **v.params},
                task_id=None,
                asset_id=None,
                status="pending",
                duration_ms=None,
                gpu_seconds=None,
            )
        )

    group = {
        "id": group_id,
        "workflow_name": body.workflow_name,
        "seed": body.seed,
        "created_at": now,
        "variants": variants,
        "base_params": body.base_params,
        "base_prompt": body.base_prompt,
        "base_negative_prompt": body.base_negative_prompt,
    }
    _compare_groups[group_id] = group

    return CompareGroupOut(
        id=group_id,
        workflow_name=body.workflow_name,
        seed=body.seed,
        created_at=now,
        variants=variants,
    )


@router.get(
    "/compare/{group_id}",
    response_model=CompareGroupOut,
    summary="获取对比组详情",
)
def get_compare_group(
    group_id: str,
    _: CurrentUser,
    db: DbSession,
) -> CompareGroupOut:
    """获取对比组及其所有变体的状态。"""
    group = _compare_groups.get(group_id)
    if not group:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"对比组 '{group_id}' 不存在",
        )

    # 刷新变体状态（从任务表读取最新）
    variants = group["variants"]
    for v in variants:
        if v.task_id:
            task = db.get(Task, v.task_id)
            if task:
                v.status = task.status
                v.duration_ms = task.duration_ms
                v.gpu_seconds = task.gpu_seconds
                # 如果任务完成，找产物
                if task.status == "succeeded":
                    from app.models.asset import Asset

                    asset = db.scalar(
                        select(Asset.id).where(
                            Asset.task_id == task.id,
                            Asset.kind == "output",
                            Asset.is_deleted.is_(False),
                        )
                    )
                    v.asset_id = asset

    return CompareGroupOut(
        id=group["id"],
        workflow_name=group["workflow_name"],
        seed=group["seed"],
        created_at=group["created_at"],
        variants=variants,
    )


@router.get(
    "/compare",
    response_model=list[CompareGroupOut],
    summary="列出所有对比组",
)
def list_compare_groups(
    _: CurrentUser,
) -> list[CompareGroupOut]:
    """列出所有对比组（V1 内存存储，按创建时间倒序）。"""
    groups = sorted(
        _compare_groups.values(), key=lambda g: g["created_at"], reverse=True
    )
    return [
        CompareGroupOut(
            id=g["id"],
            workflow_name=g["workflow_name"],
            seed=g["seed"],
            created_at=g["created_at"],
            variants=g["variants"],
        )
        for g in groups
    ]
