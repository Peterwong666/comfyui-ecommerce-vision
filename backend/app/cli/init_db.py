"""本地开发建库：`python -m app.cli.init_db`（P2-10 配套）。

**只在 SQLite 上执行，其它 DSN 一律拒绝**（返回码 2）—— 防止误在生产/共享库上
`create_all` 绕过迁移。

为什么本地引导走 `create_all` 而不是 `alembic upgrade head`
---------------------------------------------------------
迁移里硬编码了 `sa.text("now()")`（PG 函数），而模型用 `func.now()`（由 SQLAlchemy
按方言渲染）。SQLite 没有 `now()`，迁移建的 SQLite 表 DDL 能过、**INSERT 可能失败**。
`create_all` 从模型直接生成 DDL，绕开这个分歧。

⚠️ **待 SQLite 验证**（本机此前无法执行，见 `项目进展.md`）：
    DATABASE_URL='sqlite+pysqlite:///./dev.db' .venv/bin/python -m app.cli.init_db
    DATABASE_URL='sqlite+pysqlite:///./dev.db' .venv/bin/python -c \\
      "from app.db.session import SessionLocal; from app.models.task import Task; \\
       s=SessionLocal(); s.add(Task(params={})); s.commit()"
若 INSERT 报 `no such function: now()`，则确认迁移路径对 SQLite 不安全。

⚠️ **SQLite 不能替代 PG 验证**：JSONB 路径/包含查询、窗口函数、真并发、
部分唯一索引的语义都不会被行使。本项目吃过「mock 与生产分歧掩盖真缺陷」的教训。
"""

from __future__ import annotations

import argparse
import sys

from app.core.config import settings
from app.db import sqlite_compat
from app.db.session import engine
from app.models import Base

EXIT_OK = 0
EXIT_REFUSED = 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.cli.init_db",
        description="在本地 SQLite 库上建表（仅开发用）",
    )
    parser.parse_args(argv)

    if not sqlite_compat.is_sqlite_url(settings.database_url):
        print(
            "拒绝执行：init_db 只用于本地 SQLite 开发库。\n"
            f"  当前 DSN: {settings.database_url}\n"
            "  非 SQLite 请走迁移： cd backend && alembic upgrade head",
            file=sys.stderr,
        )
        return EXIT_REFUSED

    Base.metadata.create_all(engine)

    print(f"✅ 已建表（幂等）：{settings.database_url}")
    print("⚠️  这是本地开发用的 SQLite —— 它**不能**替代 PostgreSQL 验证：")
    print("    JSONB 路径查询 / 窗口函数 / 真并发 / 部分唯一索引 均未被行使。")
    print("下一步： python -m app.cli.seed_workflows")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
