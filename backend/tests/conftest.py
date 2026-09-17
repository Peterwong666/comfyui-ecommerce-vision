"""pytest 共享 fixture。

用 SQLite 内存库而非 PostgreSQL：
- 单测目标是纯 Python 逻辑（状态机、错误分类、聚合规则），不需要 PG 特性；
- SQLite 让 `pytest` 零依赖即可跑，CI 与本地都省事。

⚠️ JSONB / BigInteger 的 SQLite 编译适配**已移出本文件**，改为 import
`app.db.sqlite_compat`（**唯一一份**，app / alembic / 测试共用）。
它曾一度只存在于本文件，结果是「同一实现两份」且 app 根本起不来 ——
不要再抄回来，那是 #22 的事故类。

集成测试（需要真实 PG / Redis / ComfyUI 的）在 `tests/integration/` 下另行标记。
"""

from __future__ import annotations

from collections.abc import Generator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import sqlite_compat
from app.models import Base


@pytest.fixture
def engine():  # type: ignore[no-untyped-def]
    # 函数级作用域：每个测试用全新的内存库，避免 user/workflow fixture 提交的
    # 数据在 session 级 engine 上跨测试累积导致 IntegrityError（测试相互污染）。
    #
    # `StaticPool` + `check_same_thread=False` 是跑 API 测试的必要条件：
    # `TestClient` 会在**另一个线程**里执行同步路由函数（anyio 的 portal），
    # 而 SQLite 默认禁止跨线程使用连接；且内存库在默认池下每个线程各拿一条连接，
    # 会各自看到一个**空库**（表都不存在）。StaticPool 让全进程共用同一条连接。
    # 请求是串行处理的，因此这里不存在并发争用。
    eng = create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    # 编译适配由 import 副作用注册；外键约束走同一份兼容层实现，
    # 不在测试里再抄一遍 PRAGMA（SQLite 默认不启用外键，不打开会让级联删除失真）。
    sqlite_compat.install_sqlite_foreign_keys(eng)

    Base.metadata.create_all(eng)
    yield eng
    # ⚠️ 拆卸前**先关掉外键**再 `drop_all`。
    #
    # 模型之间存在外键环（assets ↔ batches ↔ tasks ↔ templates），`drop_all` 排不出
    # 一个满足全部外键的删表顺序（它会发一条 SAWarning 说 "Can't sort tables for DROP"），
    # 只能按一组"局部有序"的顺序删。而上面刚把 SQLite 的外键打开（见
    # `install_sqlite_foreign_keys`），于是"先删父表"会真的报
    # `sqlite3.IntegrityError: FOREIGN KEY constraint failed`。
    #
    # 这个坑长期没被发现，是因为**没有任何用例建过 `templates` 行**（AC-F6 零覆盖，
    # 2026-09-17 才补）—— 一旦有模板行，拆卸就炸。症状会记在**用例的 teardown** 上，
    # 看上去像"用例自己坏了"，与真实缺陷无关，属于典型的假证据。
    #
    # 关掉外键只影响这条**一次性内存库**的拆卸（引擎是函数级的，拆完就丢），
    # 测试运行期间的外键约束照常生效。
    with eng.connect() as conn:
        conn.exec_driver_sql("PRAGMA foreign_keys=OFF")
        Base.metadata.drop_all(conn)


@pytest.fixture
def db(engine) -> Generator[Session, None, None]:  # type: ignore[no-untyped-def]
    session_factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    session = session_factory()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture(autouse=True)
def no_broker(monkeypatch):  # type: ignore[no-untyped-def]
    """单测绝不连真实 broker（本机没有 Redis）。

    API 在 commit 之后会调 `enqueue_task` 投递 Celery 任务。测试环境里 broker 不可达，
    真实调用会走 Celery 的连接重试 —— 虽然已把重试收敛到 0.2s（见 celery_app.py），
    但**单测就不该依赖外部服务**，也不该让每个提交用例都付一次网络等待。

    这里在**API 模块的绑定处**替换掉它（`api/v1/tasks.py` 用的是 `from ... import`，
    所以必须改它自己的模块属性，改 dispatch 里的名字是无效的）。
    需要断言"确实投递过、投到哪个队列"的用例可以直接声明 `no_broker` 拿到调用记录。
    """
    from app.api.v1 import tasks as api_tasks

    calls: list[tuple[int, int]] = []

    def _fake_enqueue(task_id: int, priority: int = 5) -> bool:
        calls.append((task_id, priority))
        return True

    monkeypatch.setattr(api_tasks, "enqueue_task", _fake_enqueue)
    return calls


@pytest.fixture
def client(db: Session, user, workflow):  # type: ignore[no-untyped-def]
    """带依赖覆盖的 FastAPI 测试客户端（跑真实路由，不启动网络服务）。

    两个覆盖点：
    - `get_db` → 直接用测试会话（内存库，跑完即弃）；
    - `get_current_user` → 直接返回 `user` fixture，绕过 JWT。

    必须依赖 `workflow` fixture —— 任务路由会按 name 查活跃工作流，库里没有就是 404。
    """
    from fastapi.testclient import TestClient

    from app.api.deps import get_current_user
    from app.db.session import get_db
    from app.main import app

    def _override_db():  # type: ignore[no-untyped-def]
        yield db

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.clear()


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
    """一个契约合规的最小工作流（`param_schema` 必须能被 `engine.ParamSchema.load` 接受）。

    合规要求（契约 §3.1 / B 流 `engine/schema.py`）：`schema_version` 必填；
    每个 field 的 `key` / `label` / `type` / `default` / `targets` 都必填；
    `int` / `float` 需要 `min` / `max`，`float` 还要 `step`。
    **`targets` 即使在语义上为空也必须显式写 `[]`** —— 缺失会被判为配置错误。
    """
    from app.models.workflow import Workflow

    wf = Workflow(
        name="t2i_v1",
        version=1,
        display_name="文生图",
        # `_meta` 段是必填的（B 流 `workflow_spec.md` §2.1：节点图与仓库元数据的隔离带），
        # 渲染时会整段剥离，不提交给 ComfyUI。
        definition={
            "_meta": {},
            "1": {"class_type": "KSampler", "inputs": {"steps": 20, "cfg": 7.0}},
        },
        param_schema={
            "schema_version": 1,
            "fields": [
                {
                    "key": "steps",
                    "label": "采样步数",
                    "type": "int",
                    "default": 25,
                    "min": 1,
                    "max": 100,
                    "step": 1,
                    "targets": [{"node_id": "1", "input": "steps"}],
                },
                {
                    "key": "cfg",
                    "label": "CFG",
                    "type": "float",
                    "default": 7.0,
                    "min": 1.0,
                    "max": 20.0,
                    "step": 0.1,
                    "targets": [{"node_id": "1", "input": "cfg"}],
                },
                # 只参与业务逻辑、不进图的参数：targets 显式写 []
                {"key": "sku", "label": "SKU", "type": "str", "default": "", "targets": []},
            ],
        },
        is_active=True,
    )
    db.add(wf)
    db.commit()
    return wf
