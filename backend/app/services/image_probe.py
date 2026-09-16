"""只读图片头：取格式与尺寸，**不依赖 Pillow**（P6-09）。

为什么不引入 Pillow：
  · 契约要求校验「格式 ∈ {JPG, PNG, WebP}」与「尺寸 64×64 – 8192×8192」（PRD §6.2）
  · **用魔数判格式比看扩展名可靠** —— 扩展名可以撒谎，而 EX-10（上传非法文件）
    要防的正是"改个后缀就混进来"
  · 而魔数所在的那几个字节里**顺手就带着尺寸**，于是两件事一次做完、零新增依赖

与 `engine/metadata.py` 同源的取舍：那一层为了"不重新编码 PNG"而手写 chunk 拼接，
这一层为了"不引入重依赖"而手写头部解析。**都是拿几十行确定的代码换掉一个重依赖。**

⚠️ 本模块**只读头部**，不做完整解码。它能回答"这是不是一张合法的 PNG/JPEG/WebP、
多大尺寸"，**不能**回答"这张图能不能被完整解码"（截断的文件头部仍然合法）。
后者要靠引擎侧解码失败来兜（`ErrorType.INVALID_UPLOAD`）。
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

#: 允许上传的格式（PRD §6.2：仅 JPG / PNG / WebP）
ALLOWED_FORMATS = frozenset({"JPEG", "PNG", "WEBP"})


@dataclass(frozen=True)
class ImageInfo:
    """头部解析结果。"""

    format: str  #: JPEG | PNG | WEBP
    width: int
    height: int

    @property
    def mime_type(self) -> str:
        return {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}[self.format]


class UnsupportedImage(Exception):
    """不是受支持的图片格式，或头部不足以判定尺寸。

    ⚠️ 调用方应把它转成 422（EX-10），**并把原因如实告诉用户** ——
    只说"上传失败"会让用户反复试同一个文件。
    """


_PNG_SIG = b"\x89PNG\r\n\x1a\n"
_JPEG_SIG = b"\xff\xd8"
_RIFF = b"RIFF"
_WEBP = b"WEBP"

#: JPEG 的 SOF 标记（Start Of Frame）—— 尺寸就在这里。
#: 排除 0xC4 (DHT) / 0xC8 (JPG) / 0xCC (DAC)：它们形似 SOF 但不含尺寸
_JPEG_SOF_MARKERS = frozenset(
    m for m in range(0xC0, 0xD0) if m not in (0xC4, 0xC8, 0xCC)
)


def probe(data: bytes) -> ImageInfo:
    """解析图片头，返回格式与尺寸。失败抛 `UnsupportedImage`。"""
    if len(data) < 16:
        raise UnsupportedImage("文件过小，不足以识别图片格式")

    if data.startswith(_PNG_SIG):
        return _probe_png(data)
    if data.startswith(_JPEG_SIG):
        return _probe_jpeg(data)
    if data[:4] == _RIFF and data[8:12] == _WEBP:
        return _probe_webp(data)

    raise UnsupportedImage("仅支持 JPG / PNG / WebP 格式（按文件内容判定，不看扩展名）")


def _probe_png(data: bytes) -> ImageInfo:
    """PNG：IHDR 必须是第一个 chunk，宽高为紧邻的两个大端 u32。"""
    # 0-7 签名 | 8-11 长度 | 12-15 "IHDR" | 16-19 宽 | 20-23 高
    if data[12:16] != b"IHDR":
        raise UnsupportedImage("PNG 头部异常：首个 chunk 不是 IHDR")
    width, height = struct.unpack(">II", data[16:24])
    return ImageInfo("PNG", int(width), int(height))


def _probe_jpeg(data: bytes) -> ImageInfo:
    """JPEG：在段链里找 SOF 标记，尺寸在其后第 3-6 字节。

    为什么不直接取固定偏移：JPEG 的段是变长且顺序不固定的（APPn/EXIF/量化表…），
    按固定偏移取会**在带 EXIF 的图上取错**，而带 EXIF 恰是相机/手机导出的常态。
    """
    pos = 2  # 跳过 SOI
    n = len(data)
    while pos + 4 <= n:
        if data[pos] != 0xFF:
            # 未对齐：向前找下一个标记，容忍填充字节
            pos += 1
            continue
        marker = data[pos + 1]
        if marker in (0xFF, 0x00):  # 填充或转义，跳过
            pos += 1
            continue
        if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:  # 无载荷
            pos += 2
            continue
        seg_len = struct.unpack(">H", data[pos + 2 : pos + 4])[0]
        if seg_len < 2:
            raise UnsupportedImage("JPEG 段长度非法")
        if marker in _JPEG_SOF_MARKERS:
            if pos + 9 > n:
                raise UnsupportedImage("JPEG 头部被截断，读不到尺寸")
            height, width = struct.unpack(">HH", data[pos + 5 : pos + 9])
            return ImageInfo("JPEG", int(width), int(height))
        pos += 2 + seg_len
    raise UnsupportedImage("JPEG 头部中未找到尺寸信息（SOF 标记缺失或文件被截断）")


def _probe_webp(data: bytes) -> ImageInfo:
    """WebP：三种子格式（有损 VP8 / 无损 VP8L / 扩展 VP8X），尺寸位置各不同。

    ⚠️ 长度检查**按分支各自做**，不用一句 `len(data) < 30` 一刀切：
    三种子格式真正需要的头部长度不同（VP8L 最小 25 字节、VP8X 需要 30），
    一刀切会**误拒**头部较短但合法的文件 —— 而误拒的表现是"这张图传不上去"，
    用户完全无从判断原因。
    """
    chunk = data[12:16]

    if chunk == b"VP8 ":
        # 有损：帧头 `9d 01 2a`（偏移 23）之后是 14 位宽 / 14 位高
        if len(data) < 30:
            raise UnsupportedImage("WebP(VP8) 头部不完整")
        if data[23:26] != b"\x9d\x01\x2a":
            raise UnsupportedImage("WebP(VP8) 帧头异常")
        w = struct.unpack("<H", data[26:28])[0] & 0x3FFF
        h = struct.unpack("<H", data[28:30])[0] & 0x3FFF
        return ImageInfo("WEBP", w, h)

    if chunk == b"VP8L":
        # 无损：签名 0x2f（偏移 20）后 4 字节里打包 14 位宽高（各减 1）→ 最小 25 字节
        if len(data) < 25:
            raise UnsupportedImage("WebP(VP8L) 头部不完整")
        if data[20] != 0x2F:
            raise UnsupportedImage("WebP(VP8L) 签名异常")
        bits = struct.unpack("<I", data[21:25])[0]
        w = (bits & 0x3FFF) + 1
        h = ((bits >> 14) & 0x3FFF) + 1
        return ImageInfo("WEBP", w, h)

    if chunk == b"VP8X":
        # 扩展：画布宽高为 24 位小端（各减 1），位于偏移 24 与 27 → 最小 30 字节
        if len(data) < 30:
            raise UnsupportedImage("WebP(VP8X) 头部不完整")
        w = int.from_bytes(data[24:27], "little") + 1
        h = int.from_bytes(data[27:30], "little") + 1
        return ImageInfo("WEBP", w, h)

    raise UnsupportedImage(f"未知的 WebP 子格式 {chunk!r}")


def is_supported_format(data: bytes) -> bool:
    """只要"是不是支持的格式"，不关心尺寸时用（省掉尺寸解析的失败面）。"""
    try:
        return probe(data).format in ALLOWED_FORMATS
    except UnsupportedImage:
        return False
