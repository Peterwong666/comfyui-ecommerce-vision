"""任务接口（FR-2.5 / FR-3.4 / FR-4.1~4.5）。

本模块只做「落库 + 派发 + 查询」，**不做实际执行** ——
执行在 Celery worker（P6）。这样 API 的响应时间可与 GPU 完全解耦，
满足 NFR-1「提交响应 ≤200ms」。
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, select

from app.api.deps import CurrentUser, DbSession
from app.core.config import settings
from app.core.logging import bind_task, get_logger
from app.engine.injector import SEED_RANDOM
from app.models.enums import BatchStatus, TaskStatus
from app.models.event import AuditLog, EventName
from app.models.task import Batch, Task
from app.models.workflow import Template, Workflow
from app.schemas.task import (
    BatchOut,
    BatchSubmitIn,
    CancelOut,
    TaskEstimateOut,
    TaskOut,
    TaskSubmitIn,
)
from app.services import state_machine as sm
from app.services.dispatch import enqueue_task
from app.services.events import track

log = get_logger(__name__)
router = APIRouter(tags=["tasks"])


# ---------------------------------------------------------------- 内部工具


def _get_active_workflow(db: DbSession, name: str) -> Workflow:
    wf = db.scalar(
        select(Workflow)
        .where(Workflow.name == name, Workflow.is_active.is_(True))
        .order_by(Workflow.version.desc())
    )
    if wf is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"工作流 {name} 不存在或未启用",
        )
    return wf


def _assert_quota(user: CurrentUser, needed: int) -> None:
    """EX-12：配额耗尽时拒绝提交新任务。"""
    if user.quota_remaining < needed:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=f"额度不足：需要 {needed}，剩余 {user.quota_remaining}",
        )


def _validate_params(payload: dict[str, Any]) -> None:
    """参数二次校验（EX-3）。

    前端已经拦过一次，但后端必须再校验 —— 前端校验可被绕过，
    而非法参数进入工作流会导致 ComfyUI 报难以理解的错误。
    """
    steps = payload.get("steps")
    if steps is not None and not (settings.min_steps <= int(steps) <= settings.max_steps):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"步数需在 {settings.min_steps}-{settings.max_steps}",
        )
    cfg = payload.get("cfg")
    if cfg is not None and not (settings.min_cfg <= float(cfg) <= settings.max_cfg):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"CFG 需在 {settings.min_cfg}-{settings.max_cfg}",
        )


def _merge_params(wf: Workflow, template: Template | None, payload: dict[str, Any]) -> dict[str, Any]:
    """参数优先级：工作流默认 < 模板预设 < 用户显式传入。"""
    merged: dict[str, Any] = {}
    for field in (wf.param_schema or {}).get("fields", []):
        if isinstance(field, dict) and "key" in field and "default" in field:
            merged[field["key"]] = field["default"]
    if template is not None:
        merged.update(template.preset_params or {})
    merged.update({k: v for k, v in (payload or {}).items() if v is not None})
    return merged


# ---------------------------------------------------------------- 单次生成


@router.post("/tasks", response_model=TaskOut, status_code=status.HTTP_202_ACCEPTED,
             summary="提交单次生成（FR-2.5）")
def submit_task(payload: TaskSubmitIn, user: CurrentUser, db: DbSession) -> Task:
    _assert_quota(user, 1)
    wf = _get_active_workflow(db, payload.workflow_name)

    template = None
    if payload.template_id is not None:
        template = db.get(Template, payload.template_id)
        if template is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="模板不存在")

    params = _merge_params(wf, template, payload.params)
    if payload.steps is not None:
        params["steps"] = payload.steps
    if payload.cfg is not None:
        params["cfg"] = payload.cfg
    if payload.seed is not None:
        params["seed"] = payload.seed
    # 提示词收敛为**单通道**（契约 §3.4：顶层优先）。
    # 此前顶层 prompt 只落到 Task.prompt 列、params["prompt"] 另行合并，
    # 两条通道可以传出不同的值，注入工作流的那个与落库的那个会不一致。
    # 现在以 params["prompt"] 为唯一事实来源，Task.prompt 只是它的冗余副本。
    if payload.prompt is not None:
        params["prompt"] = payload.prompt
    if payload.negative_prompt is not None:
        params["negative_prompt"] = payload.negative_prompt
    _validate_params(params)

    task = sm.create_task(
        db,
        user_id=user.id,
        workflow_id=wf.id,
        template_id=template.id if template else None,
        params=params,
        # 从 params 回读，保证「落库的」与「注入图的」永远是同一个值
        prompt=params.get("prompt"),
        negative_prompt=params.get("negative_prompt"),
        seed=payload.seed,
        priority=settings.default_priority,
        idempotency_key=payload.idempotency_key,
    )

    sm.enqueue(db, task)
    user.quota_used += 1

    track(
        db,
        EventName.TASK_SUBMITTED,
        user_id=user.id,
        task_id=task.id,
        workflow=wf.name,
        batch_size=1,
        mode="single",
        template_id=template.id if template else None,
    )
    db.add(
        AuditLog(user_id=user.id, action="task.submit", target_type="task", target_id=str(task.id))
    )
    db.commit()

    # 派发放在 commit 之后：如果先派发后提交失败，worker 会拿不到任务
    enqueue_task(task.id, task.priority)
    return task


@router.post("/tasks/estimate", response_model=TaskEstimateOut, summary="提交前预估（FR-3.3）")
def estimate(payload: BatchSubmitIn, user: CurrentUser, db: DbSession) -> TaskEstimateOut:
    """给出张数 / 预计耗时 / 预计成本。

    设计师的核心风险是「跑完才发现成本超预算」，所以预估必须在提交之前。
    """
    wf = _get_active_workflow(db, payload.workflow_name)
    total = payload.total_images()

    # 基准耗时：优先用工作流实测的 avg_duration，没有则用保守经验值
    per_image_seconds = _avg_seconds_per_image(db, wf.id) or 20.0
    # GPU 并发固定为 1，所以总耗时 = 单张 × 张数（串行）
    estimated = per_image_seconds * total

    from app.engine.driver import ComfyUIClient, ComfyUIError

    queue_depth: int | None = None
    try:
        with ComfyUIClient() as client:
            depth = client.queue_depth()
            queue_depth = depth if depth >= 0 else None
    except ComfyUIError:
        queue_depth = None

    est_wait = (queue_depth * per_image_seconds) if queue_depth is not None else None
    est_cost = None
    if settings.gpu_cost_per_hour > 0:
        est_cost = estimated * settings.gpu_cost_per_second

    return TaskEstimateOut(
        total_images=total,
        estimated_seconds=estimated,
        estimated_cost_yuan=est_cost,
        gpu_concurrency=1,
        queue_depth=queue_depth,
        estimated_wait_seconds=est_wait,
    )


def _child_seed(base_seed: Any, idx: int) -> int | None:
    """批量子任务的 seed（契约 §2.4「同批次内 seed 自动递增」）。

    三种情况：

    | base_seed | 结果 | 理由 |
    |---|---|---|
    | `None` | `None` | 用户没指定，交给工作流 Schema 的默认值（可能根本不是参数） |
    | `-1`（随机哨兵） | `-1` | 用户要的是"每张都随机"，**不能**再叠加递增 —— 语义相矛盾 |
    | 具体值 | `base + idx - 1` | 递增：风格一致但每张不同；固定 seed 则整批一致（FR-8.2 的取舍） |

    ⚠️ **必须用全局序号 `idx` 而不是每个 SKU 内的序号**：
    用后者时 `3 SKU × 2 张` 会得到 `100,101,100,101,100,101` ——
    不同 SKU 的第 k 张撞到同一个 seed，同提示词会直接出同一张图，
    "同批次内递增"形同失效（2026-09-16 由端到端测试发现）。
    """
    if base_seed is None:
        return None
    try:
        numeric = int(base_seed)
    except (TypeError, ValueError):
        return None
    if numeric == SEED_RANDOM:
        return SEED_RANDOM
    return numeric + (idx - 1)


def _avg_seconds_per_image(db: DbSession, workflow_id: int) -> float | None:
    """从历史成功任务里算平均执行时长，让预估随实测数据收敛。"""
    row = db.execute(
        select(func.avg(Task.duration_ms)).where(
            Task.workflow_id == workflow_id, Task.status == TaskStatus.SUCCEEDED.value
        )
    ).scalar()
    if row is None:
        return None
    return float(row) / 1000.0


# ---------------------------------------------------------------- 批量


@router.post("/batches", response_model=BatchOut, status_code=status.HTTP_202_ACCEPTED,
             summary="提交批量任务（FR-3.4）")
def submit_batch(payload: BatchSubmitIn, user: CurrentUser, db: DbSession) -> Batch:
    wf = _get_active_workflow(db, payload.workflow_name)
    total = payload.total_images()
    if total > settings.max_batch_size:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"单任务最多 {settings.max_batch_size} 张，当前 {total}",
        )
    _assert_quota(user, total)

    template = db.get(Template, payload.template_id) if payload.template_id else None
    common = _merge_params(wf, template, payload.common_params)
    _validate_params(common)

    batch = Batch(
        user_id=user.id,
        name=payload.name,
        status=BatchStatus.PENDING.value,
        workflow_id=wf.id,
        template_id=template.id if template else None,
        common_params=common,
        total_count=total,
    )
    db.add(batch)
    db.flush()

    # 子任务粒度 = 一张图（PRD §3.1 决策）：
    # 这样失败能精确隔离，断点续跑才能只重跑失败的那几张。
    idx = 0
    base_seed = common.get("seed")
    for sku, asset_ids in payload.sku_assets.items():
        for _ in range(payload.images_per_sku):
            idx += 1
            params = {
                **common,
                "sku": sku,
                "upload_asset_ids": asset_ids,
                "seed": _child_seed(base_seed, idx),
            }
            child = sm.create_task(
                db,
                user_id=user.id,
                batch_id=batch.id,
                idx=idx,
                workflow_id=wf.id,
                template_id=template.id if template else None,
                params=params,
                priority=settings.default_priority,
            )
            # T2：入队。**漏掉这一步会让整个批量功能失效** ——
            # 子任务停在 `pending`，而 worker 只接受 `queued`（会直接跳过），
            # 于是批量提交后一张图都不会跑。单任务路径（submit_task）一直是这么做的，
            # 批量路径此前漏了（2026-09-16 由端到端测试发现）。
            sm.enqueue(db, child)

    db.flush()
    sm.recompute_batch_counts(db, batch)
    user.quota_used += total

    track(
        db,
        EventName.TASK_SUBMITTED,
        user_id=user.id,
        batch_id=batch.id,
        workflow=wf.name,
        batch_size=total,
        mode="batch",
        template_id=template.id if template else None,
    )
    db.add(
        AuditLog(
            user_id=user.id, action="batch.submit", target_type="batch", target_id=str(batch.id)
        )
    )
    db.commit()

    for task in batch.tasks:
        enqueue_task(task.id, task.priority)

    log.info("batch.submitted", extra=bind_task(batch_id=batch.id, total=total))
    return batch


# ---------------------------------------------------------------- 查询


@router.get("/tasks", response_model=list[TaskOut], summary="任务列表（FR-4.1）")
def list_tasks(
    user: CurrentUser,
    db: DbSession,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    batch_id: int | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[Task]:
    stmt = select(Task).where(Task.user_id == user.id)
    if status_filter:
        stmt = stmt.where(Task.status == status_filter)
    if batch_id is not None:
        stmt = stmt.where(Task.batch_id == batch_id)
    stmt = stmt.order_by(Task.id.desc()).limit(limit).offset(offset)
    return list(db.scalars(stmt).all())


@router.get("/tasks/{task_id}", response_model=TaskOut, summary="任务详情（FR-4.2）")
def get_task(task_id: int, user: CurrentUser, db: DbSession) -> Task:
    task = _owned_task(db, task_id, user.id)
    return task


@router.get("/batches", response_model=list[BatchOut], summary="批量任务列表")
def list_batches(
    user: CurrentUser,
    db: DbSession,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[Batch]:
    stmt = select(Batch).where(Batch.user_id == user.id)
    if status_filter:
        stmt = stmt.where(Batch.status == status_filter)
    stmt = stmt.order_by(Batch.id.desc()).limit(limit).offset(offset)
    return list(db.scalars(stmt).all())


@router.get("/batches/{batch_id}", response_model=BatchOut, summary="批量任务详情")
def get_batch(batch_id: int, user: CurrentUser, db: DbSession) -> Batch:
    batch = db.scalar(
        select(Batch).where(Batch.id == batch_id, Batch.user_id == user.id)
    )
    if batch is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="批量任务不存在")
    return batch


# ---------------------------------------------------------------- 操作


@router.post("/tasks/{task_id}/cancel", response_model=CancelOut, summary="取消任务（FR-4.3）")
def cancel_task(task_id: int, user: CurrentUser, db: DbSession) -> CancelOut:
    task = _owned_task(db, task_id, user.id)
    if task.status not in TaskStatus.cancellable():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"当前状态 {task.status} 不可取消",
        )

    was_running = task.status == TaskStatus.RUNNING.value
    engine_prompt_id = task.engine_prompt_id
    sm.mark_canceled(db, task)
    db.commit()

    if engine_prompt_id or was_running:
        # 执行中的任务需要通知引擎停止。
        #
        # 用 `cancel()` 而不是裸 `interrupt()`：interrupt 只对"正在执行"的任务有效，
        # 若任务已提交但还待在引擎队列里，它是**空操作** —— 那张图随后仍会被跑出来，
        # 白烧一次 GPU。`cancel()` 会先判断在队列还是在执行，再决定"整条删除"
        # 还是"步间隙中断"。
        #
        # ComfyUI 的 /interrupt 是全局的；因为 GPU 并发固定为 1（NFR-2），
        # 语义上安全。若未来多实例并发（E10），必须改为按实例路由。
        from app.engine.driver import ComfyUIClient, ComfyUIError

        try:
            with ComfyUIClient() as client:
                if engine_prompt_id:
                    outcome = client.cancel(engine_prompt_id)
                    log.info(
                        "cancel.engine prompt_id=%s outcome=%s",
                        engine_prompt_id,
                        outcome.value,
                    )
                else:
                    client.interrupt()
        except ComfyUIError as exc:
            # 引擎不可达不应让取消操作失败：状态已落库为 canceled，
            # worker 轮询时也会看到并主动停止。
            log.warning("cancel.interrupt_failed task_id=%s err=%s", task_id, exc)

    return CancelOut(task_id=task.id, status=task.status, message="已取消")


@router.post("/tasks/{task_id}/retry", response_model=TaskOut, summary="重试任务（FR-4.4）")
def retry_task(task_id: int, user: CurrentUser, db: DbSession) -> Task:
    task = _owned_task(db, task_id, user.id)
    if task.status not in (TaskStatus.FAILED.value, TaskStatus.CANCELED.value):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"当前状态 {task.status} 不可重试",
        )
    _assert_quota(user, 1)

    sm.reset_for_retry(db, task)
    sm.enqueue(db, task)
    user.quota_used += 1
    db.commit()

    enqueue_task(task.id, task.priority)
    return task


@router.post("/batches/{batch_id}/retry-failed", response_model=BatchOut,
             summary="断点续跑：只重跑失败项（FR-3.5）")
def retry_failed(batch_id: int, user: CurrentUser, db: DbSession) -> Batch:
    """批量失败后只重跑失败的那些。

    这是画像②最痛的点：「批量跑 50 张，跑到第 30 张崩了，前功尽弃」。
    所以这里**绝不重跑已成功的** —— 既省 GPU 又省用户额度。
    """
    batch = db.scalar(select(Batch).where(Batch.id == batch_id, Batch.user_id == user.id))
    if batch is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="批量任务不存在")

    failed = list(
        db.scalars(
            select(Task).where(
                Task.batch_id == batch.id,
                Task.status.in_([TaskStatus.FAILED.value, TaskStatus.CANCELED.value]),
            )
        ).all()
    )
    if not failed:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="没有失败的任务可重跑")

    _assert_quota(user, len(failed))
    for task in failed:
        sm.reset_for_retry(db, task)
        sm.enqueue(db, task)
    user.quota_used += len(failed)
    sm.recompute_batch_counts(db, batch)
    db.commit()

    for task in failed:
        enqueue_task(task.id, task.priority)

    log.info("batch.retry_failed", extra=bind_task(batch_id=batch.id, count=len(failed)))
    return batch


# ---------------------------------------------------------------- 辅助


def _owned_task(db: DbSession, task_id: int, user_id: int) -> Task:
    """带归属校验的取任务（FR-1.3 数据隔离）。"""
    task = db.scalar(select(Task).where(Task.id == task_id, Task.user_id == user_id))
    if task is None:
        # 用 404 而不是 403：不泄露「该任务存在但不属于你」
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="任务不存在")
    return task
