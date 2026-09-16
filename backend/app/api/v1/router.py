"""API v1 路由汇总。"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import auth, catalog, tasks

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(tasks.router)
api_router.include_router(catalog.router)
