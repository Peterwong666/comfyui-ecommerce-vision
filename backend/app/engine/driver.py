"""ComfyUI 引擎客户端（P2-04 冒烟脚本的工程化版本 / P6-01）。

设计要点（均来自 PRD）：
1. **进度的权威状态以 `/history` 轮询为准**，WebSocket 只做加速展示。
   理由见 `docs/flow/system_flow.md` §2.1：网络抖动时 WS 丢帧会导致状态永久错乱，
   而轮询天然自愈，直接满足 NFR-3「优雅重启后任务可恢复」。
   → 因此 `ProgressWatcher` 只提供 `latest`，**不做任何状态判定**；
   真正决定成功/失败/超时的永远是 `wait()`。
2. **错误分类要区分瞬时与致命**（ErrorType.is_retryable）。
   把「非法参数」当成可重试错误，会让队列被必然失败的任务堵死（PRD §5.4 T7 vs T11）。
3. OOM 要能识别出来，以便触发降级而非直接失败（EX-1 / AC-6.1）。
4. `client_id` 必须**在提交与订阅进度时用同一个值** —— ComfyUI 用它对消息路由，
   不一致就收不到自己任务的进度（表现为"WS 静默"）。

与 ComfyUI 的契约（目标版本 v0.36.0）：

    POST /prompt                    → {"prompt_id": "..."}
    POST /prompt                    → 校验失败时 400 + {"error": ..., "node_errors": ...}
    GET  /history/{prompt_id}       → {prompt_id: {"status": {...}, "outputs": {...}}}
    GET  /view?filename=..          → 图片二进制
    GET  /system_stats               → 系统与显存信息
    GET  /queue                      → {"queue_running": [...], "queue_pending": [...]}
    POST /queue  {"delete":[id]}     → 从队列中删除尚未开始的任务
    POST /interrupt                  → 中断当前执行（**全局**，见 interrupt() 的说明）
    POST /free                       → 释放显存
    GET  /object_info                → 节点定义
    POST /upload/image               → multipart，字段名必须是 `image`
    POST /upload/mask                → 同上，另需 `original_ref`
    WS   /ws?clientId=<client_id>    → 进度事件流

⚠️ **实测状态**（务必与代码一起读，别只看"能跑"就当成全验过）：

| 端点 | 状态 | 证据 |
|---|---|---|
| `/prompt` · `/history` · `/view` · `/system_stats` | ✅ 真机通过 | v0.36.0 + 4090，`deploy/autodl/04_api_smoke_test.py` / `27_gpu_verify.sh` |
| `GET /queue` · `POST /queue`(delete) | ✅ 真机通过 | `33_verify_comfyui_endpoints.py`，2026-09-16 |
| `POST /upload/image`（字段名 `image`） | ✅ 真机通过 | 同上 —— 这条曾是最高风险项（错了会挡住 P6-09） |
| `POST /upload/mask`（`original_ref` 为 JSON 字符串） | ✅ 真机通过 | 同上；顺带查实"裸文件名会让引擎回 500"，已在 `_normalize_original_ref` 本地拦截 |
| `WS /ws?clientId=...` | ❌ **未真机验证** | 只做了纯函数解析与假连接测试（`tests/test_driver.py`） |
| 端到端 `execute_task`（经我们的渲染器真出一张图） | ❌ **未真机验证** | 需要 worker + Redis + ComfyUI 同时就绪 |

进度事件的**事件名**尤其只有测试兜底：真机若发现出入，**优先改这里**，
不要在每个调用点分别打补丁。
"""

from __future__ import annotations

import enum
import json
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx
from websockets.sync.client import connect as ws_connect

from app.core.config import settings
from app.core.logging import bind_task, get_logger
from app.models.enums import ErrorType

log = get_logger(__name__)

# ComfyUI /prompt 返回中的错误关键词 → 本项目 ErrorType 的映射。
# 顺序有意义：先匹配更具体的。
_ERROR_PATTERNS: tuple[tuple[str, ErrorType], ...] = (
    ("out of memory", ErrorType.OOM),
    ("cuda error", ErrorType.OOM),
    ("allocation on device", ErrorType.OOM),
    ("outofmemoryerror", ErrorType.OOM),
    ("prompt outputs failed validation", ErrorType.INVALID_PARAM),
    ("required input is missing", ErrorType.INVALID_PARAM),
    ("value not in list", ErrorType.INVALID_PARAM),
    ("no such file", ErrorType.MODEL_MISSING),
    ("cannot find", ErrorType.MODEL_MISSING),
    ("no space left", ErrorType.DISK_FULL),
)


def classify_error(message: str) -> ErrorType:
    """把 ComfyUI 的报错文本映射成本项目的错误分类（PRD §6）。

    宁可归为 UNKNOWN（保守地视为可重试）也不武断判为致命，
    但要确保明确的关键词能被正确识别 —— 否则 OOM 会被当成致命错误直接失败，
    导致 AC-6.1「OOM 经降级后能成功」不成立。
    """
    low = (message or "").lower()
    for pattern, err in _ERROR_PATTERNS:
        if pattern in low:
            return err
    return ErrorType.UNKNOWN


class ComfyUIError(Exception):
    """引擎调用异常，带错误分类供状态机决策。"""

    def __init__(self, message: str, error_type: ErrorType | None = None):
        super().__init__(message)
        self.error_type = error_type or classify_error(message)
        self.retryable = self.error_type.is_retryable


class ComfyUIUnavailable(ComfyUIError):
    """引擎不可达（EX-8）。"""

    def __init__(self, message: str = "ComfyUI 不可达"):
        super().__init__(message, ErrorType.ENGINE_UNRESPONSIVE)


class ExecutionCanceled(Exception):
    """用户取消了任务。

    **故意不继承 `ComfyUIError`**：取消不是错误，没有 `error_type`、
    不该记 `error_message`、更不该走重试判定。把它混进错误体系里，
    会让"用户主动取消"在任务中心显示成一次故障。
    """

    def __init__(self, message: str = "任务已取消"):
        super().__init__(message)


@dataclass
class ExecutionResult:
    """一次执行的产出。"""

    prompt_id: str
    images: list[dict[str, Any]] = field(default_factory=list)
    raw_outputs: dict[str, Any] = field(default_factory=dict)
    duration_ms: int = 0


# ---------------------------------------------------------------- 进度事件（WS）


# ComfyUI 在 WebSocket 上发出的事件类型（`server.py` 的 send_sync）。
# 只登记我们认识的那些，不认识的一律忽略 —— 引擎升级新增事件时不应让 worker 崩掉。
WS_EVENT_TYPES = frozenset(
    {
        "status",  # 队列剩余数，用于「前面还有几个人」的展示
        "execution_start",
        "execution_cached",  # 命中缓存，不是新进度，别拿去更新进度条
        "executing",  # 当前节点；node 为 None 表示这张图跑完了
        "executed",
        "progress",  # {value, max}，进度条的唯一数据来源
        "progress_state",
        "execution_success",
        "execution_error",
        "execution_interrupted",
    }
)

# 视为「这张图已经有了结局」的事件。**注意：这不是权威判定** ——
# 权威在 /history 轮询，这里只用于让 watcher 及时收工。
WS_TERMINAL_TYPES = frozenset({"execution_success", "execution_error", "execution_interrupted"})
WS_ERROR_TYPES = frozenset({"execution_error", "execution_interrupted"})


@dataclass(frozen=True)
class ProgressEvent:
    """归一化后的进度事件。

    ComfyUI 的原始消息形状不一（`progress` 带 value/max，`status` 带 queue_remaining，
    `execution_error` 带 exception_message），这里统一成一种结构，
    让上层（worker / WS 推送）不必知道引擎的字段差异。
    """

    type: str
    prompt_id: str | None = None
    node: str | None = None
    value: int | None = None
    maximum: int | None = None
    queue_remaining: int | None = None
    message: str | None = None

    @property
    def percent(self) -> float | None:
        """完成百分比 0~1。取不到 value/max 时返回 None（宁可不显示也不乱显示）。"""
        if self.value is None or not self.maximum:
            return None
        return min(max(self.value / self.maximum, 0.0), 1.0)

    @property
    def is_terminal(self) -> bool:
        return self.type in WS_TERMINAL_TYPES

    @property
    def is_error(self) -> bool:
        return self.type in WS_ERROR_TYPES


def parse_ws_message(raw: str | bytes | None) -> ProgressEvent | None:
    """把 ComfyUI 的 WebSocket 帧解析成 `ProgressEvent`；无法识别时返回 None。

    返回 None 而不是抛异常是刻意的：WS 上既有我们关心的事件，也有
    `preview` 这类二进制预览帧（带 4 字节类型头，不是 JSON）。
    **一条噪声帧不该让整个任务失败** —— 进度本来就是"尽力而为"的增强信息。
    """
    if raw is None:
        return None
    if isinstance(raw, bytes):
        try:
            raw = raw.decode("utf-8")
        except UnicodeDecodeError:
            return None
    try:
        payload = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None

    event_type = payload.get("type")
    if not isinstance(event_type, str) or event_type not in WS_EVENT_TYPES:
        return None
    data = payload.get("data")
    if not isinstance(data, dict):
        # 已知类型但缺 data —— 是畸形帧。**返回 None 而不是补空 dict**：
        # 补空会造出一个"什么信息都没有"的事件，把上一帧有效的进度覆盖掉。
        return None

    def _int(key: str) -> int | None:
        v = data.get(key)
        return v if isinstance(v, int) else None

    prompt_id = data.get("prompt_id")
    node = data.get("node")
    queue_remaining: int | None = None
    if event_type == "status":
        status = data.get("status")
        if isinstance(status, dict):
            exec_info = status.get("exec_info")
            if isinstance(exec_info, dict) and isinstance(exec_info.get("queue_remaining"), int):
                queue_remaining = exec_info["queue_remaining"]

    return ProgressEvent(
        type=event_type,
        prompt_id=prompt_id if isinstance(prompt_id, str) else None,
        node=str(node) if node is not None else None,
        value=_int("value"),
        maximum=_int("max"),
        queue_remaining=queue_remaining,
        message=_extract_ws_error(data) if event_type in WS_ERROR_TYPES else None,
    )


def _extract_ws_error(data: dict[str, Any]) -> str:
    """从 execution_error / execution_interrupted 的 data 里拼一条可读信息。"""
    parts = [
        data.get("node_type"),
        data.get("exception_message") or data.get("message"),
        data.get("exception_type"),
    ]
    text = " | ".join(str(p) for p in parts if p)
    if text:
        return text
    # 没有异常详情时，区分「被中断」与「真出错」—— 前者不该走重试逻辑
    return "执行被中断（interrupted）" if data.get("node_id") is None else "执行出错（无详情）"


class ProgressWatcher:
    """在后台线程订阅某个 prompt 的 WebSocket 进度。

    **为什么放在线程里**：worker 的主循环必须保持「轮询 /history」这条权威路径，
    不能被 WS 阻塞或卡死。WS 只是附加的旁路信息。

    **绝不抛异常给主流程**：连不上、被服务端断开、消息畸形都只记 `warning`，
    主流程照旧靠轮询收敛（NFR-3）。
    """

    def __init__(
        self,
        client: ComfyUIClient,
        prompt_id: str,
        recv_timeout: float = 1.0,
    ) -> None:
        self._client = client
        self._prompt_id = prompt_id
        self._recv_timeout = recv_timeout
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.latest: ProgressEvent | None = None
        self.error: str | None = None
        self.warning: str | None = None

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._run, name=f"ws-progress-{self._prompt_id[:8]}", daemon=True
        )
        self._thread.start()

    def stop(self, join_timeout: float = 3.0) -> None:
        self._stop.set()
        self.join(join_timeout)

    def join(self, timeout: float = 3.0) -> None:
        """等后台线程结束（正常终止、出错终止、或到达超时）。

        与 `stop()` 的区别：`join()` **不**要求线程停下，只是等它自己结束 ——
        测试与「等到终态再收结果」的场景用这个。
        """
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    def __enter__(self) -> ProgressWatcher:
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()

    @property
    def percent(self) -> float | None:
        return self.latest.percent if self.latest is not None else None

    @property
    def queue_remaining(self) -> int | None:
        return self.latest.queue_remaining if self.latest is not None else None

    def _run(self) -> None:
        url = f"{self._client.ws_url}?clientId={self._client.client_id}"
        try:
            with ws_connect(url, open_timeout=settings.comfyui_connect_timeout) as ws:
                while not self._stop.is_set():
                    try:
                        raw = ws.recv(timeout=self._recv_timeout)
                    except TimeoutError:
                        continue  # 只是这段时间没有事件，不代表链路断了
                    event = parse_ws_message(raw)
                    if event is None:
                        continue
                    # ComfyUI 会用 client_id 路由，但同一个连接上仍可能夹带别的
                    # prompt 的事件（例如 status 是全局的），这里只收自己的。
                    if event.prompt_id not in (None, self._prompt_id):
                        continue
                    self.latest = event
                    if event.is_error:
                        self.error = event.message or "执行出错"
                    if event.is_terminal:
                        return
        except Exception as exc:  # 任何 WS 故障都不应影响主流程
            self.warning = f"WS 进度订阅失败（不影响任务）：{exc}"
            log.warning(
                "task.ws_unavailable err=%s",
                str(exc)[:200],
                extra=bind_task(prompt_id=self._prompt_id),
            )


class CancelOutcome(str, enum.Enum):
    """`ComfyUIClient.cancel()` 的结果。调用方需要据此决定后续动作。"""

    QUEUED_DELETED = "queued_deleted"  # 还在引擎队列里，已删 —— 一张都没跑
    INTERRUPTED = "interrupted"  # 正在执行，已发中断（步间隙生效）
    NOT_FOUND = "not_found"  # 队列与执行中都没有它（可能已完成）


class ComfyUIClient:
    """同步 HTTP 客户端。

    用同步而非异步：Celery worker 是同步模型，且真正的耗时在 GPU 推理（10s 级），
    网络等待占比极小，异步带来的复杂度不值得。
    """

    def __init__(
        self,
        base_url: str | None = None,
        timeout: float | None = None,
        client: httpx.Client | None = None,
        client_id: str | None = None,
        ws_url: str | None = None,
    ) -> None:
        self.base_url = (base_url or settings.comfyui_base_url).rstrip("/")
        self._timeout = timeout or settings.comfyui_read_timeout
        self._client = client or httpx.Client(
            base_url=self.base_url,
            timeout=httpx.Timeout(self._timeout, connect=settings.comfyui_connect_timeout),
            headers={"Accept": "application/json"},
        )
        # ComfyUI 用 client_id 路由 WebSocket 消息：**提交时传的值必须与订阅进度
        # 时用的值一致**，否则收不到自己任务的进度（表现为 WS 一直静默）。
        # 因此由客户端持有，而不是每次调用现取。
        self.client_id = client_id or str(uuid.uuid4())
        self.ws_url = ws_url or settings.comfyui_ws_url

    # ---------- 生命周期 ----------

    def __enter__(self) -> ComfyUIClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    # ---------- 基础探测 ----------

    def health(self) -> dict[str, Any]:
        """健康检查。失败时抛 ComfyUIUnavailable，供 EX-8 判断是否重启引擎。"""
        try:
            resp = self._client.get("/system_stats")
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError as exc:
            raise ComfyUIUnavailable(f"健康检查失败: {exc}") from exc

    def is_healthy(self) -> bool:
        try:
            self.health()
            return True
        except ComfyUIError:
            return False

    def free_memory(self) -> None:
        """释放显存与内存缓存。OOM 降级时调用（EX-1）。"""
        try:
            self._client.post("/free", json={"unload_models": True, "free_memory": True})
        except httpx.HTTPError as exc:  # 释放失败不应中断流程
            log.warning("free_memory 失败（忽略）: %s", exc)

    # ---------- 提交与轮询 ----------

    def submit(self, workflow: dict[str, Any], client_id: str | None = None) -> str:
        """提交工作流，返回 prompt_id。

        校验失败会抛 ComfyUIError(INVALID_PARAM) —— **不可重试**（T11）。

        `client_id` 默认取本客户端的值 —— 这样同一个客户端提交的任务，
        其 WebSocket 进度才能被它自己收到（见 `__init__` 的说明）。
        """
        payload: dict[str, Any] = {
            "prompt": _strip_meta(workflow),
            "client_id": client_id or self.client_id,
        }

        try:
            resp = self._client.post("/prompt", json=payload)
        except httpx.HTTPError as exc:
            raise ComfyUIUnavailable(f"提交失败: {exc}") from exc

        if resp.status_code >= 400:
            detail = _extract_error_detail(resp)
            raise ComfyUIError(f"提交被拒: {detail}", classify_error(detail))

        data = resp.json()
        prompt_id = data.get("prompt_id")
        if not prompt_id:
            raise ComfyUIError(f"响应缺少 prompt_id: {data}", ErrorType.UNKNOWN)
        return str(prompt_id)

    def get_history(self, prompt_id: str) -> dict[str, Any] | None:
        """查询执行结果。未完成时返回 None。

        **这是进度的权威来源**（见模块 docstring 第 1 点）。
        """
        try:
            resp = self._client.get(f"/history/{prompt_id}")
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPError as exc:
            raise ComfyUIUnavailable(f"查询 history 失败: {exc}") from exc

        entry = data.get(prompt_id)
        return entry if isinstance(entry, dict) else None

    def wait(
        self,
        prompt_id: str,
        timeout: float | None = None,
        poll_interval: float = 0.5,
        task_id: int | None = None,
        should_stop: Callable[[], bool] | None = None,
        on_poll: Callable[[float], None] | None = None,
    ) -> ExecutionResult:
        """轮询直到完成或超时。

        超时抛 ComfyUIError(TIMEOUT)，**可重试**（EX-2）。

        两个回调把「执行策略」留给调用方，驱动层只保留「怎么问引擎」这一件事：

        - `should_stop()`：返回 True 时抛 `ExecutionCanceled`。
          worker 用它来发现"用户点了取消"（T10 要求在**采样步间隙**生效，
          所以在轮询间隙判断，而不是在采样中途硬杀进程）。
        - `on_poll(elapsed)`：每次轮询调用一次。worker 用它刷心跳（T14 僵尸检测
          依赖它）与采样显存水位。
        """
        timeout = timeout or settings.task_timeout_seconds
        started = time.monotonic()
        ctx = bind_task(task_id=task_id, prompt_id=prompt_id)

        while True:
            elapsed = time.monotonic() - started
            if elapsed > timeout:
                raise ComfyUIError(f"执行超时（{timeout}s）", ErrorType.TIMEOUT)

            if should_stop is not None and should_stop():
                raise ExecutionCanceled()

            entry = self.get_history(prompt_id)
            if entry is not None:
                status = entry.get("status", {}) or {}
                status_str = status.get("status_str", "")

                if status.get("completed") is True:
                    if status_str == "error":
                        msg = _extract_status_error(status)
                        raise ComfyUIError(msg, classify_error(msg))
                    images = _collect_images(entry.get("outputs", {}) or {})
                    return ExecutionResult(
                        prompt_id=prompt_id,
                        images=images,
                        raw_outputs=entry.get("outputs", {}) or {},
                        duration_ms=int(elapsed * 1000),
                    )

                if status_str == "error":
                    msg = _extract_status_error(status)
                    raise ComfyUIError(msg, classify_error(msg))

            if on_poll is not None:
                on_poll(elapsed)
            log.debug("task.poll", extra=ctx)
            time.sleep(poll_interval)

    def interrupt(self) -> None:
        """中断当前执行。用于取消任务（T10）。

        ComfyUI 的 /interrupt 是**全局**的（中断当前正在跑的那个），
        因为本项目 GPU 并发固定为 1（NFR-2），所以语义上是安全的。
        若未来改成多实例并发（E10），必须改为按实例路由。
        """
        try:
            self._client.post("/interrupt")
        except httpx.HTTPError as exc:
            log.warning("interrupt 失败: %s", exc)

    def queue_depth(self) -> int:
        """当前队列深度，用于 EX-5 并发超限判断。"""
        try:
            resp = self._client.get("/queue")
            resp.raise_for_status()
            data = resp.json()
            running = len(data.get("queue_running", []) or [])
            pending = len(data.get("queue_pending", []) or [])
            return running + pending
        except httpx.HTTPError:
            return -1  # 未知，不阻断提交

    # ---------- 队列与取消 ----------

    def queue(self) -> dict[str, list[Any]]:
        """读取引擎队列。`queue_running` / `queue_pending` 都是 `[序号, prompt_id, ...]`。"""
        try:
            resp = self._client.get("/queue")
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPError as exc:
            raise ComfyUIUnavailable(f"读取队列失败: {exc}") from exc
        if not isinstance(data, dict):
            return {"queue_running": [], "queue_pending": []}
        return {
            "queue_running": list(data.get("queue_running") or []),
            "queue_pending": list(data.get("queue_pending") or []),
        }

    def _queue_ids(self, key: str) -> list[str]:
        """把队列里某一类条目的 prompt_id 抽出来（条目形状是 [num, prompt_id, ...]）。"""
        ids: list[str] = []
        for entry in self.queue().get(key, []):
            if isinstance(entry, (list, tuple)) and len(entry) >= 2:
                ids.append(str(entry[1]))
        return ids

    def queue_position(self, prompt_id: str) -> int | None:
        """该 prompt 在待执行队列中的位置（0 起）；不在队列中返回 None。

        用于「前面还有几个人」的展示，以及区分「排队中可整条删除」与
        「已在执行只能中断」—— 这两者的取消代价完全不同。
        """
        pending = self._queue_ids("queue_pending")
        try:
            return pending.index(prompt_id)
        except ValueError:
            return None

    def is_running(self, prompt_id: str) -> bool:
        return prompt_id in self._queue_ids("queue_running")

    def delete_from_queue(self, prompt_id: str) -> bool:
        """从队列中删除尚未开始执行的任务。返回是否删除成功。

        与 `interrupt()` 的关键区别：这里**一张都还没跑**，删掉即零成本；
        而 interrupt 是让正在跑的任务在采样步间隙停下。
        """
        try:
            resp = self._client.post("/queue", json={"delete": [prompt_id]})
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            log.warning("queue.delete_failed prompt_id=%s err=%s", prompt_id, str(exc)[:200])
            return False
        return True

    def cancel(self, prompt_id: str) -> CancelOutcome:
        """取消一个已提交的任务（T3/T4/T10）。

        策略是**先看它在哪、再决定怎么杀**：
        - 还在 `queue_pending` → 直接删掉，一张都没跑，最干净；
        - 已在 `queue_running` → 只能 `interrupt()`，在**采样步间隙**生效（AC-5.2），
          不在采样中途硬杀，否则可能留下损坏的显存状态与临时文件；
        - 两边都没有 → 它已经结束了（成功或失败），交给调用方按 history 结算。
        """
        if self.queue_position(prompt_id) is not None:
            return (
                CancelOutcome.QUEUED_DELETED
                if self.delete_from_queue(prompt_id)
                else CancelOutcome.NOT_FOUND
            )
        if self.is_running(prompt_id):
            self.interrupt()
            return CancelOutcome.INTERRUPTED
        return CancelOutcome.NOT_FOUND

    # ---------- 素材上传 ----------

    def upload_image(
        self,
        data: bytes,
        filename: str,
        subfolder: str = "",
        folder_type: str = "input",
        overwrite: bool = False,
    ) -> dict[str, Any]:
        """上传图片到 ComfyUI 的输入目录（P6-09 的引擎侧）。

        返回 `{"name": ..., "subfolder": ..., "type": ...}` ——
        `name` 就是工作流里 `LoadImage.image` 需要填的**文件名**
        （配合 `ref_to_filename` 这个 transform 使用，见契约 §3.3）。

        ⚠️ multipart 的**字段名必须是 `image`**（ComfyUI 硬编码），
        叫 `file` 会得到一个 400。
        """
        return self._upload("/upload/image", data, filename, subfolder, folder_type, overwrite)

    def upload_mask(
        self,
        data: bytes,
        filename: str,
        original_ref: str | dict[str, Any],
        subfolder: str = "",
        folder_type: str = "input",
        overwrite: bool = True,
    ) -> dict[str, Any]:
        """上传遮罩（局部重绘用）。

        与 `upload_image` 的差别是必须带 `original_ref` —— ComfyUI 用它把遮罩
        与"被编辑的那张原图"绑定起来，缺了会 400。

        ⚠️ **`original_ref` 必须是 JSON 对象，或其 JSON 字符串**（形如
        `{"filename":"orig.png","subfolder":"","type":"input"}`）。
        传**裸文件名**会让引擎侧 `json.loads` 抛 JSONDecodeError、端点回 **500** ——
        这是真机实测踩到的（`deploy/autodl/33_verify_comfyui_endpoints.py`，
        2026-09-16，RTX 4090）。所以这里在**本地**就拒绝，给出能指到问题的报错，
        而不是把一个 500 留给调用方去猜。
        """
        return self._upload(
            "/upload/mask",
            data,
            filename,
            subfolder,
            folder_type,
            overwrite,
            {"original_ref": _normalize_original_ref(original_ref)},
        )

    def _upload(
        self,
        path: str,
        data: bytes,
        filename: str,
        subfolder: str,
        folder_type: str,
        overwrite: bool,
        extra_fields: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        # 只有 `image` 走 files（真文件），其余是普通表单字段走 data ——
        # httpx 会把两者合并成 multipart。把字符串塞进 files 不是合法用法。
        form: dict[str, str] = {
            "type": folder_type,
            "overwrite": "true" if overwrite else "false",
        }
        if subfolder:
            form["subfolder"] = subfolder
        form.update(extra_fields or {})
        files = {"image": (filename, data, "application/octet-stream")}
        try:
            resp = self._client.post(path, data=form, files=files)
        except httpx.HTTPError as exc:
            raise ComfyUIUnavailable(f"上传失败: {exc}") from exc
        if resp.status_code >= 400:
            detail = _extract_error_detail(resp)
            # 上传失败属于 EX-10（非法素材）：格式/尺寸问题重试同样的文件
            # 不会有不同结果，所以归为不可重试。
            raise ComfyUIError(f"上传被拒: {detail}", ErrorType.INVALID_UPLOAD)
        body = resp.json()
        return body if isinstance(body, dict) else {}

    # ---------- 进度订阅 ----------

    def watch_progress(self, prompt_id: str) -> ProgressWatcher:
        """创建（但**不启动**）一个进度订阅器。

        用 `with client.watch_progress(pid) as watcher:` 会自动起停。
        记住它的定位：**只是加速展示**，`wait()` 才是权威（见模块 docstring 第 1 点）。
        """
        return ProgressWatcher(self, prompt_id)

    # ---------- 取图 ----------

    def fetch_image(self, filename: str, subfolder: str = "", folder_type: str = "output") -> bytes:
        """取图片二进制（FR-5.2）。"""
        params = {"filename": filename, "type": folder_type}
        if subfolder:
            params["subfolder"] = subfolder
        try:
            resp = self._client.get("/view", params=params)
            resp.raise_for_status()
            return resp.content
        except httpx.HTTPError as exc:
            raise ComfyUIError(f"取图失败: {exc}", ErrorType.SAVE_FAILED) from exc

    def object_info(self) -> dict[str, Any]:
        """拉取节点定义。用于开发期核对工作流参数合法性（P3-01）。"""
        try:
            resp = self._client.get("/object_info")
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError as exc:
            raise ComfyUIUnavailable(f"获取 object_info 失败: {exc}") from exc


# ---------- 内部工具 ----------


def _strip_meta(workflow: dict[str, Any]) -> dict[str, Any]:
    """剔除工作流 JSON 里的元数据键（`_` 开头的那些）。

    ComfyUI 的 `/prompt` 把顶层**每一个 key 都当成节点**去校验，
    所以 `_comment` / `_meta` 这类人写的注释会让整个提交以
    「节点类型不存在」被拒 —— 而报错信息完全指不到"注释"这件事上。
    `deploy/autodl/04_api_smoke_test.py` 也是这么处理的。
    """
    return {k: v for k, v in workflow.items() if not str(k).startswith("_")}


def _normalize_original_ref(original_ref: str | dict[str, Any]) -> str:
    """把 `original_ref` 规范成 ComfyUI 要的 **JSON 字符串**。

    接受两种输入：JSON 对象（自己序列化）或已经是 JSON 字符串（原样透传）。
    **拒绝裸文件名** —— 真机实测（`33_verify_comfyui_endpoints.py`）传裸文件名会让
    引擎侧 `json.loads` 抛 JSONDecodeError 并回 500，而那看起来像"服务器坏了"，
    实际是调用方对契约的假设错了。这种错应该在我们这里就报清楚。
    """
    if isinstance(original_ref, dict):
        return json.dumps(original_ref, ensure_ascii=False)
    text = str(original_ref)
    try:
        parsed = json.loads(text)
    except ValueError as exc:
        raise ComfyUIError(
            f"original_ref 必须是 JSON 对象或其 JSON 字符串，"
            f"收到的是裸字符串 {original_ref!r}（传裸文件名会让引擎 500）",
            ErrorType.INVALID_UPLOAD,
        ) from exc
    if not isinstance(parsed, dict):
        raise ComfyUIError(
            f"original_ref 解析出的不是对象：{parsed!r}（应为 "
            '{"filename":..., "subfolder":..., "type":...}）',
            ErrorType.INVALID_UPLOAD,
        )
    return text


def _extract_error_detail(resp: httpx.Response) -> str:
    try:
        data = resp.json()
    except ValueError:
        return resp.text[:500]
    err = data.get("error")
    if isinstance(err, dict):
        return str(err.get("message") or err)
    node_errors = data.get("node_errors")
    if node_errors:
        return f"{err} | node_errors={node_errors}"
    return str(err or data)[:500]


def _extract_status_error(status: dict[str, Any]) -> str:
    """从 history 的 status.messages 里提取可读错误信息。"""
    messages = status.get("messages") or []
    for msg in messages:
        if isinstance(msg, (list, tuple)) and len(msg) >= 2:
            kind, payload = msg[0], msg[1]
            if kind == "execution_error" and isinstance(payload, dict):
                parts = [
                    payload.get("node_type"),
                    payload.get("exception_message"),
                    payload.get("exception_type"),
                ]
                return " | ".join(str(p) for p in parts if p)
    return "执行失败（未提供详情）"


def _collect_images(outputs: dict[str, Any]) -> list[dict[str, Any]]:
    """从 outputs 里收集所有图片描述符。

    只认 SaveImage / PreviewImage 这类带 images 的输出节点。
    """
    images: list[dict[str, Any]] = []
    for node_id, node_out in outputs.items():
        if not isinstance(node_out, dict):
            continue
        for img in node_out.get("images", []) or []:
            if isinstance(img, dict) and img.get("filename"):
                images.append({**img, "node_id": node_id})
    return images
