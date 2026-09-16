"""GPU 独占位（P6-02 的并发控制）。

**为什么需要一个跨进程的锁，而不是只靠 Celery 的 `--concurrency=1`**：

NFR-2 规定 GPU 并发恒为 1。`--concurrency=1` 只是**部署配置上的保证**，
而配置会被改错（多起一个 worker、改个参数、加个 beat 消费同一个队列）。
一旦两张图同时上卡，SDXL(6.9G) + FLUX.2(7.3G) 的权重叠加会直接 OOM（EX-1），
OOM 又触发重试 —— 用户看到的现象是"平台不稳定"，根因却在部署参数上。

锁提供的是**机制上的保证**：无论有几个 worker，只有拿到锁的那个能上卡。

三个设计要点：
1. **拿不到锁 ≠ 出错**。返回 False，任务退回去等下一轮，状态仍是 `queued`。
2. **锁带 TTL 且不可续期**：worker 被 `kill -9` 后没有机会 release，
   只能靠 TTL 自动过期。TTL 取「单任务超时上限 + 余量」，因此正常运行中
   不可能提前过期。这与 T14「靠心跳超时被动检测僵尸」是同一套思路。
3. **后端不可达时绝不"降级为不加锁"**。宁可让任务退回队列重试。
   降级看似"保持可用"，实际是把唯一的硬约束悄悄关掉了。
"""

from __future__ import annotations

import abc
import threading
import time
import uuid

from app.core.config import settings
from app.core.logging import get_logger

log = get_logger(__name__)

# 比较后删除：只有当锁还属于自己时才删。
# 不能直接 DEL —— 万一自己的锁已过期、别人拿到了，DEL 会误删别人的锁，
# 于是两个 worker 同时上卡（正是这个模块要防的事）。
_RELEASE_SCRIPT = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
end
return 0
"""


class GpuLockUnavailable(Exception):
    """锁后端（Redis）不可达。

    **不要**把它当成"没拿到锁"——它是基础设施故障，调用方应当退回队列稍后重试，
    而不是绕过锁直接执行。
    """


class LockBackend(abc.ABC):
    """锁后端的抽象。抽出来是为了让 GpuSlot 的等待/超时/释放逻辑能被离线测试。"""

    @abc.abstractmethod
    def try_acquire(self, key: str, token: str, ttl_seconds: int) -> bool: ...

    @abc.abstractmethod
    def release(self, key: str, token: str) -> bool: ...

    @abc.abstractmethod
    def holder(self, key: str) -> str | None: ...


class RedisLockBackend(LockBackend):
    """基于 Redis `SET NX PX` 的实现。连接懒建，import 期不连外部服务。"""

    def __init__(self, redis_url: str | None = None) -> None:
        self._url = redis_url or settings.redis_url
        self._client = None
        self._lock = threading.Lock()

    def _conn(self):  # type: ignore[no-untyped-def]
        with self._lock:
            if self._client is None:
                import redis  # 懒导入

                self._client = redis.Redis.from_url(
                    self._url, socket_connect_timeout=3, socket_timeout=3
                )
        return self._client

    def try_acquire(self, key: str, token: str, ttl_seconds: int) -> bool:
        import redis

        try:
            return bool(self._conn().set(key, token, nx=True, px=int(ttl_seconds * 1000)))
        except redis.RedisError as exc:
            raise GpuLockUnavailable(f"Redis 不可达，无法获取 GPU 锁：{exc}") from exc

    def release(self, key: str, token: str) -> bool:
        import redis

        try:
            return bool(self._conn().eval(_RELEASE_SCRIPT, 1, key, token))
        except redis.RedisError as exc:
            # 释放失败不抛：锁会靠 TTL 过期。抛出去只会让任务被判失败，
            # 而任务其实已经跑完了。
            log.warning("gpu_lock.release_failed key=%s err=%s", key, str(exc)[:200])
            return False

    def holder(self, key: str) -> str | None:
        import redis

        try:
            value = self._conn().get(key)
        except redis.RedisError:
            return None
        return value.decode() if isinstance(value, bytes) else value


class GpuSlot:
    """GPU 独占位。用法：

    slot = GpuSlot()
    if not slot.acquire(timeout=60, on_wait=refresh_heartbeat):
        return  # 退回队列，稍后重投
    try:
        ...  # 上卡干活
    finally:
        slot.release()
    """

    def __init__(
        self,
        key: str | None = None,
        ttl_seconds: int | None = None,
        backend: LockBackend | None = None,
        poll_interval: float = 0.25,
    ) -> None:
        self.key = key or settings.gpu_lock_key
        self._ttl = ttl_seconds or settings.gpu_lock_ttl_seconds
        self._backend = backend or RedisLockBackend()
        self._poll_interval = poll_interval
        self._token: str | None = None

    @property
    def held(self) -> bool:
        return self._token is not None

    def acquire(self, timeout: float | None = None, on_wait=None) -> bool:  # type: ignore[no-untyped-def]
        """尝试获取；成功返回 True。

        `on_wait()` 在每次等待间隙被调用，返回 **False 表示放弃等待**
        （worker 用它来发现"任务已被取消"）。返回 True 则继续等。

        超时返回 False —— 这是正常情形（GPU 被别的任务占着），不是错误。
        """
        if self._token is not None:
            return True  # 可重入：同一个 slot 重复 acquire 不应死锁
        token = uuid.uuid4().hex
        deadline = None if timeout is None else time.monotonic() + timeout

        while True:
            if self._backend.try_acquire(self.key, token, self._ttl):
                self._token = token
                log.debug("gpu_lock.acquired key=%s", self.key)
                return True
            if deadline is not None and time.monotonic() >= deadline:
                log.info("gpu_lock.timeout key=%s", self.key)
                return False
            if on_wait is not None and on_wait() is False:
                log.info("gpu_lock.abandoned key=%s", self.key)
                return False
            time.sleep(self._poll_interval)

    def release(self) -> bool:
        if self._token is None:
            return False
        token, self._token = self._token, None
        released = self._backend.release(self.key, token)
        if released:
            log.debug("gpu_lock.released key=%s", self.key)
        else:
            # 锁已因 TTL 过期被别人拿走 —— 说明本次执行超过了 TTL，值得记一笔
            log.warning("gpu_lock.release_missed key=%s（可能已超 TTL 过期）", self.key)
        return released

    def holder(self) -> str | None:
        return self._backend.holder(self.key)
