"""Pydantic Schema：素材（P6-09 / P6-10）。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class AssetOut(BaseModel):
    """素材（上传素材与产物共用同一个模型，靠 `kind` 区分）。

    ⚠️ **不返回 `object_key`**：它是存储层的内部分布式路径，对外无意义，
    且暴露它等于把"素材放在哪"这一实现细节变成接口契约的一部分 ——
    将来换存储（MinIO → 其他）就会破坏兼容。取图走 `GET /assets/{id}/content`。

    `task_id` 与 `meta` 是 P6-10 补的，理由都是**前端拿不到替代数据源**：

    - `task_id`：画廊要按"哪个任务产出的"分组 / 跳转任务详情；
    - `meta`：FR-5.4「元数据查看（seed/模型/参数）」是 **Must**，
      而它是可复现性（C1）唯一的用户侧入口。⚠️ **它只在产物上非空** ——
      上传素材没有可复现性元数据。前端读之前要先判 `kind`（或判空），
      不要假设它总有值。
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    kind: str
    task_id: int | None
    mime_type: str
    size_bytes: int
    width: int | None
    height: int | None
    original_name: str | None
    is_adopted: bool
    is_favorite: bool
    meta: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class AssetListOut(BaseModel):
    """列表响应。

    ⚠️ 这里**不叫** `list[AssetOut]`（别的列表接口都是裸数组），
    因为素材列表需要带 `total` —— 前端做"已上传 N 张"和分页器都要它，
    而再发一个 count 接口是两次往返。
    本项目分页口径以契约 §2.1 为准（limit/offset），此处只多一个总数。
    """

    total: int
    items: list[AssetOut]


class AssetUpdateIn(BaseModel):
    """标记采纳 / 收藏（P6-10 / FR-2.7 · FR-5.3）。

    两个字段都是**可选**，但**至少要给一个** —— 空请求体几乎总是调用方拼错了
    字段名（拼错时 Pydantic 会忽略未知键），静默返回 200 会让前端以为"标记成功"
    而库里什么都没变。这是本项目反复吃亏的那一类"不报错的错误"。
    """

    is_adopted: bool | None = None
    is_favorite: bool | None = None

    @model_validator(mode="after")
    def _at_least_one(self) -> AssetUpdateIn:
        if self.is_adopted is None and self.is_favorite is None:
            raise ValueError("至少要提供 is_adopted 或 is_favorite 之一")
        return self


class AssetPackIn(BaseModel):
    """批量打包下载的选取方式（P6-10 / FR-5.2 · FR-3.7）。

    三种选取方式**互斥**，且 `scope` 不是入参而是推导值 —— 它只用于埋点口径
    （契约 §4 E11 的 `scope` ∈ {selected, all}）：

    - 给了 `asset_ids` → `scope=selected`（用户勾选的那些）
    - 给了 `task_id` / `batch_id` → `scope=all`（整个任务/批次的全部产物）

    让调用方自己传 `scope` 是不行的：它必须与实际的选取方式一致，
    而"同一个事实有两个输入通道"正是本项目已经吃过亏的模式（契约 §5 #5/#6）。
    """

    asset_ids: list[int] | None = Field(
        default=None, max_length=500, description="勾选下载；与 task_id/batch_id 互斥"
    )
    task_id: int | None = Field(default=None, description="打包该任务的全部产物")
    batch_id: int | None = Field(default=None, description="打包该批次的全部产物")

    @model_validator(mode="after")
    def _exactly_one_selector(self) -> AssetPackIn:
        given = [
            name
            for name, value in (
                ("asset_ids", self.asset_ids),
                ("task_id", self.task_id),
                ("batch_id", self.batch_id),
            )
            if value is not None
        ]
        if len(given) != 1:
            raise ValueError(
                "asset_ids / task_id / batch_id 必须且只能提供一个"
                f"（实际提供了 {given or '零个'}）"
            )
        return self
