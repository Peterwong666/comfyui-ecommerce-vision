"""Schema 与 ORM 派生字段的一致性测试（契约 §5 #4 / #6）。

这两条都是「契约查出来、但测试没盖住」的问题，因此这里专门建一个文件：
**能被测试钉住的不一致，才不会再退回去。**
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.enums import ErrorType, TaskStatus
from app.models.task import Task
from app.schemas.task import BatchSubmitIn, TaskOut
from app.services import state_machine as sm

# --- 契约 §5 #4：TaskOut.can_retry 必须是显式字段（Pydantic v2 不序列化 property） ---


def test_task_out_serializes_can_retry(db: Session, user, workflow) -> None:
    """`can_retry` 必须真的出现在序列化结果里。

    回归点：此前写成 `@property`，Pydantic v2 **不会**把它放进 `model_dump()`，
    前端拿到的 JSON 里根本没有这个键 —— 而契约 §2.4 声明它有。
    """
    task = sm.create_task(db, user_id=user.id, workflow_id=workflow.id, params={})
    db.commit()
    db.refresh(task)

    payload = TaskOut.model_validate(task).model_dump()
    assert "can_retry" in payload, (
        "can_retry 未出现在序列化结果中（property 不会被 Pydantic v2 序列化）"
    )
    assert payload["can_retry"] is False  # queued 不可重试


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (TaskStatus.PENDING, False),
        (TaskStatus.QUEUED, False),
        (TaskStatus.RUNNING, False),
        (TaskStatus.RETRYING, False),
        (TaskStatus.SUCCEEDED, False),
        (TaskStatus.FAILED, True),
        (TaskStatus.CANCELED, True),
    ],
)
def test_can_retry_matches_contract_retryable_column(status: TaskStatus, expected: bool) -> None:
    """`can_retry` 必须与契约 §1.1「可重试」列 + `POST /tasks/{id}/retry` 完全一致。

    尤其是 `canceled`：契约明确它可重试（覆盖 PRD T13 的旧表述），
    而此前 ORM 属性把它判为不可重试 —— 前端会把一个实际能点的按钮藏起来。
    """
    task = Task(status=status.value, retry_count=0, params={})
    assert task.can_retry is expected


def test_can_retry_is_independent_of_error_type() -> None:
    """致命错误（如模型缺失）同样允许**用户手动**重试。

    区分瞬时/致命是**自动重试**的判据（`ErrorType.is_retryable`，PRD T7 vs T11），
    不是用户能否点重试按钮的判据 —— 用户补上模型后重试是合理需求。
    """
    task = Task(status=TaskStatus.FAILED.value, error_type=ErrorType.MODEL_MISSING.value)
    assert task.can_retry is True
    assert ErrorType.MODEL_MISSING.is_retryable is False  # 自动重试仍被拦住


# --- 契约 §5 #6：max_sku_count（SKU 数）与 max_batch_size（总张数）必须分开 ---


def test_batch_sku_count_uses_max_sku_count() -> None:
    """SKU 数超限时由 `max_sku_count` 拦下，且提示文案说的是 SKU 而不是张数。"""
    too_many = {f"SKU-{i}": [1] for i in range(settings.max_sku_count + 1)}
    with pytest.raises(ValidationError) as exc:
        BatchSubmitIn(workflow_name="t2i_v1", sku_assets=too_many)
    assert "SKU 数" in str(exc.value)


def test_batch_total_images_is_not_sku_count() -> None:
    """`total_images()` 是 SKU 数 × images_per_sku，两个维度独立。

    这正是原实现的歧义所在：同一个 `max_batch_size` 名字，
    校验器量的是**行数**，`submit_batch` 量的是**张数**。
    """
    payload = BatchSubmitIn(
        workflow_name="t2i_v1",
        sku_assets={"SKU-A": [1, 2], "SKU-B": [3]},
        images_per_sku=3,
    )
    assert len(payload.sku_assets) == 2
    assert payload.total_images() == 6


def test_batch_submit_in_accepts_sku_count_at_limit() -> None:
    """边界值必须**含**上限本身（PRD §6.2 的「1-1000」是闭区间）。"""
    at_limit = {f"SKU-{i}": [] for i in range(settings.max_sku_count)}
    payload = BatchSubmitIn(workflow_name="t2i_v1", sku_assets=at_limit)
    assert len(payload.sku_assets) == settings.max_sku_count
