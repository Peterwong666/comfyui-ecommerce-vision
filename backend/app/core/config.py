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

from pydantic import Field, field_validator, model_validator
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
    # 单个批量的 **SKU 数**上限。与 `max_batch_size`（**总张数**上限）是两个维度：
    # SKU 数 × images_per_sku = 总张数。契约 §5 #6 之前两者同名，已拆分。
    #
    # ⚠️ **它的定位是「冗余的显式护栏」，不是一条独立的业务规则。**
    # PRD §6.2 只定义了「单任务张数 1-1000」，没有 SKU 维度的边界；而因为
    # `images_per_sku ≥ 1`，SKU 数恒 ≤ 总张数 —— 也就是说**总张数校验已经覆盖了它**。
    # 那为什么还要它？因为拦在 SKU 维度上时**错误信息更准**：
    # 「1000 个 SKU 各出 1 张」用 SKU 维度拒绝，比用总张数拒绝更容易让用户看懂问题。
    #
    # ⚠️ **不要把它设得比 PRD 更严**（即不要低于 max_batch_size）。无依据地收紧边界
    # 会让用户莫名其妙被拒 —— 而"莫名其妙"正是最消耗信任的一类报错。
    # 将来若 Business 真的要给 SKU 维度单独限流，改这里即可，不必动校验代码。
    max_sku_count: int = 1000
    min_prompt_length: int = 1
    max_prompt_length: int = 2000
    min_steps: int = 1
    max_steps: int = 100
    min_cfg: float = 1.0
    max_cfg: float = 20.0
    min_gen_side: int = 512
    max_gen_side: int = 2048

    # ---------- 内容安全（P6-13 / FR-9.4 的输入侧部分） ----------
    # 输入侧敏感词/合规词过滤的总开关。词表在 `app/data/content_blocklist.json`
    # （规则、分级与已知局限都写在那份文件里；机制在 `app/services/content_safety.py`）。
    #
    # ⚠️ **False = 完全不检查**：既不拒绝、也不写 warn 审计。之所以要求"连审计都不写"，
    # 是为了让这个开关的效果能被一次请求证伪（"检查真的没发生"）；留下"不拦但仍记"的
    # 中间态，既说不清也测不准。
    # ⚠️ 它**不**包含 NSFW 视觉检测 —— 那部分在 V1 未实现（见 `services/content_safety.py`
    # 的 `NullNsfwDetector`）。打开本开关**不会**让任何图片被检测，别把它读成"NSFW 过滤已上线"。
    content_safety_enabled: bool = True

    task_max_retries: int = 3
    task_max_retries_hard_limit: int = 5
    task_timeout_seconds: int = 300
    task_timeout_min: int = 60
    task_timeout_max: int = 600
    task_heartbeat_timeout_seconds: int = 300  # 僵尸任务判定（T14）
    queue_max_depth: int = 10000

    # ---------- 队列与并发（P6-02 / NFR-2） ----------
    # GPU 并发恒为 1。用 Redis 上的一个独占位来保证 —— 单 worker + concurrency=1
    # 只是"配置上的保证"，而配置会被改错；锁是机制上的保证。
    gpu_lock_key: str = "comfyui:gpu_slot"
    gpu_lock_wait_seconds: int = 60  # 等不到就交回队列，稍后重投
    gpu_lock_ttl_seconds: int = 900  # 必须 > 单任务超时上限；worker 被 kill -9 后靠它自动解锁
    # 自动重试退避：第 n 次重试等 base * 2^(n-1) 秒，封顶 max
    task_retry_backoff_seconds: int = 10
    task_retry_backoff_max_seconds: int = 120
    # 兜底扫描：把"已入队/等待重试但一直没有被消费"的任务重新投递
    # （dispatch.py 设计的前提：broker 不可用时任务留在 queued，等恢复后补投）
    orphan_requeue_seconds: int = 300
    orphan_requeue_limit: int = 200
    worker_poll_interval_seconds: float = 0.5
    worker_vram_sample_seconds: float = 2.0  # 采样显存水位（AC-6.1 排障用）

    # ---------- 公平性闸门：每用户在途任务数上限 ----------
    # 「在途」= `Task.status IN ('queued','running','retrying')`（与队列深度同一口径）。
    # 某用户已有在途 N 个、本次请求还要 M 张，`N + M > 上限` 就**整批**拒绝（不扣额、不建任务）。
    #
    # **为什么必需**：GPU 全局并发恒为 1（NFR-2），所有用户共用同一条队列。没有这道门时，
    # 单个用户一次提交 1000 张（`max_batch_size` 的上限）就能把队列占满，内测期等于
    # **独占队列**：别人排在 1000 张之后，实际是"服务不可用"。这是**公平性**问题，
    # 不是容量问题 —— 全局队列熔断（EX-5 的 `queue_max_depth` 硬阈值）只在**整体**过载时
    # 才拒人，它拦不住"一个人把额度用满"。两者互补：一个管总量，一个管分配。
    #
    # 取值 1000 = 与 `max_batch_size` 同级：单次批量本身就允许 1000 张，所以正常用户
    # **不会被这道门误伤**（一次提交 1000 张正好等于上限，"超过"才拒）；只有当用户
    # **已有一批在途、又想再塞一批**时才会撞上。真正的滥用形态（连开 N 个 1000 张批量）
    # 会在第 2 批被拦。
    max_in_flight_per_user: int = 1000

    # ---------- 资源保护（PRD EX-4） ----------
    disk_min_free_gb: int = 5
    disk_warn_free_gb: int = 10

    # ---------- 素材与产物的生命周期（P6-10） ----------
    # 回收站保留期：软删除后超过它才**物理**删除（对象 + 记录）。
    # 为什么不是立即物理删：用户误删需要挽回窗口，且素材可能被历史产物引用
    # （FR-5.4 要复现那张图，复现需要的正是参考图）。
    asset_trash_retention_days: int = 30
    # 产物保留期。**0 = 永久保留**（V1 的默认）。
    # 产物是用户花钱生成的资产，无依据地过期删除会直接摧毁信任；
    # 保留这条配置是为了 V2 做「免费用户 N 天 / 付费永久」时有位置可改。
    # ⚠️ 即使设了非 0，**已采纳 / 已收藏的产物也永不清理**（见 maintenance.cleanup_assets）。
    asset_output_retention_days: int = 0
    # 单次打包下载的图片数上限。它是**内存/磁盘护栏**，不是业务规则：
    # 打包会在临时文件里生成完整 zip，无上限时一次请求就能把盘写满（EX-4）。
    max_pack_assets: int = 200
    # 每轮清理最多处理多少条。设为有限值的理由：清理跑在 maintenance 队列上，
    # 一轮全量扫百万行会把 DB 连接占满，影响出图主链路。
    cleanup_batch_limit: int = 500

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

    @model_validator(mode="after")
    def _guard_retry_bounds(self) -> Settings:
        """task_max_retries 不得超过 task_max_retries_hard_limit。

        `task_max_retries_hard_limit` 是**全局硬上限**（运维/安全角度），
        `task_max_retries` 是**当前生效值**（业务角度）。前者必须 ≥ 后者，
        否则会出现「业务配置想重试 5 次，但全局硬上限只允许 3 次」的悖论。
        """
        if self.task_max_retries > self.task_max_retries_hard_limit:
            raise ValueError(
                f"task_max_retries ({self.task_max_retries}) 不能大于 "
                f"task_max_retries_hard_limit ({self.task_max_retries_hard_limit})"
            )
        return self

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
