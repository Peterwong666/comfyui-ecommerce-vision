#!/usr/bin/env python3
"""39b_prepare_gs_assets.py —— golden set 跑批前的素材准备（生成 asset_map.json）。

映射策略（V1 口径，限制见 README）：
  · i2i（asset 1–6）与 upscale（asset 21–25）→ 同一张参考图 `--base`
    （用例间的差异来自提示词/参数，V1 不追求每例独立素材）
  · inpaint（asset 11–15）→ 由 `--base` 生成 **5 个 alpha 变体**：
    透明圆（alpha=0 = 被重绘区）半径/圆心各不同，让 5 个用例的蒙版形状有差异
  · 其余出现的 asset_id 一律报错退出 —— 宁可失败也不静默用错图

依赖：PIL + numpy（服务器 conda 环境已有，37 号探针在用）。

用法（GPU 服务器上）：
    python deploy/autodl/39b_prepare_gs_assets.py \
        --base /root/autodl-tmp/l4_assets/ref_product.png \
        --outdir /root/autodl-tmp/gs_assets \
        --asset-map /root/autodl-tmp/l4_assets/asset_map.json
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

I2I_IDS = range(1, 7)       # 1–6
INPAINT_IDS = range(11, 16)  # 11–15
UPSCALE_IDS = range(21, 26)  # 21–25

#: 5 个 inpaint 变体的透明圆参数：(圆心 x 比例, 圆心 y 比例, 半径比例)
#: 比例相对图宽/高。半径 0.18–0.30 覆盖「小瑕疵修补」到「大面积换区」。
CIRCLES = [
    (0.50, 0.50, 0.22),   # 居中大圆（与 37 号探针同口径，便于交叉核对）
    (0.35, 0.40, 0.15),   # 左上小圆（小瑕疵）
    (0.65, 0.60, 0.18),   # 右下中圆
    (0.50, 0.30, 0.25),   # 上部大圆（换背景上沿）
    (0.30, 0.65, 0.20),   # 左下中圆
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True, help="基准参考图（RGB PNG）")
    ap.add_argument("--outdir", required=True, help="变体图片输出目录")
    ap.add_argument("--asset-map", required=True, help="asset_map.json 输出路径")
    args = ap.parse_args()

    try:
        import numpy as np
        from PIL import Image
    except ImportError as exc:
        print(f"[fatal] 需要 PIL + numpy：{exc}", file=sys.stderr)
        return 2

    base_path = pathlib.Path(args.base)
    if not base_path.is_file():
        print(f"[fatal] 基准图不存在：{base_path}", file=sys.stderr)
        return 2

    outdir = pathlib.Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    base = Image.open(base_path).convert("RGB")
    w, h = base.size
    arr = np.asarray(base)

    asset_map: dict[str, str] = {}
    for i in I2I_IDS:
        asset_map[str(i)] = str(base_path)
    for i in UPSCALE_IDS:
        asset_map[str(i)] = str(base_path)

    # RGBA 变体：圆内 alpha=0（被重绘区），圆外 alpha=255（保留区）——极性已由 37 号探针钉死
    yy, xx = np.mgrid[0:h, 0:w]
    for idx, i in enumerate(INPAINT_IDS):
        cx, cy, r = CIRCLES[idx]
        dist = np.sqrt(((xx - cx * w) / (r * w)) ** 2 + ((yy - cy * h) / (r * h)) ** 2)
        alpha = np.where(dist <= 1.0, 0, 255).astype(np.uint8)
        rgba = np.dstack([arr, alpha])
        p = outdir / f"gs_inpaint_mask_{i:02d}.png"
        Image.fromarray(rgba, mode="RGBA").save(p)
        asset_map[str(i)] = str(p)

    map_path = pathlib.Path(args.asset_map)
    map_path.parent.mkdir(parents=True, exist_ok=True)
    map_path.write_text(json.dumps(asset_map, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"[ok] 基准图 {base_path}（{w}x{h}）")
    print(f"[ok] inpaint 变体 {len(CIRCLES)} 个 → {outdir}")
    print(f"[ok] asset_map（{len(asset_map)} 项）→ {map_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
