"""ComfyUI 适配层（P6-01）单元测试。

**为什么必须用 mock 而不是真机**：ComfyUI 只监听 `127.0.0.1:8188`，
且项目当前跑在 autoDL 的**无卡模式**（无 GPU，ComfyUI 不启动）。
因此这里用 `httpx.MockTransport` 覆盖 HTTP 契约、用一个假 WebSocket 覆盖进度流；
**真实联调（尤其是 `/upload/*` 的字段名与 WS 事件名）是遗留项**，
已登记在项目的「需要 GPU 才能验证」清单里。

这些测试的价值在于把**错误分类**钉死 —— 它是 PRD §5.4 T7 vs T11 的分界：
OOM 必须可重试（AC-6.1），非法参数必须不可重试（AC-6.2），
分类错了要么白烧 GPU、要么让必然失败的任务堵死队列。
"""

from __future__ import annotations

import json
import time

import httpx
import pytest

from app.engine import driver as driver_mod
from app.engine.driver import (
    CancelOutcome,
    ComfyUIClient,
    ComfyUIError,
    ComfyUIUnavailable,
    classify_error,
    parse_ws_message,
)
from app.models.enums import ErrorType


def make_client(handler) -> ComfyUIClient:  # type: ignore[no-untyped-def]
    """用 MockTransport 造一个客户端，不产生任何真实网络请求。"""
    http = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://comfy.test")
    return ComfyUIClient(base_url="http://comfy.test", client=http, client_id="cid-test")


def json_response(payload: dict, status_code: int = 200) -> httpx.Response:
    return httpx.Response(status_code, json=payload)


# --- 错误分类（PRD §5.4 T7 vs T11 的分界） ------------------------------------


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("CUDA out of memory. Tried to allocate 2.00 GiB", ErrorType.OOM),
        ("CUDA error: an illegal memory access", ErrorType.OOM),
        ("Prompt outputs failed validation: node 3", ErrorType.INVALID_PARAM),
        ("Required input is missing: clip_name", ErrorType.INVALID_PARAM),
        ("Value not in list: ckpt_name", ErrorType.INVALID_PARAM),
        ("No such file or directory: model.safetensors", ErrorType.MODEL_MISSING),
        ("No space left on device", ErrorType.DISK_FULL),
        ("something entirely unexpected", ErrorType.UNKNOWN),
    ],
)
def test_classify_error(message: str, expected: ErrorType) -> None:
    assert classify_error(message) == expected


def test_unknown_is_not_retryable() -> None:
    """兜底按致命处理 —— 不可重试的任务最多失败一次，可重试的错误最多拖住队列几轮。

    UNKNOWN 更可能是"我们没见过的格式错误"，重复跑它收益低、代价高。
    """
    assert classify_error("") == ErrorType.UNKNOWN
    assert ErrorType.UNKNOWN.is_retryable is False


# --- 健康检查 ----------------------------------------------------------------


def test_health_ok() -> None:
    client = make_client(lambda req: json_response({"devices": [{"name": "RTX 4090"}]}))
    assert client.health()["devices"][0]["name"] == "RTX 4090"
    assert client.is_healthy() is True


def test_health_unreachable_raises_engine_unresponsive() -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    client = make_client(boom)
    with pytest.raises(ComfyUIUnavailable) as exc:
        client.health()
    # EX-8：引擎不可达是可重试的（可能是重启中）
    assert exc.value.error_type is ErrorType.ENGINE_UNRESPONSIVE
    assert exc.value.retryable is True
    assert client.is_healthy() is False


# --- 提交 --------------------------------------------------------------------


def test_submit_returns_prompt_id_and_sends_client_id() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return json_response({"prompt_id": "pid-1"})

    client = make_client(handler)
    assert client.submit({"3": {"class_type": "KSampler", "inputs": {}}}) == "pid-1"
    # client_id 必须与订阅进度时用的一致，否则收不到 WS 进度
    assert seen["client_id"] == "cid-test"


def test_submit_strips_metadata_keys() -> None:
    """`_` 开头的键是注释/元数据，不能当节点提交（否则报"节点类型不存在"）。"""
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return json_response({"prompt_id": "pid-1"})

    client = make_client(handler)
    client.submit({"_comment": "说明", "_meta": {"v": 2}, "3": {"class_type": "KSampler"}})
    assert set(seen["prompt"]) == {"3"}


def test_submit_invalid_param_is_fatal() -> None:
    """EX-3 / AC-6.2：非法参数不得进入重试（重试一万次还是错）。"""
    client = make_client(
        lambda req: json_response(
            {"error": {"message": "Prompt outputs failed validation"}, "node_errors": {}}, 400
        )
    )
    with pytest.raises(ComfyUIError) as exc:
        client.submit({"3": {}})
    assert exc.value.error_type is ErrorType.INVALID_PARAM
    assert exc.value.retryable is False


def test_submit_oom_is_retryable() -> None:
    """EX-1 / AC-6.1：OOM 必须可重试，以便降级后重跑。"""
    client = make_client(
        lambda req: json_response({"error": {"message": "CUDA out of memory"}}, 500)
    )
    with pytest.raises(ComfyUIError) as exc:
        client.submit({"3": {}})
    assert exc.value.error_type is ErrorType.OOM
    assert exc.value.retryable is True


def test_submit_missing_prompt_id_is_unknown() -> None:
    client = make_client(lambda req: json_response({"unexpected": True}))
    with pytest.raises(ComfyUIError) as exc:
        client.submit({"3": {}})
    assert exc.value.error_type is ErrorType.UNKNOWN


def test_submit_network_error_is_unavailable() -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(ComfyUIUnavailable):
        make_client(boom).submit({"3": {}})


# --- 轮询（权威状态来源） ----------------------------------------------------


_HISTORY_OK = {
    "pid-1": {
        "status": {"completed": True, "status_str": "success"},
        "outputs": {
            "9": {"images": [{"filename": "a.png", "subfolder": "", "type": "output"}]},
            "10": {"images": [{"filename": "b.png", "subfolder": "sub", "type": "output"}]},
        },
    }
}


def test_wait_collects_images() -> None:
    client = make_client(lambda req: json_response(_HISTORY_OK))
    result = client.wait("pid-1", timeout=5, poll_interval=0.01)
    assert result.prompt_id == "pid-1"
    assert [img["filename"] for img in result.images] == ["a.png", "b.png"]
    # node_id 要带上 —— 上层要靠它知道图出自哪个 SaveImage 节点
    assert {img["node_id"] for img in result.images} == {"9", "10"}


def test_wait_raises_on_execution_error() -> None:
    client = make_client(
        lambda req: json_response(
            {
                "pid-1": {
                    "status": {
                        "completed": True,
                        "status_str": "error",
                        "messages": [
                            [
                                "execution_error",
                                {
                                    "node_type": "KSampler",
                                    "exception_message": "CUDA out of memory",
                                    "exception_type": "RuntimeError",
                                },
                            ]
                        ],
                    },
                    "outputs": {},
                }
            }
        )
    )
    with pytest.raises(ComfyUIError) as exc:
        client.wait("pid-1", timeout=5, poll_interval=0.01)
    # 从 history 的错误信息里也要能正确分类出 OOM
    assert exc.value.error_type is ErrorType.OOM
    assert "KSampler" in str(exc.value)


def test_wait_times_out_on_retryable_error() -> None:
    """EX-2：超时是可重试的（不是致命），否则一次卡住就判死。"""
    client = make_client(lambda req: json_response({}))  # 永远查不到
    with pytest.raises(ComfyUIError) as exc:
        client.wait("pid-1", timeout=0.05, poll_interval=0.01)
    assert exc.value.error_type is ErrorType.TIMEOUT
    assert exc.value.retryable is True


def test_get_history_returns_none_when_absent() -> None:
    client = make_client(lambda req: json_response({"other": {}}))
    assert client.get_history("pid-1") is None


# --- 取图 --------------------------------------------------------------------


def test_fetch_image_returns_bytes() -> None:
    client = make_client(lambda req: httpx.Response(200, content=b"\x89PNG-bytes"))
    assert client.fetch_image("a.png") == b"\x89PNG-bytes"


def test_fetch_image_failure_is_save_failed() -> None:
    """EX-13：取图/落盘失败可重试（图还在引擎磁盘上，重取一次可能就好了）。"""
    client = make_client(lambda req: httpx.Response(404, text="not found"))
    with pytest.raises(ComfyUIError) as exc:
        client.fetch_image("gone.png")
    assert exc.value.error_type is ErrorType.SAVE_FAILED
    assert exc.value.retryable is True


# --- 队列与取消 ---------------------------------------------------------------


_QUEUE = {
    "queue_running": [[0, "pid-run", {}, {}, []]],
    "queue_pending": [[1, "pid-wait-1", {}, {}, []], [2, "pid-wait-2", {}, {}, []]],
}


def test_queue_depth_counts_running_and_pending() -> None:
    client = make_client(lambda req: json_response(_QUEUE))
    assert client.queue_depth() == 3


def test_queue_depth_unknown_on_error_does_not_block_submit() -> None:
    """拿不到队列深度只应影响"预计等待"，不应阻断提交。"""

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    assert make_client(boom).queue_depth() == -1


def test_queue_position() -> None:
    client = make_client(lambda req: json_response(_QUEUE))
    assert client.queue_position("pid-wait-2") == 1
    assert client.queue_position("pid-run") is None  # 在跑，不在待执行队列
    assert client.queue_position("nope") is None


def test_is_running() -> None:
    client = make_client(lambda req: json_response(_QUEUE))
    assert client.is_running("pid-run") is True
    assert client.is_running("pid-wait-1") is False


def test_cancel_pending_deletes_from_queue() -> None:
    """还没开始跑 → 整条删掉，一张都没跑，零 GPU 成本。"""
    calls: list[tuple[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return json_response(_QUEUE)
        calls.append((request.url.path, json.loads(request.content)))
        return json_response({})

    outcome = make_client(handler).cancel("pid-wait-1")
    assert outcome is CancelOutcome.QUEUED_DELETED
    assert calls == [("/queue", {"delete": ["pid-wait-1"]})]


def test_cancel_running_interrupts() -> None:
    """正在跑 → 只能 interrupt（步间隙生效），不硬杀进程。"""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return json_response(_QUEUE)
        calls.append(request.url.path)
        return json_response({})

    assert make_client(handler).cancel("pid-run") is CancelOutcome.INTERRUPTED
    assert calls == ["/interrupt"]


def test_cancel_unknown_prompt_is_not_found() -> None:
    """两边都没有 → 它已经结束了，交给调用方按 history 结算。"""
    client = make_client(lambda req: json_response(_QUEUE))
    assert client.cancel("pid-gone") is CancelOutcome.NOT_FOUND


def test_cancel_bare_interrupt_is_not_used_for_pending() -> None:
    """回归点：对"排队中"的任务发 interrupt 是**空操作**，那张图仍会被跑出来。

    曾经 API 的取消逻辑就是无条件 interrupt —— 白烧一次 GPU。
    """
    interrupted: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/queue" and request.method == "GET":
            return json_response(_QUEUE)
        if request.url.path == "/interrupt":
            interrupted.append("yes")
        return json_response({})

    make_client(handler).cancel("pid-wait-2")
    assert interrupted == []


# --- 上传 --------------------------------------------------------------------


def test_upload_image_uses_image_field_name() -> None:
    """ComfyUI 硬编码字段名为 `image`，叫 `file` 会 400。"""
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = request.content
        return json_response({"name": "sku-a.png", "subfolder": "", "type": "input"})

    body = make_client(handler).upload_image(
        b"\x89PNG", "sku-a.png", subfolder="batch1", folder_type="input"
    )
    assert seen["path"] == "/upload/image"
    assert b'name="image"' in seen["body"]
    assert b'filename="sku-a.png"' in seen["body"]
    assert b'name="subfolder"' in seen["body"]
    assert b"batch1" in seen["body"]
    # 返回的 name 就是工作流 LoadImage.image 要填的文件名
    assert body["name"] == "sku-a.png"


def test_upload_mask_accepts_dict_and_json_string() -> None:
    """`original_ref` 两种写法都要能work：JSON 对象、或已经是 JSON 字符串。"""
    seen: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.content)
        return json_response({"name": "m.png", "subfolder": "", "type": "input"})

    client = make_client(handler)
    ref = {"filename": "orig.png", "subfolder": "", "type": "input"}
    client.upload_mask(b"MASK", "m.png", original_ref=ref)
    client.upload_mask(b"MASK", "m.png", original_ref=json.dumps(ref))

    for body in seen:
        assert b'name="original_ref"' in body
        assert b"orig.png" in body


def test_upload_mask_rejects_bare_filename() -> None:
    """传**裸文件名**必须在本地就被拒。

    真机实测（`deploy/autodl/33_verify_comfyui_endpoints.py`，2026-09-16）：
    引擎侧对 `original_ref` 做 `json.loads`，裸文件名会让它抛 JSONDecodeError 并回 **500**。
    那个 500 看起来像"服务器坏了"，实际是调用方对契约的假设错了 ——
    这种错必须在我们这里就报清楚，而不是留给调用方去猜。
    """
    called: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        called.append(request.url.path)
        return json_response({})

    client = make_client(handler)
    with pytest.raises(ComfyUIError) as exc:
        client.upload_mask(b"MASK", "m.png", original_ref="orig.png")
    assert exc.value.error_type is ErrorType.INVALID_UPLOAD
    assert called == [], "不应把注定 500 的请求发出去"


def test_upload_mask_rejects_json_scalar() -> None:
    """是合法 JSON 但不是对象（如 `"123"`、`"null"`）同样要拦。"""
    client = make_client(lambda req: json_response({}))
    with pytest.raises(ComfyUIError):
        client.upload_mask(b"MASK", "m.png", original_ref='["not", "an", "object"]')


def test_upload_failure_is_invalid_upload_not_retryable() -> None:
    """EX-10：非法素材重试无意义。"""
    client = make_client(lambda req: httpx.Response(400, text="unsupported image format"))
    with pytest.raises(ComfyUIError) as exc:
        client.upload_image(b"x", "bad.txt")
    assert exc.value.error_type is ErrorType.INVALID_UPLOAD
    assert exc.value.retryable is False


# --- WebSocket 进度解析（纯函数，可完全离线验证） -----------------------------


def test_parse_progress_frame() -> None:
    event = parse_ws_message(
        json.dumps(
            {"type": "progress", "data": {"value": 3, "max": 20, "prompt_id": "p1", "node": "3"}}
        )
    )
    assert event is not None
    assert (event.type, event.value, event.maximum, event.node) == ("progress", 3, 20, "3")
    assert event.percent == pytest.approx(0.15)
    assert event.is_terminal is False


def test_parse_status_frame_carries_queue_remaining() -> None:
    event = parse_ws_message(
        json.dumps({"type": "status", "data": {"status": {"exec_info": {"queue_remaining": 4}}}})
    )
    assert event is not None
    assert event.queue_remaining == 4


def test_parse_execution_error_frame_extracts_message() -> None:
    event = parse_ws_message(
        json.dumps(
            {
                "type": "execution_error",
                "data": {
                    "prompt_id": "p1",
                    "node_type": "KSampler",
                    "exception_message": "CUDA out of memory",
                    "exception_type": "RuntimeError",
                },
            }
        )
    )
    assert event is not None
    assert event.is_error is True
    assert event.is_terminal is True
    assert "CUDA out of memory" in (event.message or "")


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "",
        "not json at all",
        b"\x00\x01\x02binary-preview",  # 预览帧不是 JSON
        json.dumps({"no_type": 1}),
        json.dumps({"type": "totally_unknown_event", "data": {}}),
        json.dumps({"type": "progress"}),  # 缺 data
        json.dumps(["not", "a", "dict"]),
    ],
)
def test_parse_ignores_noise_instead_of_raising(raw: object) -> None:
    """噪声帧不能让任务失败 —— 进度本来就是"尽力而为"的增强信息。"""
    assert parse_ws_message(raw) is None  # type: ignore[arg-type]


def test_parse_handles_bytes_frame() -> None:
    raw = json.dumps({"type": "execution_start", "data": {"prompt_id": "p1"}}).encode()
    event = parse_ws_message(raw)
    assert event is not None
    assert event.type == "execution_start"


def test_parse_percent_none_without_max() -> None:
    """拿不到 max 时宁可不显示进度条，也不要显示一个假的百分比。"""
    event = parse_ws_message(json.dumps({"type": "progress", "data": {"value": 3}}))
    assert event is not None
    assert event.percent is None


# --- WebSocket 订阅器（假连接） ------------------------------------------------


class _FakeWS:
    def __init__(self, frames: list[object]) -> None:
        self._frames = list(frames)

    def recv(self, timeout: float | None = None) -> object:
        if not self._frames:
            raise TimeoutError
        return self._frames.pop(0)


class _FakeConnect:
    """冒充 `websockets.sync.client.connect` 的上下文管理器。"""

    def __init__(self, frames: list[object] | None = None, error: Exception | None = None) -> None:
        self._frames = frames or []
        self._error = error
        self.urls: list[str] = []

    def __call__(self, url: str, **kwargs: object) -> _FakeConnect:
        self.urls.append(url)
        return self

    def __enter__(self) -> _FakeWS:
        if self._error is not None:
            raise self._error
        return _FakeWS(self._frames)

    def __exit__(self, *exc: object) -> bool:
        return False


def test_watcher_collects_events_and_stops_at_terminal(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    fake = _FakeConnect(
        [
            json.dumps({"type": "progress", "data": {"value": 2, "max": 10, "prompt_id": "p1"}}),
            json.dumps({"type": "execution_success", "data": {"prompt_id": "p1"}}),
        ]
    )
    monkeypatch.setattr(driver_mod, "ws_connect", fake)

    client = make_client(lambda req: json_response({}))
    with client.watch_progress("p1") as watcher:
        watcher.join(timeout=3)

    assert watcher.latest is not None
    assert watcher.latest.type == "execution_success"
    assert watcher.warning is None
    # client_id 必须出现在订阅 URL 上，否则 ComfyUI 不会把消息路由给我们
    assert fake.urls == ["ws://127.0.0.1:8188/ws?clientId=cid-test"]


def test_watcher_ignores_other_prompts(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """同一连接上可能有别的 prompt 的事件，只收自己的。"""
    fake = _FakeConnect(
        [
            json.dumps({"type": "progress", "data": {"value": 1, "max": 9, "prompt_id": "other"}}),
            json.dumps({"type": "execution_success", "data": {"prompt_id": "p1"}}),
        ]
    )
    monkeypatch.setattr(driver_mod, "ws_connect", fake)
    client = make_client(lambda req: json_response({}))

    with client.watch_progress("p1") as watcher:
        watcher.join(timeout=3)

    assert watcher.latest is not None
    assert watcher.latest.prompt_id == "p1"


def test_watcher_records_error_without_raising(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    fake = _FakeConnect(
        [
            json.dumps(
                {
                    "type": "execution_error",
                    "data": {"prompt_id": "p1", "exception_message": "boom"},
                }
            )
        ]
    )
    monkeypatch.setattr(driver_mod, "ws_connect", fake)
    client = make_client(lambda req: json_response({}))

    with client.watch_progress("p1") as watcher:
        watcher.join(timeout=3)

    assert watcher.error is not None and "boom" in watcher.error


def test_watcher_swallows_connection_failure(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """NFR-3：WS 连不上只记 warning，任务照旧靠轮询收敛，绝不因此失败。"""
    fake = _FakeConnect(error=OSError("ws refused"))
    monkeypatch.setattr(driver_mod, "ws_connect", fake)
    client = make_client(lambda req: json_response({}))

    with client.watch_progress("p1") as watcher:
        watcher.join(timeout=3)

    assert watcher.warning is not None
    assert watcher.latest is None
    assert watcher.error is None


def test_watcher_stop_is_idempotent(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """停不下来的后台线程会拖住 Celery worker 退出，必须能反复安全停止。"""
    monkeypatch.setattr(driver_mod, "ws_connect", _FakeConnect([]))
    client = make_client(lambda req: json_response({}))
    watcher = client.watch_progress("p1")
    watcher.start()
    time.sleep(0.05)  # 让线程先跑起来，走一遍"无帧 → TimeoutError → continue"
    watcher.stop()
    watcher.stop()
    assert watcher.warning is None
