"""健康检查（不鉴权）。

`/health` 是给负载均衡与容器编排用的存活探针；
`/health/ready` 是就绪探针，会检查下游依赖（DB / Redis / ComfyUI）。
二者分离很重要：ComfyUI 挂掉不应导致进程被重启（任务可以排队等待）。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from sqlalchemy import text

from app.api.deps import DbSession
from app.core.config import settings
from app.engine.driver import ComfyUIClient, ComfyUIError

router = APIRouter(tags=["health"])


@router.get("/health", summary="存活探针")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "app": settings.app_name,
        "version": settings.app_version,
        "env": settings.env,
    }


@router.get("/health/ready", summary="就绪探针（检查下游依赖）")
def ready(db: DbSession) -> dict[str, Any]:
    checks: dict[str, Any] = {}

    # 数据库
    try:
        db.execute(text("SELECT 1"))
        checks["database"] = {"ok": True}
    except Exception as exc:
        checks["database"] = {"ok": False, "error": str(exc)[:200]}

    # 推理引擎（不可达不算致命 —— 任务可排队等待 EX-8 恢复）
    try:
        with ComfyUIClient() as client:
            stats = client.health()
        devices = stats.get("devices") or []
        checks["comfyui"] = {
            "ok": True,
            "devices": [d.get("name") for d in devices if isinstance(d, dict)],
        }
    except ComfyUIError as exc:
        checks["comfyui"] = {"ok": False, "error": str(exc)[:200]}

    all_ok = all(bool(c.get("ok")) for c in checks.values())
    return {"status": "ok" if all_ok else "degraded", "checks": checks}
