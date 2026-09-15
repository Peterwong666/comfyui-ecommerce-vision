#!/usr/bin/env python3
# ============================================================
# 04_api_smoke_test.py —— 通过 HTTP API 提交工作流并等待出图（P2-04 验证）
# 这是未来 engine/driver.py（P6-01）的雏形。
#
# 用法：
#   python 04_api_smoke_test.py <workflow.json> [outdir]
# ============================================================
import json
import os
import pathlib
import sys
import time
import urllib.parse
import urllib.request
import urllib.error
import uuid

HOST = os.environ.get("COMFY_HOST", "127.0.0.1")
PORT = os.environ.get("COMFY_PORT", "8188")
BASE = f"http://{HOST}:{PORT}"


def http_json(url, payload=None, timeout=30):
    if payload is None:
        req = urllib.request.Request(url)
    else:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url, data=data, headers={"Content-Type": "application/json"}
        )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def load_workflow(path):
    wf = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    # ComfyUI /prompt 只接受「节点 id -> 节点」的映射，剔除元数据键
    return {k: v for k, v in wf.items() if not k.startswith("_")}


def wait_for_result(prompt_id, poll_interval=2.0, timeout=900):
    started = time.time()
    while True:
        if time.time() - started > timeout:
            raise TimeoutError(f"任务超时 {timeout}s，prompt_id={prompt_id}")
        history = http_json(f"{BASE}/history/{prompt_id}")
        if prompt_id in history:
            return history[prompt_id], time.time() - started
        time.sleep(poll_interval)


def main():
    if len(sys.argv) < 2:
        print("用法: python 04_api_smoke_test.py <workflow.json> [outdir]")
        return 1
    wf_path = sys.argv[1]
    outdir = pathlib.Path(sys.argv[2] if len(sys.argv) > 2 else ".")
    outdir.mkdir(parents=True, exist_ok=True)

    # 1) 服务健康检查
    stats = http_json(f"{BASE}/system_stats")
    devices = stats.get("devices", [{}])
    print(f"[health] ComfyUI 就绪 · 设备: {devices[0].get('name', 'unknown')}")

    # 2) 提交任务
    prompt = load_workflow(wf_path)
    client_id = str(uuid.uuid4())
    resp = http_json(f"{BASE}/prompt", {"prompt": prompt, "client_id": client_id})
    prompt_id = resp["prompt_id"]
    print(f"[submit] prompt_id={prompt_id} · 节点数={len(prompt)}")

    # 3) 等待完成
    history, elapsed = wait_for_result(prompt_id)
    status = history.get("status", {})
    print(f"[result] 耗时={elapsed:.1f}s · 完成状态={status.get('status_str')}")

    if status.get("status_str") == "error":
        for msg in status.get("messages", []):
            if msg[0] == "execution_error":
                print("[error]", json.dumps(msg[1], ensure_ascii=False, indent=2))
        return 2

    # 4) 下载产物
    outputs = history.get("outputs", {})
    saved = []
    for node_id, node_out in outputs.items():
        for img in node_out.get("images", []):
            fn, sub, typ = img["filename"], img.get("subfolder", ""), img.get("type", "output")
            url = f"{BASE}/view?filename={urllib.parse.quote(fn)}&subfolder={sub}&type={typ}"
            dest = outdir / f"{node_id}_{fn}"
            with urllib.request.urlopen(url, timeout=120) as r, open(dest, "wb") as f:
                f.write(r.read())
            saved.append(str(dest))
            print(f"[image] {dest} ({dest.stat().st_size / 1024:.0f} KB)")

    if not saved:
        print("[warn] 没有产出图片")
        return 3
    print(f"[done] 冒烟测试通过，耗时 {elapsed:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
