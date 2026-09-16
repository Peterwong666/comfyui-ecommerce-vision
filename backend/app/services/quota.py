"""配额扣减（EX-12 / FR-1.2）。

**唯一的配额判定与扣减入口。** 不要在别处再写 `user.quota_used += N` ——
那是 Python 层的读-改-写，在并发提交下会丢更新，导致额度被超发。

为什么是一条条件 UPDATE
--------------------
`get_db()` 是**每请求一个 session**（`app/db/session.py`），而 FastAPI 的同步路由
跑在线程池里：两个并发请求可以同时读到同一个 `quota_used`，再各自写回，后写的
那次覆盖先写的那次，实际扣减少于应收。把「检查」与「扣减」合并成**同一条 SQL**，
由数据库保证「读-判-写」不可分割，应用层就不再需要锁。

为什么不用 `SELECT ... FOR UPDATE`
---------------------------------
SQLite **不支持** `FOR UPDATE`，而本地开发与全部单测都跑 SQLite。条件 UPDATE 的
`WHERE quota_used + n <= quota_total` 在两边语义一致：
- PG（默认 READ COMMITTED）：并发写同一行时后到的事务会等前者提交，然后**重新
  求值 WHERE**，条件不成立就影响 0 行；
- SQLite：写事务本身就是串行的。
所以一套代码 + 一条 SQL 即可，无需为两种数据库各写一份。

`rowcount` 的可移植性
--------------------
PG 与 SQLite 的 UPDATE `rowcount` 都是**被 WHERE 匹配到的行数**（不是"值真的变了"
的行数），两边一致。这里 `needed >= 1`，匹配到就一定真的变了。
"""

from __future__ import annotations

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.models.user import User


def charge(db: Session, *, user_id: int, needed: int) -> bool:
    """原子扣减 `needed` 个额度。成功 ``True``，额度不足 ``False``。

    **本函数不 commit**：成功时由调用方在同一个事务里继续原有流程并提交；
    失败时调用方必须抛 402 且**不要提交**。失败**不留半成品写入** ——
    `rowcount == 0` 意味着那条 UPDATE 一行都没改（也不该提交任何东西）。

    ⚠️ **ORM 缓存过期**：条件 UPDATE 是 Core 语句，绕过了 ORM。`db` 里那个 `User`
    对象的 `quota_used` 仍是**旧值**（`SessionLocal` 用的是 `expire_on_commit=False`，
    连 commit 都不会自动刷新它）。因此这里在语句执行后**显式回读**，
    否则后续任何读 `user.quota_remaining` 的地方 —— 包括 402 的报错文案 ——
    拿到的都是过期数字。成功与失败都要回读：失败时调用方要报「剩余多少」，
    那个数字同样必须是真的（并发场景下它本来就可能与内存里的旧值不同）。
    """
    result = db.execute(
        update(User)
        .where(User.id == user_id, User.quota_used + needed <= User.quota_total)
        .values(quota_used=User.quota_used + needed)
        # 关掉自动同步：默认的 `synchronize_session` 会尝试在 Python 里用**内存中的
        # 旧值**重算 `quota_used` 并写回对象 —— 那正是本函数要杜绝的失真
        # （并发下内存值与库里的值可以不同，重算出来的新值会是错的）。
        # 正确做法是下面从库里回读。
        .execution_options(synchronize_session=False)
    )
    charged = result.rowcount == 1

    user = db.get(User, user_id)
    if user is not None:
        db.refresh(user, ["quota_used"])

    return charged
