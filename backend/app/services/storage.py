"""产物与素材的对象存储（P6-02 建立抽象，P6-10 补全生命周期）。

P6-10 负责「MinIO + 过期清理 + 受控取图」的完整生命周期。这里提供执行层与
生命周期任务都需要的三个原语：`put` / `get` / `delete`，并把它们做成
**可替换的抽象**，好让 worker 与清理任务在测试里不必依赖 MinIO。

设计要点：
1. **object_key 由 ID 生成，绝不接受用户传入的路径**（NFR-4 路径遍历防护）。
   key 形如 `outputs/{user_id}/{task_id}/{uuid}.png`。
2. 计算 `sha256` 并随返回值给出 —— 它同时喂给两处：
   `assets.meta.model_sha256` 一类的可复现性元数据（FR-5.4 / C1）与去重判断。
3. MinIO 不可用时**不静默降级到本地磁盘**：产物是用户的核心资产，
   悄悄写到容器本地磁盘会随实例释放一起丢。宁可让任务失败并重试。
4. **`delete` 必须幂等**（删不存在的对象算成功）。理由见该方法的 docstring ——
   它是「清理任务不会被一条坏数据永久卡死」的前提。
5. **取图不经由本模块暴露预签名 URL**：V1 经 SSH 隧道访问，浏览器到不了 MinIO，
   且对象键不该进接口契约。取图走 `api/v1/assets.py` 的受控接口（见该模块说明）。
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

    @abc.abstractmethod
    def get(self, key: str) -> bytes:
        """读回对象字节。

        用途是「把用户上传的素材取出来、转交给 ComfyUI」（参考图类工作流的必需路径）。
        **bucket 由具体实现自己决定**（见 `MinioAssetStorage.__init__`），
        因此 `object_key` 是**与存储实现无关**的，可以安全地存进数据库。
        """

    @abc.abstractmethod
    def delete(self, key: str) -> None:
        """删除对象。**必须幂等**：对象本就不存在时算成功，不得抛异常。

        ⚠️ 幂等不是"顺手加的宽容"，而是清理任务能收敛的前提。清理任务的顺序是
        「先删对象、再删 DB 记录」——由此产生两种必然出现的重入场景：

        - 上一次清理删掉了对象、但删记录时进程被杀 → 下一次会**再删一次不存在的对象**；
        - 上传流程写入了对象、DB 提交却失败 → 该对象是孤儿，清理时同样"本来就没有记录"。

        若 `delete` 对不存在的对象抛异常，这两条路径都会**永久失败**：
        记录删不掉、下一轮还会重挑到同一条，清理任务从此不再推进任何东西（且不报错）。

        实现注意事项：S3/MinIO 的 `RemoveObject` 本身就符合该语义；
        进程内实现用 `pop(key, None)`，不要用 `del`。
        """

    def close(self) -> None:  # pragma: no cover - 默认无资源可释放
        return None

    def __enter__(self) -> AssetStorage:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


class MinioAssetStorage(AssetStorage):
    """MinIO（S3 兼容）实现。客户端**懒创建**，避免 import 期就连外部服务。

    ⚠️ **`Object_key` 刻意不带 bucket 前缀**：bucket 是部署配置（`outputs` / `uploads`
    由设置决定），不是对象标识的一部分。若把 bucket 写进 key，`asset.object_key`
    就绑定了具体存储实现 —— 换后端（S3 / OSS）或多桶布局时全表数据都得迁移，
    而这正是抽象层本该挡住的。需要不同桶时，按用途构造不同实例即可。
    """

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
            object_key=key,
            size_bytes=len(data),
            mime_type=content_type,
            sha256=hashlib.sha256(data).hexdigest(),
        )

    def get(self, key: str) -> bytes:
        client = self._ensure_bucket()
        response = None
        try:
            response = client.get_object(self._bucket, key)
            return response.read()
        finally:
            if response is not None:
                response.close()
                response.release_conn()

    def delete(self, key: str) -> None:
        """S3 的 `RemoveObject` 对不存在的 key 返回成功 —— 天然满足幂等要求。"""
        client = self._ensure_bucket()
        client.remove_object(self._bucket, key)


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

    def get(self, key: str) -> bytes:
        if key not in self.objects:
            raise KeyError(f"对象不存在：{key}")
        return self.objects[key]

    def delete(self, key: str) -> None:
        # 用 pop 而不是 del：见 ABC 上 `delete` 的幂等要求。
        self.objects.pop(key, None)


def build_output_key(user_id: int, task_id: int, extension: str = "png") -> str:
    """生成产物 key。**只由 ID 与随机串拼成，不含任何用户输入**（NFR-4）。"""
    ext = (extension or "png").lstrip(".").lower()
    if ext not in _MIME_BY_EXT:
        ext = "png"  # 白名单外的扩展名一律按 png 处理，避免把用户输入带进存储路径
    return f"outputs/{int(user_id)}/{int(task_id)}/{uuid.uuid4().hex}.{ext}"


def build_upload_key(user_id: int, extension: str = "png") -> str:
    """生成上传素材的 key（P6-09）。

    与 `build_output_key` 同一条纪律：**路径里不含任何用户输入**
    （原始文件名只存 DB 的 `original_name` 字段，不进存储路径）——
    这是 NFR-4 的硬要求：用户可控的字符串一旦进对象键，就会出现
    `../` 穿越、超长名、编码歧义这一整类问题。
    """
    ext = (extension or "png").lstrip(".").lower()
    if ext not in _MIME_BY_EXT:
        ext = "png"  # 同上：白名单外一律按 png，不让用户输入影响路径
    return f"uploads/{int(user_id)}/{uuid.uuid4().hex}.{ext}"


def mime_for_filename(filename: str) -> str:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "png"
    return _MIME_BY_EXT.get(ext, "application/octet-stream")
