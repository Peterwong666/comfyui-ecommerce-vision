"""埋点服务（P1-20 埋点方案的实现入口）。

原则（`docs/prd/tracking_plan.md` §6）：
1. 核心事件**全部后端埋点** —— 前端埋点会因广告拦截/关页面丢数据，
   而北极星与良品率这类要写进 KPI 的指标不能有丢失。
2. **埋点失败绝不能影响主流程** —— 出图是第一优先，埋点是第二优先。
   所以这里吞掉所有异常。
3. 不上传商品图内容、不存提示词全文（tracking_plan §1.2 反面清单）。
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.models.event import Event

log = get_logger(__name__)


def track(
    db: Session,
    event_name: str,
    *,
    user_id: int | None = None,
    task_id: int | None = None,
    batch_id: int | None = None,
    session_id: str | None = None,
    source: str = "api",
    **props: Any,
) -> None:
    """记录一个事件。**永不抛异常**，失败只记日志。"""
    try:
        db.add(
            Event(
                event_name=event_name,
                event_id=uuid.uuid4().hex,
                user_id=user_id,
                task_id=task_id,
                batch_id=batch_id,
                session_id=session_id,
                app_version=settings.app_version,
                source=source,
                props=props or {},
            )
        )
    except Exception as exc:  # 埋点失败不影响业务
        log.warning("track.failed event=%s err=%s", event_name, exc)
