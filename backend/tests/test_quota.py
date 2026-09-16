"""配额的正确性、边界与原子性（FR-1.2 / EX-12）。

此前**零覆盖**：`tests/` 里 `quota` 只出现在 fixture 赋值里，没有任何断言
402 / `quota_used` 递增 / 边界的用例。扣减是否真的发生、发生多少、不够时
是否留脏数据，全部没有判据。

判定与扣减已收敛到 `app/services/quota.py` 的**一条条件 UPDATE**，所以这里的
断言一律读**数据库里的真实值**（`_db_used()`），不读响应体、也不读 ORM 对象 ——
"接口返回 202" 与 "额度真的扣了" 是两件事，只断言前者盖不住超发与漏扣。

⚠️ 本文件里所有"读库"都用 `_db_used()`，它是**绕开 identity map 的列查询**，
所以它读到的一定是库里的值，而不是某个对象内存里的缓存值。
"""

from __future__ import annotations

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.models.enums import ErrorType, TaskStatus
from app.models.task import Batch, Task
from app.models.user import User
from app.services import state_machine as sm


def _db_used(db: Session, user_id: int) -> int:
    """库里真实的 `users.quota_used`（不是 ORM 对象上的缓存值）。"""
    return db.scalar(select(User.quota_used).where(User.id == user_id))


def _submit(client) -> object:  # type: ignore[no-untyped-def]
    return client.post("/api/v1/tasks", json={"workflow_name": "t2i_v1"})


def _submit_batch(client, total: int):  # type: ignore[no-untyped-def]
    """提交一个恰好 `total` 张的批量（每个 SKU 1 张 → SKU 数 = 张数）。"""
    skus = {chr(ord("A") + i): [i + 1] for i in range(total)}
    return client.post(
        "/api/v1/batches",
        json={"workflow_name": "t2i_v1", "sku_assets": skus, "images_per_sku": 1},
    )


def _make_failed_task(db: Session, client) -> Task:  # type: ignore[no-untyped-def]
    """造一个 failed 任务（供重试类接口使用），走状态机而不是直接改 status。"""
    resp = _submit(client)
    assert resp.status_code == 202, resp.text
    task = db.get(Task, resp.json()["id"])
    assert task is not None
    sm.mark_running(db, task)
    sm.mark_failed(db, task, ErrorType.MODEL_MISSING, "测试用：模拟致命失败")
    db.commit()
    return task


# ------------------------------------------------------- 1. 正常扣减真的落库了


def test_submit_charges_exactly_one_in_db(client, db: Session, user) -> None:  # type: ignore[no-untyped-def]
    """正常提交：不出现 402，且**库里**的 `quota_used` 从 0 变成 1。

    只断言 202 是不够的 —— 漏扣（压根没写库）与超发（写了 2）都是 202。
    """
    assert _db_used(db, user.id) == 0

    resp = _submit(client)

    assert resp.status_code == 202, resp.text
    assert _db_used(db, user.id) == 1


# ------------------------------------------------------- 2. 耗尽时拒绝且不留脏数据


def test_submit_rejected_when_exhausted_leaves_quota_untouched(client, db: Session, user) -> None:  # type: ignore[no-untyped-def]
    """`quota_used == quota_total` 时提交 → 402，且 `quota_used` **一个都不多**。

    这条同时钉住「扣失败不留脏数据」：条件 UPDATE 在额度不足时 `rowcount=0`，
    一个字都没改。
    """
    user.quota_used = user.quota_total
    db.commit()
    before = _db_used(db, user.id)

    resp = _submit(client)

    assert resp.status_code == 402, resp.text
    assert _db_used(db, user.id) == before == user.quota_total
    # 402 时不该留下半个任务（扣减失败发生在创建任务之前）
    assert db.scalar(select(func.count()).select_from(Task)) == 0


# ------------------------------------------------------- 3. 边界：刚好用完


def test_boundary_exactly_one_remaining_then_exhausted(client, db: Session, user) -> None:  # type: ignore[no-untyped-def]
    """剩余 1 张时提交 1 张 → 成功（刚好用完）；再提交 1 张 → 402。

    边界「`>=` 还是 `>`」写错时，这一条会红。
    """
    user.quota_used = user.quota_total - 1
    db.commit()

    first = _submit(client)
    assert first.status_code == 202, first.text
    assert _db_used(db, user.id) == user.quota_total

    second = _submit(client)
    assert second.status_code == 402, second.text
    assert _db_used(db, user.id) == user.quota_total


# ------------------------------------------------------- 4. 批量：整体成功或整体失败


def test_batch_charges_all_or_nothing(client, db: Session, user) -> None:  # type: ignore[no-untyped-def]
    """批量扣 `total` 张，且**不允许扣一半**。

    剩余 3 时提交 3 张 → 成功、`quota_used` 正好 +3；
    剩余 2 时再提交 3 张 → 402，`quota_used` **纹丝不动**（不是 +2）。
    """
    user.quota_used = user.quota_total - 3
    db.commit()

    ok = _submit_batch(client, 3)
    assert ok.status_code == 202, ok.text
    assert _db_used(db, user.id) == user.quota_total
    batches_after_ok = db.scalar(select(func.count()).select_from(Batch))
    assert batches_after_ok == 1

    # 只剩 2 张额度，却要提交 3 张
    user.quota_used = user.quota_total - 2
    db.commit()
    before = _db_used(db, user.id)

    rejected = _submit_batch(client, 3)
    assert rejected.status_code == 402, rejected.text
    assert _db_used(db, user.id) == before, "批量扣减不是原子的：扣了一部分"
    # 也没留下半个批量（只有前面成功的那一个）
    assert db.scalar(select(func.count()).select_from(Batch)) == 1


# ------------------------------------------------------- 5. ⭐ 陈旧缓存 / 原子性


def test_stale_cached_remaining_cannot_overdraft(client, db: Session, user) -> None:  # type: ignore[no-untyped-def]
    """⭐ 本轮最关键的一条：**ORM 缓存里"看着够"，库里其实已经不够**。

    构造方式（模拟"另一个并发请求刚把额度用完，本请求的身份映射还是旧值"）：
    用 Core UPDATE 直接改库里的 `quota_used`，并 `synchronize_session=False`
    让 session 里那个 `User` 对象**保持旧值**；再 `commit()`
    （`expire_on_commit=False`，commit 也不会刷新它）。

    此时：
    - `user.quota_remaining` 仍是 100 → 老实现（读内存比较再 `+=`）会**放行**；
    - 库里 `quota_used == quota_total` → 条件 UPDATE 的 `WHERE` 不成立、`rowcount=0`。

    所以这条必须返回 402。**只把 Python `+=` 换成别的写法而没做条件判断的实现，
    在这里必然变红**（会返回 202 并把库里的 `quota_used` 覆盖成 1 —— 丢更新）。

    顺带钉住 ORM 缓存回读：402 的文案里「剩余」必须是**真值 0**；
    若 `charge()` 没有 `db.refresh`，文案会是「剩余 100」。
    """
    uid = user.id
    db.execute(
        update(User)
        .where(User.id == uid)
        .values(quota_used=user.quota_total)
        .execution_options(synchronize_session=False)
    )
    db.commit()

    # 夹具前提：内存里的缓存确实是陈旧的，否则本测试无意义
    assert user.quota_used == 0, "构造失败：ORM 对象被同步了，陈旧缓存场景没造出来"
    assert user.quota_remaining == user.quota_total
    assert _db_used(db, uid) == user.quota_total, "构造失败：库里没改到"

    resp = _submit(client)

    assert resp.status_code == 402, (
        f"陈旧缓存下超发了额度（实际 {_db_used(db, uid)}/{user.quota_total}）：{resp.text}"
    )
    assert "剩余 0" in resp.text, f"402 文案用的是缓存旧值而不是回读的真值：{resp.text}"
    assert _db_used(db, uid) == user.quota_total, "402 之后额度反而被改动了"


# ------------------------------------------------------- 6. 402 响应体形状（契约 §2.2）


def test_402_body_is_structured_object(client, db: Session, user) -> None:  # type: ignore[no-untyped-def]
    """契约 §2.2：需要前端分支处理时 `detail` 用**对象形式**。

    `code` 必须是 `ErrorType.QUOTA_EXCEEDED` 的取值（唯一事实来源），
    `message` 保持人类可读，`fields` 指明是哪一项不够。
    """
    user.quota_used = user.quota_total
    db.commit()

    resp = _submit(client)

    assert resp.status_code == 402, resp.text
    detail = resp.json()["detail"]
    assert isinstance(detail, dict), f"detail 仍是字符串：{detail!r}"
    assert detail["code"] == ErrorType.QUOTA_EXCEEDED.value
    assert detail["message"] == "额度不足：需要 1，剩余 0"
    assert detail["fields"] == [{"key": "count", "reason": "需要 1，剩余 0"}]


# ------------------------------------------------------- 7/8. 另外两个调用点（retry 系）


def test_retry_charges_one(client, db: Session, user) -> None:  # type: ignore[no-untyped-def]
    """`POST /tasks/{id}/retry` 也要走同一条原子扣减（此前的第 3 个调用点）。"""
    task = _make_failed_task(db, client)
    before = _db_used(db, user.id)

    resp = client.post(f"/api/v1/tasks/{task.id}/retry")

    assert resp.status_code == 200, resp.text
    assert _db_used(db, user.id) == before + 1


def test_retry_failed_charges_len_failed_and_is_all_or_nothing(
    client, db: Session, user, no_broker
) -> None:  # type: ignore[no-untyped-def]
    """`retry-failed` 扣的是 **`len(failed)`**（不是 1、不是批量总数）。

    第 4 个调用点，参数口径最容易写错。这里同时验证：
    - 剩余恰好 = 失败数 → 成功，`quota_used` 正好 +失败数；
    - 剩余 < 失败数 → 402，额度不变，且**一个失败任务都没被重新入队**
      （不能出现"扣了一半又把一半任务放跑"）。
    """
    assert _submit_batch(client, 3).status_code == 202
    batch_id = db.scalar(select(Batch.id))
    assert batch_id is not None

    children = list(db.scalars(select(Task).where(Task.batch_id == batch_id)).all())
    assert len(children) == 3
    for child in children[:2]:  # 让 2 个失败
        sm.mark_running(db, child)
        sm.mark_failed(db, child, ErrorType.MODEL_MISSING, "测试用：模拟致命失败")
    db.commit()

    # (a) 额度不够 2：402，且不改额度、不放跑任务
    user.quota_used = user.quota_total - 1
    db.commit()
    before = _db_used(db, user.id)
    dispatched_before = len(no_broker)

    resp = client.post(f"/api/v1/batches/{batch_id}/retry-failed")

    assert resp.status_code == 402, resp.text
    assert _db_used(db, user.id) == before
    assert len(no_broker) == dispatched_before, "扣减失败却派发了任务"
    still_failed = db.scalars(
        select(Task).where(Task.batch_id == batch_id, Task.status == TaskStatus.FAILED.value)
    ).all()
    assert len(still_failed) == 2, "扣减失败却改了任务状态"

    # (b) 额度刚好够 2：成功，+2
    user.quota_used = user.quota_total - 2
    db.commit()

    ok = client.post(f"/api/v1/batches/{batch_id}/retry-failed")

    assert ok.status_code == 200, ok.text
    assert _db_used(db, user.id) == user.quota_total
