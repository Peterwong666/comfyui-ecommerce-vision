"""任务队列（P6-02）单元测试。

**为什么全部用 fake 依赖**：本机没有 Redis 服务、实例处于无卡模式（ComfyUI 不启动），
真实联调做不到。`TaskExecutor` 的全部外部依赖都是构造参数注入的，
所以这里可以在**纯 Python** 环境下把整条执行链路（占坑 → 提交 → 轮询 → 落盘 →
状态迁移 → 埋点）跑通，并断言中间每一步的数据库状态。

覆盖的迁移编号：T5/T6（成功）、T7/T8/T9（重试与耗尽）、T10（取消）、T11（致命）、
T14（僵尸恢复）、T8（重试重投到达）。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.engine.driver import (
    ComfyUIError,
    ExecutionCanceled,
    ExecutionResult,
)
from app.models.asset import Asset
from app.models.enums import AssetKind, BatchStatus, ErrorType, TaskStatus
from app.models.event import Event, EventName
from app.models.task import Batch, Task
from app.services import state_machine as sm
from app.services.storage import InMemoryAssetStorage
from app.worker.concurrency import GpuLockUnavailable, GpuSlot, LockBackend
from app.worker.tasks import Outcome, TaskExecutor, retry_backoff_seconds


def minimal_schema(fields: list[dict] | None = None) -> dict:
    """契约合规的最小 `param_schema`。

    `schema_version` 与每个 field 的 `key`/`label`/`type`/`default`/`targets` 都是
    必填，`int`/`float` 还要 `min`/`max`（float 另需 `step`）—— B 流 `engine/schema.py`
    会严格校验，漏了就直接抛错。**构造测试数据时也必须守契约**，否则测的就不是生产路径。
    """
    return {"schema_version": 1, "fields": fields or []}


# ---------------------------------------------------------------- 测试替身


class FakeLockBackend(LockBackend):
    """进程内锁后端：忠实实现"只有第一个 token 能拿到、释放要比较 token"。"""

    def __init__(self, reachable: bool = True) -> None:
        self._holder: dict[str, str] = {}
        self._reachable = reachable

    def try_acquire(self, key: str, token: str, ttl_seconds: int) -> bool:
        if not self._reachable:
            raise GpuLockUnavailable("测试构造：锁后端不可达")
        if key in self._holder:
            return False
        self._holder[key] = token
        return True

    def release(self, key: str, token: str) -> bool:
        if self._holder.get(key) == token:
            del self._holder[key]
            return True
        return False

    def holder(self, key: str) -> str | None:
        return self._holder.get(key)


class FakeDriver:
    """假引擎。所有交互都用脚本化结果，不发任何网络请求。"""

    def __init__(
        self,
        *,
        submit_error: Exception | None = None,
        wait_error: Exception | None = None,
        images: list[dict] | None = None,
        wait_cancel: bool = False,
        upload_error: Exception | None = None,
    ) -> None:
        self.client_id = "fake"
        self.submit_error = submit_error
        self.wait_error = wait_error
        self.images = (
            images
            if images is not None
            else [{"filename": "out.png", "subfolder": "", "type": "output", "node_id": "9"}]
        )
        self.wait_cancel = wait_cancel
        self.upload_error = upload_error
        self.submitted: list[dict] = []
        self.cancels: list[str] = []
        #: 每次 upload_image 的 filename，用于验证 "同一素材只上传一次" 的缓存
        self.uploads: list[str] = []
        self.watchers_started = 0
        self.heartbeat_seen = False

    # 与 ComfyUIClient 对齐的接口面
    def __enter__(self) -> FakeDriver:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def close(self) -> None:
        return None

    def upload_image(
        self,
        data: bytes,
        filename: str,
        subfolder: str = "",
        folder_type: str = "input",
        overwrite: bool = False,
    ) -> dict:
        if self.upload_error is not None:
            raise self.upload_error
        self.uploads.append(filename)
        # 引擎会返回它自己命名后的文件名（内部会去重/加后缀），
        # 所以注入图里的值必须用**返回值**而不是我们传的 filename。
        return {"name": f"engine-{filename}", "subfolder": subfolder, "type": folder_type}

    def submit(self, workflow: dict, client_id: str | None = None) -> str:
        if self.submit_error is not None:
            raise self.submit_error
        self.submitted.append(workflow)
        return "pid-fake"

    def wait(
        self,
        prompt_id: str,
        timeout=None,
        poll_interval=0.5,
        task_id=None,
        should_stop=None,
        on_poll=None,
    ):  # type: ignore[no-untyped-def]
        # 模拟"轮询了一次"：让 worker 的 on_poll 回调真的被调到
        if on_poll is not None:
            on_poll(1.0)
            self.heartbeat_seen = True
        if should_stop is not None and should_stop():
            raise ExecutionCanceled()
        if self.wait_cancel:
            raise ExecutionCanceled()
        if self.wait_error is not None:
            raise self.wait_error
        return ExecutionResult(prompt_id=prompt_id, images=self.images, duration_ms=1234)

    def fetch_image(self, filename: str, subfolder: str = "", folder_type: str = "output") -> bytes:
        return f"bytes-of-{filename}".encode()

    def cancel(self, prompt_id: str):  # type: ignore[no-untyped-def]
        self.cancels.append(prompt_id)

        class _O:
            value = "queued_deleted"

        return _O()

    def health(self) -> dict:
        return {"devices": [{"vram_total": 24 * 1024**3, "vram_free": 8 * 1024**3}]}

    def watch_progress(self, prompt_id: str):  # type: ignore[no-untyped-def]
        self.watchers_started += 1
        return _NullWatcher()


class _NullWatcher:
    latest = None
    error = None
    warning = None

    def start(self) -> None:
        return None

    def stop(self, join_timeout: float = 3.0) -> None:
        return None


class RecordingSlot(GpuSlot):
    """记录 acquire/release 被调用过的次数，用于验证"锁一定会被释放"。"""

    def __init__(self, backend: LockBackend | None = None) -> None:
        super().__init__(backend=backend or FakeLockBackend(), poll_interval=0.01)
        self.acquires = 0
        self.releases = 0

    def acquire(self, timeout=None, on_wait=None):  # type: ignore[no-untyped-def]
        result = super().acquire(timeout=timeout, on_wait=on_wait)
        self.acquires += 1
        return result

    def release(self) -> bool:
        self.releases += 1
        return super().release()


class CancelOnWaitSlot(RecordingSlot):
    """模拟「等 GPU 期间用户点了取消」：锁还没拿到，任务已被改成 canceled。"""

    def __init__(self, session_factory, task_id: int) -> None:  # type: ignore[no-untyped-def]
        super().__init__(FakeLockBackend())
        self._session_factory = session_factory
        self._task_id = task_id

    def acquire(self, timeout=None, on_wait=None):  # type: ignore[no-untyped-def]
        session = self._session_factory()
        try:
            task = session.get(Task, self._task_id)
            task.status = TaskStatus.CANCELED.value
            session.commit()
        finally:
            session.close()
        # 执行器的 on_wait 会去库里看状态，返回 False = 放弃等待
        aborted = on_wait is not None and on_wait() is False
        self.acquires += 1
        assert aborted, "任务已取消时 on_wait 必须让 acquire 放弃"
        return False


def make_task_with_workflow(user_id: int, definition: dict, param_schema: dict, params: dict):  # type: ignore[no-untyped-def]
    """建一个带自定义工作流的已入队任务，返回 (session_factory 里的任务 id)。

    用于需要特定 schema（例如暴露 seed）的场景 —— 直接用 conftest 的 `workflow`
    fixture 覆盖不到这些分支。
    """
    from app.models.workflow import Workflow

    def _build(session_factory):  # type: ignore[no-untyped-def]
        session = session_factory()
        try:
            wf = Workflow(
                name=f"wf-{abs(hash(str(definition))) % 10**8}",
                version=1,
                display_name="test",
                definition=definition,
                param_schema=param_schema,
                is_active=True,
            )
            session.add(wf)
            session.commit()
            task = sm.create_task(session, user_id=user_id, workflow_id=wf.id, params=params)
            sm.enqueue(session, task)
            session.commit()
            return task.id
        finally:
            session.close()

    return _build


# ---------------------------------------------------------------- 夹具


@pytest.fixture
def session_factory(engine):  # type: ignore[no-untyped-def]
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


@pytest.fixture
def storage() -> InMemoryAssetStorage:
    return InMemoryAssetStorage()


def make_executor(session_factory, driver, storage, slot=None, **kw) -> TaskExecutor:  # type: ignore[no-untyped-def]
    return TaskExecutor(
        session_factory=session_factory,
        driver_factory=lambda: driver,
        storage_factory=lambda: storage,
        slot_factory=lambda: slot or RecordingSlot(),
        lock_wait_seconds=1,
        lock_poll_seconds=0.01,
        poll_interval=0.01,
        vram_sample_seconds=0.5,
        **kw,
    )


@pytest.fixture
def queued_task(db: Session, user, workflow) -> Task:  # type: ignore[no-untyped-def]
    """一个已入队、参数可注入的任务（workflow fixture 的 schema 含 steps / cfg）。"""
    task = sm.create_task(
        db,
        user_id=user.id,
        workflow_id=workflow.id,
        params={"steps": 25, "cfg": 7.0, "prompt": "一只白瓷杯", "sku": "SKU-A"},
    )
    sm.enqueue(db, task)
    db.commit()
    return task


def read_task(session_factory, task_id: int) -> Task:  # type: ignore[no-untyped-def]
    session = session_factory()
    try:
        return session.get(Task, task_id)  # type: ignore[return-value]
    finally:
        session.close()


def events_of(session_factory, name: str) -> list[Event]:  # type: ignore[no-untyped-def]
    session = session_factory()
    try:
        from sqlalchemy import select

        return list(session.scalars(select(Event).where(Event.event_name == name)).all())
    finally:
        session.close()


# ---------------------------------------------------------------- 成功路径


def test_success_path_full_lifecycle(session_factory, db, queued_task, storage) -> None:  # type: ignore[no-untyped-def]
    driver = FakeDriver()
    slot = RecordingSlot()
    outcome = make_executor(session_factory, driver, storage, slot).execute(queued_task.id)

    assert outcome is Outcome.SUCCEEDED
    task = read_task(session_factory, queued_task.id)
    assert task.status == TaskStatus.SUCCEEDED.value
    assert task.engine_prompt_id == "pid-fake"
    assert task.duration_ms == 1234
    assert task.gpu_seconds == pytest.approx(1.234)
    assert task.gpu_mem_peak_mb == pytest.approx(16 * 1024, rel=0.01)
    assert task.finished_at is not None
    # T5 记录了排队时长（0 秒左右，但必须非 None）
    assert task.wait_seconds is not None
    # 心跳被刷新过（T14 依赖它）
    assert task.heartbeat_at is not None
    assert driver.watchers_started == 1


def test_success_saves_asset_with_reproducible_meta(session_factory, db, user, storage) -> None:  # type: ignore[no-untyped-def]
    """产物必须带上可复现性元数据（FR-5.4 / C1）—— 客户追单时要能复现同一张图。

    seed 的实体化由 B 流内核在**渲染期**完成（`RenderResult.seed`），
    A 流负责把它回写进 `params` / `Task.seed` / 产物元数据。
    """
    definition = {
        "_meta": {},
        "3": {"class_type": "KSampler", "inputs": {"steps": 4, "seed": 12345}},
    }
    schema = minimal_schema(
        [
            {
                "key": "steps",
                "label": "采样步数",
                "type": "int",
                "default": 4,
                "min": 1,
                "max": 100,
                "step": 1,
                "targets": [{"node_id": "3", "input": "steps"}],
            },
            {
                "key": "seed",
                "label": "随机种子",
                "type": "seed",
                "default": -1,
                "targets": [{"node_id": "3", "input": "seed"}],
            },
        ]
    )
    task_id = make_task_with_workflow(user.id, definition, schema, {"steps": 8, "seed": -1})(
        session_factory
    )

    driver = FakeDriver()
    make_executor(session_factory, driver, storage).execute(task_id)

    session = session_factory()
    try:
        asset = session.query(Asset).one()
        assert asset.kind == AssetKind.OUTPUT.value
        assert asset.task_id == task_id
        assert asset.size_bytes == len(b"bytes-of-out.png")
        # seed 必须是**实体化之后**的整数值：记 -1 的话事后复现不出这张图
        assert isinstance(asset.meta["seed"], int)
        assert asset.meta["seed"] != -1
        assert asset.meta["seed"] == driver.submitted[0]["3"]["inputs"]["seed"]
        assert asset.meta["workflow"].startswith("wf-")
        assert asset.meta["engine_prompt_id"] == "pid-fake"
        assert asset.meta["params"]["steps"] == 8
        assert asset.meta["sha256"]
        # object_key 由 ID 拼成，不含用户输入（NFR-4）
        assert asset.object_key.startswith(f"outputs/{user.id}/{task_id}/")
    finally:
        session.close()


def test_seed_left_alone_when_workflow_does_not_expose_it(
    session_factory, db, queued_task, storage
) -> None:  # type: ignore[no-untyped-def]
    """工作流没把 seed 暴露成参数时，**不能**凭空记一个随机 seed。

    记了会误导：那个 seed 根本没被用过，按它复现出来的不是同一张图。
    这种情况宁可不记（`seed=None`），也不要给一个假的可复现承诺。
    """
    make_executor(session_factory, FakeDriver(), storage).execute(queued_task.id)
    session = session_factory()
    try:
        assert session.query(Asset).one().meta["seed"] is None
    finally:
        session.close()


def test_success_records_used_models_in_asset_meta(
    session_factory, db, user, storage
) -> None:  # type: ignore[no-untyped-def]
    """产物必须记下这张图**实际加载**了哪些权重（C1 / FR-5.4 / FR-5.5）。

    只有 seed + 参数是不够的：换底模、或把 LoRA 从 v1 换成 v2，seed 与参数
    全都一样，出来的却是另一张图 —— 元数据里必须能看出这一点，否则
    「客户追单要能出一样的图」就是一句口号。
    """
    definition = {
        "_meta": {},
        "2": {
            "class_type": "LoraLoader",
            "inputs": {
                "model": ["4", 0],
                "clip": ["4", 1],
                "lora_name": "cups_v2.safetensors",
                "strength_model": 0.7,
            },
        },
        "3": {
            "class_type": "DualCLIPLoader",
            "inputs": {
                "clip_name1": "t5xxl.safetensors",
                "clip_name2": "clip_l.safetensors",
                "type": "sdxl",
            },
        },
        "4": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": "sd_xl_base_1.0.safetensors"},
        },
        "9": {"class_type": "KSampler", "inputs": {"steps": 4, "sampler_name": "dpmpp_2m"}},
    }
    task_id = make_task_with_workflow(user.id, definition, minimal_schema(), {})(session_factory)
    outcome = make_executor(session_factory, FakeDriver(), storage).execute(task_id)
    assert outcome is Outcome.SUCCEEDED

    session = session_factory()
    try:
        meta = session.query(Asset).one().meta
        # 顺序 = 节点 id 数值序，同一节点内按入参名序（clip_name1 在 clip_name2 前）
        assert meta["models"] == [
            {
                "node": "2",
                "class_type": "LoraLoader",
                "input": "lora_name",
                "name": "cups_v2.safetensors",
            },
            {
                "node": "3",
                "class_type": "DualCLIPLoader",
                "input": "clip_name1",
                "name": "t5xxl.safetensors",
            },
            {
                "node": "3",
                "class_type": "DualCLIPLoader",
                "input": "clip_name2",
                "name": "clip_l.safetensors",
            },
            {
                "node": "4",
                "class_type": "CheckpointLoaderSimple",
                "input": "ckpt_name",
                "name": "sd_xl_base_1.0.safetensors",
            },
        ]
        # 既有字段一个都不能被这次改动碰坏（JSONB 加键必须向后兼容）
        assert meta["params"] == {}
        assert meta["workflow"].startswith("wf-")
        assert meta["engine_prompt_id"] == "pid-fake"
        assert meta["sha256"]
    finally:
        session.close()


def test_meta_models_is_empty_list_when_workflow_has_no_loader(
    session_factory, db, queued_task, storage
) -> None:  # type: ignore[no-untyped-def]
    """没有 loader 的工作流 → `models == []`（不是缺键、不是 None）。"""
    make_executor(session_factory, FakeDriver(), storage).execute(queued_task.id)
    session = session_factory()
    try:
        assert session.query(Asset).one().meta["models"] == []
    finally:
        session.close()


def test_pruned_branch_loader_is_not_recorded_as_used_model(
    session_factory, db, user, storage
) -> None:  # type: ignore[no-untyped-def]
    """被 bypass 裁掉的分支里的 loader **不能**出现在 `meta["models"]` 里（C1 / FR-5.4）。

    这条测试钉住 `_finish_success` 里那句注释所承诺的事：模型清单必须从
    `rendered.workflow`（已按 `_meta.switches` 裁剪、真正 `submit()` 给引擎的那张图）
    提取，**不能**从 `workflow.definition`（原始定义，含被 bypass 的分支）提取。

    在此之前那句注释只是**承诺**而非**保证**：仓库现有的工作流都没用 switches，
    两种取法恰好得到同样的结果，所以没有任何测试能检出这个回归。
    把 `extract_models(rendered.workflow)` 改成 `extract_models(workflow.definition)`
    这条测试就会变红（已做变异验证，见 `nightly-20260916.log`）。

    被裁剪的 loader 若被记进元数据，就是在写一个"引擎实际没加载过这个权重"的
    假声明 —— 用户照元数据复现时反而会疑惑，破坏可复现性的可信度。
    """
    definition = {
        "_meta": {
            "switches": [
                {
                    "key": "hires_fix",
                    "enabled_when": [True],
                    "disabled": {
                        "prune": ["4"],
                        "rewire": {"5": {"images": ["3", 0]}},
                    },
                }
            ]
        },
        # 保留分支的底模 loader：引擎确实加载了它，必须记进 models
        "1": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": "sd_xl_base_1.0.safetensors"},
        },
        # 被 bypass 的分支：关掉 hires_fix 时这个 LoRA loader 会被裁掉，
        # 引擎根本不会加载它，因此不能出现在 models 里
        "4": {
            "class_type": "LoraLoader",
            "inputs": {
                "model": ["1", 0],
                "clip": ["1", 1],
                "lora_name": "hires_detail.safetensors",
            },
        },
        "3": {"class_type": "KSampler", "inputs": {"steps": 4, "model": ["1", 0]}},
        "5": {"class_type": "SaveImage", "inputs": {"images": ["4", 0]}},
    }
    schema = minimal_schema(
        [
            {
                "key": "hires_fix",
                "label": "高清修复",
                "type": "bool",
                "default": False,
                "targets": [],
            },
        ]
    )
    task_id = make_task_with_workflow(user.id, definition, schema, {})(session_factory)
    driver = FakeDriver()
    outcome = make_executor(session_factory, driver, storage).execute(task_id)
    assert outcome is Outcome.SUCCEEDED

    # 先证明"裁剪真的发生了"，否则这条测试什么也没证明：
    # 原始定义里有节点 4，而提交给引擎的那张图里没有。
    assert "4" in definition
    assert "4" not in driver.submitted[0]

    session = session_factory()
    try:
        models = session.query(Asset).one().meta["models"]
    finally:
        session.close()

    assert [m["name"] for m in models] == ["sd_xl_base_1.0.safetensors"]
    assert all(m["node"] != "4" for m in models)


def test_malformed_definition_does_not_turn_success_into_failure(
    session_factory, db, user, storage
) -> None:  # type: ignore[no-untyped-def]
    """定义形状异常**绝不允许**把一张已经出好的图变成失败任务。

    元数据提取跑在成功收尾路径上（`_finish_success`），它不该有"杀图"的能力。
    这里把各种坏形状塞进定义 —— `engine.render` 不校验节点结构、会原样透传，
    所以它们真的会走到提取函数面前（这是真实的失败模式，不是假想的）。
    """
    definition = {
        "_meta": {},
        "1": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": "sd_xl_base_1.0.safetensors"},
        },
        "2": None,
        "3": {"class_type": "KSampler"},
        "4": {"class_type": "LoraLoader", "inputs": "不是字典"},
        "5": {"class_type": "VAELoader", "inputs": {"vae_name": None}},
        "6": {"class_type": "UNETLoader", "inputs": {"unet_name": 123}},
    }
    task_id = make_task_with_workflow(user.id, definition, minimal_schema(), {})(session_factory)
    outcome = make_executor(session_factory, FakeDriver(), storage).execute(task_id)

    assert outcome is Outcome.SUCCEEDED
    assert read_task(session_factory, task_id).status == TaskStatus.SUCCEEDED.value

    session = session_factory()
    try:
        # 图仍然落库了（"已经出好的图"没有被元数据提取的健壮性问题连累）
        asset = session.query(Asset).one()
        assert asset.kind == AssetKind.OUTPUT.value
        # 坏节点静默跳过，好节点照记
        assert asset.meta["models"] == [
            {
                "node": "1",
                "class_type": "CheckpointLoaderSimple",
                "input": "ckpt_name",
                "name": "sd_xl_base_1.0.safetensors",
            }
        ]
    finally:
        session.close()


def test_success_emits_started_and_finished_events(
    session_factory, db, queued_task, storage
) -> None:  # type: ignore[no-untyped-def]
    make_executor(session_factory, FakeDriver(), storage).execute(queued_task.id)
    assert len(events_of(session_factory, EventName.TASK_STARTED)) == 1
    finished = events_of(session_factory, EventName.TASK_FINISHED)
    assert len(finished) == 1
    # task_finished 只在终态触发，且要带 status 供看板口径使用
    assert finished[0].props["status"] == TaskStatus.SUCCEEDED.value


def test_injection_is_applied_to_workflow(session_factory, db, queued_task, storage) -> None:  # type: ignore[no-untyped-def]
    driver = FakeDriver()
    make_executor(session_factory, driver, storage).execute(queued_task.id)
    submitted = driver.submitted[0]
    # conftest 的 workflow fixture 里，steps 的 targets 指向节点 "1" 的 steps 入参
    assert submitted["1"]["inputs"]["steps"] == 25
    # 业务参数（有 targets 为空）不应被塞进图
    assert "sku" not in submitted["1"]["inputs"]


# ---------------------------------------------------------------- 并发控制


def test_gpu_slot_is_always_released(session_factory, db, queued_task, storage) -> None:  # type: ignore[no-untyped-def]
    slot = RecordingSlot()
    make_executor(session_factory, FakeDriver(), storage, slot).execute(queued_task.id)
    assert (slot.acquires, slot.releases) == (1, 1)


def test_gpu_slot_released_even_on_failure(session_factory, db, queued_task, storage) -> None:  # type: ignore[no-untyped-def]
    """失败路径也必须松手 —— 漏掉 release 会让队列永久卡死（最严重的一类 bug）。"""
    driver = FakeDriver(submit_error=ComfyUIError("CUDA out of memory", ErrorType.OOM))
    slot = RecordingSlot()
    make_executor(session_factory, driver, storage, slot).execute(queued_task.id)
    assert slot.releases == 1
    assert slot.held is False


def test_lock_busy_leaves_task_queued(session_factory, db, queued_task, storage) -> None:  # type: ignore[no-untyped-def]
    """没抢到锁 → 任务**仍是 queued**，等下一轮重投。不是失败。"""
    backend = FakeLockBackend()
    backend.try_acquire("comfyui:gpu_slot", "someone-else", 60)  # 别人占着
    slot = RecordingSlot(backend)

    outcome = make_executor(session_factory, FakeDriver(), storage, slot).execute(queued_task.id)
    assert outcome is Outcome.LOCK_BUSY
    assert read_task(session_factory, queued_task.id).status == TaskStatus.QUEUED.value


def test_lock_backend_down_does_not_bypass_lock(session_factory, db, queued_task, storage) -> None:  # type: ignore[no-untyped-def]
    """锁后端不可达时**绝不降级为不加锁执行** —— 那等于把唯一的硬约束关掉。"""
    driver = FakeDriver()
    slot = RecordingSlot(FakeLockBackend(reachable=False))
    outcome = make_executor(session_factory, driver, storage, slot).execute(queued_task.id)

    assert outcome is Outcome.LOCK_BUSY
    assert driver.submitted == []  # 一张都没提交
    assert read_task(session_factory, queued_task.id).status == TaskStatus.QUEUED.value


def test_gpu_lock_is_mutually_exclusive() -> None:
    """两个 slot 抢同一个 key：只有一个能拿到。这是 NFR-2 的机制保证。"""
    backend = FakeLockBackend()
    first, second = RecordingSlot(backend), RecordingSlot(backend)
    assert first.acquire(timeout=0.05) is True
    assert second.acquire(timeout=0.05) is False
    first.release()
    assert second.acquire(timeout=0.05) is True


def test_gpu_lock_release_is_token_checked() -> None:
    """不能误删别人的锁：自己的锁过期后 DEL 会把新的持有者删掉，导致两人同时上卡。"""
    backend = FakeLockBackend()
    a = RecordingSlot(backend)
    a.acquire(timeout=0.05)
    # 模拟 a 的锁过期、b 拿到同一个 key
    backend._holder[settings.gpu_lock_key] = "b-token"
    assert a.release() is False
    assert backend.holder(settings.gpu_lock_key) == "b-token"


def test_gpu_lock_is_reentrant_for_same_slot() -> None:
    """同一个 slot 重复 acquire 不应死锁（调用方可能忘了自己在持锁）。"""
    slot = RecordingSlot()
    assert slot.acquire(timeout=0.05) is True
    assert slot.acquire(timeout=0.05) is True
    slot.release()


# ---------------------------------------------------------------- 取消（T10）


def test_cancel_during_wait_marks_canceled_and_frees_engine(
    session_factory, db, queued_task, storage
) -> None:  # type: ignore[no-untyped-def]
    driver = FakeDriver(wait_cancel=True)
    outcome = make_executor(session_factory, driver, storage).execute(queued_task.id)

    assert outcome is Outcome.CANCELED
    task = read_task(session_factory, queued_task.id)
    assert task.status == TaskStatus.CANCELED.value
    # 必须让引擎松手，否则那张图继续占着 GPU 跑完 —— 白烧机时
    assert driver.cancels == ["pid-fake"]
    # 取消不是错误：不该记 error_type
    assert task.error_type is None
    assert events_of(session_factory, EventName.TASK_FINISHED)[0].props["status"] == "canceled"


def test_cancel_while_waiting_for_gpu_skips(session_factory, db, queued_task, storage) -> None:  # type: ignore[no-untyped-def]
    """等 GPU 期间被取消：不上卡、不提交、不改状态（API 已改成 canceled）。

    这条很实际：GPU 忙时任务可能等上一分钟，用户在这期间点取消是常态，
    白白等满一分钟再发现"已经在跑"是很糟的体验。
    """
    driver = FakeDriver()
    slot = CancelOnWaitSlot(session_factory, queued_task.id)
    outcome = make_executor(session_factory, driver, storage, slot).execute(queued_task.id)

    assert outcome is Outcome.SKIPPED
    assert driver.submitted == []
    # 没拿到锁就没有可释放的东西 —— 不该误报一次 release（会让"锁被误删"的告警失去意义）
    assert slot.releases == 0


# ---------------------------------------------------------------- 重试（T7/T8/T9）


def test_transient_error_enters_retrying(session_factory, db, queued_task, storage) -> None:  # type: ignore[no-untyped-def]
    """OOM 是可重试错误（AC-6.1）：降级后可能成功，不该直接判死。"""
    driver = FakeDriver(wait_error=ComfyUIError("CUDA out of memory", ErrorType.OOM))
    outcome = make_executor(session_factory, driver, storage).execute(queued_task.id)

    assert outcome is Outcome.RETRYING
    task = read_task(session_factory, queued_task.id)
    assert task.status == TaskStatus.RETRYING.value
    assert task.retry_count == 1
    assert task.error_type == ErrorType.OOM.value
    assert len(events_of(session_factory, EventName.TASK_RETRY)) == 1
    # 重试前必须让引擎松手
    assert driver.cancels == ["pid-fake"]
    # 重试**不是**终态，不能发 task_finished
    assert events_of(session_factory, EventName.TASK_FINISHED) == []


def test_retry_redelivery_goes_retrying_to_queued(
    session_factory, db, queued_task, storage
) -> None:  # type: ignore[no-untyped-def]
    """T8：退避结束后 Celery 重投到达，执行器把 retrying → queued 后继续跑。

    这是"重投的到达 = 退避结束"这条设计的核心断言。
    """
    failing = FakeDriver(wait_error=ComfyUIError("timeout", ErrorType.TIMEOUT))
    make_executor(session_factory, failing, storage).execute(queued_task.id)
    assert read_task(session_factory, queued_task.id).status == TaskStatus.RETRYING.value

    good = FakeDriver()
    outcome = make_executor(session_factory, good, storage).execute(queued_task.id)
    assert outcome is Outcome.SUCCEEDED
    task = read_task(session_factory, queued_task.id)
    assert task.status == TaskStatus.SUCCEEDED.value
    assert task.retry_count == 1  # 保留历史，便于排障


def test_fatal_error_does_not_retry(session_factory, db, queued_task, storage) -> None:  # type: ignore[no-untyped-def]
    """EX-3 / AC-6.2：非法参数不得进重试（重试一万次还是错，会堵死队列）。"""
    driver = FakeDriver(
        submit_error=ComfyUIError("Prompt outputs failed validation", ErrorType.INVALID_PARAM)
    )
    outcome = make_executor(session_factory, driver, storage).execute(queued_task.id)

    assert outcome is Outcome.FAILED
    task = read_task(session_factory, queued_task.id)
    assert task.status == TaskStatus.FAILED.value
    assert task.error_type == ErrorType.INVALID_PARAM.value
    assert events_of(session_factory, EventName.TASK_RETRY) == []
    assert events_of(session_factory, EventName.TASK_FINISHED)[0].props["status"] == "failed"


def test_retry_exhaustion_becomes_failed(session_factory, db, queued_task, storage) -> None:  # type: ignore[no-untyped-def]
    """T9：重试次数耗尽 → failed（而不是无限重试）。"""
    driver = FakeDriver(wait_error=ComfyUIError("oom", ErrorType.OOM))
    executor = make_executor(session_factory, driver, storage)

    outcomes = []
    for _ in range(settings.task_max_retries + 1):
        outcomes.append(executor.execute(queued_task.id))

    assert outcomes[-1] is Outcome.FAILED
    task = read_task(session_factory, queued_task.id)
    assert task.status == TaskStatus.FAILED.value
    assert task.retry_count == settings.task_max_retries + 1


def test_retry_backoff_is_exponential_and_capped() -> None:
    assert retry_backoff_seconds(0) == settings.task_retry_backoff_seconds
    assert retry_backoff_seconds(1) == settings.task_retry_backoff_seconds
    assert retry_backoff_seconds(2) == settings.task_retry_backoff_seconds * 2
    # 封顶，避免重试次数多了以后队列长时间空转
    assert retry_backoff_seconds(20) == settings.task_retry_backoff_max_seconds


# ---------------------------------------------------------------- 幂等闸门


def test_duplicate_delivery_is_skipped(session_factory, db, queued_task, storage) -> None:  # type: ignore[no-untyped-def]
    """Celery 是 at-least-once：重复消费必须被状态闸门挡住，不能重复出图。"""
    driver = FakeDriver()
    executor = make_executor(session_factory, driver, storage)
    executor.execute(queued_task.id)
    again = executor.execute(queued_task.id)

    assert again is Outcome.SKIPPED
    assert len(driver.submitted) == 1  # 只提交过一次


def test_canceled_task_is_not_executed(session_factory, db, queued_task, storage) -> None:  # type: ignore[no-untyped-def]
    """已取消的任务被投递过来时必须跳过（用户点了取消后又送到 worker 是常见的竞态）。"""
    session = session_factory()
    try:
        task = session.get(Task, queued_task.id)
        sm.mark_canceled(session, task)
        session.commit()
    finally:
        session.close()

    driver = FakeDriver()
    assert (
        make_executor(session_factory, driver, storage).execute(queued_task.id) is Outcome.SKIPPED
    )
    assert driver.submitted == []


def test_missing_task_returns_missing(session_factory, storage) -> None:  # type: ignore[no-untyped-def]
    executor = make_executor(session_factory, FakeDriver(), storage)
    assert executor.execute(999999) is Outcome.MISSING


# ---------------------------------------------------------------- 其它失败分支


def test_no_images_is_fatal_not_retried(session_factory, db, queued_task, storage) -> None:  # type: ignore[no-untyped-def]
    """跑完了却没出图 = 工作流配置错误，重试无意义。"""
    driver = FakeDriver(images=[])
    outcome = make_executor(session_factory, driver, storage).execute(queued_task.id)
    assert outcome is Outcome.FAILED
    task = read_task(session_factory, queued_task.id)
    assert task.status == TaskStatus.FAILED.value
    assert task.error_type == ErrorType.INVALID_PARAM.value


def test_storage_failure_is_retryable(session_factory, db, queued_task) -> None:  # type: ignore[no-untyped-def]
    """EX-13：落盘失败可重试 —— 图还在引擎磁盘上，重取一次可能就好了。"""

    class BrokenStorage(InMemoryAssetStorage):
        def put(self, key: str, data: bytes, content_type: str):  # type: ignore[no-untyped-def]
            raise OSError("MinIO 连接被重置")

    outcome = make_executor(session_factory, FakeDriver(), BrokenStorage()).execute(queued_task.id)
    assert outcome is Outcome.RETRYING
    task = read_task(session_factory, queued_task.id)
    assert task.error_type == ErrorType.SAVE_FAILED.value
    assert task.status == TaskStatus.RETRYING.value


def test_missing_workflow_fails_via_running(session_factory, db, user) -> None:  # type: ignore[no-untyped-def]
    """工作流被删 → 失败。**必须借道 running**：契约的迁移表里没有 queued → failed。

    现实中 `tasks.workflow_id` 是 `ON DELETE RESTRICT`，正常路径删不掉被引用的工作流；
    这条分支是纵深防御（例如数据迁移后留下了悬空 id）。
    因此这里临时关掉外键约束来构造该状态。
    """
    from sqlalchemy import text

    session = session_factory()
    try:
        from app.models.workflow import Workflow

        wf = Workflow(
            name="will_be_deleted",
            version=1,
            display_name="x",
            definition={"_meta": {}, "1": {"class_type": "KSampler", "inputs": {}}},
            param_schema=minimal_schema(),
            is_active=True,
        )
        session.add(wf)
        session.commit()
        task = sm.create_task(session, user_id=user.id, workflow_id=wf.id, params={})
        sm.enqueue(session, task)
        session.commit()
        task_id = task.id

        session.execute(text("PRAGMA foreign_keys=OFF"))
        session.execute(text("UPDATE tasks SET workflow_id = 999999 WHERE id = :i"), {"i": task_id})
        session.commit()
    finally:
        session.close()

    outcome = make_executor(session_factory, FakeDriver(), InMemoryAssetStorage()).execute(task_id)
    assert outcome is Outcome.FAILED
    task = read_task(session_factory, task_id)
    assert task.status == TaskStatus.FAILED.value
    assert task.error_type == ErrorType.MODEL_MISSING.value


def test_injection_error_fails_with_invalid_param(session_factory, db, user) -> None:  # type: ignore[no-untyped-def]
    """Schema 里的 targets 指向不存在的节点 → 报 INVALID_PARAM，而不是静默不生效。"""
    session = session_factory()
    try:
        from app.models.workflow import Workflow

        wf = Workflow(
            name="bad_targets",
            version=1,
            display_name="x",
            definition={"_meta": {}, "1": {"class_type": "KSampler", "inputs": {}}},
            param_schema=minimal_schema(
                [
                    {
                        "key": "steps",
                        "label": "采样步数",
                        "type": "int",
                        "default": 4,
                        "min": 1,
                        "max": 100,
                        "step": 1,
                        "targets": [{"node_id": "999", "input": "steps"}],
                    }
                ]
            ),
            is_active=True,
        )
        session.add(wf)
        session.commit()
        task = sm.create_task(session, user_id=user.id, workflow_id=wf.id, params={"steps": 4})
        sm.enqueue(session, task)
        session.commit()
        task_id = task.id
    finally:
        session.close()

    outcome = make_executor(session_factory, FakeDriver(), InMemoryAssetStorage()).execute(task_id)
    assert outcome is Outcome.FAILED
    assert read_task(session_factory, task_id).error_type == ErrorType.INVALID_PARAM.value


# ---------------------------------------------------------------- 兜底扫描


def test_requeue_orphans_picks_up_stale_queued(session_factory, db, engine, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """AC-5.1：broker 挂了导致任务停在 queued，兜底扫描要把它补投出去。"""
    from app.worker import tasks as worker_tasks

    session = session_factory()
    try:
        from app.models.enums import UserRole
        from app.models.user import User
        from app.models.workflow import Workflow

        u = User(
            email="o@example.com",
            password_hash="x",
            role=UserRole.USER.value,
            quota_total=10,
            quota_used=0,
        )
        wf = Workflow(
            name="o",
            version=1,
            display_name="o",
            definition={"_meta": {}, "1": {"class_type": "X"}},
            param_schema=minimal_schema(),
            is_active=True,
        )
        session.add_all([u, wf])
        session.commit()
        stale = sm.create_task(session, user_id=u.id, workflow_id=wf.id, params={})
        sm.enqueue(session, stale)
        fresh = sm.create_task(session, user_id=u.id, workflow_id=wf.id, params={})
        sm.enqueue(session, fresh)
        session.commit()
        stale_id, fresh_id = stale.id, fresh.id
        # 把 stale 的 updated_at 推到很久以前
        stale.updated_at = datetime.now(timezone.utc) - timedelta(seconds=100000)
        session.commit()
    finally:
        session.close()

    monkeypatch.setattr(worker_tasks, "SessionLocal", session_factory)
    sent: list[tuple[int, int]] = []
    monkeypatch.setattr(
        "app.services.dispatch.enqueue_task",
        lambda task_id, priority=5: sent.append((task_id, priority)) or True,
    )

    assert worker_tasks.requeue_orphans(older_than_seconds=60) == 1
    assert [task_id for task_id, _ in sent] == [stale_id]
    assert fresh_id not in [task_id for task_id, _ in sent]


def test_recover_zombies_requeues_immediately(session_factory, db, engine, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """T14 / AC-6.4：Worker 被 kill -9 后，僵尸任务必须"恢复状态 + 立刻重新投递"。

    只改状态不投递的话，任务会停在 queued 等兜底扫描 —— 用户看到的是
    "卡了很久然后突然又开始动"，而不是自动续跑。
    """
    from app.worker import tasks as worker_tasks

    session = session_factory()
    try:
        from app.models.enums import UserRole
        from app.models.user import User
        from app.models.workflow import Workflow

        u = User(
            email="z@example.com",
            password_hash="x",
            role=UserRole.USER.value,
            quota_total=10,
            quota_used=0,
        )
        wf = Workflow(
            name="z",
            version=1,
            display_name="z",
            definition={"_meta": {}, "1": {"class_type": "X"}},
            param_schema=minimal_schema(),
            is_active=True,
        )
        session.add_all([u, wf])
        session.commit()
        t = sm.create_task(session, user_id=u.id, workflow_id=wf.id, params={})
        sm.enqueue(session, t)
        sm.mark_running(session, t)
        t.heartbeat_at = datetime.now(timezone.utc) - timedelta(
            seconds=settings.task_heartbeat_timeout_seconds + 60
        )
        session.commit()
        task_id = t.id
    finally:
        session.close()

    monkeypatch.setattr(worker_tasks, "SessionLocal", session_factory)
    sent: list[int] = []
    monkeypatch.setattr(
        "app.services.dispatch.enqueue_task",
        lambda tid, priority=5: sent.append(tid) or True,
    )

    assert worker_tasks.recover_zombies() == 1
    assert sent == [task_id]
    task = read_task(session_factory, task_id)
    assert task.status == TaskStatus.QUEUED.value
    assert task.error_type == ErrorType.WORKER_CRASH.value


def test_recover_zombies_noop_when_none(session_factory, db, engine, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from app.worker import tasks as worker_tasks

    monkeypatch.setattr(worker_tasks, "SessionLocal", session_factory)
    called: list[int] = []
    monkeypatch.setattr(
        "app.services.dispatch.enqueue_task", lambda tid, priority=5: called.append(tid) or True
    )
    assert worker_tasks.recover_zombies() == 0
    assert called == []


# ---------------------------------------------------------------- asset_resolver（P6-09 接缝）


def _image_workflow(user_id: int, storage: InMemoryAssetStorage, asset_ids: list[int]):
    """建一条「带参考图」的工作流 + 已入队任务，返回 (task_id 构建器, 定义)。

    参考图是 V1 首发场景（电商商品图）的核心输入 —— 没有 resolver，
    这类工作流会在渲染期直接报错。
    """
    definition = {
        "_meta": {},
        "10": {"class_type": "LoadImage", "inputs": {"image": "placeholder.png"}},
    }
    schema = minimal_schema(
        [
            {
                "key": "image_list",
                "label": "参考图",
                "type": "image_list",
                "default": [],
                "targets": [{"node_id": "10", "input": "image", "transform": "ref_to_filename"}],
            }
        ]
    )
    return definition, schema, asset_ids


def _make_asset(session_factory, user_id: int, storage, name: str) -> int:
    """往存储里放一张「上传素材」并登记 Asset 行，返回 asset_id。"""
    from app.services.storage import build_output_key

    key = build_output_key(user_id, 0, name)
    storage.put(key, f"bytes-of-{name}".encode(), "image/png")
    session = session_factory()
    try:
        asset = Asset(
            user_id=user_id,
            kind=AssetKind.UPLOAD.value,
            object_key=key,
            mime_type="image/png",
            size_bytes=len(f"bytes-of-{name}".encode()),
            original_name=name,
        )
        session.add(asset)
        session.commit()
        return asset.id
    finally:
        session.close()


def test_resolver_uploads_asset_once_for_whole_batch(session_factory, db, user, storage) -> None:  # type: ignore[no-untyped-def]
    """**缓存是必需的**：同一张商品图在一个批次里会被引用很多次。

    不缓存就是「读存储 + 上传引擎」重复 N 次 —— 1000 张的批次下，
    这个 IO 会比 GPU 推理本身还长，直接把量产的吞吐打回去。
    """
    asset_id = _make_asset(session_factory, user.id, storage, "sku-a.png")
    definition, schema, _ = _image_workflow(user.id, storage, [asset_id])
    task_id = make_task_with_workflow(user.id, definition, schema, {"image_list": [asset_id]})(
        session_factory
    )

    driver = FakeDriver()
    make_executor(session_factory, driver, storage).execute(task_id)

    # 只上传一次（缓存生效），且注入的是**引擎返回的文件名**而不是 asset_id
    assert driver.uploads == ["sku-a.png"]
    assert driver.submitted[0]["10"]["inputs"]["image"] == ["engine-sku-a.png"]


def test_resolver_rejects_foreign_asset(session_factory, db, user, storage) -> None:  # type: ignore[no-untyped-def]
    """数据隔离（FR-1.3）：不能引用别人的素材。按「不存在」处理，不泄露存在性。"""
    session = session_factory()
    try:
        from app.models.enums import UserRole
        from app.models.user import User

        other = User(
            email="other@example.com",
            password_hash="x",
            role=UserRole.USER.value,
            quota_total=10,
            quota_used=0,
        )
        session.add(other)
        session.commit()
        other_id = other.id
    finally:
        session.close()

    foreign_asset = _make_asset(session_factory, other_id, storage, "theirs.png")
    definition, schema, _ = _image_workflow(user.id, storage, [foreign_asset])
    task_id = make_task_with_workflow(user.id, definition, schema, {"image_list": [foreign_asset]})(
        session_factory
    )

    driver = FakeDriver()
    outcome = make_executor(session_factory, driver, storage).execute(task_id)

    assert outcome is Outcome.FAILED  # 越权是致命错误，不重试
    assert driver.uploads == []  # 绝不把别人的素材传给引擎
    assert read_task(session_factory, task_id).error_type == ErrorType.INVALID_PARAM.value


def test_resolver_missing_asset_is_fatal(session_factory, db, user, storage) -> None:  # type: ignore[no-untyped-def]
    """引用了不存在的素材（已被删）→ 致命，重试无意义。"""
    definition, schema, _ = _image_workflow(user.id, storage, [999999])
    task_id = make_task_with_workflow(user.id, definition, schema, {"image_list": [999999]})(
        session_factory
    )

    driver = FakeDriver()
    outcome = make_executor(session_factory, driver, storage).execute(task_id)
    assert outcome is Outcome.FAILED
    assert read_task(session_factory, task_id).error_type == ErrorType.INVALID_PARAM.value


def test_resolver_storage_failure_is_retryable(session_factory, db, user, storage) -> None:  # type: ignore[no-untyped-def]
    """存储读不到素材是**瞬时**故障（EX-13 同类），应重试而不是判死。"""
    asset_id = _make_asset(session_factory, user.id, storage, "flaky.png")
    definition, schema, _ = _image_workflow(user.id, storage, [asset_id])
    task_id = make_task_with_workflow(user.id, definition, schema, {"image_list": [asset_id]})(
        session_factory
    )

    class FlakyStorage(InMemoryAssetStorage):
        def get(self, key: str) -> bytes:
            raise OSError("MinIO 连接被重置")

    outcome = make_executor(session_factory, FakeDriver(), FlakyStorage()).execute(task_id)
    assert outcome is Outcome.RETRYING
    task = read_task(session_factory, task_id)
    assert task.error_type == ErrorType.SAVE_FAILED.value
    assert task.status == TaskStatus.RETRYING.value


def test_resolver_reports_engine_upload_failure(session_factory, db, user, storage) -> None:  # type: ignore[no-untyped-def]
    """上传被引擎拒绝 → INVALID_UPLOAD（EX-10），不重试。"""
    asset_id = _make_asset(session_factory, user.id, storage, "bad.png")
    definition, schema, _ = _image_workflow(user.id, storage, [asset_id])
    task_id = make_task_with_workflow(user.id, definition, schema, {"image_list": [asset_id]})(
        session_factory
    )

    driver = FakeDriver(upload_error=ComfyUIError("unsupported format", ErrorType.INVALID_UPLOAD))
    outcome = make_executor(session_factory, driver, storage).execute(task_id)
    assert outcome is Outcome.FAILED
    assert read_task(session_factory, task_id).error_type == ErrorType.INVALID_UPLOAD.value


# ---------------------------------------------------------------- API ↔ Worker 端到端


def test_api_submit_then_worker_execute_batch(
    client, session_factory, db, no_broker, storage
) -> None:  # type: ignore[no-untyped-def]
    """**跨层**验证：API 写进库的任务，worker 真的能拿去执行到底。

    这条测试的价值在于它同时覆盖了三个层各自都对、但拼起来可能错的接口：

    - API 把参数写进 `Task.params`（含模板预设与默认值的合并结果）；
    - worker 从库里读出来，交给 B 流内核按 `param_schema.targets` 渲染成节点图；
    - 子任务逐张落 `Asset`，最后由状态机聚合出父任务状态（T12）。

    2026-09-16 的三流并行里，`PromptLibrary` 之类的接口错配如果只靠单层测试，
    要到真机联调才会发现 —— 那时排查成本高得多。
    """
    resp = client.post(
        "/api/v1/batches",
        json={
            "workflow_name": "t2i_v1",
            "name": "端到端",
            "sku_assets": {"SKU-A": [11], "SKU-B": [12]},
            "common_params": {"seed": 100},
            "images_per_sku": 1,
        },
    )
    assert resp.status_code == 202, resp.text
    batch_id = resp.json()["id"]
    assert len(no_broker) == 2  # 两张图 = 两条消息

    executor = make_executor(session_factory, FakeDriver(), storage)
    outcomes = [executor.execute(task_id) for task_id, _ in no_broker]
    assert outcomes == [Outcome.SUCCEEDED, Outcome.SUCCEEDED]

    session = session_factory()
    try:
        batch = session.get(Batch, batch_id)
        assert batch.status == BatchStatus.SUCCEEDED.value
        assert batch.succeeded_count == 2
        assert batch.progress == 1.0
        assert session.query(Asset).count() == 2
        # 同批次内 seed 自动递增（契约 §2.4）：风格一致但每张不同
        seeds = sorted(t.params["seed"] for t in session.query(Task).all())
        assert seeds == [100, 101]
    finally:
        session.close()


def test_api_then_worker_partial_batch(client, session_factory, db, no_broker, storage) -> None:  # type: ignore[no-untyped-def]
    """部分成功 → 父任务必须是 `partial`，而不是 succeeded/failed。

    500 张成功 3 张失败时，标 succeeded 会掩盖问题，标 failed 会让用户以为全废。
    """
    resp = client.post(
        "/api/v1/batches",
        json={"workflow_name": "t2i_v1", "sku_assets": {"A": [1], "B": [2]}},
    )
    assert resp.status_code == 202, resp.text
    batch_id = resp.json()["id"]

    good = make_executor(session_factory, FakeDriver(), storage)
    bad = make_executor(
        session_factory,
        FakeDriver(submit_error=ComfyUIError("模型缺失", ErrorType.MODEL_MISSING)),
        storage,
    )
    good.execute(no_broker[0][0])
    bad.execute(no_broker[1][0])

    session = session_factory()
    try:
        assert session.get(Batch, batch_id).status == BatchStatus.PARTIAL.value
    finally:
        session.close()
