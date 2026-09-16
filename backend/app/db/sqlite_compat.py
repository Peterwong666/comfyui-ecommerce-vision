"""SQLite 兼容层 —— **仅供本地开发**（P2-10 配套）。

为什么需要它
------------
生产用 PostgreSQL，但本机没有 PG 二进制也没有 docker daemon（见 `项目进展.md`），
导致后端无法启动、前端拿不到真实数据。把 DSN 换成 SQLite 后，
`JSONB` 与 `BigInteger` 两个 **PG 优先类型**在 SQLite 上无法编译：

- `JSONB`：SQLite 只认 `JSON`
- `BigInteger`：SQLite 只有 `INTEGER PRIMARY KEY` 才自增，`BigInteger` 会让主键不生成

所以这里注册两条**方言级**编译适配。

⚠️ 为什么只有这一份（重要）
--------------------------
这两条适配**曾经只存在于 `backend/tests/conftest.py`**，是「测试专用」的；
app 本身跑不起来。而如果为了 app 再抄一份，就正好复刻本项目 #22 的事故类 ——
**同一个东西两套实现 → 迟早分叉 → 而且分叉时不报错**。
所以：`conftest.py` / `app/db/session.py` / `alembic/env.py` **都 import 本模块**，
不得各自重新定义。

对 PostgreSQL 是 no-op（可机器校验）
----------------------------------
`@compiles(T, "sqlite")` 只往 `"sqlite"` 这个方言条目里注册。
PG 方言永远不会查这个条目，故 `JSONB` 仍是 `JSONB`、`BigInteger` 仍是 `BIGINT`。
`backend/tests/test_sqlite_compat.py` 用编译产物把这一点断言下来，
而不是靠这段注释自证。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import BigInteger, event
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Engine
from sqlalchemy.engine.url import make_url
from sqlalchemy.ext.compiler import compiles


@compiles(JSONB, "sqlite")
def _jsonb_as_json(element: Any, compiler: Any, **kw: Any) -> str:  # noqa: ARG001
    """JSONB → JSON。生产仍是 JSONB（PG 原生，支持索引与路径查询）。"""
    return "JSON"


@compiles(BigInteger, "sqlite")
def _bigint_as_integer(element: Any, compiler: Any, **kw: Any) -> str:  # noqa: ARG001
    """BigInteger → INTEGER。

    SQLite 只有 `INTEGER PRIMARY KEY` 才自增，BigInteger 会导致主键不生成。
    """
    return "INTEGER"


def is_sqlite_url(url: str) -> bool:
    """判断 DSN 是否指向 SQLite。

    只按 **URL 的 scheme** 判定，不新增任何 `Settings` 字段 ——
    ADR-004 约束 3「所有配置外置」下，DSN 本身就是那份配置。
    """
    return make_url(url).get_backend_name() == "sqlite"


def is_sqlite_engine(engine: Engine) -> bool:
    return engine.dialect.name == "sqlite"


def sqlite_connect_args() -> dict[str, Any]:
    """SQLite 专用连接参数。

    `check_same_thread=False` 对 **app 也是必需的**，不只是测试：
    FastAPI 的同步路由跑在线程池里，一条 SQLite 连接会跨线程使用，
    而 SQLite 默认禁止跨线程。
    """
    return {"check_same_thread": False}


def install_sqlite_foreign_keys(engine: Engine) -> None:
    """打开 SQLite 的外键约束。

    SQLite 默认**不启用**外键，不显式打开的话级联删除与约束校验会静默失真 ——
    测试里看起来"通过"，行为却与 PG 不同。非 SQLite 引擎直接返回。
    """
    if not is_sqlite_engine(engine):
        return

    @event.listens_for(engine, "connect")
    def _fk_on(dbapi_conn: Any, _record: Any) -> None:
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()
