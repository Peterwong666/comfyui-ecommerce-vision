"""「每用户在途任务数上限」（`settings.max_in_flight_per_user`，2026-09-17）。

它解决的是**公平性**，不是容量
================================

GPU 全局并发恒为 1（NFR-2），队列是**所有用户共用**的一条。全局队列熔断（EX-5 的
`_guard_queue`）只在**整体**过载时才拒人，拦不住"一个用户把队列占满" ——
他一次提交 1000 张（`max_batch_size` 的上限），别人就排在 1000 张后面，
体验上等于**服务不可用**。两者互补：一个管总量，一个管分配。

本文件钉住的行为（`app/api/v1/tasks.py::_guard_user_in_flight`）
==============================================================

| 条件 | 行为 |
|---|---|
| `当前在途 + 本次需要 > 上限` | **422 + `code=TOO_MANY_IN_FLIGHT`**，不扣额、不建任务 |
| 恰好等于上限 | **放行**（闭区间，与 `max_batch_size` / 队列硬阈值同一读法） |
| **整批**判定 | 999 在途 + 本次 2 张 → 拒绝（**不是**只放行 1 张） |
| 只算**该用户** | 别的用户有 1000 个在途不影响我 |
| 只加在 `POST /tasks` / `POST /batches` | `retry` / `retry-failed` **不受**它约束（重试不新增任务） |

⚠️ **构造方式**：不造 1000 条记录，而是把上限调小（`= 2` / `= 3`）再用状态机造几个
**真实**在途任务。闸门的判据是「在途数与阈值的大小关系」，等价缩小后每个分支
（< 上限 / 恰好等于 / > 上限）都能用个位数记录覆盖 —— 造 1000 条只是把同一件事跑得更慢。
造记录一律走 `state_machine`（`create_task` → `enqueue`），不给闸门开后门。

⚠️ **期望值全部写字面量**（`_LIMIT = 2` 之类），**不从被测实现推导** ——
上一轮有过教训：把期望写成 `_SOFT * 实现里的常量`，结果"改系数"的变异体全部逃逸。

⚠️ 本文件**复用** `test_queue_guard.py` 的三个脚手架（`_seed_in_flight` / `_db_used` /
`_submit_batch`）：同一件事只留一份实现。它们的语义与理由在那个文件里有完整说明。
"""

from __future__ import annotations

# 跨测试模块 import 私有 helper 是**刻意**的：这三个是"造真实在途任务 / 读库里真值 /
# 投一批量"的同一份配方，复制一遍就等于给同一件事留两份实现（迟早分叉）。
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.v1.tasks import TOO_MANY_IN_FLIGHT_CODE, _in_flight_task_count_for_user
from app.core.config import settings
from app.models.enums import ErrorType, TaskStatus, UserRole
from app.models.task import Batch, Task
from app.models.user import User
from app.services import state_machine as sm
from tests.test_queue_guard import _db_used, _seed_in_flight, _submit_batch

#: 本文件用的上限字面量。**不引用 `settings.max_in_flight_per_user` 来算期望** ——
#: 那会让"把上限改小/改大"的变异体随实现一起漂。
_LIMIT = 2


def _count_rows(db: Session, model) -> int:  # type: ignore[no-untyped-def]
    return int(db.scalar(select(func.count()).select_from(model)) or 0)


def _submit(client, **body):  # type: ignore[no-untyped-def]
    return client.post("/api/v1/tasks", json={"workflow_name": "t2i_v1", **body})


def _make_other_user(db: Session, email: str = "other@example.com") -> User:
    other = User(
        email=email,
        password_hash="x",
        role=UserRole.USER.value,
        quota_total=1000,
        quota_used=0,
    )
    db.add(other)
    db.commit()
    return other


# ================================================================
# 0. 默认值本身（写死字面量，改它必须有人工确认）
# ================================================================


def test_default_limit_is_one_thousand() -> None:
    """默认上限 = **1000**（与 `max_batch_size` 同级）。

    取这个数的理由：一次提交 1000 张本身是**合法**的（`max_batch_size`），
    所以正常用户不会被误伤；只有"已有一批在途、又想再叠一批"才会撞上这道门。
    静默改小它会误伤正常用户（"我明明只提交了 1 张"），所以在这里钉死。
    """
    assert settings.max_in_flight_per_user == 1000


# ================================================================
# 1. 低于 / 恰好等于上限 → 接单
# ================================================================


def test_below_limit_is_accepted(client, db: Session, user, workflow, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """在途 0 + 本次 1 = 1 < 上限 2：202（正常水位下不该被门挡住）。"""
    monkeypatch.setattr(settings, "max_in_flight_per_user", _LIMIT)
    assert _in_flight_task_count_for_user(db, user.id) == 0

    resp = _submit(client)
    assert resp.status_code == 202, resp.text


def test_exactly_at_limit_is_accepted(client, db: Session, user, workflow, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """⭐ **恰好等于上限 → 放行**（`>` 而非 `>=`）。

    与 `max_batch_size`（6 张上限时 6 张通过）、队列硬阈值（恰好等于时放行）保持同一种
    读法：上限本身是允许的。本用例与下一条一起把 `>` / `>=` 的歧义两端固定住。
    """
    monkeypatch.setattr(settings, "max_in_flight_per_user", _LIMIT)
    assert _seed_in_flight(db, user, workflow, _LIMIT - 1) == 1  # 在途 1

    at_limit = _submit(client)  # 1 + 1 == 2
    assert at_limit.status_code == 202, f"恰好等于上限被拒了：{at_limit.text}"


# ================================================================
# 2. 超过上限 → 422 + 结构化 detail
# ================================================================


def test_above_limit_is_rejected_with_too_many_in_flight(
    client, db: Session, user, workflow, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    """在途 2 + 本次 1 = 3 > 2 → 422，`message` 必须给出**三个具体数字**。

    `message` 只写"超过上限"用户既不知道差多少、也不知道该等谁跑完 ——
    所以断言里同时钉住「当前在途 N」「本次需要 M」「上限 L」。
    """
    monkeypatch.setattr(settings, "max_in_flight_per_user", _LIMIT)
    assert _seed_in_flight(db, user, workflow, _LIMIT) == _LIMIT

    resp = _submit(client)

    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    assert isinstance(detail, dict), f"detail 不是对象形式：{detail!r}"
    # 字面量 + 模块常量各断言一次：钉规格，也钉"码只有一处定义"
    assert detail["code"] == "TOO_MANY_IN_FLIGHT"
    assert detail["code"] == TOO_MANY_IN_FLIGHT_CODE
    assert detail["fields"] == [
        {"key": "in_flight", "reason": f"在途 {_LIMIT} + 本次 1 > 上限 {_LIMIT}"}
    ]
    assert f"当前在途 {_LIMIT} 个" in detail["message"], detail["message"]
    assert "本次需要 1 张" in detail["message"], detail["message"]
    assert f"上限 {_LIMIT} 张" in detail["message"], detail["message"]


# ================================================================
# 3. ⭐ 被拒时：额度不动、任务不建（⇒ 闸门必须跑在 `_charge_quota` 之前）
# ================================================================


def test_rejection_charges_nothing_and_creates_nothing(
    client, db: Session, user, workflow, monkeypatch
) -> None:
    """⭐ 把 `_guard_user_in_flight(...)` 挪到 `_charge_quota(...)` **之后**，本用例立刻变红
    （库里 `quota_used` 会 +1、任务会多一条）。

    被拒的请求"不扣额、不建任务"是这道闸门存在的意义 —— 否则用户"被拒还掉额度"。
    """
    monkeypatch.setattr(settings, "max_in_flight_per_user", _LIMIT)
    _seed_in_flight(db, user, workflow, _LIMIT)
    before_used = _db_used(db, user.id)
    before_tasks = _count_rows(db, Task)

    resp = _submit(client)

    assert resp.status_code == 422, resp.text
    assert _db_used(db, user.id) == before_used, "被在途上限拒绝却扣了额度"
    assert _count_rows(db, Task) == before_tasks, "被在途上限拒绝却建了任务"


# ================================================================
# 4. ⭐ 批量：**整批**判定
# ================================================================


def test_batch_is_rejected_as_a_whole(client, db: Session, user, workflow, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """⭐ 在途 2 + 本次 2 张 = 4 > 3 → **整批**拒绝（不是"只放行 1 张"）。

    这条专门钉"整批判定"：
    - 若实现写成"只算本次请求、不算已有在途"（`needed > limit`）⇒ 2 ≤ 3 ⇒ 202 ⇒ 变红；
    - 若实现写成"能塞几张算几张"（放行 1 张）⇒ 也会变红（会建出 Batch / 子任务）。

    与 `_charge_quota` 的一次性扣 `total`、「批量整体成功或整体失败」口径一致。
    """
    monkeypatch.setattr(settings, "max_in_flight_per_user", 3)
    _seed_in_flight(db, user, workflow, 2)
    before_used = _db_used(db, user.id)
    before_batches = _count_rows(db, Batch)
    before_tasks = _count_rows(db, Task)

    resp = _submit_batch(client, total=2)  # 2 + 2 = 4 > 3

    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    assert detail["code"] == "TOO_MANY_IN_FLIGHT"
    assert detail["fields"] == [{"key": "in_flight", "reason": "在途 2 + 本次 2 > 上限 3"}]
    assert "当前在途 2 个" in detail["message"] and "本次需要 2 张" in detail["message"]
    assert _db_used(db, user.id) == before_used, "批量被拒却扣了额度"
    assert _count_rows(db, Batch) == before_batches, "批量被拒却建了 Batch"
    assert _count_rows(db, Task) == before_tasks, "批量被拒却建了子任务"


def test_batch_at_limit_is_accepted(
    client, db: Session, user, workflow, monkeypatch, no_broker
) -> None:  # type: ignore[no-untyped-def]
    """批量恰好把上限填满（在途 1 + 本次 2 = 3）→ 202，且真的建了 2 条子任务。

    只看状态码会漏掉"接了但没建任务"这类回归，所以连落库条数一起断言。
    """
    monkeypatch.setattr(settings, "max_in_flight_per_user", 3)
    _seed_in_flight(db, user, workflow, 1)

    resp = _submit_batch(client, total=2)

    assert resp.status_code == 202, resp.text
    assert len(no_broker) == 2
    assert _count_rows(db, Task) == 3  # 1 条种下的在途 + 2 条新建


# ================================================================
# 5. ⭐ 只算**该用户**（别的用户不背锅）
# ================================================================


def test_other_users_in_flight_does_not_count(
    client, db: Session, user, workflow, monkeypatch
) -> None:
    """别的用户有 5 个在途时，本用户仍在 0 在途 ⇒ 正常接单。

    把判据误写成**全局**在途数（复用了 `_in_flight_task_count`）时，
    本用例变红 —— 那等于把"公平性闸门"退化成了第二道全局熔断，
    而全局的那道已经在 `_guard_queue` 里了（判定入口必须唯一）。
    """
    monkeypatch.setattr(settings, "max_in_flight_per_user", _LIMIT)
    monkeypatch.setattr(settings, "queue_max_depth", 10_000)  # 全局闸门不参与本用例
    other = _make_other_user(db)
    _seed_in_flight(db, other, workflow, 5)

    assert _in_flight_task_count_for_user(db, user.id) == 0

    resp = _submit(client)

    assert resp.status_code == 202, f"别的用户的在途被算到了我头上：{resp.text}"


# ================================================================
# 6. ⭐ 重试路径**不受**这道闸门约束（因为它不新增任务）
# ================================================================


def test_retry_is_not_blocked_by_the_per_user_limit(
    client, db: Session, user, workflow, monkeypatch
) -> None:
    """⭐ 上限 = 1、当前在途 = 1 时，**重试失败任务仍应成功（200）**。

    理由（实现里也写了）：重试**不新增任务** —— 那个任务本来就在库里，
    提交时已经计过在途数。对重试再加这道门等于**同一个任务被计两次**：
    用户攒了 999 个失败任务要重跑时，会被误判成"在途超限"而**永远重试不了**。

    若有人"顺手"把 `_guard_user_in_flight` 也加到 `retry` 上，本用例变红
    （1 + 1 > 1 ⇒ 422）。

    ⚠️ 重试当然**仍受全局队列闸门**约束（那是另一回事，见 `test_queue_guard.py`）。
    """
    monkeypatch.setattr(settings, "max_in_flight_per_user", 1)

    failed = sm.create_task(
        db, user_id=user.id, workflow_id=workflow.id, params={}, priority=settings.default_priority
    )
    sm.enqueue(db, failed)
    sm.mark_running(db, failed)
    sm.mark_failed(db, failed, ErrorType.MODEL_MISSING, "构造：终态失败")
    assert _seed_in_flight(db, user, workflow, 1) == 1  # 在途已达上限

    resp = client.post(f"/api/v1/tasks/{failed.id}/retry")

    assert resp.status_code == 200, resp.text
    assert failed.status in (TaskStatus.QUEUED.value, TaskStatus.RETRYING.value)


def test_retry_failed_is_not_blocked_by_the_per_user_limit(
    client, db: Session, user, workflow, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    """同上，`retry-failed` 也不受每用户上限约束。"""
    # 先按**默认上限**把批量提交进去（否则这一批自己就会撞上限，测不到重试那一步）
    assert _submit_batch(client, total=2).status_code == 202
    batch_id = db.scalar(select(Batch.id))
    assert batch_id is not None
    children = list(db.scalars(select(Task).where(Task.batch_id == batch_id)).all())
    assert len(children) == 2

    sm.mark_running(db, children[0])
    sm.mark_failed(db, children[0], ErrorType.MODEL_MISSING, "构造：模拟致命失败")
    db.commit()

    # 此刻：1 条在途（queued 的那条）+ 1 条失败。把上限压到 1 ⇒ 在途已达上限，
    # 若 `retry-failed` 也被这道门管，重跑会被拒（1 + 1 > 1）。
    monkeypatch.setattr(settings, "max_in_flight_per_user", 1)
    assert _in_flight_task_count_for_user(db, user.id) == 1

    resp = client.post(f"/api/v1/batches/{batch_id}/retry-failed")

    assert resp.status_code == 200, resp.text
