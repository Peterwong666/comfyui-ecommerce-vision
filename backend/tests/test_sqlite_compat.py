"""SQLite 兼容层的**对 PostgreSQL 无害**证明（P2-10）。

为什么这个测试重要
------------------
把后端从 PG 换成 SQLite 跑本地开发，最大的风险是**"本地绿了、PG 坏了"**。
本项目已经吃过一次同类教训（文档说 A、仓库是 B），所以这里不靠注释自证，
而是把「PG 方言产物未被改动」断言下来：

1. `JSONB` / `BigInteger` 在 **PG 方言**下编译结果不变；
2. 默认 DSN 仍是 PostgreSQL（没有被"为了本地方便"而改弱）；
3. PG 方言的**离线 DDL** 里 `JSONB` / `BIGINT` 与幂等索引的 partial `WHERE` 都还在 ——
   这一条不需要 PG 服务器就能跑，是 §"无法真连 PG" 下最强的替代证据。

⚠️ 仍然**不能**替代真实 PG 集成测试：SQLite 不会行使 JSONB 路径查询、
窗口函数、真并发与 `ALTER TABLE` 约束。**待 PG 验证**（本机无 PG 二进制也无 docker daemon）。
"""

from __future__ import annotations

from sqlalchemy import BigInteger, create_engine, text
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.pool import StaticPool
from sqlalchemy.schema import CreateIndex, CreateTable

from app.core.config import Settings
from app.db import sqlite_compat
from app.models import Base

# ---------------------------------------------------------------- 编译产物


def test_jsonb_compiles_to_jsonb_on_postgres() -> None:
    """核心断言：适配只挂在 sqlite 方言上，PG 侧必须原样。"""
    assert JSONB().compile(dialect=postgresql.dialect()) == "JSONB"


def test_bigint_compiles_to_bigint_on_postgres() -> None:
    assert BigInteger().compile(dialect=postgresql.dialect()) == "BIGINT"


def test_jsonb_compiles_to_json_on_sqlite() -> None:
    assert JSONB().compile(dialect=sqlite.dialect()) == "JSON"


def test_bigint_compiles_to_integer_on_sqlite() -> None:
    """SQLite 只有 `INTEGER PRIMARY KEY` 才自增，故必须降为 INTEGER。"""
    assert BigInteger().compile(dialect=sqlite.dialect()) == "INTEGER"


# ---------------------------------------------------------------- 默认配置


def test_default_dsn_is_still_postgresql() -> None:
    """默认值不得为了本地方便被改成 SQLite。

    `_env_file=None` 是为了**绕开本地 .env**：这个测试断言的是**代码里的默认值**，
    否则一个有 .env 的开发者会看到假失败。
    """
    assert Settings(_env_file=None).database_url.startswith("postgresql")


def test_is_sqlite_url_detects_by_scheme() -> None:
    assert sqlite_compat.is_sqlite_url("sqlite+pysqlite:///./dev.db") is True
    assert sqlite_compat.is_sqlite_url("sqlite:///:memory:") is True
    assert sqlite_compat.is_sqlite_url("postgresql+psycopg://u:p@127.0.0.1:5432/db") is False


# ---------------------------------------------------------------- 外键 PRAGMA


def test_install_foreign_keys_enables_pragma() -> None:
    """SQLite 默认不启用外键；不打开的话级联删除与约束会静默失真。"""
    eng = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args=sqlite_compat.sqlite_connect_args(),
        poolclass=StaticPool,
    )
    sqlite_compat.install_sqlite_foreign_keys(eng)
    with eng.connect() as conn:
        assert conn.execute(text("PRAGMA foreign_keys")).scalar() == 1


def test_connect_args_allow_cross_thread_use() -> None:
    """FastAPI 同步路由跑在线程池里，连接会跨线程 —— 不给这个参数会炸。"""
    assert sqlite_compat.sqlite_connect_args() == {"check_same_thread": False}


# ---------------------------------------------------------------- PG 离线 DDL


def test_pg_dialect_ddl_keeps_pg_types_and_partial_index() -> None:
    """PG 方言的**离线** DDL 必须仍带 JSONB / BIGINT 与 partial 索引的 WHERE。

    这是「本地 SQLite 跑通没掩盖 PG 缺陷」的核心证据：编译期产物里 PG 特征完好，
    而 sqlite 适配够不到 PG 方言。
    """
    pg = postgresql.dialect()

    # ⚠️ 用 `.tables.values()` 而非 `.sorted_tables`：模型间存在外键环
    # （batches→templates→assets→tasks→batches，见 docs/prd/er_diagram.md），
    # `sorted_tables` 遇环会抛 CircularDependencyError。
    ddl = "\n".join(
        str(CreateTable(table).compile(dialect=pg)) for table in Base.metadata.tables.values()
    )
    assert "JSONB" in ddl, "PG DDL 丢掉了 JSONB —— SQLite 适配越界了"
    assert "BIGINT" in ddl, "PG DDL 丢掉了 BIGINT —— SQLite 适配越界了"
    assert "JSON" not in ddl.replace("JSONB", ""), "PG DDL 里出现了 SQLite 的 JSON 降级"

    from app.models.task import Task

    idem = next(i for i in Task.__table__.indexes if i.name == "uq_tasks_idempotency")
    idx_ddl = str(CreateIndex(idem).compile(dialect=pg))
    assert "UNIQUE" in idx_ddl.upper()
    # SQLite 没有 partial index 的等价物，若这条 WHERE 丢了，幂等约束会变成"约束所有行"
    assert "WHERE" in idx_ddl.upper()
    assert "idempotency_key IS NOT NULL" in idx_ddl
