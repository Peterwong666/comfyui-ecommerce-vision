"""结构化日志（P2-08）。

关键要求（PRD NFR-6）：日志必须含 `task_id` 串联，否则无法满足
「任一失败任务可查到完整上下文」（FR-4.5 / NFR-3）。

用法：
    from app.core.logging import get_logger, bind_task
    log = get_logger(__name__)
    log.info("task.enqueue", extra=bind_task(task_id=123, workflow="t2i_v1"))
"""

from __future__ import annotations

import json
import logging
import sys
from contextvars import ContextVar
from typing import Any

from app.core.config import settings

# 跨调用链传递的上下文（task_id / user_id / batch_id）
# 默认值用 None 而非 {}：可变对象作默认值会被所有上下文共享，是隐患。
_log_context: ContextVar[dict[str, Any] | None] = ContextVar("log_context", default=None)


def _ctx() -> dict[str, Any]:
    return _log_context.get() or {}


def bind_task(**kwargs: Any) -> dict[str, Any]:
    """把 task_id / batch_id / user_id 绑进当前上下文，后续日志自动携带。"""
    ctx = {**_ctx(), **{k: v for k, v in kwargs.items() if v is not None}}
    _log_context.set(ctx)
    return {"ctx": ctx}


def clear_context() -> None:
    _log_context.set({})


class JsonFormatter(logging.Formatter):
    """结构化 JSON 日志，便于 Loki / ELK 采集（ADR-004）。"""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }

        ctx = _ctx()
        if ctx:
            payload.update(ctx)

        # 通过 extra=bind_task(...) 传入的 ctx
        extra_ctx = getattr(record, "ctx", None)
        if isinstance(extra_ctx, dict):
            payload.update(extra_ctx)

        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)

        return json.dumps(payload, ensure_ascii=False, default=str)


class HumanFormatter(logging.Formatter):
    """本地开发用：可读性优先。"""

    def format(self, record: logging.LogRecord) -> str:
        ctx = {**_ctx(), **(getattr(record, "ctx", None) or {})}
        ctx_str = " ".join(f"{k}={v}" for k, v in ctx.items())
        base = f"{self.formatTime(record, '%H:%M:%S')} {record.levelname:<5} {record.name:<28} {record.getMessage()}"
        if ctx_str:
            base = f"{base}  [{ctx_str}]"
        if record.exc_info:
            base = f"{base}\n{self.formatException(record.exc_info)}"
        return base


def setup_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter() if settings.log_json else HumanFormatter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(settings.log_level.upper())

    # 降噪：uvicorn 访问日志与 SQL 日志单独控制
    logging.getLogger("uvicorn.access").setLevel("WARNING")
    logging.getLogger("sqlalchemy.engine").setLevel("WARNING")


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
