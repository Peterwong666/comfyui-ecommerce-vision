"""素材上传（P6-09）与图片头解析的测试。

分两部分：
1. **`image_probe`** —— 按魔数判格式 + 从头部取尺寸。这是新写的、且**安全相关**
   （EX-10 要防"改后缀混进来"），所以测得比较细，含两个"看起来能过其实是错的"的陷阱
2. **上传接口** —— 四层校验（体积 / 格式 / 尺寸 / 落库）逐层验，且**原因必须具体**
3. **产物消费（P6-10）** —— 取图 / 标记采纳 / 打包下载。这一部分的原则是：
   **接口返回 200 不算通过，得看副作用**（字节逐字节相等、`events` 表真的多了一条）。
   `image_adopted`（良品率分子）与 `image_downloaded`（北极星分子）在本次之前
   全仓库零处触发，所以埋点必须由测试钉死 —— 否则"指标没有数据源"会再次静默发生。
"""

from __future__ import annotations

import hashlib
import struct
import uuid
import zipfile
from datetime import datetime, timedelta, timezone
from io import BytesIO

import pytest
from sqlalchemy import select

from app.models.asset import Asset
from app.models.enums import AssetKind, UserRole
from app.models.event import AuditLog, Event, EventName
from app.models.task import Batch, Task
from app.models.user import User
from app.services.image_probe import ImageInfo, UnsupportedImage, has_alpha, probe
from app.services.storage import AssetStorage

# ============================================================
# 测试用图片头构造器（不依赖 Pillow，只造**头部**）
#
# probe 只读头部，所以这里不需要造完整可解码的图 —— 这正是该模块的设计前提。
# ============================================================


def png_header(width: int, height: int, *, color_type: int = 6) -> bytes:
    """PNG：签名 + IHDR 长度 + "IHDR" + 宽 + 高 + 位深/色型/压缩/滤波/隔行 + CRC 占位。

    `color_type` 默认 6（RGBA，含 alpha）。可传 2（RGB，无 alpha）用于 alpha 校验测试。
    """
    return (
        b"\x89PNG\r\n\x1a\n"
        + struct.pack(">I", 13)
        + b"IHDR"
        + struct.pack(">II", width, height)
        + b"\x08" + bytes([color_type]) + b"\x00\x00\x00"  # 位深/色型/压缩/滤波/隔行
        + b"\x00\x00\x00\x00"      # CRC 占位（probe 不校验 CRC）
    )


def jpeg_header(width: int, height: int, *, exif: bool = False) -> bytes:
    """JPEG：SOI + 可选 APP1(EXIF) + SOF0。

    ⚠️ `exif=True` 会插入一个**变长 APP1 段** —— 这是关键用例：
    按固定偏移取尺寸的实现会在带 EXIF 的图上取错，而带 EXIF 恰是相机/手机导出的常态。
    """
    out = b"\xff\xd8"  # SOI
    if exif:
        payload = b"Exif\x00\x00" + b"P" * 40  # 40 字节伪 EXIF
        out += b"\xff\xe1" + struct.pack(">H", len(payload) + 2) + payload
    # SOF0：FFC0 + 长度 + 精度 + 高 + 宽 + 3 个分量各 3 字节
    out += b"\xff\xc0" + struct.pack(">H", 17) + b"\x08"
    out += struct.pack(">HH", height, width)
    out += b"\x03" + b"\x01\x11\x00" * 3
    return out


def webp_vp8x(width: int, height: int) -> bytes:
    """WebP 扩展格式（VP8X）：画布宽高为 24 位小端、均减 1。"""
    return (
        b"RIFF" + struct.pack("<I", 100) + b"WEBP" + b"VP8X"
        + struct.pack("<I", 10) + b"\x00\x00\x00\x00"
        + (width - 1).to_bytes(3, "little")
        + (height - 1).to_bytes(3, "little")
    )


def webp_vp8l(width: int, height: int) -> bytes:
    """WebP 无损（VP8L）：14 位宽高打包在一个 u32 里、均减 1。"""
    bits = ((width - 1) & 0x3FFF) | (((height - 1) & 0x3FFF) << 14)
    return (
        b"RIFF" + struct.pack("<I", 100) + b"WEBP" + b"VP8L"
        + struct.pack("<I", 5) + b"\x2f" + struct.pack("<I", bits)
    )


def webp_vp8(width: int, height: int) -> bytes:
    """WebP 有损（VP8）：帧头 9d 01 2a 之后是 14 位宽高。"""
    return (
        b"RIFF" + struct.pack("<I", 100) + b"WEBP" + b"VP8 "
        + struct.pack("<I", 10) + b"\x00\x00\x00" + b"\x9d\x01\x2a"
        + struct.pack("<HH", width, height)
    )


# ============================================================
# 1. image_probe
# ============================================================


class TestImageProbe:
    @pytest.mark.parametrize("size", [(1024, 768), (64, 64), (8192, 100)])
    def test_png(self, size):
        info = probe(png_header(*size))
        assert info == ImageInfo("PNG", *size)
        assert info.mime_type == "image/png"

    @pytest.mark.parametrize("size", [(1024, 768), (64, 64)])
    def test_jpeg(self, size):
        info = probe(jpeg_header(*size))
        assert info == ImageInfo("JPEG", *size)
        assert info.mime_type == "image/jpeg"

    def test_jpeg_with_exif_still_finds_size(self):
        """⭐ 关键用例：带变长 EXIF 段时仍能取到正确尺寸。

        按固定偏移（如"尺寸在偏移 5"）实现的做法在这里必然取到 EXIF 的垃圾值，
        而**它不会报错**，只会把尺寸读成一个荒谬的数字 —— 于是合法图被拒、
        或非法图被放行。这个用例就是钉这一点。
        """
        info = probe(jpeg_header(1920, 1080, exif=True))
        assert info == ImageInfo("JPEG", 1920, 1080)

    @pytest.mark.parametrize("builder", [webp_vp8x, webp_vp8l, webp_vp8])
    def test_webp_three_subformats(self, builder):
        """WebP 三种子格式的尺寸位置各不同，必须都能解析。"""
        assert probe(builder(800, 600)) == ImageInfo("WEBP", 800, 600)

    def test_rejects_unknown_bytes(self):
        with pytest.raises(UnsupportedImage, match="仅支持"):
            probe(b"this is definitely not an image, but it is long enough")

    def test_rejects_truncated(self):
        with pytest.raises(UnsupportedImage, match="过小"):
            probe(b"\x89PNG\r\n\x1a\n")

    def test_rejects_png_without_ihdr(self):
        """签名对、但首个 chunk 不是 IHDR —— 不能因为签名对就放行。"""
        bad = b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"XXXX" + b"\x00" * 16
        with pytest.raises(UnsupportedImage, match="IHDR"):
            probe(bad)

    def test_extension_does_not_matter(self):
        """⭐ 按**内容**判格式，不看扩展名 —— 这正是"改后缀混进来"要防的。

        本测试断言的是"内容说了算"：一段 JPEG 字节永远被识别为 JPEG，
        无论调用方把它命名成什么。
        """
        assert probe(jpeg_header(300, 300)).format == "JPEG"
        assert probe(png_header(300, 300)).format == "PNG"

    def test_has_alpha_true_for_rgba(self):
        assert has_alpha(png_header(300, 300, color_type=6)) is True

    def test_has_alpha_true_for_grayscale_alpha(self):
        assert has_alpha(png_header(300, 300, color_type=4)) is True

    def test_has_alpha_false_for_rgb(self):
        assert has_alpha(png_header(300, 300, color_type=2)) is False

    def test_has_alpha_false_for_jpeg_and_webp(self):
        assert has_alpha(jpeg_header(300, 300)) is False
        assert has_alpha(webp_vp8x(300, 300)) is False

    def test_has_alpha_false_for_truncated(self):
        """头部不足 26 字节时安全返回 False，不抛异常。"""
        assert has_alpha(b"\x89PNG\r\n\x1a\n" + b"\x00" * 10) is False


# ============================================================
# 2. 上传接口
# ============================================================


@pytest.fixture
def mem_storage(monkeypatch):
    """把路由里的存储换成内存实现，并返回它以便断言"字节真的落进去了"。"""
    from app.api.v1 import assets as assets_router
    from app.services.storage import InMemoryAssetStorage

    store = InMemoryAssetStorage()
    monkeypatch.setattr(
        assets_router, "MinioAssetStorage", lambda *a, **kw: store, raising=True
    )
    return store


def _upload(client, data: bytes, filename: str = "ref.png"):
    return client.post(
        "/api/v1/assets", files={"file": (filename, data, "image/png")}
    )


class TestUploadAsset:
    def test_upload_png_success(self, client, mem_storage, db):
        """⚠️ 体积下限是 100KB，所以正文要够大 —— 头部在前，后面补零填充。"""
        body = png_header(1024, 768) + b"\x00" * (120 * 1024)
        resp = _upload(client, body)
        assert resp.status_code == 201, resp.text
        out = resp.json()
        assert out["kind"] == "upload"
        assert out["width"] == 1024 and out["height"] == 768
        assert out["mime_type"] == "image/png"
        assert out["size_bytes"] == len(body)
        assert out["original_name"] == "ref.png"
        # key 不出现在响应里（存储实现细节不进契约）
        assert "object_key" not in out

    def test_uploaded_bytes_actually_land_in_storage(self, client, mem_storage, db):
        """上传成功必须意味着**字节真的进了存储** —— 不是只写了一条 DB 记录。"""
        from app.models.asset import Asset

        body = png_header(512, 512) + b"\x00" * (110 * 1024)
        resp = _upload(client, body)
        assert resp.status_code == 201
        asset = db.get(Asset, resp.json()["id"])
        assert mem_storage.get(asset.object_key) == body

    def test_rejects_too_small(self, client, mem_storage):
        """PRD §6.2 下限 100KB —— 过小的图放大到出图尺寸会严重失真。"""
        resp = _upload(client, png_header(256, 256) + b"\x00" * 100)
        assert resp.status_code == 422
        assert "过小" in resp.json()["detail"]

    def test_rejects_too_large(self, client, mem_storage):
        """PRD §6.2 上限 20MB。"""
        body = png_header(256, 256) + b"\x00" * (21 * 1024 * 1024)
        resp = _upload(client, body)
        assert resp.status_code == 422
        assert "过大" in resp.json()["detail"]

    def test_rejects_unsupported_format_with_specific_reason(self, client, mem_storage):
        body = b"GIF89a" + b"\x00" * (120 * 1024)
        resp = _upload(client, body, filename="anim.gif")
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert "不接受的文件" in detail
        # 原因要具体到用户能自己改（只说"上传失败"会让人反复试同一个文件）
        assert "JPG" in detail or "PNG" in detail

    def test_rejects_lying_extension(self, client, mem_storage):
        """⭐ 改后缀骗不过去：内容是 GIF，命名成 .png 也必须被拒。"""
        body = b"GIF89a" + b"\x00" * (120 * 1024)
        resp = _upload(client, body, filename="evil.png")
        assert resp.status_code == 422

    @pytest.mark.parametrize("side,word", [(32, "小于下限"), (9000, "超过上限")])
    def test_rejects_out_of_range_dimensions(self, client, mem_storage, side, word):
        """PRD §6.2：64 – 8192。两边都要拦，且原因要说明是宽还是高。"""
        body = png_header(side, 1024) + b"\x00" * (110 * 1024)
        resp = _upload(client, body)
        assert resp.status_code == 422
        assert word in resp.json()["detail"]

    def test_storage_failure_is_500_not_201(self, client, monkeypatch):
        """存储写失败必须**不返回 201** —— 否则会留下"有记录没文件"的脏数据。"""

        class Boom:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def put(self, *a, **kw):
                raise RuntimeError("storage down")

        from app.api.v1 import assets as assets_router

        monkeypatch.setattr(assets_router, "MinioAssetStorage", lambda *a, **kw: Boom())
        resp = _upload(client, png_header(512, 512) + b"\x00" * (110 * 1024))
        assert resp.status_code == 500
        assert "存储" in resp.json()["detail"]


class TestAssetQueries:
    def _make(self, client, width=512):
        return _upload(client, png_header(width, width) + b"\x00" * (110 * 1024)).json()

    def test_list_returns_total_and_excludes_deleted(self, client, mem_storage):
        a = self._make(client, 512)
        self._make(client, 640)
        resp = client.get("/api/v1/assets")
        assert resp.status_code == 200
        assert resp.json()["total"] == 2
        client.delete(f"/api/v1/assets/{a['id']}")
        resp = client.get("/api/v1/assets")
        assert resp.json()["total"] == 1  # 默认不含回收站

    def test_delete_is_soft(self, client, mem_storage, db):
        """软删除：记录仍在，只置 `is_deleted`。

        物理删除会让已引用该素材的历史产物变成"指向空洞"的记录，
        而 FR-5.4 要求产物可复现 —— 复现需要的正是那张参考图。
        """
        from app.models.asset import Asset

        a = self._make(client)
        resp = client.delete(f"/api/v1/assets/{a['id']}")
        assert resp.status_code == 200 and resp.json()["id"] == a["id"]
        row = db.get(Asset, a["id"])
        assert row is not None and row.is_deleted is True

    def test_double_delete_conflicts(self, client, mem_storage):
        a = self._make(client)
        client.delete(f"/api/v1/assets/{a['id']}")
        assert client.delete(f"/api/v1/assets/{a['id']}").status_code == 409

    def test_soft_deleted_asset_detail_is_404(self, client, mem_storage):
        """⭐ 软删除后**详情接口**（`GET /assets/{id}`）也是 404 —— P6-10 刻意的口径统一。

        素材一进回收站，就该在**所有**接口上一致地表现为"不存在"；否则会出现
        "详情 200（拿得到元数据、还能看到缩略图占位）但取图 404"这种自相矛盾的状态，
        前端据此判断不出到底发生了什么。这也正是 V1 没有回收站恢复接口的必然结果：
        既然看不到、也取不回，就没有任何接口需要"看得见"它。

        ⚠️ 这是 P6-10 引入的**行为变更**：`_owned_asset` 的默认口径从"不过滤
        `is_deleted`"改成"默认过滤"。于是本接口对已软删除素材的返回码
        从 **200 变成 404**（删除前 200 / 删除 200 / 删除后再详情 200 → 现在 404 /
        重复删除仍是 409）。旧语义下"删除后再详情"返回 200。

        这条用例就是钉住新语义：**若有人把 `include_deleted` 的默认值改回 `True`
        （或从 `_owned_asset` 的默认条件里去掉 `is_deleted`），本用例必须变红。**
        此前只有 `/content` 子路径的软删除用例（`test_soft_deleted_asset_is_404`），
        详情接口这一条无人覆盖 —— 改回去不会被任何测试发现。
        """
        a = self._make(client)
        assert client.get(f"/api/v1/assets/{a['id']}").status_code == 200
        assert client.delete(f"/api/v1/assets/{a['id']}").status_code == 200
        assert client.get(f"/api/v1/assets/{a['id']}").status_code == 404

    def test_missing_asset_is_404(self, client, mem_storage):
        assert client.get("/api/v1/assets/999999").status_code == 404

    def test_other_users_asset_is_404_not_403(self, client, mem_storage, db):
        """⭐ 数据隔离（FR-1.3）：别人的素材一律当**不存在**。

        用 404 而不是 403 —— 403 等于告诉调用方"这个 id 确实存在，只是不属于你"，
        那就把资源枚举变成了一次探测游戏。
        """
        from app.models.asset import Asset
        from app.models.enums import AssetKind, UserRole
        from app.models.user import User

        other = User(
            email="other@example.com", password_hash="x", role=UserRole.USER.value,
            quota_total=10, quota_used=0,
        )
        db.add(other)
        db.flush()
        theirs = Asset(
            user_id=other.id, kind=AssetKind.UPLOAD.value,
            object_key="uploads/999/secret.png", mime_type="image/png",
            size_bytes=1234, width=512, height=512, original_name="secret.png",
        )
        db.add(theirs)
        db.commit()

        assert client.get(f"/api/v1/assets/{theirs.id}").status_code == 404
        assert client.delete(f"/api/v1/assets/{theirs.id}").status_code == 404
        # 列表里也看不到
        assert client.get("/api/v1/assets").json()["total"] == 0

    def test_filter_by_task_and_batch(self, client, mem_storage, db, user, workflow):
        """FR-5.1 的「按任务筛选」。

        ⚠️ `total` 与 `items` 必须同时正确 —— 计数查询漏掉 join 时，接口会
        "说有 2 条、实际返回 0 条"，而两者单看都像是合理的。
        """
        batch = Batch(user_id=user.id, workflow_id=workflow.id, name="b1", common_params={})
        db.add(batch)
        db.flush()
        t1 = _make_task(db, user, workflow, batch_id=batch.id)
        t2 = _make_task(db, user, workflow, batch_id=batch.id)
        lone = _make_task(db, user, workflow)
        _make_output(db, user, t1, mem_storage, _blob())
        _make_output(db, user, t2, mem_storage, _blob())
        outside = _make_output(db, user, lone, mem_storage, _blob())

        by_task = client.get("/api/v1/assets", params={"task_id": t1.id}).json()
        assert by_task["total"] == 1 and len(by_task["items"]) == 1

        by_batch = client.get("/api/v1/assets", params={"batch_id": batch.id}).json()
        assert by_batch["total"] == 2
        assert len(by_batch["items"]) == 2
        assert outside.id not in [item["id"] for item in by_batch["items"]]

    def test_filter_by_favorite_and_created_range(self, client, mem_storage):
        keep = _upload(client, _blob()).json()
        _upload(client, _blob(640))

        patched = client.patch(f"/api/v1/assets/{keep['id']}", json={"is_favorite": True})
        assert patched.status_code == 200
        fav = client.get("/api/v1/assets", params={"favorite_only": True}).json()
        assert fav["total"] == 1 and fav["items"][0]["id"] == keep["id"]

        now, day = datetime.now(timezone.utc), timedelta(days=1)
        window = client.get(
            "/api/v1/assets",
            params={
                "created_from": (now - day).isoformat(),
                "created_to": (now + day).isoformat(),
            },
        ).json()
        assert window["total"] == 2
        # naive 时间同样可用：库里存 UTC，naive 按 UTC 解释（`_as_utc`）。
        # 少了这步归一化不会报错，只会静默按服务器时区解释、少返回一批数据。
        naive = client.get(
            "/api/v1/assets",
            params={"created_from": (now - day).replace(tzinfo=None).isoformat()},
        ).json()
        assert naive["total"] == 2
        future = client.get(
            "/api/v1/assets", params={"created_from": (now + day).isoformat()}
        ).json()
        assert future["total"] == 0


# ============================================================
# 3. 产物消费（P6-10）：取图 / 标记采纳 / 打包下载
#
# 这一段的测试原则与上传不同：**接口返回 200 不算通过**。
# `image_adopted`（良品率分子）与 `image_downloaded`（北极星分子）在本次之前
# 全仓库零处触发 —— 也就是说指标不是"还没做看板"，而是压根没有数据源。
# 所以凡是有埋点的路径，都必须断言 `events` 表里真的多了（并且只多了）那一条。
# ============================================================


def _blob(width: int = 512) -> bytes:
    """造一张"内容是 PNG"的大字节块（≥100KB，能过上传的体积下限）。"""
    return png_header(width, width) + b"\x00" * (110 * 1024)


def _make_task(db, user, workflow, *, params=None, batch_id=None) -> Task:
    """造一个任务。产物 Asset 靠它承载 `params`（SKU）与归属。"""
    task = Task(
        user_id=user.id, workflow_id=workflow.id, params=params or {},
        batch_id=batch_id, idx=0,
    )
    db.add(task)
    db.flush()
    return task


def _make_output(db, user, task, store, blob, *, mime="image/png", ext="png") -> Asset:
    """造一条 `kind=output` 的产物。

    ⚠️ 字节**真的写进存储**：只写 DB 记录的话，取图那条链路在测试里永远读不到东西，
    于是"取图 500"会被误当成实现 bug，改错地方。
    `object_key` 每次用新 uuid —— 它是唯一约束，重复会直接撞 IntegrityError。
    """
    key = f"outputs/{user.id}/{task.id}/{uuid.uuid4().hex}.{ext}"
    store.put(key, blob, mime)
    asset = Asset(
        user_id=user.id, task_id=task.id, kind=AssetKind.OUTPUT.value,
        object_key=key, mime_type=mime, size_bytes=len(blob), width=512, height=512,
    )
    db.add(asset)
    db.commit()
    return asset


def _other_users_asset(db, store) -> Asset:
    """造一个属于**别人**的素材（越权用例用）。"""
    other = User(
        email="intruder@example.com", password_hash="x", role=UserRole.USER.value,
        quota_total=10, quota_used=0,
    )
    db.add(other)
    db.flush()
    key = f"uploads/{other.id}/{uuid.uuid4().hex}.png"
    blob = _blob()
    store.put(key, blob, "image/png")
    asset = Asset(
        user_id=other.id, kind=AssetKind.UPLOAD.value, object_key=key,
        mime_type="image/png", size_bytes=len(blob), width=512, height=512,
    )
    db.add(asset)
    db.commit()
    return asset


def _events(db, name: str) -> list[Event]:
    return list(db.scalars(select(Event).where(Event.event_name == name)).all())


class _BoomStorage(AssetStorage):
    """读必炸的存储替身：模拟"对象丢了 / MinIO 不可达"这类**运维事故**。"""

    def put(self, key, data, content_type):  # pragma: no cover - 本用例不走写路径
        raise RuntimeError("should not put")

    def get(self, key):
        raise RuntimeError("storage down")

    def delete(self, key):  # pragma: no cover - 本用例不走写路径
        raise RuntimeError("should not delete")


class TestAssetContent:
    """取图：`GET /assets/{id}/content`（受控代理，不走预签名 URL）。"""

    def test_returns_exact_bytes_and_mime(self, client, mem_storage):
        """⭐ 逐字节相等。只断言 200 的话，"返回了半张图 / 返回了占位图"也会通过。"""
        body = _blob()
        asset_id = _upload(client, body).json()["id"]

        resp = client.get(f"/api/v1/assets/{asset_id}/content")
        assert resp.status_code == 200, resp.text
        assert resp.headers["content-type"] == "image/png"
        assert resp.content == body
        # ETag 是内容寻址（带引号）；Cache-Control 必须 private（按用户隔离）
        assert resp.headers["etag"].startswith('"') and resp.headers["etag"].endswith('"')
        assert "private" in resp.headers["cache-control"]

    def test_etag_is_content_addressed_sha256(self, client, mem_storage):
        """⭐ ETag 必须是**响应字节的 sha256**（内容寻址），不是 id / 时间等非内容标识。

        为什么值得单独钉一条：现有的 ETag 断言（`test_returns_exact_bytes_and_mime`）
        只检查首尾带引号 —— 于是把实现改成 `ETag = f'"{asset.id}"'` 也能存活。
        而 ETag 用非内容标识会导致**内容没变却缓存失效、重新下载整张图**（浪费带宽），
        且同一字节内容被上传两次会得到两个不同 ETag，缓存完全失效。

        四条断言各钉一件事：
        ① 形状：带引号 + 去掉引号后 64 位十六进制（sha256 的形态）；
        ② **内容寻址（决定性的一条）**：等于 `sha256(resp.content)`，测试里独立算一遍；
        ③ 幂等：同一张图再次请求 ETag 不变；
        ④ 判别力：内容不同的两张图 ETag 不同（钉住"ETag 不是常量"）。

        关于"哪几条能杀掉 `ETag = asset.id` 这个变异体"：**决定性的是 ① ②**。
        ④ 杀不掉它 —— 两张内容不同的图 id 也不同，用 id 当 ETag 时它们照样不等；
        ③ 同样杀不掉（同一张图 id 当然相同）。② 直接断言"ETag 是内容的函数"，
        候选若与内容无关（id）必然不等；① 则从形态上就排除了短数字 id。
        ④ 真正杀的是"ETag 是常量"那类变异。
        """
        first = _upload(client, _blob(512)).json()["id"]
        second = _upload(client, _blob(640)).json()["id"]

        resp = client.get(f"/api/v1/assets/{first}/content")
        assert resp.status_code == 200, resp.text
        etag = resp.headers["etag"]

        # ① 形如 "<64 位十六进制>"
        assert etag.startswith('"') and etag.endswith('"'), etag
        digest = etag.strip('"')
        assert len(digest) == 64, etag
        assert all(c in "0123456789abcdef" for c in digest), etag

        # ② 内容寻址：等于响应字节的 sha256
        assert digest == hashlib.sha256(resp.content).hexdigest()

        # ③ 同一张图再次请求，ETag 不变
        again = client.get(f"/api/v1/assets/{first}/content")
        assert again.headers["etag"] == etag

        # ④ 内容不同的两张图，ETag 必须不同
        other = client.get(f"/api/v1/assets/{second}/content")
        assert other.content != resp.content
        assert other.headers["etag"] != etag

    def test_disposition_inline_by_default_attachment_when_download(self, client, mem_storage):
        asset_id = _upload(client, _blob()).json()["id"]

        inline = client.get(f"/api/v1/assets/{asset_id}/content")
        assert inline.headers["content-disposition"].startswith("inline")
        assert f'filename="asset_{asset_id}.png"' in inline.headers["content-disposition"]

        attached = client.get(f"/api/v1/assets/{asset_id}/content", params={"download": 1})
        assert attached.headers["content-disposition"].startswith("attachment")
        # 附件与内联是**同一份字节**，只是响应头不同
        assert attached.content == inline.content

    def test_other_users_asset_is_404(self, client, mem_storage, db):
        theirs = _other_users_asset(db, mem_storage)
        assert client.get(f"/api/v1/assets/{theirs.id}/content").status_code == 404

    def test_soft_deleted_asset_is_404(self, client, mem_storage):
        """回收站里的图不该还能取到 —— 否则"删除"只是列表里看不见而已。"""
        asset_id = _upload(client, _blob()).json()["id"]
        assert client.get(f"/api/v1/assets/{asset_id}/content").status_code == 200
        client.delete(f"/api/v1/assets/{asset_id}")
        assert client.get(f"/api/v1/assets/{asset_id}/content").status_code == 404

    def test_storage_failure_is_500_not_404(self, client, mem_storage, monkeypatch, db):
        """⭐ 记录在、对象不在 = 运维事故，必须 500 而不是 404。

        伪装成 404 会让前端以为"用户删过它"而静默丢弃这张图 —— 事故就此被埋掉，
        谁也不会去查（这正是"不报错的错误"里最贵的一种）。
        """
        from app.api.v1 import assets as assets_router

        asset_id = _upload(client, _blob()).json()["id"]
        monkeypatch.setattr(assets_router, "MinioAssetStorage", lambda *a, **kw: _BoomStorage())

        resp = client.get(f"/api/v1/assets/{asset_id}/content")
        assert resp.status_code == 500, resp.text

    def test_download_emits_exactly_one_event(self, client, mem_storage, db):
        """⭐ "埋点真的接上了"的唯一证据。

        `image_downloaded` 是北极星分子；断言接口 200 完全不能说明它在写。
        `pack=False` + `count=1` 是单张下载的口径（契约 §4 E11）。
        """
        asset_id = _upload(client, _blob()).json()["id"]
        client.get(f"/api/v1/assets/{asset_id}/content", params={"download": 1})

        events = _events(db, EventName.IMAGE_DOWNLOADED)
        assert len(events) == 1
        assert events[0].props["pack"] is False
        assert events[0].props["count"] == 1
        assert events[0].props["scope"] == "selected"

    def test_inline_preview_does_not_emit_event(self, client, mem_storage, db):
        """预览不算交付：否则在画廊里划一遍就等于"交付"了几十张，北极星随之失真。"""
        asset_id = _upload(client, _blob()).json()["id"]
        client.get(f"/api/v1/assets/{asset_id}/content")
        assert _events(db, EventName.IMAGE_DOWNLOADED) == []


class TestAssetUpdate:
    """标记采纳（FR-2.7，良品率的唯一数据来源）与收藏（FR-5.3）。"""

    def test_adopt_output_succeeds_and_emits_event(self, client, mem_storage, db, user, workflow):
        task = _make_task(db, user, workflow)
        asset = _make_output(db, user, task, mem_storage, _blob())

        resp = client.patch(f"/api/v1/assets/{asset.id}", json={"is_adopted": True})
        assert resp.status_code == 200, resp.text
        assert resp.json()["is_adopted"] is True
        assert db.get(Asset, asset.id).is_adopted is True

        events = _events(db, EventName.IMAGE_ADOPTED)
        assert len(events) == 1
        assert events[0].task_id == task.id
        # E10 冻结的 4 个属性：task_id · image_id · workflow · template_id
        assert events[0].props["image_id"] == asset.id
        assert events[0].props["workflow"] == workflow.name
        assert "template_id" in events[0].props

    def test_repeated_adopt_does_not_duplicate_event(self, client, mem_storage, db, user, workflow):
        """⭐ 良品率 = `image_adopted 数 / succeeded 图数`。

        重复 PATCH true 若每次都埋点，用户点两下就能把良品率灌到 100% 以上 ——
        指标一旦可以被用户的操作次数污染，就不再是质量指标。
        """
        task = _make_task(db, user, workflow)
        asset = _make_output(db, user, task, mem_storage, _blob())
        for _ in range(3):
            assert (
                client.patch(f"/api/v1/assets/{asset.id}", json={"is_adopted": True}).status_code
                == 200
            )
        assert len(_events(db, EventName.IMAGE_ADOPTED)) == 1

    def test_unadopt_updates_field_without_event(self, client, mem_storage, db, user, workflow):
        """取消采纳：字段正常变，但**不该**凭空造一个契约里没有的事件。

        口径是"累计采纳过多少张"，不是"当前有多少张处于采纳态"。
        """
        task = _make_task(db, user, workflow)
        asset = _make_output(db, user, task, mem_storage, _blob())
        client.patch(f"/api/v1/assets/{asset.id}", json={"is_adopted": True})

        resp = client.patch(f"/api/v1/assets/{asset.id}", json={"is_adopted": False})
        assert resp.status_code == 200 and resp.json()["is_adopted"] is False
        assert len(_events(db, EventName.IMAGE_ADOPTED)) == 1

    def test_adopt_upload_asset_is_409(self, client, mem_storage):
        """上传素材没有"这次生成得好不好"的语义 —— 放开会让良品率的分子分母跨集合。"""
        asset_id = _upload(client, _blob()).json()["id"]
        resp = client.patch(f"/api/v1/assets/{asset_id}", json={"is_adopted": True})
        assert resp.status_code == 409, resp.text
        assert "产物" in resp.json()["detail"]

    def test_empty_body_is_422(self, client, mem_storage):
        """空 body 几乎总是前端拼错了字段名；静默 200 会让它以为"标记成功"。"""
        asset_id = _upload(client, _blob()).json()["id"]
        assert client.patch(f"/api/v1/assets/{asset_id}", json={}).status_code == 422

    def test_other_users_asset_is_404(self, client, mem_storage, db):
        theirs = _other_users_asset(db, mem_storage)
        resp = client.patch(f"/api/v1/assets/{theirs.id}", json={"is_favorite": True})
        assert resp.status_code == 404

    def test_deleted_asset_is_409(self, client, mem_storage):
        """回收站里的素材不能被标记：它在其它接口上都"不存在"，这里不该例外。"""
        asset_id = _upload(client, _blob()).json()["id"]
        client.delete(f"/api/v1/assets/{asset_id}")
        resp = client.patch(f"/api/v1/assets/{asset_id}", json={"is_favorite": True})
        assert resp.status_code == 409

    def test_favorite_writes_audit_log(self, client, mem_storage, db):
        """FR-1.4 留痕：采纳/收藏是用户侧状态变更，同样要进审计日志。"""
        asset_id = _upload(client, _blob()).json()["id"]
        client.patch(f"/api/v1/assets/{asset_id}", json={"is_favorite": True})

        logs = list(
            db.scalars(select(AuditLog).where(AuditLog.action == "asset.favorite")).all()
        )
        assert len(logs) == 1
        assert logs[0].target_id == str(asset_id)


class TestAssetPack:
    """打包下载（FR-5.2 / FR-3.7）。"""

    def _two_outputs(self, client, mem_storage, db, user, workflow, sku="SKU-A"):
        """上传两张图，再造两条**引用同样字节**的产物（带同一个 task_id 与 sku）。"""
        task = _make_task(db, user, workflow, params={"sku": sku})
        first = _make_output(db, user, task, mem_storage, _blob())
        second = _make_output(db, user, task, mem_storage, _blob(640))
        return task, first, second

    def test_pack_by_asset_ids_is_a_valid_zip(self, client, mem_storage, db, user, workflow):
        """⭐ 必须真的能当 zip 打开 —— 只断言 200 的话，返回一坨垃圾也算过。"""
        _, first, second = self._two_outputs(client, mem_storage, db, user, workflow)

        resp = client.post(
            "/api/v1/assets/pack", json={"asset_ids": [first.id, second.id]}
        )
        assert resp.status_code == 200, resp.text
        assert resp.headers["content-type"] == "application/zip"

        with zipfile.ZipFile(BytesIO(resp.content)) as zf:
            assert zf.testzip() is None  # CRC 自检：内容完整
            names = zf.namelist()
            assert len(names) == 2
            # FR-3.7「按 SKU 分目录」
            assert all(name.startswith("SKU-A/") for name in names)
            assert sorted(names) == sorted(
                [f"SKU-A/{first.id}.png", f"SKU-A/{second.id}.png"]
            )

    def test_pack_mixing_another_users_asset_is_404(self, client, mem_storage, db, user, workflow):
        """⭐ 不静默少给：用户明确要了 3 张，少 1 张他当时不会发现，发现时已经晚了。"""
        _, first, second = self._two_outputs(client, mem_storage, db, user, workflow)
        theirs = _other_users_asset(db, mem_storage)

        resp = client.post(
            "/api/v1/assets/pack",
            json={"asset_ids": [first.id, second.id, theirs.id]},
        )
        assert resp.status_code == 404, resp.text
        assert "1" in resp.json()["detail"]

    def test_pack_asset_ids_pointing_at_deleted_is_404(self, client, mem_storage, db, user, workflow):
        """回收站里的素材在点名下载时算"不存在"（404），与详情/取图口径一致。"""
        _, first, _ = self._two_outputs(client, mem_storage, db, user, workflow)
        client.delete(f"/api/v1/assets/{first.id}")

        resp = client.post("/api/v1/assets/pack", json={"asset_ids": [first.id]})
        assert resp.status_code == 404

    def test_pack_over_limit_is_422(self, client, mem_storage, db, user, workflow, monkeypatch):
        """上限是内存/磁盘护栏（EX-4）。这里把上限改成 1，避免真造 200 条记录。"""
        from app.core.config import settings

        _, first, second = self._two_outputs(client, mem_storage, db, user, workflow)
        monkeypatch.setattr(settings, "max_pack_assets", 1)

        resp = client.post(
            "/api/v1/assets/pack", json={"asset_ids": [first.id, second.id]}
        )
        assert resp.status_code == 422, resp.text
        assert "1" in resp.json()["detail"]  # 原因要说明上限值

    def test_pack_empty_selection_is_422(self, client, mem_storage, db, user, workflow):
        """任务没有产出图是正常的，但**空 zip 不是**：用户无从判断是"还没出图"还是"下载坏了"。"""
        task = _make_task(db, user, workflow)
        resp = client.post("/api/v1/assets/pack", json={"task_id": task.id})
        assert resp.status_code == 422, resp.text
        assert "没有可下载" in resp.json()["detail"]

    def test_pack_by_task_id_returns_all_outputs(self, client, mem_storage, db, user, workflow):
        """按任务打包：scope 是 `all`（整任务的产物），与"勾选"区分开。"""
        task, first, second = self._two_outputs(client, mem_storage, db, user, workflow)

        resp = client.post("/api/v1/assets/pack", json={"task_id": task.id})
        assert resp.status_code == 200, resp.text
        with zipfile.ZipFile(BytesIO(resp.content)) as zf:
            assert len(zf.namelist()) == 2

        events = _events(db, EventName.IMAGE_DOWNLOADED)
        assert len(events) == 1
        assert events[0].task_id == task.id

    def test_pack_by_batch_id_collects_all_tasks(self, client, mem_storage, db, user, workflow):
        batch = Batch(user_id=user.id, workflow_id=workflow.id, name="b1", common_params={})
        db.add(batch)
        db.flush()
        for sku in ("SKU-X", "SKU-Y"):
            task = _make_task(db, user, workflow, params={"sku": sku}, batch_id=batch.id)
            _make_output(db, user, task, mem_storage, _blob())

        resp = client.post("/api/v1/assets/pack", json={"batch_id": batch.id})
        assert resp.status_code == 200, resp.text
        with zipfile.ZipFile(BytesIO(resp.content)) as zf:
            names = sorted(zf.namelist())
        assert len(names) == 2
        assert {name.split("/")[0] for name in names} == {"SKU-X", "SKU-Y"}

    def test_other_users_task_is_404(self, client, mem_storage, db, user, workflow):
        """按 task_id 打包要先校验任务归属，否则等于用 task_id 遍历别人的产物。"""
        other = User(
            email="stranger@example.com", password_hash="x", role=UserRole.USER.value,
            quota_total=10, quota_used=0,
        )
        db.add(other)
        db.commit()
        theirs = _make_task(db, other, workflow)

        resp = client.post("/api/v1/assets/pack", json={"task_id": theirs.id})
        assert resp.status_code == 404

    def test_pack_emits_event_with_pack_true_and_real_count(self, client, mem_storage, db, user, workflow):
        """⭐ 北极星分子是 `Σ image_downloaded.count`，所以 count 必须是**实际张数**。"""
        _, first, second = self._two_outputs(client, mem_storage, db, user, workflow)

        resp = client.post(
            "/api/v1/assets/pack", json={"asset_ids": [first.id, second.id]}
        )
        assert resp.status_code == 200

        events = _events(db, EventName.IMAGE_DOWNLOADED)
        assert len(events) == 1
        assert events[0].props["pack"] is True
        assert events[0].props["count"] == 2
        assert events[0].props["scope"] == "selected"

    @pytest.mark.parametrize("sku", ["../../evil", "a/b", "..", "///", "C:\\temp\\x"])
    def test_pack_sku_cannot_escape_the_zip(
        self, client, mem_storage, db, user, workflow, sku
    ):
        """⭐ zip-slip 防护回归。

        SKU 是**用户可控**字符串。直接拼进 zip 条目名，就能产出
        `../../.ssh/authorized_keys` 这种条目，用户在本机解压时把文件写到压缩包之外。
        这里断言条目名里既没有 `..` 也没有多余的分隔符 —— 目录只能有一层。
        """
        task = _make_task(db, user, workflow, params={"sku": sku})
        asset = _make_output(db, user, task, mem_storage, _blob())

        resp = client.post("/api/v1/assets/pack", json={"asset_ids": [asset.id]})
        assert resp.status_code == 200, resp.text

        with zipfile.ZipFile(BytesIO(resp.content)) as zf:
            names = zf.namelist()
        assert len(names) == 1
        name = names[0]
        assert ".." not in name
        assert "\\" not in name
        assert not name.startswith("/")
        parts = name.split("/")
        assert len(parts) == 2, name  # 只有我们自己的那一层分隔符
        assert parts[1] == f"{asset.id}.png"

    def test_pack_falls_back_when_sku_is_blank(self, client, mem_storage, db, user, workflow):
        """SKU 为空（没填）时要回退到 `task_{id}`，而不是产出 `/5.png` 这种以分隔符开头的条目。"""
        task = _make_task(db, user, workflow, params={})
        asset = _make_output(db, user, task, mem_storage, _blob())

        resp = client.post("/api/v1/assets/pack", json={"asset_ids": [asset.id]})
        with zipfile.ZipFile(BytesIO(resp.content)) as zf:
            names = zf.namelist()
        assert names == [f"task_{task.id}/{asset.id}.png"]
