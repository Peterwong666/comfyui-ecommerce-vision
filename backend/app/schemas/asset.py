"""Pydantic Schema：素材（P6-09）。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class AssetOut(BaseModel):
    """素材（上传素材与产物共用同一个模型，靠 `kind` 区分）。

    ⚠️ **不返回 `object_key`**：它是存储层的内部分布式路径，对外无意义，
    且暴露它等于把"素材放在哪"这一实现细节变成接口契约的一部分 ——
    将来换存储（MinIO → 其他）就会破坏兼容。取图应走受控的下载接口（P6-10）。
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    kind: str
    mime_type: str
    size_bytes: int
    width: int | None
    height: int | None
    original_name: str | None
    is_adopted: bool
    is_favorite: bool
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
