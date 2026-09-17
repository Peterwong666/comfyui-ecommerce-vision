"""EX-5 队列深度闸门（软阈值告警 / 硬阈值熔断）的行为用例。

背景：`settings.queue_max_depth` 此前是**死配置**（全仓库无消费方），
`ErrorType.QUEUE_FULL` 也**零产生者** —— 也就是说「队列深度」这条边界值
只有配置声明，没有任何可执行的行为。本文件把它钉住。

两层语义（`app/api/v1/tasks.py::_guard_queue`）：

| 层 | 条件 | 行为 |
|---|---|---|
| 软 | 在途 > `queue_max_depth` | `log.warning`，**仍然 202 接单** |
| 硬 | 在途 > `2 × queue_max_depth` | **422** + `detail.code == "queue_full"` |

⚠️ **构造「在途任务数」的方式**：**不造上万条记录**，而是把阈值调小
（`soft=2` ⇒ `hard=4`），再直接用状态机造少量在途任务。理由：
① 闸门的判据是「`在途` 与阈值的大小关系」，把它等价地缩小后，判据的**每一个分支**
   （< 软 / 软~硬之间 / 恰好等于硬 / > 硬）都能用个位数记录覆盖到，
   而造 10000 条记录只是把同一件事跑得更慢，并没有多证明任何东西；
② 造记录走 `state_machine`（`create_task` → `enqueue` / `mark_running`），
   不给闸门开后门 —— 测试用的在途任务是**真的**在途任务。

⚠️ 本文件**不重复**钉「retry / retry-failed 按新一次生成计额」：
`test_quota.py::test_retry_charges_one`（retry 后 `quota_used` +1）与
`::test_retry_failed_charges_len_failed_and_is_all_or_nothing`（只计失败张数）
已经覆盖同一口径。口径的**文档**出处是 `docs/prd/PRD_v1.md` FR-1.2。

⚠️ 本文件**新增**的是「终态失败 / 取消**不退额**」—— 那是 2026-09-17 的**当前裁定**，
不是需求原文（需求文档从未规定过退额），见下方那两个用例的 docstring。

⚠️ 告警断言**不用 `caplog`**，改用 `_capture_warnings()` 直接替换 `log.warning`
（理由见该函数的 docstring：`alembic/env.py` 的 `fileConfig()` 会把
`app.api.v1.tasks` logger 永久置为 `disabled`，caplog 会静默拿到空列表）。
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.v1 import tasks as api_tasks
from app.api.v1.tasks import _in_flight_task_count
from app.core.config import settings
from app.models.enums import ErrorType, TaskStatus
from app.models.task import Batch, Task
from app.models.user import User
from app.services import state_machine as sm

# 测试用的软阈值。硬阈值 = 软阈值 × `_QUEUE_HARD_LIMIT_FACTOR`（= 2）→ 4。
_SOFT = 2
# ⚠️ 硬阈值**写死成字面量 4**，而不是 `_SOFT * api_tasks._QUEUE_HARD_LIMIT_FACTOR`。
# 从被测实现里推导期望值，会让「把系数从 2 改成别的数」这类变异体**逃过**测试
# （期望值跟着实现一起变，断言永远成立）—— 本轮 M2 变异验证就是先踩到了这一点：
# 把系数改成 1000 时，按公式推导的用例全部照旧通过。
# 这里钉的是**规格本身**：硬阈值恒为软阈值的 2 倍。
_HARD = 4


def test_hard_limit_is_twice_the_soft_limit() -> None:
    """钉住「硬阈值 = 2 × 软阈值」这条规格（实现为具名模块常量）。"""
    assert api_tasks._QUEUE_HARD_LIMIT_FACTOR == 2


def _db_used(db: Session, user_id: int) -> int:
    """库里真实的 `users.quota_used`（绕开 ORM 缓存，与 test_quota.py 同一手法）。"""
    return db.scalar(select(User.quota_used).where(User.id == user_id))


def _seed_in_flight(
    db: Session,
    user,  # type: ignore[no-untyped-def]
    workflow,  # type: ignore[no-untyped-def]
    count: int,
    status: str = TaskStatus.QUEUED.value,
) -> int:
    """造 `count` 个**指定在途状态**的任务，返回当前在途总数。

    走状态机而不是直接改 `Task.status`：绕开状态机就绕开了非法迁移校验，
    造出来的「在途」可能与真实运行时的状态分布不是一回事。
    """
    for _ in range(count):
        task = sm.create_task(
            db,
            user_id=user.id,
            workflow_id=workflow.id,
            params={},
            priority=settings.default_priority,
        )
        sm.enqueue(db, task)
        if status == TaskStatus.RUNNING.value:
            sm.mark_running(db, task)
        elif status == TaskStatus.RETRYING.value:
            sm.mark_running(db, task)
            assert sm.mark_retrying(db, task, ErrorType.OOM, "构造：在途 retrying") is True
    db.commit()
    return _in_flight_task_count(db)


def _capture_warnings(monkeypatch) -> list[str]:  # type: ignore[no-untyped-def]
    """抓 `app.api.v1.tasks` 发出的 warning，返回「已格式化」的消息列表。

    ⚠️ **为什么不用 `caplog`**：`alembic/env.py:27` 调的是
    `fileConfig(config.config_file_name)`，而 `fileConfig` 的
    `disable_existing_loggers` **默认是 True** —— 它会把进程里**已存在**的、
    且没写进 `alembic.ini` 的 logger 全部 `disabled = True`，其中就包括
    `app.api.v1.tasks`。这个副作用**不会**随测试结束回滚，
    而 `caplog.at_level()` 只处理 `logging.disable()`（manager 级），
    **不会**把 `logger.disabled` 打开。

    后果：只要 `test_migration.py` 先跑过，任何依赖 `caplog` 的断言都会**静默**
    拿到空列表 —— 那是测试基础设施的假红，不是被测代码的问题
    （已用探针复现：`fileConfig` 前后 `disabled` 由 `False` 变 `True`）。
    这里直接替换方法，绕开全局日志状态；被测的仍是「守卫**确实调用了**
    带深度信息的 warning」这一事实。
    """
    messages: list[str] = []

    def _spy(msg: str, *args: object, **kwargs: object) -> None:
        messages.append(msg % args if args else str(msg))

    monkeypatch.setattr(api_tasks.log, "warning", _spy)
    return messages


def _submit(client, **body):  # type: ignore[no-untyped-def]
    return client.post("/api/v1/tasks", json={"workflow_name": "t2i_v1", **body})


def _submit_batch(client, total: int = 1):  # type: ignore[no-untyped-def]
    skus = {chr(ord("A") + i): [i + 1] for i in range(total)}
    return client.post(
        "/api/v1/batches",
        json={"workflow_name": "t2i_v1", "sku_assets": skus, "images_per_sku": 1},
    )


# ================================================================
# 1. 低于软阈值 → 正常接单
# ================================================================


def test_below_soft_limit_accepts(client, db: Session, user, workflow, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """在途 < 软阈值：202，且不发队列告警（闸门不该在正常水位下刷日志）。"""
    monkeypatch.setattr(settings, "queue_max_depth", _SOFT)
    _seed_in_flight(db, user, workflow, _SOFT - 1)

    resp = _submit(client)

    assert resp.status_code == 202, resp.text


# ================================================================
# 2. 超过软阈值但未到硬阈值 → 仍然接单（附告警）
# ================================================================


def test_above_soft_limit_still_accepts_with_warning(
    client, db: Session, user, workflow, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    """⭐ 软层**只告警不拒绝**：在途 > 软阈值但仍 ≤ 硬阈值时，必须返回 202。

    这条同时钉住两件事：

    1. **不拒绝**（EX-5 的关键取舍：C12「排队比崩掉好」+ `queue_full` 是可重试错误）；
       把软层也改成拒绝（直接 `raise`），这条立刻变红；
    2. **确实发了 warning，且带当前深度** —— 删掉那行 `log.warning`、或把深度写成常量，
       断言会变红。
    """
    monkeypatch.setattr(settings, "queue_max_depth", _SOFT)
    depth = _seed_in_flight(db, user, workflow, _SOFT + 1)  # 3 > 2(软)，3 ≤ 4(硬)
    assert depth == _SOFT + 1
    warn_messages = _capture_warnings(monkeypatch)

    resp = _submit(client)

    assert resp.status_code == 202, f"软阈值把用户拒了（本轮明确不这么做）：{resp.text}"
    assert any(
        "queue.soft_limit_exceeded" in msg and str(depth) in msg for msg in warn_messages
    ), f"超过软阈值没有发出带深度的告警：{warn_messages}"


# ================================================================
# 3. 超过硬阈值 → 422 + 结构化 detail
# ================================================================


def test_above_hard_limit_rejects_with_queue_full(
    client, db: Session, user, workflow, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    """在途 > 硬阈值 → 422，`detail.code` 取 `ErrorType.QUEUE_FULL` 的枚举值。

    `message` 必须含 EX-5 要求的两条用户可见信息：**排队数**与**预计等待分钟**。
    把硬阈值改成「永不触发」（如 ×1000）会让这条变红。
    """
    monkeypatch.setattr(settings, "queue_max_depth", _SOFT)
    depth = _seed_in_flight(db, user, workflow, _HARD + 1)
    assert depth == _HARD + 1

    resp = _submit(client)

    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    assert isinstance(detail, dict), f"detail 不是对象形式：{detail!r}"
    # 用枚举值断言（唯一事实来源），不写死小写字面量
    assert detail["code"] == ErrorType.QUEUE_FULL.value
    assert detail["fields"] == [
        {"key": "queue_depth", "reason": f"在途 {depth} 个，已超过硬上限 {_HARD} 个"}
    ]
    # EX-5 的「用户可见」列原文要求：当前排队 N 个 + 预计等待 X 分钟
    assert f"当前排队 {depth} 个" in detail["message"], detail["message"]
    assert "预计等待" in detail["message"] and "分钟" in detail["message"], detail["message"]


def test_wait_estimate_reuses_per_image_seconds(
    client, db: Session, user, workflow, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    """预计等待分钟 = 在途数 × 单张基准耗时 ÷ 60（**向上取整**，最小 1 分钟）。

    基准耗时与 `/tasks/estimate` 共用 `_seconds_per_image`（历史成功均值，
    无样本时 20s）。这里钉住这个口径：造一个成功的样本把均值钉成确定值，
    再断言拒绝文案里的数字。

    ⚠️ 若把公式里的 `_seconds_per_image` 换成别的常数，或把 `ceil` 换成 `round`，
    这条会变红。
    """
    monkeypatch.setattr(settings, "queue_max_depth", _SOFT)
    sample = sm.create_task(
        db, user_id=user.id, workflow_id=workflow.id, params={}, priority=settings.default_priority
    )
    sm.enqueue(db, sample)
    sm.mark_running(db, sample)
    # 单张 120s：在途 5 个 → 5 × 120 / 60 = 10 分钟
    sm.mark_succeeded(db, sample, duration_ms=120_000)
    db.commit()

    depth = _seed_in_flight(db, user, workflow, _HARD + 1)
    assert depth == _HARD + 1  # 成功任务是终态，不计入在途

    resp = _submit(client)

    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["message"] == f"当前排队 {depth} 个，预计等待 10 分钟"


# ================================================================
# 4. ⭐ 被队列拒绝时：额度不动、任务不建
# ================================================================


def test_rejection_does_not_charge_quota_nor_create_task(
    client, db: Session, user, workflow, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    """⭐ 本轮最关键的一条：**闸门必须跑在 `_charge_quota` 之前**。

    被队列拒绝的请求绝不能扣额度、也绝不能建任务 —— 否则用户「被拒还掉额度」。
    把 `_guard_queue(...)` 挪到 `_charge_quota(...)` **之后**，这条立刻变红
    （额度会 +1、任务会多一条）。
    """
    monkeypatch.setattr(settings, "queue_max_depth", _SOFT)
    _seed_in_flight(db, user, workflow, _HARD + 1)
    before_used = _db_used(db, user.id)
    before_tasks = db.scalar(select(func.count()).select_from(Task))

    resp = _submit(client)

    assert resp.status_code == 422, resp.text
    assert _db_used(db, user.id) == before_used, "被队列拒绝却扣了额度"
    assert db.scalar(select(func.count()).select_from(Task)) == before_tasks, "被拒却建了任务"


# ================================================================
# 5. 批量提交走同一道门
# ================================================================


def test_batch_is_guarded_the_same_way(client, db: Session, user, workflow, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """`POST /batches` 也受闸门约束，且**整批**被拒（不扣 `total`、不建 Batch）。"""
    monkeypatch.setattr(settings, "queue_max_depth", _SOFT)
    _seed_in_flight(db, user, workflow, _HARD + 1)
    before_used = _db_used(db, user.id)
    before_batches = db.scalar(select(func.count()).select_from(Batch))

    resp = _submit_batch(client, total=3)

    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["code"] == ErrorType.QUEUE_FULL.value
    assert _db_used(db, user.id) == before_used, "批量被队列拒绝却扣了额度"
    assert db.scalar(select(func.count()).select_from(Batch)) == before_batches


# ================================================================
# 6. 边界：恰好等于硬阈值
# ================================================================


def test_exactly_at_hard_limit_is_accepted(
    client, db: Session, user, workflow, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    """**恰好等于硬阈值 → 放行**（本轮的选择）。

    理由：两层闸门都是「**超过**才动作」（`>` 而非 `>=`），与 PRD §6.2
    「队列深度 ≤ 上限」的**闭区间**口径一致 —— 上限本身是允许的，
    这与 `max_batch_size`（6 张上限时 6 张通过）等既有边界保持同一读法。
    本用例同时钉住「硬阈值 + 1 就拒绝」，两端一起固定住 `>` 与 `>=` 的歧义。
    """
    monkeypatch.setattr(settings, "queue_max_depth", _SOFT)
    assert _seed_in_flight(db, user, workflow, _HARD) == _HARD

    at_limit = _submit(client)
    assert at_limit.status_code == 202, f"恰好等于硬阈值被拒了：{at_limit.text}"

    _seed_in_flight(db, user, workflow, 1)  # 现在在途 = 硬阈值 + 1
    over_limit = _submit(client)
    assert over_limit.status_code == 422, over_limit.text


# ================================================================
# 7. 「在途」的口径：哪三种状态算、哪些不算
# ================================================================


def test_in_flight_counts_queued_running_retrying_only(
    client, db: Session, user, workflow, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    """在途 = `queued` / `running` / `retrying`；**终态（含 `pending`）一律不算**。

    喂一个**空的** `workflow` / `user` 之外还要有终态样本，才能证明
    「不算」是真的不算（只造在途样本的话，多算少算都看不出来）。

    漏掉 `running`（最常见的写错方式）时这条变红 —— 正在跑的那张同样在占 GPU，
    不算进去会系统性低估等待时间。
    """
    monkeypatch.setattr(settings, "queue_max_depth", _SOFT)
    # 每个状态各造一个，其中终态三种都是「跑到终态」的真实路径
    _seed_in_flight(db, user, workflow, 1, TaskStatus.QUEUED.value)
    _seed_in_flight(db, user, workflow, 1, TaskStatus.RUNNING.value)
    _seed_in_flight(db, user, workflow, 1, TaskStatus.RETRYING.value)

    succeeded = sm.create_task(
        db, user_id=user.id, workflow_id=workflow.id, params={}, priority=settings.default_priority
    )
    sm.enqueue(db, succeeded)
    sm.mark_running(db, succeeded)
    sm.mark_succeeded(db, succeeded, duration_ms=1000)

    failed = sm.create_task(
        db, user_id=user.id, workflow_id=workflow.id, params={}, priority=settings.default_priority
    )
    sm.enqueue(db, failed)
    sm.mark_running(db, failed)
    sm.mark_failed(db, failed, ErrorType.MODEL_MISSING, "构造：终态失败")

    canceled = sm.create_task(
        db, user_id=user.id, workflow_id=workflow.id, params={}, priority=settings.default_priority
    )
    sm.mark_canceled(db, canceled)  # pending → canceled

    # 还有一条停在 pending 的：它「已创建但未入队」，同样不算在途
    sm.create_task(
        db, user_id=user.id, workflow_id=workflow.id, params={}, priority=settings.default_priority
    )
    db.commit()

    assert db.scalar(select(func.count()).select_from(Task)) == 7
    assert _in_flight_task_count(db) == 3


# ================================================================
# 8. 终态失败 / 取消 **不退还**额度（当前裁定，非需求原文）
# ================================================================


def test_terminal_failure_does_not_refund_quota(client, db: Session, user) -> None:  # type: ignore[no-untyped-def]
    """⚠️ **当前裁定（2026-09-17），不是需求原文**：任务走到**终态失败**后 `quota_used` 不变。

    `docs/prd/PRD_v1.md` 与 `docs/sop/contracts.md` 都**从未规定**过退额；
    「提交时预扣、失败不退」是协调者就 FR-1.2 作出的裁定
    （理由与代价见 PRD FR-1.2 的说明）。这条测试钉住的是**裁定本身**，
    所以它被推翻时应该**连用例一起改**，而不是偷偷让实现漂移。

    构造：提交（扣 1）→ 致命错误 `failed`（不重试）。断言此刻额度**仍是 1**。
    """
    resp = _submit(client)
    assert resp.status_code == 202, resp.text
    task = db.get(Task, resp.json()["id"])
    assert task is not None
    assert _db_used(db, user.id) == 1

    sm.mark_running(db, task)
    sm.mark_failed(db, task, ErrorType.MODEL_MISSING, "测试用：终态失败")
    db.commit()

    assert task.status == TaskStatus.FAILED.value
    assert _db_used(db, user.id) == 1, "终态失败后额度被退还了（当前裁定是不退）"


def test_cancel_does_not_refund_quota(client, db: Session, user) -> None:  # type: ignore[no-untyped-def]
    """同上，**取消**也不退额（当前裁定，非需求原文）。"""
    resp = _submit(client)
    assert resp.status_code == 202, resp.text
    task_id = resp.json()["id"]

    cancel = client.post(f"/api/v1/tasks/{task_id}/cancel")
    assert cancel.status_code == 200, cancel.text

    assert db.get(Task, task_id).status == TaskStatus.CANCELED.value  # type: ignore[union-attr]
    assert _db_used(db, user.id) == 1, "取消后额度被退还了（当前裁定是不退）"
