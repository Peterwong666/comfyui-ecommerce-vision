"""把注册表灌进数据库：`python -m app.cli.seed_workflows`（P2-10 配套）。

配合 `python -m app.cli.init_db` 完成本地零依赖起库：

    DATABASE_URL='sqlite+pysqlite:///./dev.db' .venv/bin/python -m app.cli.init_db
    DATABASE_URL='sqlite+pysqlite:///./dev.db' .venv/bin/python -m app.cli.seed_workflows

生产/共享库请用 `alembic upgrade head` 建表，再执行本命令灌数据
（本命令本身对 PG 完全可用，只有 `--include-disabled` 被限制在 SQLite）。
"""

from __future__ import annotations

import argparse
import json
import sys

from engine import WorkflowEngineError

from app.core.config import settings
from app.db import sqlite_compat
from app.db.session import session_scope
from app.services.registry_loader import SeedReport, load_workflows

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_REFUSED = 2

_LABELS = (
    ("created", "新增"),
    ("updated", "更新"),
    ("unchanged", "未变"),
    ("deactivated", "停用（被更高版本取代）"),
)


def _print_human(report: SeedReport, *, dry_run: bool) -> None:
    suffix = "  —— dry-run，**未写库**" if dry_run else ""
    print(f"灌库完成：共 {report.total} 条{suffix}")
    for attr, label in _LABELS:
        items = getattr(report, attr)
        if items:
            print(f"  {label} {len(items)}：{', '.join(items)}")

    if report.dropped:
        print("  ⚠️ 下列键在 `workflows` 表里**没有对应列**，因此未落库：")
        for name in sorted(report.dropped):
            print(f"     {name}: {', '.join(report.dropped[name])}")

    for warning in report.warnings:
        print(f"  ⚠️ {warning}")

    if not dry_run:
        print("下一步：启动后端后 GET /api/v1/workflows 应只列出 is_active 的工作流")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.cli.seed_workflows",
        description="把 workflows/registry.yaml 灌进 workflows 表（幂等 upsert）",
    )
    parser.add_argument("--registry", default=None, help="注册表路径（默认 workflows/registry.yaml）")
    parser.add_argument("--dry-run", action="store_true", help="只报告，不写库")
    parser.add_argument("--json", dest="as_json", action="store_true", help="输出机器可读 JSON")
    parser.add_argument(
        "--include-disabled",
        action="store_true",
        help="把 status=disabled 的工作流也置为 active（**仅限 SQLite**，供本地前端联调）",
    )
    args = parser.parse_args(argv)

    if args.include_disabled and not sqlite_compat.is_sqlite_url(settings.database_url):
        print(
            "拒绝执行：--include-disabled 只允许用在本地 SQLite 库上。\n"
            f"  当前 DSN: {settings.database_url}\n"
            "  它会把 status=disabled 的工作流置为 active（即对用户可见）——\n"
            "  而在生产/共享库上，那等于把尚未通过渲染路径验证的工作流放出去。",
            file=sys.stderr,
        )
        return EXIT_REFUSED

    try:
        with session_scope() as db:
            report = load_workflows(
                db,
                registry_path=args.registry,
                include_disabled=args.include_disabled,
                dry_run=args.dry_run,
            )
    except WorkflowEngineError as exc:
        # 注册表结构错误 / 定义文件缺失。engine 的报错文案已经很具体，直接呈现，
        # 不要用 traceback 把它淹掉（但仍然是**非零退出**，不静默）。
        print(f"灌库失败：{exc}", file=sys.stderr)
        return EXIT_FAILED

    if args.as_json:
        print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))
    else:
        _print_human(report, dry_run=args.dry_run)
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
