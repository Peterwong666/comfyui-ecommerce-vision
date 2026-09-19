#!/usr/bin/env python3
"""38_baseline_workflow.py —— 按注册表口径跑稳态基准（N=12 warmup=1）。

用途：为 `verification: gpu_verified` 回填 `baseline.measured_at` 与 `min_s`。
与 `07_bench_workflow.py` 的区别：
  · 本脚本走 **engine/render** 渲染路径（与生产同构）
  · 参数来源是注册表 `param_schema` 的 default + 命令行覆盖
  · 支持 `reference_image` 等需要 `asset_resolver` 的字段

用法：
    python deploy/autodl/38_baseline_workflow.py <workflow_id> \
        --asset 1=/path/to/ref.png \
        [--n 12] [--warmup 1] [--seed-base 20260918] \
        [--param width=1024] [--param steps=20]

退出码：0 = 成功打印 min/median/P95 · 1 = 失败
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

# 复用 verify_render_path 的 HTTP 客户端与素材解析，避免重复造轮子
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from engine.registry import Registry
from engine.render import RenderOptions, load_definition, render
from engine.tools.verify_render_path import (
    ComfyClient,
    _field_defaults,
    _needs_assets,
    _parse_assets,
    _seed_field_key,
    _upload_resolver,
)

HOST = "127.0.0.1"
PORT = 8188
BASE = f"http://{HOST}:{PORT}"


def percentile_nearest_rank(sorted_vals: list[float], pct: float) -> float:
    n = len(sorted_vals)
    if n == 0:
        return float("nan")
    k = max(1, math.ceil(pct / 100.0 * n))
    return sorted_vals[min(k, n) - 1]


def run_once(client: ComfyClient, workflow: dict[str, Any], timeout: float) -> float:
    started = time.monotonic()
    pid = client.submit(workflow)
    entry = client.wait(pid, timeout)
    img = client.first_image(entry)
    if img is None:
        raise RuntimeError("产物中没有图片")
    # 顺手读一次字节，确保产物真的可用
    client.fetch_image(img)
    return time.monotonic() - started


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("workflow", help="注册表里的工作流 id，如 i2i_v1")
    ap.add_argument("--registry", default="workflows/registry.yaml")
    ap.add_argument("--base-url", default=BASE)
    ap.add_argument("--asset", action="append", help="ASSET_ID=本地图片路径")
    ap.add_argument("--param", action="append", help="key=value 覆盖 Schema default")
    ap.add_argument("--n", type=int, default=12, help="计入统计的运行次数")
    ap.add_argument("--warmup", type=int, default=1, help="warmup 次数，不计入统计")
    ap.add_argument("--seed-base", type=int, default=20260918)
    ap.add_argument("--timeout", type=float, default=600.0, help="单张超时（秒）")
    args = ap.parse_args()

    registry = Registry.load(args.registry)
    entry = registry.require(args.workflow)
    definition = load_definition(registry.definition_path(entry))

    seed_key = _seed_field_key(entry)
    if seed_key is None:
        print(f"[fatal] {entry.id} 没有 type=seed 字段，无法做确定性基准", file=sys.stderr)
        return 1

    params = _field_defaults(entry)
    for item in args.param or []:
        key, sep, value = item.partition("=")
        if not sep:
            print(f"[fatal] --param 需要 key=value：{item}", file=sys.stderr)
            return 1
        # 尝试数字转换
        try:
            params[key] = int(value)
        except ValueError:
            try:
                params[key] = float(value)
            except ValueError:
                params[key] = value

    assets = _parse_assets(args.asset)
    needs = _needs_assets(entry)
    if needs and not assets:
        print(f"[fatal] {entry.id} 需要素材：{needs}，请用 --asset 提供", file=sys.stderr)
        return 1

    client = ComfyClient(args.base_url, timeout=args.timeout)
    try:
        stats = client.health()
        dev = (stats.get("devices") or [{}])[0]
        print(f"引擎: {dev.get('name')} · vram_total={(dev.get('vram_total') or 0) / 1024**3:.1f}GiB")
    except (urllib.error.URLError, OSError) as exc:
        print(f"[fatal] 连不上 {args.base_url}: {exc}", file=sys.stderr)
        return 1

    resolver = _upload_resolver(client, assets) if assets else None

    print(f"=== 稳态基准：{entry.id} (version {entry.version}) ===")
    print(f"参数: {params}")
    print(f"warmup={args.warmup} · n={args.n} · 判读用 min")

    # warmup
    for i in range(args.warmup):
        params[seed_key] = args.seed_base + i
        result = render(definition, entry.schema, params, options=RenderOptions(asset_resolver=resolver))
        t = run_once(client, result.workflow, args.timeout)
        print(f"  warmup {i+1}/{args.warmup}: {t:.2f}s")

    times: list[float] = []
    for i in range(args.n):
        params[seed_key] = args.seed_base + args.warmup + i
        result = render(definition, entry.schema, params, options=RenderOptions(asset_resolver=resolver))
        t = run_once(client, result.workflow, args.timeout)
        times.append(t)
        print(f"  run {i+1}/{args.n}: {t:.2f}s")

    times.sort()
    print()
    print("=== 结果 ===")
    print(f"min    = {min(times):.2f}s")
    print(f"median = {statistics.median(times):.2f}s")
    print(f"P95    = {percentile_nearest_rank(times, 95):.2f}s")
    print(f"mean   = {statistics.mean(times):.2f}s")
    print(f"max    = {max(times):.2f}s")
    print()
    print("回填 registry.yaml 的 baseline 段：")
    print(f"  measured_at: \"{time.strftime('%Y-%m-%dT%H:%M:%S%z', time.localtime())}\"")
    print(f"  min_s: {min(times):.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
