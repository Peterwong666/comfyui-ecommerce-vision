"""产物与素材的对象存储（P6-02 所需的最小实现）。

⚠️ **这不是 P6-10**。P6-10 负责「MinIO + 过期清理 + 签名 URL 下载」的完整生命周期；
这里只提供执行层落盘必需的一个 `put` 接口，并把它做成**可替换的抽象**，
好让 worker 在测试里不必依赖 MinIO、也让 P6-10 能平滑接管而不动 worker。

设计要点：
1. **object_key 由 ID 生成，绝不接受用户传入的路径**（NFR-4 路径遍历防护）。
   key 形如 `outputs/{user_id}/{task_id}/{uuid}.png`。
2. 计算 `sha256` 并随返回值给出 —— 它同时喂给两处：
   `assets.meta.model_sha256` 一类的可复现性元数据（FR-5.4 / C1）与去重判断。
3. MinIO 不可用时**不静默降级到本地磁盘**：产物是用户的核心资产，
   悄悄写到容器本地磁盘会随实例释放一起丢。宁可让任务失败并重试。
"""

from __future__ import annotations

import abc
import hashlib
import uuid
from dataclasses import dataclass
from io import BytesIO

from app.core.config import settings
from app.core.logging import get_logger

log = get_logger(__name__)

# 扩展名 → MIME。ComfyUI 的产物基本只有这两种。
_MIME_BY_EXT = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
}


@dataclass(frozen=True)
class StoredObject:
    object_key: str
    size_bytes: int
    mime_type: str
    sha256: str


class AssetStorage(abc.ABC):
    """对象存储的最小接口（P6-10 会在此基础上扩展）。"""

    @abc.abstractmethod
    def put(self, key: str, data: bytes, content_type: str) -> StoredObject:
        """写入并返回对象的元信息。失败应抛异常（不返回 False）。"""

    def close(self) -> None:  # pragma: no cover - 默认无资源可释放
        return None

    def __enter__(self) -> AssetStorage:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


class MinioAssetStorage(AssetStorage):
    """MinIO（S3 兼容）实现。客户端**懒创建**，避免 import 期就连外部服务。"""

    def __init__(
        self,
        endpoint: str | None = None,
        access_key: str | None = None,
        secret_key: str | None = None,
        secure: bool | None = None,
        bucket: str | None = None,
    ) -> None:
        self._endpoint = endpoint or settings.minio_endpoint
        self._access_key = access_key or settings.minio_access_key
        self._secret_key = secret_key or settings.minio_secret_key
        self._secure = settings.minio_secure if secure is None else secure
        self._bucket = bucket or settings.minio_bucket_outputs
        self._client = None

    def _ensure_bucket(self):  # type: ignore[no-untyped-def]
        if self._client is None:
            from minio import Minio  # 懒导入：不在无对象存储的环境里也 import 得起

            self._client = Minio(
                self._endpoint,
                access_key=self._access_key,
                secret_key=self._secret_key,
                secure=self._secure,
            )
        if not self._client.bucket_exists(self._bucket):
            self._client.make_bucket(self._bucket)
        return self._client

    def put(self, key: str, data: bytes, content_type: str) -> StoredObject:
        client = self._ensure_bucket()
        client.put_object(
            self._bucket,
            key,
            BytesIO(data),
            length=len(data),
            content_type=content_type,
        )
        return StoredObject(
            object_key=f"{self._bucket}/{key}",
            size_bytes=len(data),
            mime_type=content_type,
            sha256=hashlib.sha256(data).hexdigest(),
        )


class InMemoryAssetStorage(AssetStorage):
    """进程内实现。**只给测试与本地开发用**，进程退出即丢。"""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put(self, key: str, data: bytes, content_type: str) -> StoredObject:
        self.objects[key] = data
        return StoredObject(
            object_key=key,
            size_bytes=len(data),
            mime_type=content_type,
            sha256=hashlib.sha256(data).hexdigest(),
        )


def build_output_key(user_id: int, task_id: int, extension: str = "png") -> str:
    """生成产物 key。**只由 ID 与随机串拼成，不含任何用户输入**（NFR-4）。"""
    ext = (extension or "png").lstrip(".").lower()
    if ext not in _MIME_BY_EXT:
        ext = "png"  # 白名单外的扩展名一律按 png 处理，避免把用户输入带进存储路径
    return f"outputs/{int(user_id)}/{int(task_id)}/{uuid.uuid4().hex}.{ext}"


def mime_for_filename(filename: str) -> str:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "png"
    return _MIME_BY_EXT.get(ext, "application/octet-stream")
