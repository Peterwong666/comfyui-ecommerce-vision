"""素材接口（P6-09 / FR-1.x）。

本模块是**「参考图」这条链路的入口端** —— 没有它，`Asset(kind=upload)` 记录
就不会存在，worker 里的 `asset_resolver`（把素材搬到引擎）也就没有素材可搬，
图生图 / 局部重绘 / 高清修复三条工作流会在运行时全部报错。

## 关于状态码的一个取舍

契约 §2.2 的状态码表里**没有 413**。所以上传校验失败（格式/尺寸/体积）**统一用 422**，
而不是按 HTTP 语义拆成 413 + 422。理由：**接口契约的一致性优先于 HTTP 用词的精确性** ——
前端只需处理契约里列出的那几个码，多一个"约定外"的码就多一条分支。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, File, HTTPException, Query, UploadFile, status
from sqlalchemy import func, select

from app.api.deps import CurrentUser, DbSession
from app.core.config import settings
from app.core.logging import bind_task, get_logger
from app.models.asset import Asset
from app.models.enums import AssetKind
from app.models.event import AuditLog, EventName
from app.schemas.asset import AssetListOut, AssetOut
from app.services.events import track
from app.services.image_probe import UnsupportedImage, probe
from app.services.storage import MinioAssetStorage, build_upload_key

log = get_logger(__name__)
router = APIRouter(prefix="/assets", tags=["assets"])


def _reject(reason: str) -> HTTPException:
    """上传校验失败一律 422（EX-10），且**原因必须具体**。

    只说"上传失败"会让用户反复试同一个文件；而这里的每一条原因
    用户都能自己修（换格式 / 换尺寸 / 压缩体积）。
    """
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=reason)


@router.post("", response_model=AssetOut, status_code=status.HTTP_201_CREATED,
             summary="上传素材（P6-09）")
async def upload_asset(
    user: CurrentUser,
    db: DbSession,
    file: Annotated[UploadFile, File(description="JPG / PNG / WebP，100KB–20MB，64–8192px")],
) -> Asset:
    """上传一张素材图。校验分四层，**每一层的失败原因都具体到用户能自己改**。

    ① 体积 → ② 魔数判格式（**不看扩展名**）→ ③ 尺寸边界（PRD §6.2）
    ④ 落存储 + 落库
    """
    data = await file.read()
    size = len(data)

    # ---------- ① 体积（PRD §6.2：100KB – 20MB）----------
    min_bytes = settings.max_upload_min_kb * 1024
    max_bytes = settings.max_upload_size_mb * 1024 * 1024
    if size < min_bytes:
        raise _reject(
            f"文件过小：{size} 字节，最小 {settings.max_upload_min_kb}KB。"
            "（过小的图放大到出图尺寸会严重失真，故直接拒收）"
        )
    if size > max_bytes:
        raise _reject(
            f"文件过大：{size / 1048576:.1f}MB，最大 {settings.max_upload_size_mb}MB"
        )

    # ---------- ② 格式：按**魔数**判定，不看扩展名（EX-10）----------
    try:
        info = probe(data)
    except UnsupportedImage as exc:
        raise _reject(f"不接受的文件：{exc}") from exc

    # ---------- ③ 尺寸（PRD §6.2：64×64 – 8192×8192）----------
    for name, value in (("宽", info.width), ("高", info.height)):
        if value < settings.min_image_side:
            raise _reject(
                f"图片{name}度 {value}px 小于下限 {settings.min_image_side}px"
            )
        if value > settings.max_image_side:
            raise _reject(
                f"图片{name}度 {value}px 超过上限 {settings.max_image_side}px"
            )

    # ---------- ④ 落存储 + 落库 ----------
    extension = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp"}[info.format]
    key = build_upload_key(user.id, extension)

    try:
        with MinioAssetStorage() as storage:
            storage.put(key, data, info.mime_type)
    except Exception as exc:  # noqa: BLE001 - 存储实现多样，统一转成可读错误
        # 存储不可达/写入失败：对客户端是 500（它只需重传），
        # 对 worker 侧的同类故障则记为可重试（EX-13）
        log.error("asset.put_failed user_id=%s key=%s err=%s", user.id, key, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="素材存储写入失败，请稍后重试",
        ) from exc

    asset = Asset(
        user_id=user.id,
        kind=AssetKind.UPLOAD.value,
        object_key=key,
        mime_type=info.mime_type,
        size_bytes=size,
        width=info.width,
        height=info.height,
        # ⚠️ 原始文件名**只存 DB**，不进存储路径（NFR-4：路径里不含用户输入）
        original_name=file.filename,
    )
    db.add(asset)
    db.flush()

    track(
        db,
        EventName.IMAGE_UPLOADED,
        user_id=user.id,
        count=1,
        total_size=size,
        format=info.format,
        mode="single",
    )
    db.add(
        AuditLog(
            user_id=user.id, action="asset.upload", target_type="asset",
            target_id=str(asset.id),
        )
    )
    db.commit()
    log.info(
        "asset.uploaded asset_id=%s size=%s dim=%sx%s",
        asset.id, size, info.width, info.height,
        extra=bind_task(task_id=None),
    )
    return asset


@router.get("", response_model=AssetListOut, summary="素材列表（P6-09）")
def list_assets(
    user: CurrentUser,
    db: DbSession,
    kind: Annotated[str | None, Query(description="upload / output，不传为全部")] = None,
    adopted_only: bool = Query(default=False, description="只看已采纳（良品率相关）"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> AssetListOut:
    """素材列表。**默认不含回收站**（`is_deleted` 为真的不返回）。"""
    conditions = [Asset.user_id == user.id, Asset.is_deleted.is_(False)]
    if kind:
        conditions.append(Asset.kind == kind)
    if adopted_only:
        conditions.append(Asset.is_adopted.is_(True))

    total = db.scalar(select(func.count()).select_from(Asset).where(*conditions)) or 0
    rows = db.scalars(
        select(Asset).where(*conditions).order_by(Asset.id.desc()).limit(limit).offset(offset)
    ).all()
    return AssetListOut(total=int(total), items=[AssetOut.model_validate(r) for r in rows])


@router.get("/{asset_id}", response_model=AssetOut, summary="素材详情")
def get_asset(asset_id: int, user: CurrentUser, db: DbSession) -> Asset:
    return _owned_asset(db, asset_id, user.id)


@router.delete("/{asset_id}", response_model=AssetOut, summary="删除素材（软删除）")
def delete_asset(asset_id: int, user: CurrentUser, db: DbSession) -> Asset:
    """**软删除**，不是物理删除。

    理由：素材可能已被某个任务的产物引用（`Task` ↔ `Asset` 关联），
    物理删除会让历史产物变成"指向空洞"的记录，而 FR-5.4 要求产物可复现 ——
    复现需要的正是那张参考图。物理清理由生命周期任务统一做（P6-10）。
    """
    asset = _owned_asset(db, asset_id, user.id)
    if asset.is_deleted:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="该素材已在回收站"
        )
    asset.is_deleted = True
    db.add(
        AuditLog(
            user_id=user.id, action="asset.delete", target_type="asset",
            target_id=str(asset.id),
        )
    )
    db.commit()
    return asset


def _owned_asset(db: DbSession, asset_id: int, user_id: int) -> Asset:
    """带归属校验的取素材。越权返回 404 而不是 403（不泄露资源存在性，契约 §2.1）。"""
    asset = db.scalar(
        select(Asset).where(Asset.id == asset_id, Asset.user_id == user_id)
    )
    if asset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="素材不存在")
    return asset
