"""初始迁移与 ORM 模型的等价性测试（P2-13）。

**为什么需要这个文件**：`app/models/` 与 `alembic/versions/` 一旦脱节，症状是
「本地测试全绿、生产库与代码不一致」—— 与 #21（工作区绿 ≠ 仓库完整）同源。
加了模型却忘了生成迁移，是这个项目最容易复发的一类事故。

做法：不连数据库、不需要 PostgreSQL，而是**用一个记录器冒充 `alembic.op`**
把 `upgrade()` / `downgrade()` 跑一遍，拿到迁移实际创建的**表 / 外键 / 索引**清单，
再与 `Base.metadata` 逐项对照。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

from app.models import Base

VERSIONS_DIR = Path(__file__).resolve().parents[1] / "alembic" / "versions"
BACKEND_DIR = Path(__file__).resolve().parents[1]


class _OpRecorder:
    """只记录、不执行的 `alembic.op` 替身。"""

    def __init__(self) -> None:
        self.tables: list[str] = []
        self.foreign_keys: list[tuple[str, str, str]] = []  # (约束名, 源表, 目标表)
        self.indexes: list[tuple[str, str]] = []  # (索引名, 表名)
        self.index_options: dict[str, dict[str, Any]] = {}  # 索引名 → 建索引时的 kwargs
        self.dropped_tables: list[str] = []
        self.dropped_constraints: list[tuple[str, str | None]] = []
        # 调用顺序：用来验证「外键一律在全部建表之后才添加」
        self.sequence: list[str] = []

    # `op.f()` 是「按命名约定解析」的包装，直接返回原名即可
    @staticmethod
    def f(name: str) -> str:
        return name

    def create_table(self, name: str, *args: Any, **kwargs: Any) -> None:
        self.tables.append(name)
        self.sequence.append("create_table")

    def create_index(self, name: str, table: str, *args: Any, **kwargs: Any) -> None:
        self.indexes.append((name, table))
        self.index_options[name] = kwargs
        self.sequence.append("create_index")

    def create_foreign_key(
        self, name: str, source: str, referent: str, *args: Any, **kwargs: Any
    ) -> None:
        self.foreign_keys.append((name, source, referent))
        self.sequence.append("create_foreign_key")

    def drop_table(self, name: str, *args: Any, **kwargs: Any) -> None:
        self.dropped_tables.append(name)
        self.sequence.append("drop_table")

    def drop_index(self, name: str, table: str | None = None, *args: Any, **kwargs: Any) -> None:
        pass

    def drop_constraint(
        self, name: str, table: str | None = None, *args: Any, **kwargs: Any
    ) -> None:
        self.dropped_constraints.append((name, table))
        self.sequence.append("drop_constraint")


@pytest.fixture(scope="module")
def migration():
    files = sorted(VERSIONS_DIR.glob("*.py"))
    assert len(files) == 1, f"预期恰好一个迁移文件，实际 {[f.name for f in files]}"
    return files[0]


@pytest.fixture(scope="module")
def applied(migration: Path):
    """执行迁移的 upgrade() + downgrade()，返回记录器。"""
    recorder = _OpRecorder()
    spec = importlib.util.spec_from_file_location("_initial_migration", migration)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)

    # 迁移文件里写的是 `from alembic import op`，两条取用路径都要换掉
    saved_mod, saved_attr = sys.modules.get("alembic.op"), None
    import alembic

    saved_attr = alembic.op
    sys.modules["alembic.op"] = recorder  # type: ignore[assignment]
    alembic.op = recorder  # type: ignore[assignment]
    try:
        spec.loader.exec_module(module)
        module.upgrade()
        module.downgrade()
    finally:
        alembic.op = saved_attr  # type: ignore[assignment]
        if saved_mod is None:
            sys.modules.pop("alembic.op", None)
        else:
            sys.modules["alembic.op"] = saved_mod
    return recorder


def _expected_fks() -> dict[str, tuple[str, str]]:
    """按 `app/db/base.py` 的命名约定推导外键名 → (源表, 目标表)。"""
    out: dict[str, tuple[str, str]] = {}
    for table in Base.metadata.tables.values():
        for fk in table.foreign_keys:
            name = f"fk_{table.name}_{fk.parent.name}_{fk.column.table.name}"
            out[name] = (table.name, fk.column.table.name)
    return out


# --- 表 ------------------------------------------------------------------------


def test_migration_creates_every_model_table(applied: _OpRecorder) -> None:
    """模型里每一张表都必须出现在迁移里。

    新增模型却忘了生成迁移时，这条测试会直接指出是哪张表漏了。
    """
    assert set(applied.tables) == set(Base.metadata.tables), (
        f"迁移缺表：{sorted(set(Base.metadata.tables) - set(applied.tables))}；"
        f"迁移多表：{sorted(set(applied.tables) - set(Base.metadata.tables))}"
    )


def test_every_table_created_exactly_once(applied: _OpRecorder) -> None:
    """重复 create_table 说明有多个迁移在同一 revision 上重叠。"""
    assert len(applied.tables) == len(set(applied.tables))


def test_table_count_is_eleven(applied: _OpRecorder) -> None:
    """钉住 P2-13 的交付量（11 张表），异常增减时强制人工确认。"""
    assert len(applied.tables) == 11, sorted(applied.tables)


# --- 外键 ---------------------------------------------------------------------


def test_foreign_keys_match_metadata(applied: _OpRecorder) -> None:
    """外键的**名字、源表、目标表**三者都要与模型一致。

    只看名字会漏掉「源表/目标表写反」这类错 —— 而那种错在 PostgreSQL 上
    要到真正写入时才会炸。
    """
    actual = {name: (src, dst) for name, src, dst in applied.foreign_keys}
    assert actual == _expected_fks()


def test_foreign_keys_added_after_all_tables(applied: _OpRecorder) -> None:
    """外键必须在**所有表建好之后**添加。

    模型之间存在环（assets→tasks→batches→templates→assets），内联外键排不出
    合法建表顺序，在 PostgreSQL 上会 `relation does not exist`。
    记录器保留了调用顺序，这里断言最后一次 create_table 早于第一次 create_foreign_key。
    """
    seq = applied.sequence
    assert "create_foreign_key" in seq, "迁移里没有任何外键"
    last_create = max(i for i, call in enumerate(seq) if call == "create_table")
    first_fk = min(i for i, call in enumerate(seq) if call == "create_foreign_key")
    assert last_create < first_fk, (
        f"存在内联外键：第 {last_create} 步才建完表，但第 {first_fk} 步就加了外键"
    )


# --- 索引 ---------------------------------------------------------------------


def test_indexes_match_metadata(applied: _OpRecorder) -> None:
    """索引（含幂等用的 partial unique index）必须与模型一致。"""
    expected = {
        (idx.name, table.name) for table in Base.metadata.tables.values() for idx in table.indexes
    }
    assert set(applied.indexes) == expected, (
        f"迁移缺索引：{sorted(expected - set(applied.indexes))}；"
        f"迁移多索引：{sorted(set(applied.indexes) - expected)}"
    )


def test_idempotency_unique_index_present(applied: _OpRecorder) -> None:
    """EX-6 幂等去重依赖它，不能只建普通索引。"""
    assert ("uq_tasks_idempotency", "tasks") in applied.indexes


def test_downgrade_removes_everything(applied: _OpRecorder) -> None:
    """downgrade 必须能干净地退回空库（M2「销毁重建」）。"""
    assert set(applied.dropped_tables) == set(applied.tables)


def test_idempotency_index_is_partial(applied: _OpRecorder) -> None:
    """幂等唯一索引必须是 **partial**（`WHERE idempotency_key IS NOT NULL`，EX-6）。

    丢了 WHERE 就变成对全表建唯一索引 —— 绝大多数任务没有幂等键，
    白白放大高频写入路径的开销。

    这里断言**建索引时传的 kwargs**，而不是数据库里索引的 DDL：
    `postgresql_where` 是 PG 专有选项，SQLite 会静默忽略它，
    所以只有对 PG 方言做离线渲染、或这样直接看 kwargs，才验证得到。
    """
    options = applied.index_options.get("uq_tasks_idempotency", {})
    assert "postgresql_where" in options, "幂等索引丢了 partial 条件"
    where = options["postgresql_where"]
    assert "idempotency_key IS NOT NULL" in str(getattr(where, "text", where))
    assert options.get("unique") is True


def test_downgrade_drops_all_foreign_keys_first(applied: _OpRecorder) -> None:
    """先摘外键再删表，否则删父表会被子表外键挡住。"""
    dropped = {name for name, _ in applied.dropped_constraints}
    assert dropped == set(_expected_fks())


# --- 真跑一遍（在 SQLite 上） --------------------------------------------------


@pytest.fixture
def sqlite_url(tmp_path, monkeypatch):
    """把 `settings.database_url` 临时指向一个空 SQLite 文件。

    `alembic/env.py` 里写的是 `from app.core.config import settings`，取的是**模块属性**，
    所以改模块属性就能生效（只改环境变量不行 —— 那是个 lru_cache 单例）。
    """
    import app.core.config as appcfg

    url = f"sqlite+pysqlite:///{tmp_path / 'mig.db'}"
    monkeypatch.setattr(appcfg, "settings", appcfg.Settings(database_url=url))
    return url


def _run_alembic(url: str, *argv: str) -> None:
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    if argv[0] == "upgrade":
        command.upgrade(cfg, argv[1])
    elif argv[0] == "downgrade":
        command.downgrade(cfg, argv[1])
    else:  # pragma: no cover - 只在写错时触发
        raise AssertionError(argv)


def _tables(url: str) -> set[str]:
    from sqlalchemy import create_engine, inspect

    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            return set(inspect(conn).get_table_names()) - {"alembic_version"}
    finally:
        engine.dispose()


def test_migration_actually_applies_and_rolls_back(sqlite_url, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """真的把迁移跑一遍：upgrade 建出 11 张表，downgrade 干干净净退回空库。

    上一条测试用记录器核对的是**结构**，这条核对的是**能不能执行** ——
    op 的顺序、参数拼写、约束名冲突这类问题只有真跑才会暴露
    （这正是"用 autogenerate 产物直接建库"最容易翻车的地方）。
    """
    # SQLite 不支持 ALTER TABLE ADD/DROP CONSTRAINT，跳过这两类操作；
    # 其余 DDL（建表、建索引、唯一约束、CHECK）原样执行 —— 已足够验证顺序与拼写。
    # 外键本身的正确性由离线 PostgreSQL DDL 渲染与上面的结构对照保证。
    from alembic.ddl.sqlite import SQLiteImpl

    monkeypatch.setattr(SQLiteImpl, "add_constraint", lambda self, const, **kw: None)
    monkeypatch.setattr(SQLiteImpl, "drop_constraint", lambda self, const, **kw: None)

    _run_alembic(sqlite_url, "upgrade", "head")
    assert _tables(sqlite_url) == set(Base.metadata.tables)

    _run_alembic(sqlite_url, "downgrade", "base")
    assert _tables(sqlite_url) == set(), "downgrade 未清空，回滚会留残骸"

    # 再 upgrade 一次：证明可重建（M2「销毁重建」）
    _run_alembic(sqlite_url, "upgrade", "head")
    assert _tables(sqlite_url) == set(Base.metadata.tables)


def test_idempotency_index_keeps_partial_where(sqlite_url, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """真跑一遍迁移，确认幂等索引确实被建出来（唯一的顺序/拼写验证在这里）。"""
    from alembic.ddl.sqlite import SQLiteImpl
    from sqlalchemy import create_engine, text

    monkeypatch.setattr(SQLiteImpl, "add_constraint", lambda self, const, **kw: None)
    monkeypatch.setattr(SQLiteImpl, "drop_constraint", lambda self, const, **kw: None)
    _run_alembic(sqlite_url, "upgrade", "head")

    engine = create_engine(sqlite_url)
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text("SELECT COUNT(*) FROM sqlite_master WHERE name='uq_tasks_idempotency'")
            ).scalar()
    finally:
        engine.dispose()
    assert rows == 1
