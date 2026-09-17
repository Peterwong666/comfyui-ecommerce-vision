"""`alembic/env.py` 的日志配置**不得污染进程内已有的 logger**（回归用例）。

为什么这条必须存在（它是"测试静默变假绿"的根因，不是代码洁癖）
============================================================

`alembic/env.py` 会执行 `fileConfig(config.config_file_name)`。`fileConfig()` 的
`disable_existing_loggers` 参数**默认是 `True`**：它遍历进程里**已存在**的 logger，
把凡是没写进 `alembic.ini` 的统统 `logger.disabled = True` ——
其中就包括 `app.api.v1.tasks`、`app.core.*` 这些应用 logger。

三个性质让这个副作用特别难发现：

1. **不随调用结束回滚**：`disabled` 是 logger 对象上的进程级状态；
2. **不报错**：禁用后的 logger 只是"什么都不输出"，调用方拿不到任何信号；
3. **`caplog` 不受它影响**：`caplog.at_level()` 操作的是 `logging.disable()`
   （manager 级开关），**不会**去改 `logger.disabled`。于是"日志一条都没有"
   会被 pytest 呈现成**空列表**，看起来像被测代码没记日志。

失效链条：`test_migration.py`（或任何真的跑过 alembic 的用例）先执行
⇒ `fileConfig` 把 `app.api.v1.tasks` 置为 disabled ⇒ 此后**任何**用 `caplog`
断言应用日志的用例都拿到空列表。症状是**假绿**（断言 `assert msgs == []` 这类）
或**假红**（断言"必须有告警"）。

`app/api/v1/tasks.py:27-31` 的注释里记着这条，`tests/test_queue_guard.py` 甚至因此
绕开了 `caplog`（改用 monkeypatch 替换 `log.warning`）。本文件负责把**根因**钉住 ——
让"绕开"不再是唯一的选择。

⚠️ **本文件的两条用例是成对的**：第一条是被测行为（不许禁用），第二条是**元测试**
（证明"如果参数改回 True，第一条真的会变红"）。只留第一条的话，一个
`disable_existing_loggers=True` 的写法若恰好没命中我们的 logger，用例会**永远绿**；
元测试把"这个断言不是空的"变成可执行的证据。
"""

from __future__ import annotations

import logging
from logging.config import fileConfig
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
ALEMBIC_INI = BACKEND_DIR / "alembic.ini"

#: 被测的 logger：一个"已存在、但没写进 `alembic.ini`"的应用 logger。
#: 必须挑一个**不在** `alembic.ini`（root / sqlalchemy.engine / alembic）里的名字，
#: 否则它会被 `fileConfig` 正常配置、`disabled` 恒为 False，用例就永远绿了。
APP_LOGGER_NAME = "app.api.v1.tasks"


def _snapshot_logger_disabled() -> dict[str, bool]:
    """记录当前进程里所有 logger 的 `disabled` 标志（含 root）。

    元测试要主动触发一次"真·禁用"，必须能把现场**完整**还原 ——
    只还原我们关心的那个 logger 会让 `alembic` / `sqlalchemy.engine` 等
    一直留在禁用状态，污染后面的用例。
    """
    state = {name: lg.disabled for name, lg in logging.Logger.manager.loggerDict.items()
             if isinstance(lg, logging.Logger)}
    state[""] = logging.getLogger().disabled
    return state


def _restore_logger_disabled(state: dict[str, bool]) -> None:
    for name, disabled in state.items():
        logging.getLogger(name).disabled = disabled


def _run_alembic(sqlite_url: str) -> None:
    """真的跑一遍 `alembic upgrade head`。

    ⚠️ **必须走 `command.upgrade`（而不是直接 `fileConfig`）**：这条用例要钉的是
    `env.py` 那一行的参数。直接调 `fileConfig` 只能证明"`fileConfig` 有那个参数"，
    把 `env.py` 改回 `True` 时用例**照旧通过** —— 那就是个假测试。
    """
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    cfg.set_main_option("sqlalchemy.url", sqlite_url)
    command.upgrade(cfg, "head")


@pytest.fixture
def sqlite_url(tmp_path, monkeypatch):
    """把 `settings.database_url` 指向一个临时 SQLite。

    必须改**模块属性**而不是环境变量：`app.core.config.settings` 是 `lru_cache`
    单例，而 `env.py` 写的是 `from app.core.config import settings`（取属性）。
    与 `test_migration.py::sqlite_url` 同一手法。
    """
    import app.core.config as appcfg

    url = f"sqlite+pysqlite:///{tmp_path / 'logging-probe.db'}"
    monkeypatch.setattr(appcfg, "settings", appcfg.Settings(database_url=url))
    return url


@pytest.fixture
def sqlite_ddl(monkeypatch):
    """SQLite 不支持 ALTER TABLE ADD/DROP CONSTRAINT —— 跳过这两类 DDL。

    其余 DDL 原样执行：本用例只关心 `fileConfig` 的副作用，建表是否完整由
    `test_migration.py` 负责，这里不必重复。
    """
    from alembic.ddl.sqlite import SQLiteImpl

    monkeypatch.setattr(SQLiteImpl, "add_constraint", lambda self, const, **kw: None)
    monkeypatch.setattr(SQLiteImpl, "drop_constraint", lambda self, const, **kw: None)


def test_alembic_env_does_not_disable_existing_app_loggers(sqlite_url, sqlite_ddl) -> None:
    """走一遍真实 alembic 路径后，`app.api.v1.tasks` 必须**仍然启用**。

    破坏方式（变异验证）：把 `env.py` 的 `disable_existing_loggers=False` 删掉或改成
    `True` ⇒ 本用例变红。见交付报告里的变异记录。
    """
    logger = logging.getLogger(APP_LOGGER_NAME)
    # 前置条件：模拟"测试会话已经 import 过 app.api.v1.tasks"的真实状态
    # （logger 必须**先存在**，fileConfig 才可能把它禁掉）。
    logger.disabled = False
    assert logger.disabled is False

    _run_alembic(sqlite_url)

    assert logger.disabled is False, (
        f"`alembic/env.py` 的 fileConfig 把 {APP_LOGGER_NAME} 永久禁用了 —— "
        "此后所有依赖 caplog 的断言都会静默拿到空列表（假绿/假红）。"
        "修法：fileConfig(..., disable_existing_loggers=False)"
    )


def test_the_probe_would_detect_the_old_behaviour() -> None:
    """**元测试**：证明上一条用例的断言不是空的。

    这里故意用 `disable_existing_loggers=True`（即 `env.py` 修复前的行为）调一次
    `fileConfig`，断言那个 logger **真的**会被置为 disabled。
    若哪天 `fileConfig` 的语义变了、或 `APP_LOGGER_NAME` 被我误改成一个被 ini 配置的
    logger（于是永远不会被禁用），本用例会先红 —— 提醒"上面那条已经没有判别力了"。

    全域快照 + 还原：这次调用会禁用一大票 logger，必须一个不漏地放回去。
    """
    snapshot = _snapshot_logger_disabled()
    try:
        logger = logging.getLogger(APP_LOGGER_NAME)
        logger.disabled = False

        fileConfig(str(ALEMBIC_INI), disable_existing_loggers=True)

        assert logger.disabled is True, (
            "预期 fileConfig(disable_existing_loggers=True) 会禁用未在 ini 中声明的 logger；"
            "它没有 —— 说明上一条用例已失去判别力（例如 logger 名字被改成 ini 里有的）"
        )
    finally:
        _restore_logger_disabled(snapshot)

    # 还原必须干净：本文件不能给别的用例留下被禁用的 logger
    assert logging.getLogger(APP_LOGGER_NAME).disabled is False
