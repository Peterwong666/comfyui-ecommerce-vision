"""素材上传（P6-09）与图片头解析的测试。

分两部分：
1. **`image_probe`** —— 按魔数判格式 + 从头部取尺寸。这是新写的、且**安全相关**
   （EX-10 要防"改后缀混进来"），所以测得比较细，含两个"看起来能过其实是错的"的陷阱
2. **上传接口** —— 四层校验（体积 / 格式 / 尺寸 / 落库）逐层验，且**原因必须具体**
"""

from __future__ import annotations

import struct

import pytest

from app.services.image_probe import ImageInfo, UnsupportedImage, probe

# ============================================================
# 测试用图片头构造器（不依赖 Pillow，只造**头部**）
#
# probe 只读头部，所以这里不需要造完整可解码的图 —— 这正是该模块的设计前提。
# ============================================================


def png_header(width: int, height: int) -> bytes:
    """PNG：签名 + IHDR 长度 + "IHDR" + 宽 + 高。"""
    return (
        b"\x89PNG\r\n\x1a\n"
        + struct.pack(">I", 13)
        + b"IHDR"
        + struct.pack(">II", width, height)
        + b"\x08\x06\x00\x00\x00"  # 位深/色型/压缩/滤波/隔行
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
