"""素材与产物接口（P6-09 上传 / P6-10 取图 · 标记 · 打包 · 生命周期）。

本模块是**「参考图」这条链路的入口端** —— 没有它，`Asset(kind=upload)` 记录
就不会存在，worker 里的 `asset_resolver`（把素材搬到引擎）也就没有素材可搬，
图生图 / 局部重绘 / 高清修复三条工作流会在运行时全部报错。

同时也是**产物消费链路的出口端**（P6-10）：取图 / 标记采纳 / 打包下载。
⚠️ 这三件事此前**全都不存在** —— `AssetOut` 的 docstring 一直写着"取图应走受控的
下载接口（P6-10）"，而那个接口从未被实现；`is_adopted` 更是没有任何写入口，
于是 **`image_adopted`（良品率分子）与 `image_downloaded`（北极星分子）
两个已冻结的埋点事件全仓库零处触发** —— 指标不是"还没看板"，是压根没有数据源。

## 关于状态码的一个取舍

契约 §2.2 的状态码表里**没有 413**。所以上传校验失败（格式/尺寸/体积）**统一用 422**，
而不是按 HTTP 语义拆成 413 + 422。理由：**接口契约的一致性优先于 HTTP 用词的精确性** ——
前端只需处理契约里列出的那几个码，多一个"约定外"的码就多一条分支。

## 取图为什么不用 MinIO 预签名 URL（P6-10 的原始措辞是"签名 URL 下载"）

结论：**用本模块的受控代理接口，不用预签名 URL**。三条理由，按重要性排序：

1. **V1 的部署形态下浏览器根本到不了 MinIO** —— autoDL 实例上的 9000 端口只能经
   SSH 隧道访问（`docs/sop/runbook.md`），预签名 URL 里的 host 是 `127.0.0.1:9000`，
   浏览器拿到它就是一个打不开的地址。这是**功能性**阻塞，不是偏好问题。
2. **对象键不该进接口契约** —— `AssetOut` 刻意不返回 `object_key`（见其 docstring），
   而预签名 URL 恰恰把 object_key 编码进了 URL。
3. **软删除与过期必须立刻生效** —— 预签名 URL 一旦签发，在 TTL 内无法撤销；
   而代理接口每次请求都重新校验归属与 `is_deleted`。

代价是产物字节要过一次 API 进程（多一次拷贝、占 API 带宽）。在 V1「单机 + 单用户量级」
下这个代价可忽略；真要优化应等 V2 上 CDN 时再评估 —— 那时是**新增**一条路径，
而不是改这条。
"""

from __future__ import annotations

import hashlib
import re
import zipfile
from collections.abc import Iterator
from datetime import datetime, timezone
from tempfile import SpooledTemporaryFile
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, File, HTTPException, Query, UploadFile, status
from fastapi.responses import Response, StreamingResponse
from sqlalchemy import func, select

from app.api.deps import CurrentUser, DbSession
from app.core.config import settings
from app.core.logging import bind_task, get_logger
from app.models.asset import Asset
from app.models.enums import AssetKind
from app.models.event import AuditLog, EventName
from app.models.task import Batch, Task
from app.schemas.asset import AssetListOut, AssetOut, AssetPackIn, AssetUpdateIn
from app.services.events import track
from app.services.image_probe import UnsupportedImage, probe
from app.services.storage import AssetStorage, MinioAssetStorage, build_upload_key

log = get_logger(__name__)
router = APIRouter(prefix="/assets", tags=["assets"])

#: 取图的浏览器缓存时长（秒）。**刻意取小值**：软删除要能很快生效，
#: 而 `private` 保证不会被中间代理缓存成"别人的图"。
_CONTENT_CACHE_SECONDS = 300

#: 打包时整包先落在临时文件里（超过该阈值自动溢写到磁盘，不占内存）。
#: 阈值取 32MB 是因为单张 1024² PNG 约 1-2MB，几十张仍在内存里，速度最好；
#: 而 1000 张这种量级会溢写磁盘 —— 那正是我们想要的（宁可慢，不要 OOM）。
_PACK_SPOOL_MAX_MB = 32

#: zip 条目名里允许保留的字符。⚠️ 这个白名单是**安全措施**，不是美化：
#: `sku` 来自用户参数、`original_name` 来自上传文件名，都是用户可控字符串。
#: 直接拼进 zip 条目名会产出 `../` 形态的条目（zip-slip）—— 用户在本机解压时
#: 会把文件写到压缩包之外的地方。白名单里没有 `/` 与 `\`，路径分隔符无法存活。
_SAFE_NAME = re.compile(r"[^0-9A-Za-z._\u4e00-\u9fff-]+")

#: 由 MIME 推导下载用的扩展名。**不从用户输入里取扩展名**（NFR-4）：
#: `original_name` 是用户可控的，拿它拼文件名等于把 `../` 那类字符串放进响应头。
_EXT_BY_MIME = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}

#: 打包响应分块大小。64KB 是「足够摊薄开销、又不至于在慢客户端上积压内存」的折中。
_PACK_CHUNK_BYTES = 64 * 1024


def _as_utc(dt: datetime) -> datetime:
    """把客户端传来的 datetime 归一化成 aware-UTC。

    ⚠️ 库里存的是 **UTC**。客户端完全可能传 `2026-01-01T00:00:00`（无时区），
    若直接拿它去比较，数据库会按**服务器时区**解释这个时间点 —— 于是同一份代码
    在东八区的机器上会把"今天的图"漏掉 8 小时，而这类错误不会报任何错。

    这里对 naive 一律按 UTC 处理，而不是去猜调用方的时区：猜错是静默的错误数据，
    而"显式带 `Z` / `+08:00`"是调用方一眼能改的事。
    """
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def _extension_for(mime_type: str | None) -> str:
    """由 MIME 推导扩展名，白名单外一律 `bin`（绝不回落到用户输入）。"""
    return _EXT_BY_MIME.get((mime_type or "").lower(), "bin")


def _safe_zip_dir(raw: object, fallback: str) -> str:
    """把用户可控的字符串洗成安全的 zip 目录名（zip-slip 防护）。

    `_SAFE_NAME` 白名单里**没有 `/` 与 `\\`**，所以清洗后不可能再包含路径分隔符 ——
    这是结构性保证。此外连续的点也一并消掉：分隔符已被挡死（穿越本就不可能），
    但留下 `..` 会让条目名读起来像路径回溯，也让"条目名里不含 `..`"这条回归断言
    失去意义。

    结果为空（例如 SKU 全是标点）时回退到 `fallback`，避免产出 `/{id}.png` 那种
    以分隔符开头的条目名。
    """
    cleaned = _SAFE_NAME.sub("_", "" if raw is None else str(raw))
    cleaned = re.sub(r"\.{2,}", "_", cleaned)
    return cleaned.strip(" .")[:40].strip(" .") or fallback


def _storage() -> AssetStorage:
    """构造存储实例。

    抽成函数是为了让调用点只有一处 `MinioAssetStorage()` —— 测试通过替换本模块的
    `MinioAssetStorage` 属性注入内存实现（既有 `test_assets.py` 就是这么做的）。
    """
    return MinioAssetStorage()


def _reject(reason: str) -> HTTPException:
    """上传校验失败一律 422（EX-10），且**原因必须具体**。

    只说"上传失败"会让用户反复试同一个文件；而这里的每一条原因
    用户都能自己修（换格式 / 换尺寸 / 压缩体积）。
    """
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=reason)


# ---------------------------------------------------------------- 上传（P6-09）


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
        with _storage() as storage:
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
    task_id: Annotated[int | None, Query(description="只看某个任务名下的素材")] = None,
    batch_id: Annotated[int | None, Query(description="只看某个批次下所有任务的素材")] = None,
    adopted_only: bool = Query(default=False, description="只看已采纳（良品率相关）"),
    favorite_only: bool = Query(default=False, description="只看已收藏"),
    created_from: Annotated[
        datetime | None, Query(description="创建时间下界（含），如 2026-01-01T00:00:00Z")
    ] = None,
    created_to: Annotated[
        datetime | None, Query(description="创建时间上界（含）")
    ] = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> AssetListOut:
    """素材列表。**默认不含回收站**（`is_deleted` 为真的不返回）。

    筛选维度按 FR-5.1「按任务/时间/模板筛选」：`task_id` 取单个任务的素材，
    `batch_id` 取一批任务的素材（批次只挂在 `tasks` 上，所以要 join 才映射得过来），
    `created_from`/`created_to` 按创建时间切片。

    ⚠️ **时区**：库里存 UTC，而客户端可能传 naive datetime；比较前一律按 UTC
    归一化（`_as_utc`）。这一步漏掉不会报错，只会静默少返回一批数据。

    `total` 与列表查询共用**同一组 conditions（含同一个 join）** —— 否则分页器上
    的总条数与实际能翻到的条数不一致，用户看到"最后一页是空的"会当成 bug 报上来。
    """
    conditions = [Asset.user_id == user.id, Asset.is_deleted.is_(False)]
    if kind:
        conditions.append(Asset.kind == kind)
    if task_id is not None:
        conditions.append(Asset.task_id == task_id)
    if batch_id is not None:
        conditions.append(Task.batch_id == batch_id)
    if adopted_only:
        conditions.append(Asset.is_adopted.is_(True))
    if favorite_only:
        conditions.append(Asset.is_favorite.is_(True))
    if created_from is not None:
        conditions.append(Asset.created_at >= _as_utc(created_from))
    if created_to is not None:
        conditions.append(Asset.created_at <= _as_utc(created_to))

    count_stmt = select(func.count()).select_from(Asset).where(*conditions)
    list_stmt = select(Asset).where(*conditions)
    if batch_id is not None:
        # 同一个 join 必须同时出现在计数与列表上；漏掉任何一边都会让两者不一致。
        count_stmt = count_stmt.join(Task, Asset.task_id == Task.id)
        list_stmt = list_stmt.join(Task, Asset.task_id == Task.id)

    total = db.scalar(count_stmt) or 0
    rows = db.scalars(
        list_stmt.order_by(Asset.id.desc()).limit(limit).offset(offset)
    ).all()
    return AssetListOut(total=int(total), items=[AssetOut.model_validate(r) for r in rows])


# ---------------------------------------------------------------- 打包（P6-10）


@router.post("/pack", summary="打包下载（P6-10 / FR-5.2 · FR-3.7）")
def pack_assets(body: AssetPackIn, user: CurrentUser, db: DbSession) -> StreamingResponse:
    """把多张素材打成一个 zip（FR-5.2 Must）。

    ## 为什么整包先落临时文件，而不是边查边流

    `db` 是**请求级**依赖，响应开始流式发送之后它就会被关掉。若在生成器里边读库
    边写 zip，客户端网速一慢就会踩到已关闭的会话（这类失败是间歇性的，最难查）。
    所以：先把 zip 完整写进 `SpooledTemporaryFile`，`seek(0)` 之后再分块流出去。
    代价是"首字节延迟 = 整包生成时间"，换来的是不会随机失败。

    ## 为什么用 `ZIP_STORED` 而不是 `ZIP_DEFLATED`

    PNG/JPEG 本身已是压缩格式，再 deflate 一遍通常只挤出个位数百分比，却要付一遍
    全量 CPU —— 上限 200 张 × 1-2MB 时这笔开销纯属浪费。

    ## zip-slip 防护（安全相关，不是美化）

    目录名取 `task.params["sku"]`，它是**用户可控**字符串。直接拼进 zip 条目名，
    就能产出 `../../.ssh/authorized_keys` 这种条目，用户在本机解压时把文件写到
    压缩包之外。这里过 `_SAFE_NAME` 白名单（不含 `/` 与 `\\`），穿越不可能发生。

    ## 空结果为什么是 422 而不是空 zip

    任务失败/被取消时"没有图"是正常情况，但**返回空 zip 不是**：用户拿到一个
    0 条目的压缩包，无从判断是"还没出图"还是"下载坏了"。
    """
    assets, scope = _select_pack_assets(db, body, user.id)

    if not assets:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="没有可下载的图片（该任务/批次可能尚未产出，或素材已全部删除）",
        )
    if len(assets) > settings.max_pack_assets:
        # 护栏而非业务规则：整包要在临时文件里落地，无上限时一次请求就能写满磁盘（EX-4）。
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"单次最多打包 {settings.max_pack_assets} 张，当前 {len(assets)} 张，"
                "请分批下载"
            ),
        )

    spool = SpooledTemporaryFile(max_size=_PACK_SPOOL_MAX_MB * 1024 * 1024)  # noqa: SIM115
    # ⚠️ 上面这行**不能**改成 `with`：spool 要在本函数 return 之后、由 `_iter_spool`
    # 的 finally 关闭（响应流跑完才关）。用 `with` 会在返回响应前就把它关掉。
    try:
        with zipfile.ZipFile(spool, "w", zipfile.ZIP_STORED) as zf, _storage() as storage:
            for asset in assets:
                zf.writestr(_zip_entry_name(asset), storage.get(asset.object_key))
    except Exception as exc:  # noqa: BLE001 - 存储实现多样，统一转成可读错误
        spool.close()
        log.error("asset.pack_failed user_id=%s count=%s err=%s", user.id, len(assets), exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="打包失败：有素材读取不到，请稍后重试",
        ) from exc

    # 埋点必须在**开始流式响应之前** commit —— 生成器执行时请求级 session 已经关了。
    track(
        db,
        EventName.IMAGE_DOWNLOADED,
        user_id=user.id,
        # 只有按任务打包才有明确归属；勾选/按批次时留空，不要瞎猜一个 task_id
        task_id=body.task_id,
        count=len(assets),
        pack=True,
        scope=scope,
    )
    db.commit()

    spool.seek(0)  # 写指针在末尾，不回到开头会流出一个空包
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    headers = {"Content-Disposition": f'attachment; filename="{quote(f"images-{stamp}.zip")}"'}
    return StreamingResponse(
        _iter_spool(spool), media_type="application/zip", headers=headers
    )


def _select_pack_assets(db: DbSession, body: AssetPackIn, user_id: int) -> tuple[list[Asset], str]:
    """按选取方式取出待打包素材，并推导埋点 `scope`（契约 §4 E11）。

    三种选取方式互斥已由 `AssetPackIn` 的校验器保证，这里不再判。

    共同口径：**只看未删除的素材**。回收站里的东西不该还能被打包下走。

    由此产生一个状态码取舍：`asset_ids` 里点名了已删除的素材时，它落在"找不到"
    那一类 → **404**（与详情/取图的"回收站 = 不存在"口径一致）；而按
    `task_id`/`batch_id` 打包时已删除的素材被静默排除，排完为空才是 422
    （"这个任务确实没有图"）。两种码各对应一个不同的事实，不是随手选的。
    """
    if body.asset_ids is not None:
        # 去重但保序：重复 id 会让"找到的条数 == 请求的条数"这个判据失真。
        ids = list(dict.fromkeys(body.asset_ids))
        rows = db.scalars(
            select(Asset).where(
                Asset.id.in_(ids),
                Asset.user_id == user_id,  # 越权 id 查不到 → 下面的条数比对会拦下
                Asset.is_deleted.is_(False),
            )
        ).all()
        if len(rows) != len(ids):
            # ⚠️ 不静默过滤掉：用户明确要了 N 张，少给几张他当时不会发现，
            # 等发现时已经晚了（返工/发错货）。宁可整个请求失败。
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"有 {len(ids) - len(rows)} 个素材不存在或不属于你",
            )
        return list(rows), "selected"

    if body.task_id is not None:
        task = db.scalar(select(Task).where(Task.id == body.task_id, Task.user_id == user_id))
        if task is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="任务不存在")
        rows = db.scalars(
            select(Asset)
            .where(
                Asset.task_id == task.id,
                Asset.user_id == user_id,
                Asset.is_deleted.is_(False),
            )
            .order_by(Asset.id)
        ).all()
        return list(rows), "all"

    batch = db.scalar(select(Batch).where(Batch.id == body.batch_id, Batch.user_id == user_id))
    if batch is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="批次不存在")
    rows = db.scalars(
        select(Asset)
        .join(Task, Asset.task_id == Task.id)
        .where(
            Task.batch_id == batch.id,
            Asset.user_id == user_id,
            Asset.is_deleted.is_(False),
        )
        .order_by(Asset.id)
    ).all()
    return list(rows), "all"


def _zip_entry_name(asset: Asset) -> str:
    """`{SKU 目录}/{asset_id}.{ext}` —— FR-3.7 原文「按 SKU 分目录」。

    `sku` 在 `task.params` 里（用户提交任务时填的）；为空则回退到 `task_{task_id}`，
    保证每个产物都有归属目录，而不是散在包的根下。
    """
    task = asset.task
    sku = (task.params or {}).get("sku") if task is not None else None
    directory = _safe_zip_dir(sku, fallback=f"task_{asset.task_id}")
    return f"{directory}/{asset.id}.{_extension_for(asset.mime_type)}"


def _iter_spool(spool: SpooledTemporaryFile) -> Iterator[bytes]:
    """从临时文件分块读出。

    `finally` 里必须 `close()` —— 否则每打包一次就泄漏一个临时文件，
    这正是"磁盘慢慢被吃掉"那类事故的成因（SpooledTemporaryFile 溢写到磁盘后
    由 OS 持有，不 close 不释放）。
    """
    try:
        while chunk := spool.read(_PACK_CHUNK_BYTES):
            yield chunk
    finally:
        spool.close()


@router.get("/{asset_id}", response_model=AssetOut, summary="素材详情")
def get_asset(asset_id: int, user: CurrentUser, db: DbSession) -> Asset:
    return _owned_asset(db, asset_id, user.id)


@router.get("/{asset_id}/content", response_class=Response, summary="取图（P6-10，受控代理）")
def get_asset_content(
    asset_id: int,
    user: CurrentUser,
    db: DbSession,
    download: bool = Query(
        default=False,
        description="true = 作为附件下载并计入交付埋点；false = 内联预览（画廊用）",
    ),
) -> Response:
    """取图片字节。**这是唯一的取图入口**（`AssetOut` 刻意不返回 URL / `object_key`）。

    为什么是受控代理而不是 MinIO 预签名 URL：见模块 docstring（浏览器到不了 9000
    端口 / 对象键不进契约 / 软删除必须立刻生效）。

    几个刻意的取舍：

    1. **软删除 = 404**（由 `_owned_asset` 的默认口径挡住）。回收站里的图不该还能取到。
    2. **对象缺失 = 500，不是 404**。记录在、对象不在是**运维事故**（存储丢数据、
       清理任务删错对象），不是用户输入问题。伪装成 404 会让前端以为"用户删过它"
       而静默丢弃这张图 —— 事故就此被藏起来，谁也不会去查。
    3. **只有 `download=True` 才算"有效交付"**。画廊里的 `inline` 预览不算：
       北极星指标是"交付给用户的图"，划过去看一眼不是交付；否则埋点会被浏览行为
       灌水，指标随之失去意义。
    4. `Cache-Control: private` 是**必须的**：图按用户隔离（FR-1.3），一旦被中间
       代理缓存成公共资源，B 用户就可能拿到 A 用户的图。`max-age` 取小值是为了让
       软删除尽快生效。
    5. `ETag` 用**内容哈希**：字节不变则缓存不失效；用 id/更新时间做 ETag 会在内容
       没变时产生无谓的重新下载。
    """
    asset = _owned_asset(db, asset_id, user.id)

    try:
        with _storage() as storage:
            data = storage.get(asset.object_key)
    except Exception as exc:  # noqa: BLE001 - 存储实现多样，统一转成可读错误
        log.error("asset.get_failed asset_id=%s key=%s err=%s", asset.id, asset.object_key, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="素材文件读取失败，请稍后重试或联系管理员",
        ) from exc

    # 文件名用 ASCII 安全的 `asset_{id}.{ext}`，所以不需要 RFC 5987 转义。
    filename = f"asset_{asset.id}.{_extension_for(asset.mime_type)}"
    headers = {
        "Content-Type": asset.mime_type,
        "ETag": f'"{hashlib.sha256(data).hexdigest()}"',
        "Cache-Control": f"private, max-age={_CONTENT_CACHE_SECONDS}",
        "Content-Disposition": (
            f'{"attachment" if download else "inline"}; filename="{filename}"'
        ),
    }

    if download:
        # 单张下载必然是"用户点的那一张"，故 scope=selected（契约 §4 E11）。
        track(
            db,
            EventName.IMAGE_DOWNLOADED,
            user_id=user.id,
            task_id=asset.task_id,
            count=1,
            pack=False,
            scope="selected",
        )
        db.commit()

    # ⚠️ 不用 FileResponse：字节已经在内存里，绕一圈磁盘只会更慢。
    return Response(content=data, headers=headers)


@router.patch("/{asset_id}", response_model=AssetOut, summary="标记采纳 / 收藏（P6-10）")
def update_asset(asset_id: int, body: AssetUpdateIn, user: CurrentUser, db: DbSession) -> Asset:
    """标记「采纳」（FR-2.7，良品率的唯一数据来源）与「收藏」（FR-5.3）。

    - **只有 `kind=output` 的产物能标记采纳**：采纳回答的是"这次生成的结果好不好"，
      对上传素材没有意义。放开它会让良品率（采纳数 / 成功出图数）的分子分母跨到
      两个不同的集合上，指标直接失真。
    - 回收站里的素材不能被标记（409）：它在所有**其它**接口上都表现为"不存在"
      （详情/取图 404、列表不返回），唯独还能改状态是自相矛盾的。这里**刻意**让
      `_owned_asset` 把已删除的记录也取出来（`include_deleted=True`），只为了给一句
      明确的 409，而不是让调用方去猜为什么突然 404。V1 也没有回收站恢复接口。
    - `image_adopted` **只在 False→True 的跃迁上**埋点：良品率按事件计数，重复
      PATCH true 若每次都埋点，用户点两下就把分子翻了倍。
    - 取消采纳（True→False）**不埋点**，但正常更新字段 —— 契约 §4 没有"取消采纳"
      这个事件，指标口径是"累计采纳过多少张"，不是"当前采纳了多少张"。
    """
    asset = _owned_asset(db, asset_id, user.id, include_deleted=True)
    if asset.is_deleted:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="该素材已在回收站")

    changed: list[str] = []

    if body.is_adopted is not None:
        if asset.kind != AssetKind.OUTPUT.value:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="只有生成产物可以标记采纳（上传素材没有“是否生成得好”的语义）",
            )
        if body.is_adopted and not asset.is_adopted:
            task = asset.task
            track(
                db,
                EventName.IMAGE_ADOPTED,
                user_id=user.id,
                task_id=asset.task_id,
                image_id=asset.id,
                # workflow / template_id 从任务上取。上传素材没有任务、任务也可能没挂
                # 模板，**判空是常态路径**而不是异常路径（契约不允许埋点抛异常，
                # 所以这里不用 try/except，用明确的判空）。
                workflow=(task.workflow.name if task is not None and task.workflow else None),
                template_id=(task.template_id if task is not None else None),
            )
        asset.is_adopted = body.is_adopted
        changed.append("is_adopted")

    if body.is_favorite is not None:
        # 收藏对上传素材与产物都合理，不需要额外判据。
        asset.is_favorite = body.is_favorite
        changed.append("is_favorite")

    if len(changed) == 2:
        action, detail = (
            "asset.update",
            {"is_adopted": body.is_adopted, "is_favorite": body.is_favorite},
        )
    elif changed[0] == "is_adopted":
        action, detail = "asset.adopt", {"is_adopted": body.is_adopted}
    else:
        action, detail = "asset.favorite", {"is_favorite": body.is_favorite}

    db.add(
        AuditLog(
            user_id=user.id, action=action, target_type="asset",
            target_id=str(asset.id), detail=detail,
        )
    )
    db.commit()
    return asset


@router.delete("/{asset_id}", response_model=AssetOut, summary="删除素材（软删除）")
def delete_asset(asset_id: int, user: CurrentUser, db: DbSession) -> Asset:
    """**软删除**，不是物理删除。

    理由：素材可能已被某个任务的产物引用（`Task` ↔ `Asset` 关联），
    物理删除会让历史产物变成"指向空洞"的记录，而 FR-5.4 要求产物可复现 ——
    复现需要的正是那张参考图。物理清理由生命周期任务统一做（P6-10）。
    """
    asset = _owned_asset(db, asset_id, user.id, include_deleted=True)
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


def _owned_asset(
    db: DbSession, asset_id: int, user_id: int, *, include_deleted: bool = False
) -> Asset:
    """带归属校验的取素材。越权返回 404 而不是 403（不泄露资源存在性，契约 §2.1）。

    `include_deleted=False`（默认）时，软删除的记录**也当作不存在**（404）。理由：
    素材一进回收站，就该在**所有**接口上一致地表现为"不存在"，而不是"详情 200、
    取图 404"这种自相矛盾的行为 —— 那种不一致会让前端判断不出到底发生了什么。
    V1 没有回收站恢复接口，所以不存在"用户需要看到它"的场景。

    唯一需要看见回收站记录的是两个**改状态**的接口（`delete_asset` / `update_asset`）：
    它们要能把"重复删除"和"标记回收站里的素材"识别成 409，而不是含糊的 404。
    因此只有它们显式传 `include_deleted=True`。
    """
    conditions = [Asset.id == asset_id, Asset.user_id == user_id]
    if not include_deleted:
        conditions.append(Asset.is_deleted.is_(False))
    asset = db.scalar(select(Asset).where(*conditions))
    if asset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="素材不存在")
    return asset
