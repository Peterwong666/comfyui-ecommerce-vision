"""Celery 任务：执行单张出图（P6-02）。

**设计上的一个关键取舍**：Celery 的 task 函数只做「取依赖 + 调 `TaskExecutor` + 决定是否重投」，
真正的执行逻辑全在 `TaskExecutor.execute()` 里，外部依赖（DB / 引擎 / 存储 / 锁）
**全部从构造函数注入**。这样：
- 单测可以不碰 Redis / Celery / ComfyUI / MinIO（当前实例是无卡模式，本来也连不上）；
- 换实现（比如把存储从 MinIO 换成 S3）不需要动执行逻辑。

执行流程与 PRD §5.4 的迁移编号一一对应：

    queued ──拿 GPU 锁──► running ──成功──► succeeded            (T5/T6)
                            │
                            ├─ 瞬时错误 ──► retrying ──► queued   (T7/T8)
                            ├─ 致命错误 ──► failed                (T11)
                            └─ 用户取消 ──► canceled              (T10)

`retrying → queued`（T8）由**本次 Celery 重投的到达**触发：Celery 的 `countdown`
就是退避本身，到达即说明退避结束。好处是只有一个地方在管"什么时候能再跑"。
"""

from __future__ import annotations

import enum
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import bind_task, get_logger
from app.db.session import SessionLocal
from app.engine.driver import (
    ComfyUIClient,
    ComfyUIError,
    ExecutionCanceled,
    ExecutionResult,
    ProgressWatcher,
)
from app.engine.injector import InjectionError, InjectionResult, inject_params
from app.models.asset import Asset
from app.models.enums import AssetKind, ErrorType, TaskStatus
from app.models.event import AuditLog, EventName
from app.models.task import Task
from app.models.workflow import Workflow
from app.services import state_machine as sm
from app.services.events import track
from app.services.storage import (
    AssetStorage,
    MinioAssetStorage,
    build_output_key,
    mime_for_filename,
)
from app.worker.celery_app import celery_app
from app.worker.concurrency import GpuLockUnavailable, GpuSlot

log = get_logger(__name__)


class Outcome(str, enum.Enum):
    """一次执行尝试的结局。用于日志、指标与测试断言。"""

    SUCCEEDED = "succeeded"
    FAILED = "failed"  # 已到终态，不再重试
    RETRYING = "retrying"  # 已排好退避，等 Celery 重投
    CANCELED = "canceled"
    SKIPPED = "skipped"  # 状态不是 queued/retrying（重复投递、已被取消…）
    MISSING = "missing"  # 任务不存在（被删了）
    LOCK_BUSY = "lock_busy"  # 没抢到 GPU 独占位，退回队列
    TIMED_OUT = "timed_out"  # Celery 软超时兜底


class TaskExecutor:
    """执行单个任务。

    所有外部依赖都可注入 —— 这是本模块能在无 GPU / 无 Redis 环境下被测试的原因。
    """

    def __init__(
        self,
        *,
        session_factory: Callable[[], Session] = SessionLocal,
        driver_factory: Callable[[], ComfyUIClient] = ComfyUIClient,
        storage_factory: Callable[[], AssetStorage] = MinioAssetStorage,
        slot_factory: Callable[[], GpuSlot] = GpuSlot,
        lock_wait_seconds: int | None = None,
        lock_poll_seconds: float = 0.25,
        poll_interval: float | None = None,
        vram_sample_seconds: float | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._driver_factory = driver_factory
        self._storage_factory = storage_factory
        self._slot_factory = slot_factory
        self._lock_wait = (
            settings.gpu_lock_wait_seconds if lock_wait_seconds is None else lock_wait_seconds
        )
        self._lock_poll = lock_poll_seconds
        self._poll_interval = (
            settings.worker_poll_interval_seconds if poll_interval is None else poll_interval
        )
        self._vram_sample = (
            settings.worker_vram_sample_seconds
            if vram_sample_seconds is None
            else vram_sample_seconds
        )

    # ---------------------------------------------------------------- 入口

    def execute(self, task_id: int) -> Outcome:
        db = self._session_factory()
        try:
            task = db.get(Task, task_id)
            if task is None:
                log.warning("task.execute_missing", extra=bind_task(task_id=task_id))
                return Outcome.MISSING

            # 幂等闸门：Celery 是 at-least-once，重复投递/重复消费是常态。
            # 状态判断放在最前面，重复的那次会直接跳过而不是重跑一遍图。
            if task.status == TaskStatus.RETRYING.value:
                # T8：退避结束，重新入队。重投的到达本身就是"退避结束"的信号。
                sm.enqueue(db, task)
                db.commit()
            elif task.status != TaskStatus.QUEUED.value:
                log.info(
                    "task.execute_skipped status=%s", task.status, extra=bind_task(task_id=task_id)
                )
                return Outcome.SKIPPED

            slot = self._slot_factory()
            acquired = False
            try:
                acquired = slot.acquire(
                    timeout=self._lock_wait,
                    on_wait=lambda: self._still_wanted(db, task_id, TaskStatus.QUEUED.value),
                )
            except GpuLockUnavailable as exc:
                # 锁后端故障 ≠ 没抢到锁。退回队列稍后重投，绝不绕过去直接上卡。
                log.warning(
                    "task.lock_unavailable err=%s", str(exc)[:200], extra=bind_task(task_id=task_id)
                )
                return Outcome.LOCK_BUSY

            if not acquired:
                # 可能只是没等到（GPU 忙），也可能是等待期间被取消了 —— 前者重投，后者结束
                db.refresh(task)
                if task.status != TaskStatus.QUEUED.value:
                    return Outcome.SKIPPED
                return Outcome.LOCK_BUSY
            try:
                return self._run_locked(db, task)
            finally:
                slot.release()
        finally:
            db.close()

    # ---------------------------------------------------------------- 内部

    def _still_wanted(self, db: Session, task_id: int, expected: str) -> bool:
        """任务是否仍处于我们期望的状态。

        两个阶段用不同的期望值，**不能共用一个判断**：
        - 等 GPU 锁时任务应是 `queued` —— 一旦不是（用户取消/别的进程接管）就别等了，
          否则会白等满 `gpu_lock_wait_seconds`；
        - 执行中任务应是 `running` —— 一旦不是（用户取消/被僵尸恢复接管）就要停下。

        （曾经两者都按 `queued` 判断，结果执行中每次轮询都判定"不要了"，
        任务刚跑起来就被当成取消 —— 这个 bug 是被单测挡下来的。）
        """
        db.expire_all()
        fresh = db.get(Task, task_id)
        return fresh is not None and fresh.status == expected

    def _run_locked(self, db: Session, task: Task) -> Outcome:
        db.refresh(task)
        if task.status != TaskStatus.QUEUED.value:
            return Outcome.SKIPPED

        workflow = db.get(Workflow, task.workflow_id)
        if workflow is None:
            return self._fail_immediately(
                db,
                task,
                ErrorType.MODEL_MISSING,
                f"工作流 {task.workflow_id} 不存在（workflow 被删或 id 失效）",
            )

        # 参数注入要在标 running **之前**做完：
        # 注入错误是"这张图根本没法跑"，不该占用排队时长统计，也不该显示成"跑过"。
        # 但状态机不允许 queued → failed（契约 §1.3 的迁移表里没有这条边），
        # 所以必须先 running 再立刻 failed —— 见 _fail_immediately 的说明。
        try:
            injection = inject_params(
                workflow.definition,
                workflow.param_schema,
                task.params,
                workflow.param_bindings,
                seed=task.seed,
            )
        except InjectionError as exc:
            return self._fail_immediately(db, task, ErrorType.INVALID_PARAM, f"参数注入失败：{exc}")

        sm.mark_running(db, task)
        db.commit()
        track(
            db,
            EventName.TASK_STARTED,
            user_id=task.user_id,
            task_id=task.id,
            batch_id=task.batch_id,
            source="worker",
            workflow=workflow.name,
        )
        db.commit()

        prompt_id: str | None = None
        client = self._driver_factory()
        watcher: ProgressWatcher | None = None
        vram_samples: list[float] = []
        try:
            prompt_id = client.submit(injection.definition)
            # 立刻落库：取消要按它去引擎里删/中断（T3/T4/T10），
            # trace 也要靠它把平台与引擎两侧的日志串起来（FR-4.5）。
            task.engine_prompt_id = prompt_id
            db.commit()

            watcher = client.watch_progress(prompt_id)
            try:
                watcher.start()
            except Exception as exc:  # 订阅失败不影响执行
                log.warning(
                    "task.watcher_start_failed err=%s",
                    str(exc)[:200],
                    extra=bind_task(task_id=task.id),
                )
                watcher = None

            result = client.wait(
                prompt_id,
                timeout=settings.task_timeout_seconds,
                poll_interval=self._poll_interval,
                task_id=task.id,
                should_stop=lambda: not self._still_wanted(db, task.id, TaskStatus.RUNNING.value),
                on_poll=lambda elapsed: self._on_poll(db, task, client, elapsed, vram_samples),
            )
            return self._finish_success(
                db,
                task,
                workflow,
                client,
                result,
                injection,
                max(vram_samples) if vram_samples else None,
            )

        except ExecutionCanceled:
            # T10：状态已由 API 改成 canceled（或马上就会）。
            # 先让引擎松手 —— 否则那张图会继续占着 GPU 跑完。
            self._best_effort_cancel(client, prompt_id, task.id)
            db.refresh(task)
            if task.status != TaskStatus.CANCELED.value:
                sm.mark_canceled(db, task)
            self._track_finished(db, task, TaskStatus.CANCELED.value)
            db.commit()
            log.info("task.canceled", extra=bind_task(task_id=task.id))
            return Outcome.CANCELED

        except ComfyUIError as exc:
            # **先让引擎松手再决定重试**：否则失败的图还占着 GPU，
            # 下一次重试会与它抢锁，白等一个超时周期。
            self._best_effort_cancel(client, prompt_id, task.id)

            # 用户可能在引擎报错的同一瞬间点了取消。此时状态已不是 running，
            # 直接走 mark_retrying 会抛 InvalidTransition（canceled 不能 → retrying），
            # 把一次正常的"取消"变成 worker 崩溃。所以先认一次真实状态。
            db.refresh(task)
            if task.status == TaskStatus.CANCELED.value:
                self._track_finished(db, task, TaskStatus.CANCELED.value)
                db.commit()
                return Outcome.CANCELED
            if task.status != TaskStatus.RUNNING.value:
                return Outcome.SKIPPED

            retried = sm.mark_retrying(db, task, exc.error_type, str(exc))
            if retried:
                track(
                    db,
                    EventName.TASK_RETRY,
                    user_id=task.user_id,
                    task_id=task.id,
                    batch_id=task.batch_id,
                    source="worker",
                    error_type=exc.error_type.value,
                    retry_count=task.retry_count,
                )
                db.commit()
                log.warning(
                    "task.will_retry error_type=%s retry=%s",
                    exc.error_type.value,
                    task.retry_count,
                    extra=bind_task(task_id=task.id),
                )
                return Outcome.RETRYING
            self._track_finished(db, task, TaskStatus.FAILED.value, error_type=exc.error_type.value)
            db.commit()
            log.error(
                "task.failed error_type=%s", exc.error_type.value, extra=bind_task(task_id=task.id)
            )
            return Outcome.FAILED
        finally:
            if watcher is not None:
                watcher.stop()

    # ---------------------------------------------------------------- 各阶段

    def _fail_immediately(self, db: Session, task: Task, error: ErrorType, message: str) -> Outcome:
        """从 `queued` 直接判失败。

        ⚠️ 必须借道 `running`：契约 §1.3 的迁移表里**没有** `queued → failed` 这条边，
        唯一的路径是 `queued → running → failed`。这是刻意的收紧（"没跑过的东西不该
        有执行结果"），代价是这类"根本无法开始"的失败也要先标一次 running。
        若将来要加直达边，属于契约变更（§0.3），不要在流内私自加。
        """
        sm.mark_running(db, task)
        sm.mark_failed(db, task, error, message)
        self._track_finished(db, task, TaskStatus.FAILED.value, error_type=error.value)
        db.commit()
        log.error(
            "task.failed_before_run error_type=%s msg=%s",
            error.value,
            message[:200],
            extra=bind_task(task_id=task.id),
        )
        return Outcome.FAILED

    def _on_poll(
        self,
        db: Session,
        task: Task,
        client: ComfyUIClient,
        elapsed: float,
        vram_samples: list[float],
    ) -> None:
        """轮询间隙的维护动作：刷心跳 + 按固定间隔采样显存。

        心跳是 T14 僵尸检测的前提：worker 只要还在推进就必须刷，
        否则自己会被别的进程判成"挂了"并被重新入队（导致重复出图）。
        """
        sm.heartbeat(db, task)
        db.commit()

        # 按「已采样本数」推算下一次采样时刻，省掉一个额外的状态变量
        interval = max(self._vram_sample, 0.1)
        if elapsed >= interval * (len(vram_samples) + 1):
            sample = self._vram_used(client)
            if sample is not None:
                vram_samples.append(sample)
        log.debug("task.progress elapsed=%.1fs", elapsed, extra=bind_task(task_id=task.id))

    def _vram_used(self, client: ComfyUIClient) -> float | None:
        """读取当前显存占用（MB）。拿不到就返回 None —— 这是排障信息，不值得让任务失败。"""
        try:
            stats = client.health()
            device = (stats.get("devices") or [{}])[0]
            total = device.get("vram_total")
            free = device.get("vram_free")
            if isinstance(total, int) and isinstance(free, int):
                return round((total - free) / (1024 * 1024), 1)
        except Exception as exc:
            log.debug("task.vram_sample_failed err=%s", str(exc)[:120])
        return None

    def _finish_success(
        self,
        db: Session,
        task: Task,
        workflow: Workflow,
        client: ComfyUIClient,
        result: ExecutionResult,
        injection: InjectionResult,
        vram_peak: float | None,
    ) -> Outcome:
        """成功后：取图 → 落存储 → 记 Asset → 标 succeeded。"""
        # 用户可能在图刚跑完、我们还没落库的这几百毫秒里点了取消。
        # 此时**尊重取消**：不落产物、不标 succeeded。
        # 若不检查，mark_succeeded 会从 canceled 迁移并抛 InvalidTransition。
        db.refresh(task)
        if task.status == TaskStatus.CANCELED.value:
            self._track_finished(db, task, TaskStatus.CANCELED.value)
            db.commit()
            log.info("task.canceled_before_save", extra=bind_task(task_id=task.id))
            return Outcome.CANCELED
        if task.status != TaskStatus.RUNNING.value:
            return Outcome.SKIPPED

        if not result.images:
            # 图上没有 SaveImage/PreviewImage 之类的产物节点 —— 这是**配置错误**，
            # 重试一万次还是不会出图，所以按致命处理（EX-3 的语义）。
            return self._fail_after_run(
                db,
                task,
                ErrorType.INVALID_PARAM,
                "工作流执行完成但没有产出任何图片（检查是否缺少 SaveImage 节点）",
            )

        saved = 0
        try:
            with self._storage_factory() as storage:
                for image in result.images:
                    data = client.fetch_image(
                        image["filename"], image.get("subfolder", ""), image.get("type", "output")
                    )
                    key = build_output_key(task.user_id, task.id, image["filename"])
                    stored = storage.put(key, data, mime_for_filename(image["filename"]))
                    db.add(
                        Asset(
                            user_id=task.user_id,
                            task_id=task.id,
                            kind=AssetKind.OUTPUT.value,
                            object_key=stored.object_key,
                            mime_type=stored.mime_type,
                            size_bytes=stored.size_bytes,
                            # 可复现性元数据（FR-5.4 / C1）：客户追单时要能复现同一张图。
                            # seed 用的是**实体化之后**的值 —— 用 -1 记下来的话，
                            # 事后根本复现不出这张图。
                            meta={
                                "seed": injection.seed,
                                "workflow": f"{workflow.name}@v{workflow.version}",
                                "engine_prompt_id": result.prompt_id,
                                "params": injection.resolved,
                                "filename": image["filename"],
                                "node_id": image.get("node_id"),
                                "sha256": stored.sha256,
                            },
                        )
                    )
                    saved += 1
                db.flush()
        except Exception as exc:
            # 落盘失败 = EX-13，可重试（图还在引擎磁盘上，重取一次可能就好了）
            return self._fail_after_run(db, task, ErrorType.SAVE_FAILED, f"产物保存失败：{exc}")

        duration_ms = result.duration_ms
        sm.mark_succeeded(
            db,
            task,
            duration_ms=duration_ms,
            # GPU 秒的近似：本机 GPU 并发恒为 1 且模型常驻（--highvram），
            # submit→completed 这段时间 GPU 基本没有空转，排队时长已单独记在
            # wait_seconds 里、不计入成本。若将来并发 > 1，必须改为引擎侧上报。
            gpu_seconds=round(duration_ms / 1000, 3),
            gpu_mem_peak_mb=vram_peak,
            engine_prompt_id=result.prompt_id,
        )
        self._track_finished(db, task, TaskStatus.SUCCEEDED.value, images=saved)
        db.commit()
        log.info(
            "task.succeeded images=%s duration_ms=%s",
            saved,
            duration_ms,
            extra=bind_task(task_id=task.id),
        )
        return Outcome.SUCCEEDED

    def _fail_after_run(self, db: Session, task: Task, error: ErrorType, message: str) -> Outcome:
        """已经在 running 状态下的失败：`mark_retrying` 会替我们决定重试还是终结。"""
        retried = sm.mark_retrying(db, task, error, message)
        if retried:
            track(
                db,
                EventName.TASK_RETRY,
                user_id=task.user_id,
                task_id=task.id,
                batch_id=task.batch_id,
                source="worker",
                error_type=error.value,
                retry_count=task.retry_count,
            )
            db.commit()
            return Outcome.RETRYING
        self._track_finished(db, task, TaskStatus.FAILED.value, error_type=error.value)
        db.commit()
        log.error(
            "task.failed error_type=%s msg=%s",
            error.value,
            message[:200],
            extra=bind_task(task_id=task.id),
        )
        return Outcome.FAILED

    def _best_effort_cancel(
        self, client: ComfyUIClient, prompt_id: str | None, task_id: int
    ) -> None:
        """尽力把引擎里那张图停掉。失败只记日志 —— 它不该改变本次失败的归因。"""
        if not prompt_id:
            return
        try:
            outcome = client.cancel(prompt_id)
            log.info(
                "task.engine_canceled outcome=%s", outcome.value, extra=bind_task(task_id=task_id)
            )
        except Exception as exc:
            log.warning(
                "task.engine_cancel_failed err=%s",
                str(exc)[:200],
                extra=bind_task(task_id=task_id),
            )

    def _track_finished(
        self,
        db: Session,
        task: Task,
        status: str,
        error_type: str | None = None,
        **props: Any,
    ) -> None:
        """`task_finished` **只在终态触发**（契约 §4 / tracking_plan §3.3）。

        `error_type` 显式声明成具名参数（而不是塞进 `**props`）——
        否则调用方再传一次 `error_type` 就会与 `task.error_type` 撞成重复关键字参数。
        """
        track(
            db,
            EventName.TASK_FINISHED,
            user_id=task.user_id,
            task_id=task.id,
            batch_id=task.batch_id,
            source="worker",
            status=status,
            wait_seconds=task.wait_seconds,
            gpu_seconds=task.gpu_seconds,
            error_type=error_type or task.error_type,
            **props,
        )
        db.add(
            AuditLog(
                user_id=task.user_id,
                action="task.finish",
                target_type="task",
                target_id=str(task.id),
                detail={"status": status, "retry_count": task.retry_count},
            )
        )


# ---------------------------------------------------------------- 重试退避


def retry_backoff_seconds(retry_count: int) -> int:
    """第 n 次重试的退避时长（指数退避 + 封顶）。

    为什么要退避而不是立刻重投：瞬时错误（OOM / 显存没释放 / 引擎刚崩）
    立刻重试大概率还是同样结果，还会把 GPU 锁的等待时间浪费掉。
    """
    exponent = max(int(retry_count) - 1, 0)
    return min(
        settings.task_retry_backoff_seconds * (2**exponent),
        settings.task_retry_backoff_max_seconds,
    )


# ---------------------------------------------------------------- Celery 入口


@celery_app.task(bind=True, name="app.worker.tasks.execute_task", max_retries=None)
def execute_task(self, task_id: int) -> str:  # type: ignore[no-untyped-def,type-arg]
    """执行一张图。

    `max_retries=None`（不限次）是刻意的：**重试次数由状态机单点决定**
    （`settings.task_max_retries`，`sm.mark_retrying` 会返回"是否还重试"）。
    若这里再设一个上限，就出现了第二个真相来源，两边不一致时行为会很难解释 ——
    项目已经因为"两个地方各说各话"踩过若干次（见 `项目进展.md` #5 / #6 / #21）。
    """
    executor = TaskExecutor()
    try:
        outcome = executor.execute(task_id)
    except Exception as exc:  # 未预期异常：标记可重试，避免任务永远卡在 running
        log.exception(
            "task.unexpected_error err=%s", str(exc)[:300], extra=bind_task(task_id=task_id)
        )
        _mark_unexpected_failure(task_id)
        raise

    if outcome is Outcome.RETRYING:
        # 退避由 Celery 的 countdown 承担；重投到达时执行器会走 T8（retrying → queued）
        delay = retry_backoff_seconds(_current_retry_count(task_id))
        log.info("task.retry_scheduled countdown=%ss", delay, extra=bind_task(task_id=task_id))
        raise self.retry(countdown=delay)
    if outcome is Outcome.LOCK_BUSY:
        # 没抢到 GPU：不退避太久，GPU 一空就该立刻上
        raise self.retry(countdown=settings.gpu_lock_wait_seconds)
    return outcome.value


def _current_retry_count(task_id: int) -> int:
    db = SessionLocal()
    try:
        task = db.get(Task, task_id)
        return task.retry_count if task is not None else 0
    finally:
        db.close()


def _mark_unexpected_failure(task_id: int) -> None:
    """把未预期异常落到重试路径上，保证 `running` 不会永久滞留（AC-5.1）。"""
    db = SessionLocal()
    try:
        task = db.get(Task, task_id)
        if task is None or task.status != TaskStatus.RUNNING.value:
            return
        sm.mark_retrying(db, task, ErrorType.UNKNOWN, "Worker 内未预期异常")
        db.commit()
    except Exception as exc:  # 兜底路径本身失败就只能记日志了
        log.error("task.mark_unexpected_failed err=%s", str(exc)[:200])
    finally:
        db.close()


# ---------------------------------------------------------------- 兜底扫描


@celery_app.task(name="app.worker.tasks.requeue_orphans")
def requeue_orphans(older_than_seconds: int | None = None, limit: int | None = None) -> int:
    """把「已入队 / 等待重试但迟迟没被消费」的任务重新投递，返回补投数量。

    **这是 AC-5.1「无任务永久停留非终态」的兜底**（NFR-3）。两种来源：

    1. `queued` 超时未被执行 —— `dispatch.py` 在 broker 不可用时**故意**让任务
       停在 `queued`（不让用户看到"提交失败"），那么 broker 恢复后必须有人补投；
    2. `retrying` 超时未被重投 —— Celery 的 `retry` 消息可能因 broker 重启而丢，
       丢了就再也没人会碰它，任务会永远停在 `retrying`。

    重复投递是**安全**的：执行器开头的状态闸门 + GPU 独占位保证同一张图只跑一次。
    """
    threshold = (
        settings.orphan_requeue_seconds if older_than_seconds is None else older_than_seconds
    )
    cap = settings.orphan_requeue_limit if limit is None else limit
    deadline = datetime.now(timezone.utc) - timedelta(seconds=threshold)

    db = SessionLocal()
    try:
        orphans = list(
            db.scalars(
                select(Task)
                .where(
                    Task.status.in_([TaskStatus.QUEUED.value, TaskStatus.RETRYING.value]),
                    Task.engine_prompt_id.is_(None),  # 已经交给引擎的不重投
                    Task.updated_at < deadline,
                )
                .order_by(Task.priority.asc(), Task.id.asc())
                .limit(cap)
            ).all()
        )
        ids = [(t.id, t.priority) for t in orphans]
    finally:
        db.close()

    from app.services.dispatch import enqueue_task

    requeued = sum(1 for task_id, priority in ids if enqueue_task(task_id, priority))
    if requeued:
        log.warning("requeue.orphans count=%s", requeued)
    return requeued


@celery_app.task(name="app.worker.tasks.recover_zombies")
def recover_zombies() -> int:
    """T14 / EX-7 / AC-6.4：把心跳超时的运行中任务恢复并**重新投递**，返回恢复数。

    与 `requeue_orphans` 是互补的两套兜底，覆盖"任务停在非终态"的全部路径：

    | 卡在哪个状态 | 原因 | 谁来救 |
    |---|---|---|
    | `queued` | broker 挂了 / 投递丢失 | `requeue_orphans` |
    | `retrying` | Celery 退避消息丢在 broker 重启里 | `requeue_orphans` |
    | `running` | Worker 被 kill -9，没机会清理 | **本任务**（心跳超时） |

    注意**恢复与投递必须一起做**：`recover_zombie_tasks` 只把状态改回 `queued`，
    若不同时重新投递，任务会静静停在 `queued` 直到兜底扫描再次发现它 ——
    用户看到的是"卡了很久然后突然又开始动"，而不是预期中的自动续跑。
    """
    db = SessionLocal()
    try:
        pending = [(t.id, t.priority) for t in sm.find_zombie_tasks(db)]
        if not pending:
            return 0
        recovered = sm.recover_zombie_tasks(db)
        db.commit()
    finally:
        db.close()

    from app.services.dispatch import enqueue_task

    for task_id, priority in pending:
        enqueue_task(task_id, priority)
    log.warning("recover.zombies count=%s", recovered)
    return recovered
