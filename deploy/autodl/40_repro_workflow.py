#!/usr/bin/env python3
"""40_repro_workflow.py —— 单工作流同 seed 两次出图可复现性验证（P8-09）。

用法（在 GPU 服务器上，$REPO 为仓库副本）：
    python deploy/autodl/40_repro_workflow.py <workflow_id> \
        --repo /root/autodl-tmp/l4_repo \
        --asset-map /root/autodl-tmp/l4_assets/asset_map.json \
        --case-id gs-<workflow>-001 \
        --outdir /root/autodl-tmp/repro_out

退出码：0 = 两次 IDAT 一致 · 1 = 不一致或失败
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time

REPO_DEFAULT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_DEFAULT))

from engine.registry import Registry  # noqa: E402
from engine.render import RenderOptions, load_definition, render  # noqa: E402
from engine.tools.verify_render_path import (  # noqa: E402
    ComfyClient,
    _needs_assets,
    _upload_resolver,
    idat_digest,
)


def load_asset_map(path: pathlib.Path) -> dict[int, pathlib.Path]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    out: dict[int, pathlib.Path] = {}
    for key, value in raw.items():
        p = pathlib.Path(value)
        if not p.is_file():
            raise FileNotFoundError(f"asset_map[{key}] 指向的文件不存在：{p}")
        out[int(key)] = p
    return out


def run_once(
    client: ComfyClient,
    registry: Registry,
    wid: str,
    params: dict,
    asset_map: dict[int, pathlib.Path],
    timeout: float,
) -> tuple[bytes, str]:
    entry = registry.require(wid)
    definition = load_definition(registry.definition_path(entry))
    resolver = _upload_resolver(client, asset_map)
    result = render(
        definition,
        entry.schema,
        dict(params),
        options=RenderOptions(asset_resolver=resolver),
    )
    pid = client.submit(result.workflow)
    entry_h = client.wait(pid, timeout)
    img = client.first_image(entry_h)
    if img is None:
        raise RuntimeError("产物里没有 images")
    png = client.fetch_image(img)
    return png, idat_digest(png)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("workflow", help="工作流 id，如 i2i_v1")
    ap.add_argument("--repo", default=str(REPO_DEFAULT), help="仓库副本根目录")
    ap.add_argument("--case-id", default=None, help="用例 id，默认 gs-<workflow>-001")
    ap.add_argument("--registry", default=None, help="默认 <repo>/workflows/registry.yaml")
    ap.add_argument("--asset-map", required=True, help="asset_id → 本地文件 的 JSON")
    ap.add_argument("--outdir", default="/tmp/repro_out", help="产物输出目录")
    ap.add_argument("--base-url", default="http://127.0.0.1:8188")
    ap.add_argument("--timeout", type=float, default=600.0, help="单张超时（秒）")
    ap.add_argument("--cooldown", type=float, default=2.0, help="两次生成之间冷却（秒）")
    args = ap.parse_args()

    repo = pathlib.Path(args.repo).resolve()
    registry_path = pathlib.Path(args.registry) if args.registry else repo / "workflows/registry.yaml"
    cases_dir = repo / "tests/golden_set/cases"
    case_id = args.case_id or f"gs-{args.workflow}-001"
    case_file = cases_dir / f"{case_id}.json"
    if not case_file.is_file():
        print(f"[fatal] 用例不存在：{case_file}", file=sys.stderr)
        return 2

    case = json.loads(case_file.read_text(encoding="utf-8"))
    asset_map = load_asset_map(pathlib.Path(args.asset_map))
    registry = Registry.load(str(registry_path))
    client = ComfyClient(args.base_url, timeout=args.timeout)
    client.health()

    needs = _needs_assets(registry.require(args.workflow))
    missing = [k for k in needs if int(case["params"].get(k, 0)) not in asset_map]
    if missing:
        print(f"[fatal] asset_map 缺 {missing}", file=sys.stderr)
        return 2

    outdir = pathlib.Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    params = dict(case["params"])
    seed = params.get("seed", 20260916)
    params["seed"] = seed

    print(f"=== 可复现性验证：{args.workflow} · case={case_id} · seed={seed} ===")

    png1, idat1 = run_once(client, registry, args.workflow, params, asset_map, args.timeout)
    dest1 = outdir / f"{case_id}_run1.png"
    dest1.write_bytes(png1)
    print(f"  run1: {dest1} · idat={idat1[:16]} · {len(png1)}B")

    time.sleep(args.cooldown)

    png2, idat2 = run_once(client, registry, args.workflow, params, asset_map, args.timeout)
    dest2 = outdir / f"{case_id}_run2.png"
    dest2.write_bytes(png2)
    print(f"  run2: {dest2} · idat={idat2[:16]} · {len(png2)}B")

    print()
    if idat1 == idat2:
        print(f"✅ 两次出图 IDAT 一致（{idat1[:16]}）")
        return 0
    else:
        print(f"❌ 两次出图 IDAT 不一致：run1={idat1[:16]} run2={idat2[:16]}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
