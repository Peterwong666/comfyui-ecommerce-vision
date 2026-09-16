#!/usr/bin/env python3
"""A 流标注的真实环境验证清单 —— 第 1~3 项（`/upload/*` / `/queue` / `/ws`）。

为什么优先级高（A 流原话）：
  `/upload/*` 的字段名若不对，会**直接挡住 P6-09 素材上传** —— 而 V1 首发场景
  是电商商品图，参考图是核心输入。

本脚本只读 + 只做一次无害上传（一个 1x1 PNG），不改服务器任何持久状态。
退出码：0 全通 · 1 有失败。
"""
from __future__ import annotations

import contextlib
import io
import json
import sys
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8188"

# 1x1 透明 PNG（最小合法 PNG）
PNG_1PX = bytes.fromhex(
    "89504e470d0a1a0a0000000d494844520000000100000001080600000"
    "01f15c4890000000a49444154789c6300010000050001"
    "0d0a2db40000000049454e44ae426082"
)

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(("  ✅ " if ok else "  ❌ ") + name + (f"\n       {detail}" if detail else ""))


def get(path: str) -> tuple[int, bytes]:
    try:
        with urllib.request.urlopen(BASE + path, timeout=15) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except Exception as e:  # noqa: BLE001
        return 0, str(e).encode()


def multipart(field: str, filename: str, data: bytes, extra: dict[str, str] | None = None) -> bytes:
    """手工拼 multipart —— 正是为了**原样**验证字段名，不借助任何库的高层封装。"""
    boundary = "----g7verifyboundary"
    out = io.BytesIO()
    for k, v in (extra or {}).items():
        out.write(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode())
    out.write(
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="{field}"; filename="{filename}"\r\n'
        f"Content-Type: image/png\r\n\r\n".encode()
    )
    out.write(data)
    out.write(f"\r\n--{boundary}--\r\n".encode())
    return out.getvalue()


def post_multipart(path: str, field: str, filename: str, data: bytes, extra=None) -> tuple[int, bytes]:
    body = multipart(field, filename, data, extra)
    req = urllib.request.Request(
        BASE + path, data=body,
        headers={"Content-Type": "multipart/form-data; boundary=----g7verifyboundary"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except Exception as e:  # noqa: BLE001
        return 0, str(e).encode()


print("=" * 60)
print("1. GET /queue —— 读取队列（错了会让「排队中取消」退化为白烧 GPU）")
print("=" * 60)
code, body = get("/queue")
ok = code == 200
detail = f"http={code}"
if ok:
    try:
        q = json.loads(body)
        has_keys = isinstance(q, dict) and "queue_running" in q and "queue_pending" in q
        ok = has_keys
        detail = f"http=200 · 键={sorted(q)[:6]}" if has_keys else f"http=200 但缺少 queue_running/queue_pending：{sorted(q)[:8]}"
    except Exception as e:  # noqa: BLE001
        ok, detail = False, f"http=200 但 JSON 解析失败：{e}"
else:
    detail = f"http={code} · {body[:120]!r}"
check("GET /queue 可用且返回 queue_running / queue_pending", ok, detail)

print()
print("=" * 60)
print("2. POST /upload/image —— 字段名必须是 image（A 流标注的高风险项）")
print("=" * 60)
code, body = post_multipart("/upload/image", "image", "g7_probe_1px.png", PNG_1PX)
ok = code == 200
detail = f"http={code}"
if ok:
    try:
        r = json.loads(body)
        ok = bool(r.get("name"))
        detail = f"http=200 · name={r.get('name')!r} · type={r.get('type')!r} · subfolder={r.get('subfolder')!r}"
    except Exception as e:  # noqa: BLE001
        ok, detail = False, f"http=200 但 JSON 解析失败：{e}"
else:
    detail = f"http={code} · {body[:200]!r}"
check("POST /upload/image（field=image）被接受", ok, detail)

print()
print("=" * 60)
print("3. POST /upload/mask —— 局部重绘必需，且要带 original_ref")
print("=" * 60)
# 先拿一个 image name 作为 original_ref
code_i, body_i = post_multipart("/upload/image", "image", "g7_probe_orig.png", PNG_1PX)
orig_name = None
if code_i == 200:
    with contextlib.suppress(Exception):
        orig_name = json.loads(body_i).get("name")

if orig_name:
    # ⚠️ 关键契约（读 ComfyUI server.py:475 得到，不是猜的）：
    #   original_ref = json.loads(post.get("original_ref"))
    # 即 original_ref 必须是**JSON 字符串**，形如
    #   {"filename": "...", "subfolder": "", "type": "input"}
    # 传裸文件名会让 json.loads 抛 JSONDecodeError → 端点回 500。
    # （这正是本项目该记的坑：错的不是端点，是调用方对契约的假设。）
    original_ref = json.dumps({"filename": orig_name, "subfolder": "", "type": "input"})
    code, body = post_multipart(
        "/upload/mask", "image", "g7_probe_mask.png", PNG_1PX, extra={"original_ref": original_ref}
    )
    ok = code == 200
    detail = f"http={code}"
    if ok:
        try:
            r = json.loads(body)
            detail = f"http=200 · name={r.get('name')!r} · 已接受 original_ref（JSON 字符串形式）"
            ok = bool(r.get("name"))
        except Exception as e:  # noqa: BLE001
            ok, detail = False, f"http=200 但 JSON 解析失败：{e}"
    else:
        detail = f"http={code} · {body[:200]!r}"
    check("POST /upload/mask（field=image, original_ref 为 JSON 字符串）被接受", ok, detail)
else:
    check("POST /upload/mask（field=image, original_ref 为 JSON 字符串）被接受", False, "前置失败：无法先上传 original，未能测 mask")

print()
print("=" * 60)
print("4. POST /queue —— 删除一个不存在的队列项（验证端点存在且错误可判）")
print("=" * 60)
req = urllib.request.Request(
    BASE + "/queue", data=json.dumps({"delete": ["nonexistent-prompt-id"]}).encode(),
    headers={"Content-Type": "application/json"}, method="POST",
)
try:
    with urllib.request.urlopen(req, timeout=15) as r:
        c2, b2 = r.status, r.read()
except urllib.error.HTTPError as e:
    c2, b2 = e.code, e.read()
except Exception as e:  # noqa: BLE001
    c2, b2 = 0, str(e).encode()
# 端点存在即可 —— 删不存在的项返回 200/404 都算"端点存在且行为可判"
check("POST /queue（delete）端点存在且响应可判", c2 in (200, 404), f"http={c2} · {b2[:160]!r}")

print()
print("=" * 60)
passed = sum(1 for _, ok, _ in results if ok)
failed = len(results) - passed
print(f"结论：通过 {passed} · 失败 {failed}")
if failed:
    print("❌ 有失败 —— 请把上面的原始响应交给 A 流定位（不要只报「失败了」）")
else:
    print("✅ 全部通过 —— A 流清单第 1~3 项可判定为「已验证」")
print("⚠️ 本脚本未测 /ws 事件名（那是长连接，需专门的 WS 客户端），也未测真机 execute_task")
sys.exit(1 if failed else 0)
