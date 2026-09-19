#!/usr/bin/env python3
"""39_golden_set_run.py —— golden set 真实出图跑批（P8-08b / P8-01 的 L4 部分）。

遍历 `tests/golden_set/cases/*.json`，对每个用例：
  1. engine/render 渲染（asset_id 经 `--asset-map` 解析为本地文件 → /upload/image）
  2. 提交 ComfyUI 真实出图
  3. 产物存 `--outdir/<case_id>.png`，结果记录进 `--results`（JSON）

设计要点：
  · **不改用例文件**：本脚本只产出「跑批结果」，评分与 expected 回填由后续人工/评审做
  · 失败例**记录原因后继续**，不中断整批（连续失败 ≥3 次才中止，防引擎坏掉后白烧机时）
  · 每次 run 前后打时间戳，机时核算用
  · 资产映射缺项 → 该用例记 skipped，不算失败

用法（在 GPU 服务器上，$REPO 为仓库副本）：
    python deploy/autodl/39_golden_set_run.py \
        --repo /root/autodl-tmp/l4_repo \
        --asset-map /root/autodl-tmp/l4_assets/asset_map.json \
        --outdir /root/autodl-tmp/gs_out \
        --results /root/autodl-tmp/l4_evidence/39_golden_set_results.json

退出码：0 = 全部跑完（允许有失败/跳过）· 1 = 连续失败中止 · 2 = 前置错误
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import pathlib
import sys
import time

REPO_DEFAULT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_DEFAULT))

from engine.render import RenderOptions, load_definition, render  # noqa: E402
from engine.registry import Registry  # noqa: E402
from engine.tools.verify_render_path import (  # noqa: E402
    ComfyClient,
    _needs_assets,
    _upload_resolver,
    idat_digest,
)


def load_asset_map(path: pathlib.Path) -> dict[int, pathlib.Path]:
    """`--asset-map` 的 JSON：`{"1": "/path/a.png", "11": "/path/b.png", ...}`。"""
    raw = json.loads(path.read_text(encoding="utf-8"))
    out: dict[int, pathlib.Path] = {}
    for key, value in raw.items():
        p = pathlib.Path(value)
        if not p.is_file():
            raise FileNotFoundError(f"asset_map[{key}] 指向的文件不存在：{p}")
        out[int(key)] = p
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=str(REPO_DEFAULT), help="仓库副本根目录")
    ap.add_argument("--cases-dir", default=None, help="默认 <repo>/tests/golden_set/cases")
    ap.add_argument("--registry", default=None, help="默认 <repo>/workflows/registry.yaml")
    ap.add_argument("--asset-map", required=True, help="asset_id → 本地文件 的 JSON")
    ap.add_argument("--outdir", required=True, help="产物输出目录")
    ap.add_argument("--results", required=True, help="结果 JSON 输出路径")
    ap.add_argument("--base-url", default="http://127.0.0.1:8188")
    ap.add_argument("--timeout", type=float, default=600.0, help="单张超时（秒）")
    ap.add_argument("--max-consecutive-fail", type=int, default=3)
    args = ap.parse_args()

    repo = pathlib.Path(args.repo).resolve()
    cases_dir = pathlib.Path(args.cases_dir) if args.cases_dir else repo / "tests/golden_set/cases"
    registry_path = pathlib.Path(args.registry) if args.registry else repo / "workflows/registry.yaml"
    outdir = pathlib.Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    results_path = pathlib.Path(args.results)
    results_path.parent.mkdir(parents=True, exist_ok=True)

    asset_map = load_asset_map(pathlib.Path(args.asset_map))
    registry = Registry.load(str(registry_path))
    client = ComfyClient(args.base_url, timeout=args.timeout)

    client.health()  # 前置：连不上就别开跑
    print(f"[引擎] {args.base_url} 可达")
    print(f"[用例] {cases_dir} · [资产] {len(asset_map)} 项")

    case_files = sorted(cases_dir.glob("gs-*.json"))
    if not case_files:
        print(f"[fatal] {cases_dir} 下没有 gs-*.json 用例", file=sys.stderr)
        return 2

    started_at = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
    results: list[dict] = []
    consecutive_fail = 0

    for cf in case_files:
        case = json.loads(cf.read_text(encoding="utf-8"))
        cid = case["id"]
        wid = case["workflow"]
        rec: dict = {
            "id": cid,
            "workflow": wid,
            "seed": case.get("params", {}).get("seed"),
            "status": "pending",
            "started_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        }
        t0 = time.monotonic()
        try:
            entry = registry.require(wid)
            definition = load_definition(registry.definition_path(entry))

            needs = _needs_assets(entry)
            missing = [k for k in needs if int(case["params"].get(k, 0)) not in asset_map]
            if missing:
                rec.update(status="skipped", reason=f"asset_map 缺 {missing}")
                results.append(rec)
                print(f"⏭ {cid} ({wid}) skipped：asset_map 缺 {missing}")
                continue

            resolver = _upload_resolver(client, asset_map)
            result = render(
                definition,
                entry.schema,
                dict(case["params"]),
                options=RenderOptions(asset_resolver=resolver),
            )
            for w in result.warnings:
                print(f"  ⚠ {cid}: {w}")

            pid = client.submit(result.workflow)
            entry_h = client.wait(pid, args.timeout)
            img = client.first_image(entry_h)
            if img is None:
                raise RuntimeError("产物里没有 images")
            png = client.fetch_image(img)
            dest = outdir / f"{cid}.png"
            dest.write_bytes(png)

            duration = time.monotonic() - t0
            rec.update(
                status="ok",
                duration_s=round(duration, 2),
                output=str(dest),
                bytes=len(png),
                idat=idat_digest(png)[:16],
                engine_seed=result.seed,
            )
            consecutive_fail = 0
            print(f"✅ {cid} ({wid}) {duration:.2f}s · {len(png)}B · {dest.name}")
        except Exception as exc:  # noqa: BLE001 —— 单例失败要继续，原因必须留档
            duration = time.monotonic() - t0
            rec.update(status="failed", duration_s=round(duration, 2), reason=str(exc)[:500])
            consecutive_fail += 1
            print(f"❌ {cid} ({wid}) {duration:.2f}s · {str(exc)[:200]}")
        finally:
            rec["finished_at"] = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
            results.append(rec)
            # 每例落一次盘：中途断了也有已完成部分的结果
            results_path.write_text(
                json.dumps(
                    {"started_at": started_at, "results": results},
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

        if consecutive_fail >= args.max_consecutive_fail:
            print(
                f"[abort] 连续 {consecutive_fail} 例失败，中止整批（防引擎故障白烧机时）",
                file=sys.stderr,
            )
            break

    ok = sum(1 for r in results if r["status"] == "ok")
    failed = sum(1 for r in results if r["status"] == "failed")
    skipped = sum(1 for r in results if r["status"] == "skipped")
    total_elapsed = (
        datetime.datetime.now().astimezone() - datetime.datetime.fromisoformat(started_at)
    ).total_seconds()
    summary = {
        "started_at": started_at,
        "finished_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "total_elapsed_s": round(total_elapsed, 1),
        "total": len(results),
        "ok": ok,
        "failed": failed,
        "skipped": skipped,
        "results": results,
    }
    results_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print()
    print(f"=== 汇总 === ok={ok} failed={failed} skipped={skipped} / {len(results)}")
    print(f"开始 {started_at} · 结束 {summary['finished_at']} · 总耗时 {total_elapsed:.0f}s")
    print(f"结果 → {results_path}")
    print("⚠️ 本脚本只产出「跑批结果」；评分与 expected 回填属后续评审（quality_rubric.md）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
