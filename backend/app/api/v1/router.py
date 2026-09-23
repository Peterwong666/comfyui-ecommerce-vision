"""API v1 路由汇总。"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import assets, auth, catalog, compare, stats, tasks

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(assets.router)
api_router.include_router(tasks.router)
api_router.include_router(catalog.router)
api_router.include_router(stats.router)
api_router.include_router(compare.router)
