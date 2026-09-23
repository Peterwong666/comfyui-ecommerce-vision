#!/usr/bin/env python3
"""41_p4_inspect_models.py —— 在 GPU 机上核实 P4 依赖模型，只读不写。

三个问题必须用事实回答（不许猜）：
  ① `CLIP-ViT-bigG-14-...safetensors` 的真实 hidden dim 是多少？
     IP-Adapter 报错 `proj_in [1280,1280] vs [1280,1664]` —— 需要确认 clip_vision
     到底是 bigG(1664) 还是 ViT-H(1280)。
  ② 两个 IP-Adapter(vit-h) 的 `image_proj.proj_in.weight` 期望输入维度是多少？
  ③ 已下载的 `controlnet-{canny,depth}-sdxl-1.0.safetensors` 键名前缀是
     ComfyUI 原生（`controlnet_cond_embedding.*` / `input_blocks.*`）还是 diffusers
     （`down_blocks.*`）？后者 ComfyUI `ControlNetLoader` 未必能直接吃。

只做 header 解析（safetensors 头部是明文的 JSON），不加载权重、不占显存。
"""

from __future__ import annotations

import json
import struct
import sys
from pathlib import Path

MODELS = Path("/root/ComfyUI/models")


def read_header(path: Path) -> dict:
    """读 safetensors 头部 JSON（前 8 字节 = u64 little-endian 头长）。"""
    with path.open("rb") as f:
        raw = f.read(8)
        if len(raw) != 8:
            raise ValueError(f"{path} 太小，不是 safetensors")
        (n,) = struct.unpack("<Q", raw)
        if n <= 0 or n > 200 * 1024 * 1024:
            raise ValueError(f"{path} 头长异常：{n}")
        head = f.read(n)
    return json.loads(head)


def fmt_numel(n: int) -> str:
    if n >= 1024**3:
        return f"{n / 1024**3:.2f}G"
    if n >= 1024**2:
        return f"{n / 1024**2:.1f}M"
    if n >= 1024:
        return f"{n / 1024:.1f}K"
    return str(n)


def report_clip_vision() -> None:
    print("=" * 68)
    print("① clip_vision —— 判定 hidden dim")
    print("=" * 68)
    d = MODELS / "clip_vision"
    for f in sorted(d.glob("*.safetensors")):
        try:
            h = read_header(f)
        except Exception as exc:  # noqa: BLE001
            print(f"  [破损] {f.name}: {exc}")
            continue
        shapes = {k: v["shape"] for k, v in h.items() if k != "__metadata__"}
        # 用视觉 transformer 的第一层投影输入维度判定 hidden dim
        hidden = None
        for key in (
            "vision_model.embeddings.patch_embedding.weight",
            "vision_model.embeddings.class_embedding",
        ):
            if key in shapes:
                hidden = shapes[key][-1] if "weight" in key else shapes[key][0]
                break
        if hidden is None:
            # 兜底：找所有 4D 或 1D 卷积/嵌入，取最常见的最大维
            cand = {v[-1] for k, v in shapes.items() if len(v) >= 2}
            hidden = max(cand) if cand else None
        nparam = sum(
            int(__import__("math").prod(v["shape"]))
            for k, v in h.items()
            if k != "__metadata__"
        )
        verdict = {1664: "bigG", 1280: "ViT-H", 1024: "ViT-L", 768: "ViT-B"}.get(
            hidden, "未知"
        )
        print(f"  {f.name}")
        print(f"    文件大小 : {f.stat().st_size / 1024**3:.2f} GiB")
        print(f"    张量数   : {len(shapes)} · 参数量约 {fmt_numel(nparam)}")
        print(f"    视觉宽度 : {hidden}  =>  {verdict}")
        meta = h.get("__metadata__")
        if meta:
            print(f"    metadata : {json.dumps(meta, ensure_ascii=False)[:200]}")


def report_ipadapter() -> None:
    print()
    print("=" * 68)
    print("② IP-Adapter —— 判定 image_proj 期望输入维度")
    print("=" * 68)
    d = MODELS / "ipadapter"
    for f in sorted(d.glob("*.safetensors")):
        try:
            h = read_header(f)
        except Exception as exc:  # noqa: BLE001
            print(f"  [破损] {f.name}: {exc}")
            continue
        shapes = {k: v["shape"] for k, v in h.items() if k != "__metadata__"}
        proj = {
            k: v for k, v in shapes.items() if "image_proj" in k and "proj_in" in k
        }
        expects = None
        for k, v in proj.items():
            if "weight" in k:
                expects = v[1]  # Linear weight = [out, in]
                break
        verdict = {1280: "需要 ViT-H", 1664: "需要 bigG", 1024: "需要 ViT-L"}.get(
            expects, "未知"
        )
        print(f"  {f.name}")
        print(f"    文件大小 : {f.stat().st_size / 1024**3:.2f} GiB")
        print(f"    张量数   : {len(shapes)}")
        for k, v in list(proj.items())[:3]:
            print(f"    {k}: {v}")
        print(f"    image_proj 输入宽度 : {expects}  =>  {verdict}")


def report_controlnet() -> None:
    print()
    print("=" * 68)
    print("③ ControlNet —— 判定键名风格（ComfyUI 原生 vs diffusers）")
    print("=" * 68)
    d = MODELS / "controlnet"
    native_marks = ("controlnet_cond_embedding", "input_blocks.", "middle_block.", "zero_convs.")
    diff_marks = ("down_blocks.", "mid_block.", "up_blocks.", "controlnet_cond_embedding.conv_in")
    for f in sorted(d.glob("*.safetensors")):
        try:
            h = read_header(f)
        except Exception as exc:  # noqa: BLE001
            print(f"  [破损] {f.name}: {exc}")
            continue
        keys = [k for k in h if k != "__metadata__"]
        s = set()
        for k in keys:
            s.add(k)
        native_hit = [m for m in native_marks if any(m in k for k in keys)]
        diff_hit = [m for m in diff_marks if any(m in k for k in keys)]
        style = "原生(ComfyUI)" if native_hit and not diff_hit else (
            "diffusers" if diff_hit and not native_hit else "混合/存疑"
        )
        print(f"  {f.name}")
        print(f"    文件大小 : {f.stat().st_size / 1024**3:.2f} GiB")
        print(f"    张量数   : {len(keys)}")
        print(f"    风格判定 : {style}")
        print(f"    原生线索 : {native_hit or '无'}")
        print(f"    diff线索 : {diff_hit or '无'}")
        print(f"    前 4 个键 : {keys[:4]}")
        meta = h.get("__metadata__")
        if meta:
            print(f"    metadata : {json.dumps(meta, ensure_ascii=False)[:200]}")


def report_inventory() -> None:
    print()
    print("=" * 68)
    print("④ 目录清单")
    print("=" * 68)
    for sub in ("checkpoints", "controlnet", "ipadapter", "clip_vision", "loras", "sams", "upscale_models"):
        p = MODELS / sub
        if not p.exists():
            print(f"  {sub}/: (不存在)")
            continue
        files = sorted(f for f in p.iterdir() if f.is_file() and f.stat().st_size > 0)
        total = sum(f.stat().st_size for f in files)
        print(f"  {sub}/: {len(files)} 个 · {total / 1024**3:.2f} GiB")
        for f in files:
            print(f"     - {f.name} ({f.stat().st_size / 1024**3:.2f} GiB)")


def main() -> int:
    if not MODELS.exists():
        print(f"[fatal] {MODELS} 不存在", file=sys.stderr)
        return 1
    report_clip_vision()
    report_ipadapter()
    report_controlnet()
    report_inventory()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
