"""枚举定义（P2-08）。

TaskStatus 严格对齐 `docs/prd/PRD_v1.md` §5.2 的单张任务状态（7 个）；
BatchStatus 对齐 §5.3 的父任务状态（含父任务专用的 `partial`，共 7 个）。
**不要随意增删状态** —— 前端徽章、埋点、看板都依赖这套取值。

跨流共享的完整契约见 `docs/sop/contracts.md` §1（与代码冲突时以该文档为准）。
"""

from __future__ import annotations

import enum


class TaskStatus(str, enum.Enum):
    """单张图（子任务）的状态机。见 PRD §5.2。"""

    PENDING = "pending"  # 已创建，待调度入队
    QUEUED = "queued"  # 已入队，等待 GPU
    RUNNING = "running"  # 正在执行
    RETRYING = "retrying"  # 瞬时错误，退避等待重试
    SUCCEEDED = "succeeded"  # 成功产出（终态）
    FAILED = "failed"  # 最终失败，重试耗尽（终态）
    CANCELED = "canceled"  # 用户取消（终态）

    @classmethod
    def terminal(cls) -> frozenset[TaskStatus]:
        """终态集合。埋点 `task_finished` 只在这些状态触发（见 tracking_plan §3.3）。"""
        return frozenset({cls.SUCCEEDED, cls.FAILED, cls.CANCELED})

    @classmethod
    def cancellable(cls) -> frozenset[TaskStatus]:
        """可被用户取消的状态（PRD §5.2）。"""
        return frozenset({cls.PENDING, cls.QUEUED, cls.RUNNING, cls.RETRYING})

    @property
    def is_terminal(self) -> bool:
        return self in self.terminal()


class BatchStatus(str, enum.Enum):
    """批量任务（父任务）的状态。见 PRD §5.2 / §5.3。

    `partial` 是关键：500 张成功 497 失败 3，既不能标 succeeded（掩盖问题），
    也不能标 failed（用户会以为全废）。
    """

    PENDING = "pending"
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELED = "canceled"
    PARTIAL = "partial"  # 部分成功部分失败（终态）

    @classmethod
    def terminal(cls) -> frozenset[BatchStatus]:
        return frozenset({cls.SUCCEEDED, cls.FAILED, cls.CANCELED, cls.PARTIAL})


class ErrorType(str, enum.Enum):
    """错误分类（PRD §6）。

    区分「瞬时」与「致命」是本项目状态机的核心设计点（PRD §5.4 T7 vs T11）：
    OOM 值得重试（降级后可能成功），非法参数不值得重试（重试一万次还是错）。
    """

    OOM = "oom"  # EX-1，可重试
    TIMEOUT = "timeout"  # EX-2，可重试
    INVALID_PARAM = "invalid_param"  # EX-3，致命
    DISK_FULL = "disk_full"  # EX-4，可重试（清理后）
    QUEUE_FULL = "queue_full"  # EX-5，可重试
    DUPLICATE = "duplicate"  # EX-6
    WORKER_CRASH = "worker_crash"  # EX-7，可重试
    ENGINE_UNRESPONSIVE = "engine_unresponsive"  # EX-8，可重试
    MODEL_MISSING = "model_missing"  # EX-9，致命
    INVALID_UPLOAD = "invalid_upload"  # EX-10
    SAVE_FAILED = "save_failed"  # EX-13，可重试
    QUOTA_EXCEEDED = "quota_exceeded"  # EX-12，致命
    UNKNOWN = "unknown"

    @classmethod
    def retryable(cls) -> frozenset[ErrorType]:
        """可重试的错误类型（对应 PRD §5.4 T7）。"""
        return frozenset(
            {
                cls.OOM,
                cls.TIMEOUT,
                cls.DISK_FULL,
                cls.QUEUE_FULL,
                cls.WORKER_CRASH,
                cls.ENGINE_UNRESPONSIVE,
                cls.SAVE_FAILED,
            }
        )

    @property
    def is_retryable(self) -> bool:
        return self in self.retryable()


class AssetKind(str, enum.Enum):
    UPLOAD = "upload"  # 用户上传的素材
    OUTPUT = "output"  # 生成的产物


class UserRole(str, enum.Enum):
    USER = "user"
    ADMIN = "admin"


class ModelKind(str, enum.Enum):
    CHECKPOINT = "checkpoint"
    UNET = "unet"
    VAE = "vae"
    TEXT_ENCODER = "text_encoder"
    LORA = "lora"
    CONTROLNET = "controlnet"
    IPADAPTER = "ipadapter"
    UPSCALE = "upscale"
    OTHER = "other"
