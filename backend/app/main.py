"""FastAPI 应用入口（P2-08）。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1.health import router as health_router
from app.api.v1.router import api_router
from app.core.config import settings
from app.core.logging import get_logger, setup_logging
from app.engine.driver import ComfyUIError

setup_logging()
log = get_logger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    setup_logging()
    log.info(
        "app.startup env=%s version=%s comfyui=%s",
        settings.env,
        settings.app_version,
        settings.comfyui_base_url,
    )
    if settings.jwt_secret == "CHANGE_ME_IN_ENV":
        log.warning("jwt_secret 仍为默认值，生产环境必须在 .env 中覆盖！")
    yield
    log.info("app.shutdown")


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description=(
        "电商视觉 AIGC 量产平台 · 后端 API\n\n"
        "设计文档：`docs/prd/PRD_v1.md`（功能需求 / 状态机 / 异常处理）"
    ),
    lifespan=lifespan,
    docs_url="/docs" if not settings.is_prod else None,
    redoc_url=None,
)

# CORS：V1 前后端同源部署（Nginx 反代），这里只为本地开发放开。
# 生产必须收敛到具体域名，不能用 "*"（NFR-4）。
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health_router)
app.include_router(api_router, prefix=settings.api_v1_prefix)


@app.exception_handler(ComfyUIError)
async def comfyui_error_handler(_: Request, exc: ComfyUIError) -> JSONResponse:
    """把引擎异常翻译成明确的 HTTP 响应。

    返回 `retryable` 给前端，让用户界面上能区分
    「会自动重试」与「需要你改参数」（PRD §6 的「用户可见」列）。
    """
    return JSONResponse(
        status_code=503,
        content={
            "detail": str(exc),
            "error_type": exc.error_type.value,
            "retryable": exc.retryable,
        },
    )
