"""pytest 共享 fixture。

用 SQLite 内存库而非 PostgreSQL：
- 单测目标是纯 Python 逻辑（状态机、错误分类、聚合规则），不需要 PG 特性；
- SQLite 让 `pytest` 零依赖即可跑，CI 与本地都省事。
- **但 JSONB / BigInteger 是 PG 优先的类型**，所以这里做两项编译期适配。

集成测试（需要真实 PG / Redis / ComfyUI 的）在 `tests/integration/` 下另行标记。
"""

from __future__ import annotations

from collections.abc import Generator

import pytest
from sqlalchemy import BigInteger, create_engine, event
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker

from app.models import Base

# --- SQLite 相容层（仅测试用，不影响生产行为） ---------------------------------


@compiles(JSONB, "sqlite")
def _jsonb_as_json(element, compiler, **kw):  # type: ignore[no-untyped-def]
    """JSONB → JSON。生产仍是 JSONB（PG 原生，支持索引与路径查询）。"""
    return "JSON"


@compiles(BigInteger, "sqlite")
def _bigint_as_integer(element, compiler, **kw):  # type: ignore[no-untyped-def]
    """BigInteger → INTEGER。

    SQLite 只有 `INTEGER PRIMARY KEY` 才自增，BigInteger 会导致主键不生成。
    """
    return "INTEGER"


@pytest.fixture
def engine():  # type: ignore[no-untyped-def]
    # 函数级作用域：每个测试用全新的内存库，避免 user/workflow fixture 提交的
    # 数据在 session 级 engine 上跨测试累积导致 IntegrityError（测试相互污染）。
    eng = create_engine("sqlite+pysqlite:///:memory:", future=True)

    @event.listens_for(eng, "connect")
    def _fk_on(dbapi_conn, _record):  # type: ignore[no-untyped-def]
        # SQLite 默认不启用外键约束，显式打开，否则级联删除相关的测试会失真
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    Base.metadata.create_all(eng)
    yield eng
    Base.metadata.drop_all(eng)


@pytest.fixture
def db(engine) -> Generator[Session, None, None]:  # type: ignore[no-untyped-def]
    session_factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    session = session_factory()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def user(db: Session):  # type: ignore[no-untyped-def]
    from app.models.enums import UserRole
    from app.models.user import User

    u = User(
        email="tester@example.com",
        password_hash="x",
        role=UserRole.USER.value,
        quota_total=100,
        quota_used=0,
    )
    db.add(u)
    db.commit()
    return u


@pytest.fixture
def workflow(db: Session):  # type: ignore[no-untyped-def]
    from app.models.workflow import Workflow

    wf = Workflow(
        name="t2i_v1",
        version=1,
        display_name="文生图",
        definition={"1": {"class_type": "KSampler", "inputs": {}}},
        param_schema={
            "fields": [
                {"key": "steps", "type": "int", "default": 25, "min": 1, "max": 100},
                {"key": "cfg", "type": "float", "default": 7.0, "min": 1.0, "max": 20.0},
            ]
        },
        param_bindings={"steps": ["1", "inputs", "steps"]},
        is_active=True,
    )
    db.add(wf)
    db.commit()
    return wf
