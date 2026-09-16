"""产物元数据写入 / 读取（任务 **P3-11**）—— 可复现性的落点。

对应需求：**FR-5.4**「元数据查看（seed / 模型 / 参数）」· **FR-5.5**「用同参数再生成」
口径依据：`docs/prd/tracking_plan.md`（服务端埋点只记提示词长度与 hash）

---

### 三个刻意的设计决策

**① 只拼接 PNG chunk，不重新编码图片。**

常见的做法是 `Pillow: Image.open() → save(pnginfo=...)`，但那会**整图解码再编码**：
- 产物字节会变（zlib 级别/滤波策略不同）→ 与 ComfyUI 原始产出不一致，
  破坏「产物 hash 可比对」与去重能力
- 每张图多一次全图解码+编码（1MP 约几十毫秒）→ 量产场景下是纯开销

本模块改为**定位 IEND chunk 并在其前插入新的 tEXt / iTXt**，
图像数据（IDAT）**逐字节不动**。这一点的正确性可离线证明（见 §测试）。

**② 不依赖 Pillow。** 全部用标准库（`struct` + `binascii`）实现，
`engine/` 因此保持**零第三方依赖**，A 流导入时不必关心虚拟环境里装了什么。
（Pillow 只在测试里用来做「独立第三方读回」的交叉验证。）

**③ 分两类 chunk 写。**

| chunk | key | 内容 | 为什么 |
|---|---|---|---|
| `iTXt`（UTF-8） | `wf_meta` | 完整元数据 JSON（含中文提示词） | tEXt 只能存 latin-1，中文提示词必须走 iTXt |
| `tEXt`（latin-1） | `workflow_id` / `seed` / `models` / … | 少量 ASCII 标量 | tEXt 是通用性最好的字段，多数图片查看器能直接显示 |

ComfyUI 自己写的 `prompt` / `workflow` 两个 chunk **原样保留**（它们是引擎侧的可复现凭据，
我们只做追加）。

---

### 与埋点的分工（`tracking_plan.md` §1.2）

| 场景 | 提示词全文 | 说明 |
|---|---|---|
| 产物 PNG 元数据（用户自己的文件） | ✅ 写入 | FR-5.5 需要它才能"同参数再生成" |
| 服务端埋点事件 E01~E12 | ❌ 只记长度 + sha256 | 隐私最小化原则 |

**同一个提示词「写给用户」但不「写给埋点」。**
两种口径都落在这里：`build_metadata(include_prompt=False)` 会剔除提示词全文，
但**始终附带 `prompt_digest`**（长度 + sha256），使产物与服务端日志可以交叉核对。
"""

from __future__ import annotations

import binascii
import hashlib
import hmac
import json
import pathlib
import struct
import tempfile
from collections.abc import Iterable, Iterator, Mapping
from datetime import UTC, datetime
from typing import Any

from engine.errors import MetadataError

#: 元数据 JSON 的格式版本。字段增删**向后兼容**时可不变；破坏性改动必须递增。
METADATA_SCHEMA_VERSION = 1

#: 提示词摘要所用的算法标识。
#:
#: ⚠️ **必须与 `_prompt_digest()` 的实际构造一致** —— 它存在的意义就是
#: "让两份来源不同的摘要能判断是否可比"，标错等于给了错误的比对许可。
#: 当前构造是 **HMAC-SHA256**（`hmac.new(salt, text, sha256)`）。
#: 换构造（如改回加盐前缀式、或换 BLAKE3）**必须同时改这里的字符串**。
PROMPT_DIGEST_ALG = "hmac-sha256"

#: 完整元数据 JSON 的 chunk 关键字（iTXt）
METADATA_JSON_KEY = "wf_meta"

#: 以 tEXt 写出的 ASCII 标量字段 → 元数据 dict 里的取值路径
SCALAR_TEXT_KEYS: tuple[tuple[str, str], ...] = (
    ("workflow_id", "workflow_id"),
    ("workflow_version", "workflow_version"),
    ("seed", "seed"),
    ("models", "models"),
    ("task_id", "task_id"),
    ("batch_id", "batch_id"),
    ("idx", "idx"),
    ("created_at", "created_at"),
    ("metadata_schema_version", "schema_version"),
)

#: 视为「提示词」的参数字段名。用于计算 digest / 决定是否写入全文。
PROMPT_KEYS: tuple[str, ...] = ("prompt", "negative_prompt")

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


# ============================================================ 元数据构造


def _prompt_digest(text: str, salt: str | bytes | None) -> str:
    """提示词摘要（**HMAC-SHA256**）。

    ⚠️ **为什么加盐**：埋点侧只记 hash 时，若用**不加盐**的裸 sha256，
    短提示词会被彩虹表 / 暴力枚举还原（`"白色杯子"` 这类候选空间很小）。
    加盐后摘要不可逆，同时仍可用于分布统计与去重。

    ⚠️ **为什么是 HMAC 而不是 `sha256(salt ‖ text)`**：
    后者对**长度扩展攻击**脆弱 —— 已知 `sha256(salt‖P)` 的人可以算出
    `sha256(salt‖P‖padding‖X)`，即**为另一个（更长的）提示词伪造出合法摘要**。
    对我们的用途威胁不高，但 HMAC 是**零成本的标准构造**，没有理由用弱的那一个。
    （2026-09-16 team-lead 批准改为 HMAC；当时无生产数据，改动成本为零。）

    ⚠️ **跨系统比对的前提**：产物元数据里的摘要若要与服务端事件的摘要对上，
    两侧必须用**同一个盐 + 同一个算法**。盐取自配置（如 `settings.prompt_hash_salt`），
    **不要写死在代码里** —— 换盐等于作废全部历史摘要，必须是一次有意识的迁移。
    """
    return hmac.new(
        b"" if salt is None else (salt.encode("utf-8") if isinstance(salt, str) else salt),
        text.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def build_metadata(
    *,
    workflow_id: str,
    workflow_version: int,
    params: Mapping[str, Any],
    seed: int | None = None,
    models: Iterable[str] = (),
    task_id: int | None = None,
    batch_id: int | None = None,
    idx: int | None = None,
    schema_version: int = METADATA_SCHEMA_VERSION,
    created_at: str | None = None,
    prompt_keys: tuple[str, ...] = PROMPT_KEYS,
    include_prompt: bool = True,
    hash_salt: str | bytes | None = None,
) -> dict[str, Any]:
    """组装写入产物的元数据字典（纯函数，不碰文件系统，便于单测）。

    * `params` 应传**渲染后**生效的完整参数（`RenderResult.params`），
      这样元数据里记录的 seed 是**解析后的真实值**而不是 `-1`。
    * `include_prompt=False` 时剔除提示词全文，但仍写入 `prompt_digest`。
    * `hash_salt` 建议由调用方从 settings 传入（见 `_prompt_digest`）。
      摘要里会附带 `salted` 标志，使"加盐/未加盐"的摘要在数据上也**可区分** ——
      否则两份来源不同的摘要混在一起比对，会得出"提示词不同"的错误结论。
    """
    params_out: dict[str, Any] = dict(params)
    prompt_digest: dict[str, dict[str, Any]] = {}

    for key in prompt_keys:
        value = params_out.get(key)
        if isinstance(value, str):
            prompt_digest[key] = {
                "sha256": _prompt_digest(value, hash_salt),
                "length": len(value),
                "salted": hash_salt is not None,
                # 算法标识：跨系统比对的前提是**同盐 + 同算法**。
                # 少了它，将来换了构造（如 HMAC→BLAKE3）后新旧摘要会被直接比对，
                # 得出"提示词不同"的错误结论（与 `salted` 标志同一个理由）。
                "alg": PROMPT_DIGEST_ALG,
            }

    if not include_prompt:
        for key in prompt_keys:
            params_out.pop(key, None)

    meta: dict[str, Any] = {
        "schema_version": schema_version,
        "workflow_id": workflow_id,
        "workflow_version": workflow_version,
        "seed": seed,
        "models": list(models),
        "task_id": task_id,
        "batch_id": batch_id,
        "idx": idx,
        "created_at": created_at or datetime.now(UTC).isoformat(timespec="seconds"),
        "params": params_out,
    }
    if prompt_digest:
        meta["prompt_digest"] = prompt_digest
        if not include_prompt:
            meta["prompt_included"] = False
    return meta


# ============================================================ PNG chunk 底层


def _make_chunk(chunk_type: bytes, data: bytes) -> bytes:
    crc = binascii.crc32(chunk_type + data) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + chunk_type + data + struct.pack(">I", crc)


def _itxt_chunk(keyword: str, value: str, *, lang: str = "", translated: str = "") -> bytes:
    """iTXt：UTF-8，支持中文。"""
    kw = keyword.encode("latin-1")
    if not 1 <= len(kw) <= 79:
        raise MetadataError(f"iTXt 关键字长度非法: {keyword!r}")
    data = (
        kw
        + b"\x00"
        + b"\x00"  # compression flag = 未压缩
        + b"\x00"  # compression method
        + lang.encode("ascii")
        + b"\x00"
        + translated.encode("utf-8")
        + b"\x00"
        + value.encode("utf-8")
    )
    return _make_chunk(b"iTXt", data)


def _text_chunk(keyword: str, value: str) -> bytes:
    """tEXt：latin-1，通用性最好但存不了中文。"""
    kw = keyword.encode("latin-1")
    if not 1 <= len(kw) <= 79:
        raise MetadataError(f"tEXt 关键字长度非法: {keyword!r}")
    return _make_chunk(b"tEXt", kw + b"\x00" + value.encode("latin-1"))


def _iter_chunks(raw: bytes) -> Iterator[tuple[int, bytes, int, bytes]]:
    """按顺序解析 PNG chunk，产出 `(起始偏移, 类型, 数据长度, 数据)`。

    顺序解析而不是 `rfind(b"IEND")`：IEND 的字节序列理论上可能出现在
    IDAT 的压缩数据里，而顺序解析是 PNG 规范定义的方式，不存在该风险。
    """
    if not raw.startswith(PNG_SIGNATURE):
        raise MetadataError("不是合法的 PNG（签名不匹配）")
    pos = len(PNG_SIGNATURE)
    total = len(raw)
    while pos + 8 <= total:
        (length,) = struct.unpack(">I", raw[pos : pos + 4])
        chunk_type = raw[pos + 4 : pos + 8]
        data_start = pos + 8
        data_end = data_start + length
        if data_end + 4 > total:
            raise MetadataError(f"PNG 结构损坏：chunk {chunk_type!r} 声明长度 {length} 超出文件")
        yield pos, chunk_type, length, raw[data_start:data_end]
        pos = data_end + 4
        if chunk_type == b"IEND":
            return
    raise MetadataError("PNG 结构损坏：找不到 IEND chunk")


def _parse_keyword(chunk_type: bytes, data: bytes) -> str | None:
    """取出 tEXt / iTXt 的关键字；其它类型返回 None。"""
    if chunk_type not in (b"tEXt", b"iTXt", b"zTXt"):
        return None
    nul = data.find(b"\x00")
    if nul <= 0:
        return None
    try:
        return data[:nul].decode("latin-1")
    except UnicodeDecodeError:  # pragma: no cover
        return None


def _parse_text_value(chunk_type: bytes, data: bytes) -> str | None:
    """取出 tEXt / iTXt 的文本内容。zTXt（压缩）不支持，返回 None。"""
    nul = data.find(b"\x00")
    if nul < 0:
        return None
    body = data[nul + 1 :]
    if chunk_type == b"tEXt":
        try:
            return body.decode("latin-1")
        except UnicodeDecodeError:  # pragma: no cover
            return None
    if chunk_type == b"iTXt":
        if len(body) < 2:
            return None
        flag = body[0]
        if flag != 0:
            return None  # 压缩的 iTXt 不处理（我们写出的从不压缩）
        rest = body[2:]
        # lang\0translated\0text
        for _ in range(2):
            n = rest.find(b"\x00")
            if n < 0:
                return None
            rest = rest[n + 1 :]
        return rest.decode("utf-8", errors="replace")
    return None


#: 我们认为「属于本模块」的 chunk 关键字 —— 重写时要先剔除，避免重复堆积
def _owned_keywords() -> set[str]:
    return {METADATA_JSON_KEY} | {k for k, _ in SCALAR_TEXT_KEYS}


def _build_chunks(meta: Mapping[str, Any]) -> bytes:
    """把元数据字典转成待插入的 chunk 字节串。"""
    chunks = bytearray()
    chunks += _itxt_chunk(
        METADATA_JSON_KEY,
        json.dumps(meta, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
    )

    for chunk_key, meta_path in SCALAR_TEXT_KEYS:
        value = meta.get(meta_path)
        if value is None:
            continue
        if isinstance(value, (list, tuple)):
            value = ",".join(str(v) for v in value)
        text = str(value)
        if not text.isascii():
            # 中文等非 latin-1 内容无法进 tEXt；不静默丢弃，交给 iTXt 承载
            continue
        chunks += _text_chunk(chunk_key, text)
    return bytes(chunks)


# ============================================================ 对外 API


def write_png_metadata(path: str | pathlib.Path, meta: Mapping[str, Any]) -> None:
    """把元数据写进 PNG（原地、原子替换）。

    * **图像数据逐字节不变** —— 只插入 / 替换 chunk
    * 会先剔除本模块此前写过的 chunk（幂等：重复调用不会堆积）
    * ComfyUI 写的 `prompt` / `workflow` chunk 原样保留
    """
    p = pathlib.Path(path)
    try:
        raw = p.read_bytes()
    except OSError as exc:
        raise MetadataError(f"读取产物失败: {p} —— {exc}") from exc

    owned = _owned_keywords()
    out = bytearray(PNG_SIGNATURE)
    inserted = False
    for pos, chunk_type, length, data in _iter_chunks(raw):
        keyword = _parse_keyword(chunk_type, data)
        if chunk_type == b"IEND" and not inserted:
            out += _build_chunks(meta)
            inserted = True
        if keyword is not None and keyword in owned:
            continue  # 剔除旧的本模块 chunk
        out += raw[pos : pos + 12 + length]

    if not inserted:  # pragma: no cover —— _iter_chunks 保证会遇到 IEND
        raise MetadataError(f"未能定位 IEND，写入中止: {p}")

    _atomic_write(p, bytes(out))


def read_png_metadata(path: str | pathlib.Path) -> dict[str, Any]:
    """读出本模块写入的元数据。

    与 `write_png_metadata` 对称，用于**回读验证**（A 流保存产物后自检、
    以及 `engine/tests` 的正确性证明）。返回空 dict 表示产物没有我们的元数据。
    """
    p = pathlib.Path(path)
    try:
        raw = p.read_bytes()
    except OSError as exc:
        raise MetadataError(f"读取产物失败: {p} —— {exc}") from exc

    result: dict[str, Any] = {}
    for _pos, chunk_type, _length, data in _iter_chunks(raw):
        keyword = _parse_keyword(chunk_type, data)
        if keyword != METADATA_JSON_KEY:
            continue
        value = _parse_text_value(chunk_type, data)
        if value is None:
            raise MetadataError(f"wf_meta chunk 无法解析: {p}")
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise MetadataError(f"wf_meta 不是合法 JSON: {p} —— {exc}") from exc
        if not isinstance(parsed, dict):
            raise MetadataError(f"wf_meta 顶层必须是对象: {p}")
        result = parsed
    return result


def read_png_text_chunks(path: str | pathlib.Path) -> dict[str, str]:
    """读出**全部** tEXt/iTXt 文本 chunk（含 ComfyUI 写的 prompt/workflow）。

    用于排障：确认我们追加元数据后，引擎自己写的 chunk 仍在。
    """
    raw = pathlib.Path(path).read_bytes()
    out: dict[str, str] = {}
    for _pos, chunk_type, _length, data in _iter_chunks(raw):
        keyword = _parse_keyword(chunk_type, data)
        if keyword is None:
            continue
        value = _parse_text_value(chunk_type, data)
        if value is not None:
            out[keyword] = value
    return out


def _atomic_write(p: pathlib.Path, payload: bytes) -> None:
    """写临时文件再 `os.replace`，避免写一半失败留下损坏的产物。

    产物是用户的交付物，**不能容忍半写状态**（对应 EX-13 的精神：
    保存失败要能被重试，而不是留下一个坏文件）。
    """
    try:
        with tempfile.NamedTemporaryFile(
            dir=str(p.parent), prefix=f".{p.name}.", suffix=".tmp", delete=False
        ) as fh:
            tmp = pathlib.Path(fh.name)
            fh.write(payload)
        tmp.replace(p)
    except OSError as exc:
        raise MetadataError(f"写入产物元数据失败: {p} —— {exc}") from exc


# ============================================================ 其它格式

#: 说明：V1 产物是 ComfyUI `SaveImage` 输出的 **PNG**，所以只实现 PNGInfo。
#: JPEG/EXIF 在本项目当前链路里用不到（`workflow_spec.md` §4.2 规定唯一产物节点是 SaveImage）。
#: 若未来需要 JPEG 交付，`build_metadata()` 是格式无关的，可在其上新增 EXIF 写入，
#: 无需改动本模块的既有行为。
SUPPORTED_CONTAINERS: tuple[str, ...] = ("png",)
