"""ORM 模型导出。

Alembic 的 autogenerate 依赖这里把所有模型导入到同一个 MetaData 上，
**新增模型必须在这里注册**，否则不会生成迁移。
"""

from app.db.base import Base
from app.models.asset import Asset, DefectKnowledge, PromptLibrary
from app.models.enums import (
    AssetKind,
    BatchStatus,
    ErrorType,
    ModelKind,
    TaskStatus,
    UserRole,
)
from app.models.event import AuditLog, Event, EventName
from app.models.registry import ModelRegistry
from app.models.task import Batch, Task
from app.models.user import User
from app.models.workflow import Template, Workflow

__all__ = [
    "Asset",
    "AssetKind",
    "AuditLog",
    "Base",
    "Batch",
    "BatchStatus",
    "DefectKnowledge",
    "ErrorType",
    "Event",
    "EventName",
    "ModelKind",
    "ModelRegistry",
    "PromptLibrary",
    "Task",
    "TaskStatus",
    "Template",
    "User",
    "UserRole",
    "Workflow",
]
