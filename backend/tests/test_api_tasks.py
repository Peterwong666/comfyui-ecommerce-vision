"""任务 API 的契约一致性测试（契约 §3.4 提示词单通道 / §5 #6 两个维度上限 / §2.5 派发）。

用 `TestClient` + 依赖覆盖跑**真实的路由代码**，而不是直接调函数 ——
这几条问题都出在「路由层怎么组装 params / 用哪个配置项校验」这一层，
只测 schema 是盖不住的。

`client` fixture 定义在 `tests/conftest.py`：worker 的端到端测试也要用它。
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.enums import TaskStatus
from app.models.task import Task

# --- 契约 §3.4：提示词只有一条通道，顶层优先 -----------------------------------


def test_top_level_prompt_wins_over_params(client, db: Session) -> None:
    """同时传顶层 `prompt` 与 `params["prompt"]` 时，顶层为准（契约 §3.4）。

    并且**落库的 Task.prompt 与注入用的 params["prompt"] 必须是同一个值** ——
    此前它们是两条独立通道，可以传出不一致的结果。
    """
    resp = client.post(
        "/api/v1/tasks",
        json={
            "workflow_name": "t2i_v1",
            "prompt": "顶层提示词",
            "params": {"prompt": "params 里的提示词"},
        },
    )
    assert resp.status_code == 202, resp.text
    assert resp.json()["params"]["prompt"] == "顶层提示词"

    task = db.get(Task, resp.json()["id"])
    assert task is not None
    assert task.params["prompt"] == "顶层提示词"
    assert task.prompt == task.params["prompt"]


def test_prompt_from_params_when_top_level_absent(client, db: Session) -> None:
    """只传 `params["prompt"]` 时也要生效，且同步到 Task.prompt。"""
    resp = client.post(
        "/api/v1/tasks",
        json={"workflow_name": "t2i_v1", "params": {"prompt": "来自 params"}},
    )
    assert resp.status_code == 202, resp.text

    task = db.get(Task, resp.json()["id"])
    assert task is not None
    assert task.params["prompt"] == "来自 params"
    assert task.prompt == "来自 params"


def test_negative_prompt_converges_the_same_way(client, db: Session) -> None:
    """负向提示词与正向同构，走同一条收敛逻辑。"""
    resp = client.post(
        "/api/v1/tasks",
        json={
            "workflow_name": "t2i_v1",
            "negative_prompt": "模糊",
            "params": {"negative_prompt": "低质量"},
        },
    )
    assert resp.status_code == 202, resp.text
    task = db.get(Task, resp.json()["id"])
    assert task is not None
    assert task.params["negative_prompt"] == "模糊"
    assert task.negative_prompt == "模糊"


# --- 契约 §5 #4：响应里必须带 can_retry ----------------------------------------


def test_submit_response_exposes_can_retry(client) -> None:  # type: ignore[no-untyped-def]
    resp = client.post("/api/v1/tasks", json={"workflow_name": "t2i_v1"})
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert "can_retry" in body
    assert body["can_retry"] is False  # 刚提交为 queued
    assert body["status"] == "queued"  # 契约 §1.4


def test_submit_dispatches_to_queue_after_commit(client, no_broker) -> None:  # type: ignore[no-untyped-def]
    """派发必须在 commit **之后**发生。

    顺序反过来的话，worker 可能在事务提交前就去读任务 —— 读不到，
    于是任务留在 queued 而 worker 已经消费掉了那条消息（要靠兜底扫描才救得回来）。
    """
    resp = client.post("/api/v1/tasks", json={"workflow_name": "t2i_v1"})
    assert resp.status_code == 202, resp.text
    assert no_broker == [(resp.json()["id"], settings.default_priority)]


def test_batch_dispatches_one_message_per_image(client, no_broker, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """子任务粒度 = 一张图，所以投递次数必须等于**张数**而不是 SKU 数。"""
    resp = client.post(
        "/api/v1/batches",
        json={
            "workflow_name": "t2i_v1",
            "sku_assets": {"A": [1], "B": [2]},
            "images_per_sku": 3,
        },
    )
    assert resp.status_code == 202, resp.text
    assert resp.json()["total_count"] == 6
    assert len(no_broker) == 6


# --- 契约 §5 #6：SKU 数与总张数由两个独立配置项管 ------------------------------


def test_sku_count_is_guarded_by_max_sku_count(client, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """SKU 数超限 → 由 `max_sku_count` 拦下（与总张数上限无关）。"""
    monkeypatch.setattr(settings, "max_sku_count", 2)
    monkeypatch.setattr(settings, "max_batch_size", 1000)

    resp = client.post(
        "/api/v1/batches",
        json={
            "workflow_name": "t2i_v1",
            "sku_assets": {"A": [1], "B": [2], "C": [3]},
        },
    )
    assert resp.status_code == 422
    assert "SKU 数" in resp.text


def test_total_images_is_guarded_by_max_batch_size(client, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """总张数超限 → 由 `max_batch_size` 拦下，且提示说的是「张」不是「SKU」。"""
    monkeypatch.setattr(settings, "max_sku_count", 1000)
    monkeypatch.setattr(settings, "max_batch_size", 2)

    resp = client.post(
        "/api/v1/batches",
        json={
            "workflow_name": "t2i_v1",
            "sku_assets": {"A": [1], "B": [2], "C": [3]},
        },
    )
    assert resp.status_code == 422
    assert "最多 2 张" in resp.text


def test_batch_children_are_queued_not_pending(client, db: Session) -> None:
    """批量子任务必须**在提交时就入队**（T2），状态是 `queued`。

    ⚠️ 回归点：此前 `submit_batch` 只 `create_task` 没 `sm.enqueue`，
    子任务全部停在 `pending` —— 而 worker 只接受 `queued`（否则跳过），
    于是**批量提交后一张图都不会跑**。单任务路径一直是对的，批量路径漏了这一步，
    直到端到端测试跑起来才暴露。
    """
    resp = client.post(
        "/api/v1/batches",
        json={"workflow_name": "t2i_v1", "sku_assets": {"A": [1], "B": [2]}},
    )
    assert resp.status_code == 202, resp.text

    children = db.scalars(select(Task).where(Task.batch_id == resp.json()["id"])).all()
    assert len(children) == 2
    assert {c.status for c in children} == {TaskStatus.QUEUED.value}
    # 排队时间戳也要落上（P95 排队时长指标依赖它）
    assert all(c.queued_at is not None for c in children)


def test_batch_seed_increments_across_skus(client, db: Session) -> None:
    """契约 §2.4：同批次内 seed 自动递增，**跨 SKU 也必须连续**。

    ⚠️ 回归点：此前递增用的是「每个 SKU 内的序号」，`3 SKU × 2 张` 会得到
    `100,101,100,101,100,101` —— 不同 SKU 的第 k 张撞同一个 seed，
    同提示词直接出同一张图，"递增"形同失效。
    """
    resp = client.post(
        "/api/v1/batches",
        json={
            "workflow_name": "t2i_v1",
            "sku_assets": {"A": [1], "B": [2], "C": [3]},
            "common_params": {"seed": 100},
            "images_per_sku": 2,
        },
    )
    assert resp.status_code == 202, resp.text

    children = sorted(
        db.scalars(select(Task).where(Task.batch_id == resp.json()["id"])).all(),
        key=lambda t: t.idx,
    )
    seeds = [c.params["seed"] for c in children]
    assert seeds == [100, 101, 102, 103, 104, 105]
    assert len(set(seeds)) == len(seeds)  # 无重复


def test_batch_random_seed_uses_kernel_semantics(client, db: Session) -> None:
    """`seed = -1` 的批内语义**由内核 `derive_seed()` 定义**：先随机出基准，再递增。

    即「整批随机，但批内递增」——不是「每张都塞 -1」。
    这条口径写在 `docs/sop/workflow_spec.md` §7.1（强制级）：
    `-1` 只是"随机"的哨兵，**最终落库必须是具体整数**，否则产物元数据里只有 `-1`，
    用户再也复现不出那一批图（FR-5.4 / FR-5.5 静默失效）。

    ⚠️ 我上一版自己实现了「`-1` 保持 `-1` 不递增」，并写了条测试把它钉住 ——
    **测试断言的是实现当时的行为，而实现本身错了**。这条测试改为断言内核语义。
    """
    resp = client.post(
        "/api/v1/batches",
        json={
            "workflow_name": "t2i_v1",
            "sku_assets": {"A": [1], "B": [2], "C": [3]},
            "common_params": {"seed": -1},
            "images_per_sku": 2,
        },
    )
    assert resp.status_code == 202, resp.text
    children = sorted(
        db.scalars(select(Task).where(Task.batch_id == resp.json()["id"])).all(),
        key=lambda t: t.idx,
    )
    seeds = [c.params["seed"] for c in children]

    assert len(seeds) == 6
    assert all(isinstance(s, int) and s != -1 for s in seeds), f"哨兵未被实体化：{seeds}"
    assert len(set(seeds)) == 6, f"批内 seed 撞车（会出同一张图）：{seeds}"
    # 批内递增：相邻两张差 1。用模运算表达，避免 base 落在取值域末尾时回绕导致 flaky
    space = 2**32
    assert [(b - a) % space for a, b in zip(seeds, seeds[1:], strict=False)] == [1] * 5


def test_batch_without_seed_leaves_it_unset(client, db: Session) -> None:
    """没传 seed 时不要凭空造一个 —— 工作流 Schema 的默认值才是兜底。"""
    resp = client.post(
        "/api/v1/batches",
        json={"workflow_name": "t2i_v1", "sku_assets": {"A": [1], "B": [2]}},
    )
    assert resp.status_code == 202, resp.text
    children = db.scalars(select(Task).where(Task.batch_id == resp.json()["id"])).all()
    assert {c.params["seed"] for c in children} == {None}


def test_batch_at_total_limit_succeeds(client, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """边界值含上限：3 SKU × 2 张 = 6 张，上限 6 时应通过。"""
    monkeypatch.setattr(settings, "max_batch_size", 6)
    resp = client.post(
        "/api/v1/batches",
        json={
            "workflow_name": "t2i_v1",
            "sku_assets": {"A": [1], "B": [2], "C": [3]},
            "images_per_sku": 2,
        },
    )
    assert resp.status_code == 202, resp.text
    assert resp.json()["total_count"] == 6
