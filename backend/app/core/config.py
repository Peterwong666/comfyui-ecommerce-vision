"""全局配置（P2-12）。

设计原则（ADR-004 约束 3）：**所有配置外置，不写死路径与密钥**。
为 V2 私有化部署（E9）预留：客户只需改 `.env` 即可迁移。

用法：
    from app.core.config import settings
    settings.comfyui_base_url
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---------- 应用 ----------
    app_name: str = "电商视觉 AIGC 量产平台"
    app_version: str = "0.1.0"
    env: Literal["dev", "staging", "prod"] = "dev"
    debug: bool = False
    api_v1_prefix: str = "/api/v1"

    # ---------- 数据库 ----------
    database_url: str = Field(
        default="postgresql+psycopg://platform:platform@127.0.0.1:5432/platform",
        description="SQLAlchemy 连接串",
    )
    db_pool_size: int = 5
    db_max_overflow: int = 10
    db_echo: bool = False

    # ---------- Redis / 队列 ----------
    redis_url: str = "redis://127.0.0.1:6379/0"
    celery_broker_url: str = "redis://127.0.0.1:6379/1"
    celery_result_backend: str = "redis://127.0.0.1:6379/2"

    # ---------- 对象存储（MinIO，S3 兼容） ----------
    minio_endpoint: str = "127.0.0.1:9000"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin"
    minio_secure: bool = False
    minio_bucket_uploads: str = "uploads"
    minio_bucket_outputs: str = "outputs"

    # ---------- ComfyUI 推理引擎 ----------
    # 安全红线（PRD NFR-4）：ComfyUI 无鉴权，只允许本机访问。
    comfyui_base_url: str = "http://127.0.0.1:8188"
    comfyui_ws_url: str = "ws://127.0.0.1:8188/ws"
    comfyui_connect_timeout: float = 5.0
    comfyui_read_timeout: float = 30.0
    # ComfyUI 输出目录（数据盘），用于兜底取图
    comfyui_output_dir: str = "/root/autodl-tmp/comfyui-data/output"

    # ---------- 安全 ----------
    jwt_secret: str = "CHANGE_ME_IN_ENV"  # 生产必须覆盖
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60 * 24 * 7  # 7 天
    bcrypt_rounds: int = 12

    # ---------- 业务边界值（严格对齐 PRD_v1.md §6.2） ----------
    max_upload_size_mb: int = 20
    max_upload_min_kb: int = 100
    min_image_side: int = 64
    max_image_side: int = 8192

    max_batch_size: int = 1000
    min_prompt_length: int = 1
    max_prompt_length: int = 2000
    min_steps: int = 1
    max_steps: int = 100
    min_cfg: float = 1.0
    max_cfg: float = 20.0
    min_gen_side: int = 512
    max_gen_side: int = 2048

    task_max_retries: int = 3
    task_max_retries_hard_limit: int = 5
    task_timeout_seconds: int = 300
    task_timeout_min: int = 60
    task_timeout_max: int = 600
    task_heartbeat_timeout_seconds: int = 300  # 僵尸任务判定（T14）
    queue_max_depth: int = 10000

    # ---------- 资源保护（PRD EX-4） ----------
    disk_min_free_gb: int = 5
    disk_warn_free_gb: int = 10

    # ---------- 默认配额 ----------
    default_quota_total: int = 50  # 新用户赠送额度（旅程 2：免信用卡+送额度）
    default_priority: int = 5  # 1(最高) ~ 9(最低)

    # ---------- 可观测 ----------
    log_level: str = "INFO"
    log_json: bool = True  # 生产用 JSON，本地可关
    gpu_cost_per_hour: float = 0.0  # 待核实后在 .env 覆盖（成本模型 P9-09）

    @field_validator("comfyui_base_url")
    @classmethod
    def _guard_comfyui_binding(cls, v: str) -> str:
        """防止把 ComfyUI 暴露到公网（PRD NFR-4）。

        ComfyUI 无任何鉴权，若指向 0.0.0.0 或公网地址等于把 GPU 送人。
        这里只做告警不阻断 —— 分布式部署（E10）时 worker 需连远程 ComfyUI。
        """
        if "0.0.0.0" in v:
            raise ValueError("comfyui_base_url 不允许使用 0.0.0.0（ComfyUI 无鉴权）")
        return v

    @property
    def is_prod(self) -> bool:
        return self.env == "prod"

    @property
    def gpu_cost_per_second(self) -> float:
        """单张成本模型用（PRD FR-7.5）：GPU 秒 → 元。"""
        return self.gpu_cost_per_hour / 3600.0


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
