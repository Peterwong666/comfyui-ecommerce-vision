"""Alembic 迁移入口（P2-13）。

设计要点：`target_metadata` 必须来自 `app.models`（聚合导入），
否则 autogenerate 会「看不见」新模型，生成空迁移。
"""

from __future__ import annotations

import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

# 让 alembic 能 import app（从 backend/ 目录执行时）
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import settings  # noqa: E402
from app.db import sqlite_compat  # noqa: E402,F401  注册 SQLite 编译适配（import 副作用）
from app.models import Base  # noqa: E402  聚合导入，见 app/models/__init__.py

config = context.config
config.set_main_option("sqlalchemy.url", settings.database_url)

if config.config_file_name is not None:
    # ⚠️ `disable_existing_loggers=False` **不是代码洁癖，是防「测试静默变假绿」**。
    #
    # `fileConfig()` 的该参数默认是 `True`：它会把进程里**已存在**、且没写进
    # `alembic.ini` 的 logger 全部 `logger.disabled = True` —— 其中就包括
    # `app.api.v1.tasks` 等应用 logger。这个副作用**不随调用结束回滚**，是进程级全局状态。
    #
    # 后果链条：`test_migration.py` 会真的跑一遍 alembic（于是走到这一行）⇒ 应用 logger
    # 被永久置 `disabled=True` ⇒ 此后任何用 `caplog` 的断言都拿到**空列表**。
    # 而 `caplog.at_level()` 只处理 `logging.disable()`（manager 级开关），
    # **不会**把 `logger.disabled` 打开 —— 所以症状不是"报错"，而是
    # **"该断言的日志一条都没有"**：测试静默变成假绿（或假红），看起来像被测代码坏了。
    #
    # 回归用例：`backend/tests/test_alembic_logging.py`。把那行参数改回 `True`
    # （或删掉它）时该用例立刻变红。
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=settings.database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
