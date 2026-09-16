#!/usr/bin/env python3
# ============================================================
# 07_bench_workflow.py —— 工作流出图耗时基准测试
#
# 用途：实测单张出图耗时的 P50/P95，用于回填 PRD NFR-1。
#       是 P9-04 压测脚本的前身（当前为串行单并发）。
#
# 设计要点：
#   1) 先跑 1 次 warmup 不计入统计 —— 首次执行含模型加载/图编译开销，
#      若混入统计会严重高估 P95（本项目已因此踩过坑）。
#   2) 每次运行改 seed，避免 ComfyUI 侧缓存整条执行结果导致耗时失真。
#   3) P95 用 nearest-rank 法（小样本下比插值法更保守、更可解释）。
#
# 用法：
#   python 07_bench_workflow.py <workflow.json> [-n 12] [--warmup 1]
#          [--steps 30] [--seed-base 20260915] [--outdir DIR]
# ============================================================
import argparse
import json
import pathlib
import statistics
import sys
import time
import urllib.parse
import urllib.request

HOST = "127.0.0.1"
PORT = "8188"
BASE = f"http://{HOST}:{PORT}"


def http_json(url, payload=None, timeout=60):
    if payload is None:
        req = urllib.request.Request(url)
    else:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def load_workflow(path):
    wf = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    return {k: v for k, v in wf.items() if not k.startswith("_")}


def find_input_targets(wf, key_names):
    """按「入参名」而非「节点类型」定位目标节点。

    不同采样范式下 seed/steps 挂在完全不同的节点上：
      - KSampler / KSamplerAdvanced : inputs.seed（steps 也在同节点）
      - SamplerCustomAdvanced 系    : RandomNoise.inputs.noise_seed，
                                      步数在 Flux2Scheduler.inputs.steps
    故按入参名匹配，才能同时覆盖 SDXL 与 FLUX.2 两条链路。
    """
    hits = {}
    for nid, node in wf.items():
        for key in key_names:
            if key in node.get("inputs", {}):
                hits.setdefault(key, []).append(nid)
    return hits


def percentile_nearest_rank(sorted_vals, pct):
    """nearest-rank 法：ceil(pct/100 * N) 取第几个值（1-based）。"""
    import math
    n = len(sorted_vals)
    if n == 0:
        return float("nan")
    k = max(1, math.ceil(pct / 100.0 * n))
    return sorted_vals[min(k, n) - 1]


def run_once(wf, outdir, tag):
    client_id = "bench"
    started = time.time()
    resp = http_json(f"{BASE}/prompt", {"prompt": wf, "client_id": client_id})
    prompt_id = resp["prompt_id"]
    while True:
        if time.time() - started > 1800:
            raise TimeoutError(f"run {tag} 超时")
        hist = http_json(f"{BASE}/history/{prompt_id}")
        if prompt_id in hist:
            break
        time.sleep(0.3)
    elapsed = time.time() - started

    entry = hist[prompt_id]
    if entry.get("status", {}).get("status_str") == "error":
        for msg in entry["status"].get("messages", []):
            if msg[0] == "execution_error":
                print("[error]", json.dumps(msg[1], ensure_ascii=False)[:800])
        raise RuntimeError(f"run {tag} 执行失败")

    # 存第一张图，便于人工确认「不是靠报错提前返回」
    if tag == "warmup":
        for _nid, no in entry.get("outputs", {}).items():
            for img in no.get("images", []):
                q = urllib.parse.urlencode(
                    {
                        "filename": img["filename"],
                        "subfolder": img.get("subfolder", ""),
                        "type": img.get("type", "output"),
                    }
                )
                dest = outdir / f"{tag}_{img['filename']}"
                with urllib.request.urlopen(f"{BASE}/view?{q}", timeout=120) as r:
                    dest.write_bytes(r.read())
                print(f"[image] {dest}")
                break
    return elapsed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("workflow")
    ap.add_argument("-n", type=int, default=12, help="计入统计的运行次数")
    ap.add_argument("--warmup", type=int, default=1)
    ap.add_argument("--steps", type=int, default=None, help="覆盖 KSampler steps")
    ap.add_argument("--seed-base", type=int, default=20260915)
    ap.add_argument("--outdir", default="bench_out")
    args = ap.parse_args()

    wf_path = pathlib.Path(args.workflow)
    outdir = pathlib.Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    stats = http_json(f"{BASE}/system_stats")
    dev = stats.get("devices", [{}])[0]
    print(f"[health] {dev.get('name')} · vram_total={dev.get('vram_total', 0)/1024**3:.1f}GiB")

    # 定位 seed / steps 挂载点（按入参名匹配，兼容 SDXL 与 FLUX.2 两种范式）
    targets = find_input_targets(load_workflow(wf_path), ["seed", "noise_seed", "steps"])
    seed_slots = targets.get("seed", []) + targets.get("noise_seed", [])
    step_slots = targets.get("steps", [])
    if not seed_slots:
        print("[fatal] 工作流里找不到 seed / noise_seed 入参，无法避免缓存")
        return 1
    print(f"[info] seed 挂载点: {seed_slots} · steps 挂载点: {step_slots} · steps 覆盖: {args.steps}")

    times = []
    for i in range(args.warmup + args.n):
        wf = load_workflow(wf_path)
        for nid, key in [(n, "seed") for n in targets.get("seed", [])] + \
                        [(n, "noise_seed") for n in targets.get("noise_seed", [])]:
            wf[nid]["inputs"][key] = args.seed_base + i
        if args.steps is not None:
            for nid in step_slots:
                wf[nid]["inputs"]["steps"] = args.steps
        tag = "warmup" if i < args.warmup else f"run{i - args.warmup + 1:02d}"
        try:
            t = run_once(wf, outdir, tag)
        except Exception as exc:  # noqa: BLE001
            print(f"[{tag}] 失败: {exc}")
            return 2
        if i < args.warmup:
            print(f"[{tag}] {t:.2f}s （不计入统计）")
        else:
            times.append(t)
            print(f"[{tag}] {t:.2f}s")

    s = sorted(times)
    print("\n================ 统计结果 ================")
    print(f"样本数 N = {len(times)}")
    print(f"min   = {min(s):.2f}s")
    print(f"mean  = {statistics.mean(s):.2f}s")
    print(f"median= {statistics.median(s):.2f}s")
    print(f"P95   = {percentile_nearest_rank(s, 95):.2f}s")
    print(f"max   = {max(s):.2f}s")
    print(f"stdev = {statistics.pstdev(s):.2f}s")
    print("原始样本:", ", ".join(f"{t:.2f}" for t in times))
    return 0


if __name__ == "__main__":
    sys.exit(main())
