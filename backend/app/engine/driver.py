"""ComfyUI 引擎客户端（P2-04 冒烟脚本的工程化版本）。

设计要点（均来自 PRD）：
1. **进度的权威状态以 `/history` 轮询为准**，WebSocket 只做加速展示。
   理由见 `docs/flow/system_flow.md` §2.1：网络抖动时 WS 丢帧会导致状态永久错乱，
   而轮询天然自愈，直接满足 NFR-3「优雅重启后任务可恢复」。
2. **错误分类要区分瞬时与致命**（ErrorType.is_retryable）。
   把「非法参数」当成可重试错误，会让队列被必然失败的任务堵死（PRD §5.4 T7 vs T11）。
3. OOM 要能识别出来，以便触发降级而非直接失败（EX-1 / AC-6.1）。

与 ComfyUI 的契约（v0.3.75）：
    POST /prompt            → {"prompt_id": "..."}
    GET  /history/{id}      → {id: {"status": {...}, "outputs": {...}}}
    GET  /view?filename=..  → 图片二进制
    GET  /system_stats      → 系统与显存信息
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import httpx

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


@dataclass
class ExecutionResult:
    """一次执行的产出。"""

    prompt_id: str
    images: list[dict[str, Any]] = field(default_factory=list)
    raw_outputs: dict[str, Any] = field(default_factory=dict)
    duration_ms: int = 0


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
    ) -> None:
        self.base_url = (base_url or settings.comfyui_base_url).rstrip("/")
        self._timeout = timeout or settings.comfyui_read_timeout
        self._client = client or httpx.Client(
            base_url=self.base_url,
            timeout=httpx.Timeout(self._timeout, connect=settings.comfyui_connect_timeout),
            headers={"Accept": "application/json"},
        )

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
        """
        payload: dict[str, Any] = {"prompt": workflow}
        if client_id:
            payload["client_id"] = client_id

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
    ) -> ExecutionResult:
        """轮询直到完成或超时。

        超时抛 ComfyUIError(TIMEOUT)，**可重试**（EX-2）。
        这里不做取消检测 —— 取消由 worker 在轮询间隙判断并调用
        interrupt()，以保持本方法职责单一。
        """
        timeout = timeout or settings.task_timeout_seconds
        started = time.monotonic()
        ctx = bind_task(task_id=task_id, prompt_id=prompt_id)

        while True:
            elapsed = time.monotonic() - started
            if elapsed > timeout:
                raise ComfyUIError(
                    f"执行超时（{timeout}s）", ErrorType.TIMEOUT
                )

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

    # ---------- 取图 ----------

    def fetch_image(
        self, filename: str, subfolder: str = "", folder_type: str = "output"
    ) -> bytes:
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
