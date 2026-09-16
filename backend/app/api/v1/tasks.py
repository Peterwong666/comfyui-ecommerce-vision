"""任务接口（FR-2.5 / FR-3.4 / FR-4.1~4.5）。

本模块只做「落库 + 派发 + 查询」，**不做实际执行** ——
执行在 Celery worker（P6）。这样 API 的响应时间可与 GPU 完全解耦，
满足 NFR-1「提交响应 ≤200ms」。
"""

from __future__ import annotations

from typing import Annotated, Any

# seed 的递增口径由工作流内核统一定义（契约 §2.4），A 流不自己算 ——
# 两边各有一套取模/边界语义迟早分叉。
from engine import RenderError, derive_seed
from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, select

from app.api.deps import CurrentUser, DbSession
from app.core.config import settings
from app.core.logging import bind_task, get_logger
from app.models.enums import BatchStatus, ErrorType, TaskStatus
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
from app.services import content_safety, quota
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


def _charge_quota(db: DbSession, user: CurrentUser, needed: int) -> None:
    """EX-12：扣减额度，不足则 402。

    **判定与扣减是同一条条件 UPDATE**（`app/services/quota.py`），不是
    「先读 `quota_remaining` 比较、再 `+=`」—— 后者是两个并发请求各读各写时的
    丢更新来源，会让额度被超发。这里只负责把「不够」翻译成 HTTP 错误，
    **不要在本函数之外再判一次额度**（两套判据迟早分叉）。
    """
    if quota.charge(db, user_id=user.id, needed=needed):
        return

    # `charge()` 已经从库里回读过，这里拿到的是**真值**而不是内存里的旧值
    remaining = user.quota_remaining
    message = f"额度不足：需要 {needed}，剩余 {remaining}"
    # 契约 §2.2：需要前端分支处理时用**对象形式**的 detail。
    # `code` 取 `ErrorType` 的取值（唯一事实来源，见 models/enums.py），
    # 不写字面量 —— 否则同一个码会出现第二个真相来源。
    raise HTTPException(
        status_code=status.HTTP_402_PAYMENT_REQUIRED,
        detail={
            "code": ErrorType.QUOTA_EXCEEDED.value,
            "message": message,
            "fields": [{"key": "count", "reason": f"需要 {needed}，剩余 {remaining}"}],
        },
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


def _merge_params(
    wf: Workflow, template: Template | None, payload: dict[str, Any]
) -> dict[str, Any]:
    """参数优先级：工作流默认 < 模板预设 < 用户显式传入。"""
    merged: dict[str, Any] = {}
    for field in (wf.param_schema or {}).get("fields", []):
        if isinstance(field, dict) and "key" in field and "default" in field:
            merged[field["key"]] = field["default"]
    if template is not None:
        merged.update(template.preset_params or {})
    merged.update({k: v for k, v in (payload or {}).items() if v is not None})
    return merged


# ---------------------------------------------------------------- 内容安全（P6-13）


def _screen_content(params: dict[str, Any]) -> dict[str, list[content_safety.RuleHit]]:
    """输入侧内容安全检查（P6-13 / FR-9.4 的输入侧部分）。

    ⚠️ 必须在 `_merge_params` **之后**调用：`params` 是提示词的唯一事实来源
    （契约 §3.4），顶层 `prompt` / `negative_prompt` 都已收敛进去。只查顶层字段会漏掉
    `params["prompt"]` 这条通道 —— 而它此前**没有任何**长度或内容校验（顶层字段至少还有
    `max_length`）。

    命中硬红线 → **422**。不发明 451/403-内容码：契约 §2.2 的状态码表里 422 就是
    「参数校验失败」，多一个表外的码就多一条前端分支 —— 这与上传校验因表里没有 413 而
    统一用 422 是同一个取舍（见 `assets.py` 头部说明）。
    命中 warn → **放行**，命中清单返回给调用方写审计（`_record_content_warnings`）。

    开关 `settings.content_safety_enabled=False` 时**完全不检查**（不拦、也不留痕）。
    """
    if not settings.content_safety_enabled:
        return {}

    hits_by_field = content_safety.check_params(params)
    if any(hit.blocks for hits in hits_by_field.values() for hit in hits):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=content_safety.blocked_detail(hits_by_field),
        )
    return hits_by_field


def _record_content_warnings(
    db: DbSession,
    user: CurrentUser,
    warned: dict[str, list[content_safety.RuleHit]],
    params: dict[str, Any],
    *,
    target_type: str,
    target_id: int,
) -> None:
    """给 warn 级命中留痕（FR-1.4 审计）。

    🔴 **隐私红线**：`detail` 里只有规则 id / hash / 长度 / 阶段 / 字段名，
    **没有命中的原词、也没有提示词全文** —— `tracking_plan.md` §1.2 规定提示词可能含
    商品名/品牌等敏感信息，**只记长度与 hash**。payload 一律由
    `content_safety.warn_detail` 构造，本函数不做任何字符串拼接：这样就没有一个
    "顺手把 `params[field]` 塞进去"的位置。
    """
    for field, hits in warned.items():
        db.add(
            AuditLog(
                user_id=user.id,
                action=content_safety.WARN_AUDIT_ACTION,
                target_type=target_type,
                target_id=str(target_id),
                detail=content_safety.warn_detail(field, hits, params[field]),
            )
        )


# ---------------------------------------------------------------- 单次生成


@router.post(
    "/tasks",
    response_model=TaskOut,
    status_code=status.HTTP_202_ACCEPTED,
    summary="提交单次生成（FR-2.5）",
)
def submit_task(payload: TaskSubmitIn, user: CurrentUser, db: DbSession) -> Task:
    _charge_quota(db, user, 1)
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
    # 内容安全放在参数合并之后：两条提示词通道（顶层 / params）都已收敛进 params
    warned = _screen_content(params)

    task = sm.create_task(
        db,
        user_id=user.id,
        workflow_id=wf.id,
        template_id=template.id if template else None,
        params=params,
        # 从 params 回读，保证「落库的」与「注入图的」永远是同一个值
        prompt=params.get("prompt"),
        negative_prompt=params.get("negative_prompt"),
        # ⚠️ `Task.seed` 是 `params["seed"]` 的**派生投影，不是第二条输入通道**
        # （契约 §3.4：params 是唯一事实来源）。
        # 它保留成独立列，是因为它是一等可查询属性（列表筛选、看板统计要用）；
        # 但**任何写入都必须来自 params**，不要再往它写别的值 ——
        # 曾经 `Task.seed` 与 `params["seed"]` 是两条独立通道，可以传出不一致的结果。
        seed=params.get("seed"),
        priority=settings.default_priority,
        idempotency_key=payload.idempotency_key,
    )

    sm.enqueue(db, task)

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
    _record_content_warnings(
        db, user, warned, params, target_type="task", target_id=task.id
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


def _batch_seed_base(base_seed: Any) -> int | None:
    """把批次的 seed 基准解析成**具体整数**（`None` = 不指定，交给 Schema 默认值）。

    递增口径**由 B 流内核的 `derive_seed()` 定义，A 流不自己算** ——
    否则两边各有一套取模/边界语义，迟早分叉（本项目已多次踩这个坑型）。

    | 输入 | 结果 |
    |---|---|
    | `None` | `None`，不生成 |
    | `-1` | 内核随机出一个基准 |
    | 具体值 | 校验取值域后原样返回 |

    ⚠️ **`-1` 必须在整批上只随机一次**。`derive_seed(base, idx)` 在 `base == -1` 时
    会**每次都重新随机**，若逐张调用就得到一批互不相关的随机 seed ——
    「同批次风格一致但每张不同」（契约 §2.4）会**静默失效**：产物看着正常，
    但批内不再有任何递增关系，用户无法按 seed 复现某一批。
    （2026-09-16 由测试发现：`derive_seed(-1, idx-1)` 逐张调用得到 3.3e9 级别的乱序值。）

    这里借 `derive_seed(x, 0)` 取基准（`idx=0` 时结果就是基准本身），
    保证与内核共用同一套取值域与随机源口径，而不是自己 `randrange`。
    """
    if base_seed is None:
        return None
    try:
        numeric = int(base_seed)
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"seed 必须是整数，实际 {base_seed!r}",
        ) from exc
    try:
        return derive_seed(numeric, 0)
    except RenderError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"seed 非法：{exc}"
        ) from exc


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


@router.post(
    "/batches",
    response_model=BatchOut,
    status_code=status.HTTP_202_ACCEPTED,
    summary="提交批量任务（FR-3.4）",
)
def submit_batch(payload: BatchSubmitIn, user: CurrentUser, db: DbSession) -> Batch:
    wf = _get_active_workflow(db, payload.workflow_name)
    total = payload.total_images()
    if total > settings.max_batch_size:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"单任务最多 {settings.max_batch_size} 张，当前 {total}",
        )
    # 批量是**整体成功或整体失败**：一条条件 UPDATE 扣 `total`，
    # 剩余不足时 rowcount=0 → 402，不会出现「扣了一半」的中间态。
    _charge_quota(db, user, total)

    template = db.get(Template, payload.template_id) if payload.template_id else None
    common = _merge_params(wf, template, payload.common_params)
    _validate_params(common)
    # 批量与单任务走同一套检查：批量的提示词只可能来自 common_params
    # （`BatchSubmitIn` 没有顶层 prompt 字段），所以查 common 即可覆盖整批。
    warned = _screen_content(common)

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
    # 整批共用一个 seed 基准（`-1` 只在这里随机一次，见 _batch_seed_base 的说明）
    seed_base = _batch_seed_base(common.get("seed"))
    for sku, asset_ids in payload.sku_assets.items():
        for _ in range(payload.images_per_sku):
            idx += 1
            params = {
                **common,
                "sku": sku,
                "upload_asset_ids": asset_ids,
                # 全局序号 idx（不是 SKU 内序号）递增：用后者会让不同 SKU 的第 k 张撞同一 seed
                "seed": None if seed_base is None else derive_seed(seed_base, idx - 1),
            }
            child = sm.create_task(
                db,
                user_id=user.id,
                batch_id=batch.id,
                idx=idx,
                workflow_id=wf.id,
                template_id=template.id if template else None,
                params=params,
                # 同单任务：`Task.seed` 是 params 的派生投影，不是第二条通道
                seed=params.get("seed"),
                priority=settings.default_priority,
            )
            # T2：入队。**漏掉这一步会让整个批量功能失效** ——
            # 子任务停在 `pending`，而 worker 只接受 `queued`（会直接跳过），
            # 于是批量提交后一张图都不会跑。单任务路径（submit_task）一直是这么做的，
            # 批量路径此前漏了（2026-09-16 由端到端测试发现）。
            sm.enqueue(db, child)

    db.flush()
    sm.recompute_batch_counts(db, batch)

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
    # 留痕挂在 **batch** 上（不是某一张子任务）：提示词是整批共用的，
    # 逐张写会在一个 500 张的批量里留下 500 条一模一样的审计。
    _record_content_warnings(
        db, user, warned, common, target_type="batch", target_id=batch.id
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
    batch = db.scalar(select(Batch).where(Batch.id == batch_id, Batch.user_id == user.id))
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
    _charge_quota(db, user, 1)

    sm.reset_for_retry(db, task)
    sm.enqueue(db, task)
    db.commit()

    enqueue_task(task.id, task.priority)
    return task


@router.post(
    "/batches/{batch_id}/retry-failed",
    response_model=BatchOut,
    summary="断点续跑：只重跑失败项（FR-3.5）",
)
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

    _charge_quota(db, user, len(failed))
    for task in failed:
        sm.reset_for_retry(db, task)
        sm.enqueue(db, task)
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
