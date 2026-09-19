#!/usr/bin/env python3
"""inpaint_v1 的「蒙版极性」探针 —— 只产出**客观信号**，语义判断留给人工。

背景
----
`inpaint_v1` 的设计前提是「**透明区 = 待重绘区**」，但这个前提
**从未在真实出图上验证过**（见 `workflows/registry.yaml` 的 `verification_note`）。
`LoadImage` 的 MASK 输出极性在 `/object_info` 快照里读不到语义，
而它直接决定「重绘哪一块」—— 反了就等于**去重绘非选区**。

本探针做什么
------------
用**同一张参考图、同一 seed**，只改一个变量：参考图的 **alpha 通道取反**。

> 为什么改 alpha 就等于改蒙版：`LoadImage` 的 MASK 输出是由 alpha 导出的
> （ComfyUI 里是 `mask = 1 - alpha`）。所以
> `alpha` 与 `1-alpha` 两个输入 → 恰好互为补集的两个蒙版。
> **不需要改任何节点、不需要插 `InvertMask`** —— 因此本探针不触碰节点图，
> 不产生"第二份工作流 JSON"（`workflow_spec.md` §6.1 禁止多份同义 JSON）。

「蒙版原样」= 变体 A（输入 alpha 原样）；「蒙版反相」= 变体 B（输入 alpha 取反）。

产出哪些客观信号
----------------
1. **两次输出是否逐字节相同**（整文件 sha256 + 仅像素的 IDAT sha256）。
   相同 ⇒ 蒙版**根本没起作用** —— 那是比极性反了更严重的 bug。
2. **各区域的像素差异统计**（均值 / 最大值 / P99 / 完全相同像素占比）：
   * `A vs B`         —— 两变体之间的差异落在哪一区
   * `输出 vs 参考图` —— 哪一区的像素**被新画过**、哪一区被原样贴回
     （`ImageCompositeMasked` 会把非选区按原图贴回，所以被贴回的那一区
     与参考图的差异应≈0）
3. 两道**自证**（否则"相同/不同"都可能是假结论）：
   * 离线断言：两个变体的 alpha **逐像素互补** —— 否则不是单变量实验
   * GPU 正对照：变体 A 跑两次，**必须**逐字节一致 —— 否则管线不确定，
     A 与 B 的差异**不可归因**于蒙版，本探针全部结论作废

⚠️ 本探针**不下极性结论**。它只回答"哪一区被改了、幅度多大"，
   "这符不符合产品预期"必须人看图判定（与 `probe_g7_weight.py` 同一纪律）。

用法（远端，需 ComfyUI 在跑）::

    PYTHONPATH=<repo> /root/miniconda3/bin/python 37_probe_inpaint_mask_polarity.py \
        --base-image /root/autodl-tmp/l4_assets/ref_product.png \
        --seed 20260916 --json /root/autodl-tmp/l4_evidence/36_inpaint_mask_polarity.json

退出码：0 = 探针跑完（**不代表极性正确**）· 1 = 探针自身失败 ·
       2 = 用法/环境前提不满足（参数非法、输入文件不存在、缺依赖）

依赖声明
--------
本脚本需要 **numpy** 与 **Pillow(PIL)**，而它们**不在任何 pyproject 里**：

* 仓库根 `pyproject.toml` 刻意声明 `engine/` 核心**零运行时依赖**（只用标准库），
  不能为了一个部署脚本破坏这条契约；
* `backend/pyproject.toml` 是**后端服务**的依赖，后端运行期并不用 numpy/PIL，
  声明进去等于让服务背上一份它不 import 的依赖（语义错误）；
* `deploy/` 不是包、没有 pyproject —— 这些脚本的实际宿主是**远端 conda 环境**
  （`/root/miniconda3/bin/python`，见上面的用法），那个环境**不是**按本仓库的
  打包元数据装的，所以在此处声明**没有任何读取方**，只会造成"声明过=有保障"的假象。

故采取**脚本内显式前置检查**：缺包时立即以退出码 2 响亮失败并说明装什么，
而不是甩出一段 ImportError 崩栈（后者容易被误读成"脚本写错了"）。
远端这两个包由 ComfyUI 自身依赖带入，通常已存在；此为**防空转**的兜底。
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import pathlib
import sys
from typing import Any

from engine.registry import Registry
from engine.render import RenderOptions, load_definition, render
from engine.tools.verify_render_path import ComfyClient, idat_digest

#: 本探针的第三方依赖（部署脚本宿主环境提供，不在本仓库打包元数据里）
_REQUIRED_THIRD_PARTY = ("numpy", "PIL")


def _require_third_party() -> None:
    """缺依赖时响亮失败，**不要**让 ImportError 崩栈。

    刻意先探测全部再看结果：一次把缺的包都列出来，省得用户装一个再跑一次。
    """
    missing = []
    for mod in _REQUIRED_THIRD_PARTY:
        try:
            __import__(mod)
        except ImportError:
            missing.append(mod)
    if missing:
        pkgs = " ".join("Pillow" if m == "PIL" else m for m in missing)
        print(f"[fatal] 本探针缺少依赖: {', '.join(missing)}")
        print(f"        装法: pip install {pkgs}")
        print("        （远端 conda 环境通常已由 ComfyUI 依赖带入；本仓库不声明这两个包，"
              "原因见模块 docstring『依赖声明』一节）")
        raise SystemExit(2)


_require_third_party()

import numpy as np  # noqa: E402  # 依赖检查必须在导入之前，故不置于文件顶部
from PIL import Image, ImageFilter  # noqa: E402

#: 探针画在参考图上的「假想重绘区」：以图心为圆心的圆
#: ⚠️ 用**圆**而不是矩形：`GrowMask.tapered_corners=true` 会把直角磨圆，
#:    矩形四角的归属会变得含糊；圆没有角，边界行为更干净。
CIRCLE_RADIUS = 220

#: 统计时把边界带排除掉的腐蚀半径（像素）。
#: 需要 > `GrowMask.expand`（工作流里为 8）+ 羽化余量，否则统计会把
#: 「边界过渡像素」算进核心区，得出偏小的差异。
ERODE_PX = 16


def _erode(binary: np.ndarray, k: int) -> np.ndarray:
    """二值图腐蚀（PIL 的 MinFilter，避免引入 scipy 依赖）。k 必须为奇数。"""
    im = Image.fromarray((binary.astype(np.uint8) * 255), mode="L")
    return np.asarray(im.filter(ImageFilter.MinFilter(k))) > 127


def _stats(diff: np.ndarray, region: np.ndarray) -> dict[str, Any]:
    """某个区域内的差异统计。diff 取的是**逐通道**绝对值（HxWxC）。"""
    if not region.any():
        return {"pixels": 0, "note": "该区域为空，未统计"}
    vals = diff[region]                      # (N, C)
    per_px = vals.max(axis=1)                # 每个像素取通道最大差（更保守）
    return {
        "pixels": int(region.sum()),
        "mean_abs": round(float(per_px.mean()), 4),
        "max_abs": int(per_px.max()),
        "p99_abs": int(np.percentile(per_px, 99)),
        "frac_exactly_equal": round(float((per_px == 0).mean()), 6),
    }


def _save_gray(arr2d: np.ndarray, path: pathlib.Path) -> None:
    Image.fromarray(np.clip(arr2d, 0, 255).astype(np.uint8), mode="L").save(path)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="inpaint_v1 蒙版极性探针（只给客观信号）")
    ap.add_argument("--workflow", default="inpaint_v1")
    ap.add_argument("--registry", default=None)
    ap.add_argument("--base-image", required=True, help="参考图（RGB 基准，会被加上 alpha）")
    ap.add_argument("--seed", type=int, default=20260916)
    ap.add_argument("--base-url", default="http://127.0.0.1:8188")
    ap.add_argument("--timeout", type=float, default=600.0)
    ap.add_argument("--outdir", default="inpaint_polarity_out")
    ap.add_argument("--json", default="inpaint_mask_polarity.json")
    args = ap.parse_args(argv)

    if args.seed < 0:
        print("[fatal] --seed 必须 ≥ 0（-1 是随机，无法做单变量对比）")
        return 2

    outdir = pathlib.Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    json_path = pathlib.Path(args.json)
    json_path.parent.mkdir(parents=True, exist_ok=True)

    base_path = pathlib.Path(args.base_image)
    if not base_path.is_file():
        print(f"[fatal] 参考图不存在: {base_path}")
        return 2
    base_rgb = Image.open(base_path).convert("RGB")
    W, H = base_rgb.size
    print(f"参考图: {base_path}  {W}x{H}  sha256={_sha256(base_path.read_bytes())[:16]}…")

    # ---------- 1. 构造两个变体（唯一变量 = alpha） ----------
    yy, xx = np.mgrid[0:H, 0:W]
    inside = ((xx - W / 2) ** 2 + (yy - H / 2) ** 2) <= CIRCLE_RADIUS**2
    alpha_a = np.where(inside, 0, 255).astype(np.uint8)      # 变体 A：圆内透明
    alpha_b = (255 - alpha_a).astype(np.uint8)               # 变体 B：互补

    # 自证 1：两个变体必须逐像素互补（否则不是单变量实验）
    if not np.array_equal(alpha_a + alpha_b, np.full_like(alpha_a, 255)):
        print("[fatal] 两个变体的 alpha 不是逐像素互补 —— 不是单变量实验，拒绝继续")
        return 1
    print(f"自证 1 ✅ 两变体 alpha 逐像素互补（圆半径 {CIRCLE_RADIUS}px，圆心=图心）")

    base_arr = np.asarray(base_rgb).astype(np.int16)
    files: dict[str, pathlib.Path] = {}
    variants: dict[str, dict[str, Any]] = {}
    for tag, alpha in (("A_mask_as_is", alpha_a), ("B_mask_inverted", alpha_b)):
        rgba = np.dstack([base_arr.astype(np.uint8), alpha])
        p = outdir / f"probe_input_{tag}.png"
        Image.fromarray(rgba, mode="RGBA").save(p)
        files[f"input_{tag}"] = p
        variants[tag] = {
            "asset_id": 1 if tag.startswith("A") else 2,
            "input_png": str(p),
            "input_sha256": _sha256(p.read_bytes()),
            "alpha_meaning": (
                "alpha=0 在圆内（透明），alpha=255 在圆外"
                if tag.startswith("A") else
                "alpha=255 在圆内（不透明），alpha=0 在圆外 —— A 的逐像素取反"
            ),
        }
        print(f"  变体 {tag}: {p}  alpha 圆内={alpha[H // 2, W // 2]}"
              f" 圆外={alpha[0, 0]}")

    # 蒙版几何（供人工比对；保存成图便于直接看）
    _save_gray(inside.astype(np.uint8) * 255, outdir / "probe_region_circle.png")
    files["region_circle"] = outdir / "probe_region_circle.png"

    # ---------- 2. 渲染 + 提交 ----------
    registry = Registry.load(args.registry)
    entry = registry.require(args.workflow)
    definition = load_definition(registry.definition_path(entry))
    defaults = {f.key: f.default for f in entry.schema}
    ref_key = next(
        (f.key for f in entry.schema
         if any(t.transform == "ref_to_filename" for t in f.targets)),
        None,
    )
    if ref_key is None:
        print(f"[fatal] 工作流 {entry.id} 的 Schema 里没有 ref_to_filename 字段 —— "
              "本探针是为『蒙版驱动』工作流写的")
        return 1

    client = ComfyClient(args.base_url, timeout=args.timeout)
    try:
        stats = client.health()
        dev = (stats.get("devices") or [{}])[0]
        print(f"引擎: {dev.get('name')} · vram_total="
              f"{(dev.get('vram_total') or 0) / 1024**3:.1f}GiB")
    except Exception as exc:  # noqa: BLE001
        print(f"[fatal] ComfyUI 不可达 {args.base_url}: {exc}")
        return 1

    # 素材经真实 /upload/image 进引擎（与生产 _asset_resolver 同构）
    name_by_id: dict[int, str] = {}
    for info in variants.values():
        p = pathlib.Path(info["input_png"])
        up = client.upload_image(p.read_bytes(), p.name)
        name = up.get("name")
        if not isinstance(name, str) or not name:
            print(f"[fatal] 上传 {p} 后未返回文件名: {up}")
            return 1
        name_by_id[info["asset_id"]] = name
        info["engine_filename"] = name
        print(f"  上传 asset_id={info['asset_id']} → {name}")

    runs = [
        ("A_mask_as_is", 1),
        ("A2_repeat_same_input", 1),   # 正对照：输入完全相同，必须与 A 逐字节一致
        ("B_mask_inverted", 2),
    ]
    outputs: dict[str, bytes] = {}
    for tag, asset_id in runs:
        params = dict(defaults)
        params[ref_key] = asset_id
        params["seed"] = args.seed
        try:
            result = render(definition, entry.schema, params,
                            options=RenderOptions(asset_resolver=lambda _a, n=name_by_id: n[_a]))
        except Exception as exc:  # noqa: BLE001
            print(f"[fatal] 渲染 {tag} 失败: {exc}")
            return 1
        for w in result.warnings:
            print(f"  ⚠ 渲染告警: {w}")
        try:
            pid = client.submit(result.workflow)
            entry_h = client.wait(pid, args.timeout)
        except Exception as exc:  # noqa: BLE001
            print(f"[fatal] {tag} 提交/执行失败: {exc}")
            return 1
        img = client.first_image(entry_h)
        if img is None:
            print(f"[fatal] {tag} 的产物里没有 images")
            return 1
        png = client.fetch_image(img)
        outputs[tag] = png
        p = outdir / f"probe_output_{tag}.png"
        p.write_bytes(png)
        files[f"output_{tag}"] = p
        print(f"  {tag}: {img['filename']} → {p}  ({len(png)} bytes)")

    # ---------- 3. 客观信号 ----------
    a, a2, b = outputs["A_mask_as_is"], outputs["A2_repeat_same_input"], outputs["B_mask_inverted"]
    digests = {
        "A_mask_as_is": {"file_sha256": _sha256(a), "idat_sha256": idat_digest(a)},
        "A2_repeat_same_input": {"file_sha256": _sha256(a2), "idat_sha256": idat_digest(a2)},
        "B_mask_inverted": {"file_sha256": _sha256(b), "idat_sha256": idat_digest(b)},
    }
    same_aa2 = digests["A_mask_as_is"]["idat_sha256"] == digests["A2_repeat_same_input"]["idat_sha256"]
    same_ab = digests["A_mask_as_is"]["idat_sha256"] == digests["B_mask_inverted"]["idat_sha256"]

    print()
    print("==================== 客观信号 ====================")
    for k, v in digests.items():
        print(f"  {k}:  file={v['file_sha256'][:16]}…  IDAT={v['idat_sha256'][:16]}…")
    print(f"  ① A 与 A2（输入完全相同）逐字节相同 : {same_aa2}")
    print(f"  ② A 与 B（蒙版互补）逐字节相同     : {same_ab}")

    if not same_aa2:
        print("  ❌ 正对照失败：输入完全相同却没得到同一张图 →")
        print("     管线不确定，A 与 B 的差异**不可归因**于蒙版变量。")
        print("     ⚠️ 本探针关于『哪一区被改了』的全部数字**作废**，只看图像人工判断。")
    if same_ab:
        print("  ⚠️ A 与 B 完全相同 → 蒙版**根本没起作用**（比极性反了更严重）")

    # 区域统计
    img_a = np.asarray(Image.open(pathlib.Path(files["output_A_mask_as_is"])).convert("RGB")).astype(np.int16)
    img_b = np.asarray(Image.open(pathlib.Path(files["output_B_mask_inverted"])).convert("RGB")).astype(np.int16)
    shape_note = None
    if img_a.shape != img_b.shape:
        shape_note = f"A/B 形状不同（{img_a.shape} vs {img_b.shape}）—— 无法做区域统计"
        print(f"  ⚠️ {shape_note}")
        region_stats: dict[str, Any] = {"note": shape_note}
    else:
        if (img_a.shape[0], img_a.shape[1]) != (H, W):
            shape_note = (f"输出 {img_a.shape[1]}x{img_a.shape[0]} 与参考图 {W}x{H} 不同；"
                          "区域统计按输出尺寸重建蒙版（按比例缩放）")
            print(f"  ⚠️ {shape_note}")
            region_full = np.asarray(
                Image.fromarray((inside.astype(np.uint8) * 255), mode="L").resize(
                    (img_a.shape[1], img_a.shape[0]), Image.NEAREST)
            ) > 127
        else:
            region_full = inside

        repaint_core = _erode(region_full, ERODE_PX * 2 + 1)
        keep_core = _erode(~region_full, ERODE_PX * 2 + 1)
        boundary = region_full ^ repaint_core   # 被排除的边界带（透明区侧）

        diff_ab = np.abs(img_a - img_b)
        # 参考图 vs 输出：参考图已是 HxW；若输出同尺寸则直接比
        base_arr_full = base_arr
        if (img_a.shape[0], img_a.shape[1]) != (H, W):
            base_arr_full = np.asarray(base_rgb.resize(
                (img_a.shape[1], img_a.shape[0]), Image.LANCZOS)).astype(np.int16)
        diff_a_base = np.abs(img_a - base_arr_full)
        diff_b_base = np.abs(img_b - base_arr_full)

        region_stats = {
            "shape_note": shape_note,
            "regions": {
                "repaint_core(alpha=0 的圆内, 腐蚀后)": int(repaint_core.sum()),
                "keep_core(alpha=255 的圆外, 腐蚀后)": int(keep_core.sum()),
                "boundary_band(被排除)": int(boundary.sum()),
                "erode_px": ERODE_PX,
            },
            "A_vs_B": {"repaint_core": _stats(diff_ab, repaint_core),
                       "keep_core": _stats(diff_ab, keep_core)},
            "A_vs_base_image": {"repaint_core": _stats(diff_a_base, repaint_core),
                                "keep_core": _stats(diff_a_base, keep_core)},
            "B_vs_base_image": {"repaint_core": _stats(diff_b_base, repaint_core),
                                "keep_core": _stats(diff_b_base, keep_core)},
        }

        print()
        print("  区域统计（mean/max = 每像素通道最大差；frac_equal = 完全相同像素占比）")
        print(f"    repaint_core 像素数={int(repaint_core.sum())}  "
              f"keep_core 像素数={int(keep_core.sum())}")
        for label, key in (("A vs B        ", "A_vs_B"),
                           ("A vs 参考图   ", "A_vs_base_image"),
                           ("B vs 参考图   ", "B_vs_base_image")):
            r = region_stats[key]["repaint_core"]
            k = region_stats[key]["keep_core"]
            print(f"    {label} | repaint_core: mean={r['mean_abs']:>8} max={r['max_abs']:>4} "
                  f"eq={r['frac_exactly_equal']:.4f}"
                  f" | keep_core: mean={k['mean_abs']:>8} max={k['max_abs']:>4} "
                  f"eq={k['frac_exactly_equal']:.4f}")

        # 差异可视化（×8 提亮），便于人工看
        _save_gray(diff_ab.max(axis=2) * 8, outdir / "probe_diff_A_vs_B_x8.png")
        _save_gray(diff_a_base.max(axis=2) * 8, outdir / "probe_diff_A_vs_base_x8.png")
        files["diff_A_vs_B_x8"] = outdir / "probe_diff_A_vs_B_x8.png"
        files["diff_A_vs_base_x8"] = outdir / "probe_diff_A_vs_base_x8.png"

    # ---------- 4. 事实记录 ----------
    facts = {
        "probe": "inpaint_v1 蒙版极性（mask polarity）",
        "workflow": entry.id,
        "workflow_version": entry.version,
        "engine_base_url": args.base_url,
        "generated_at": datetime.datetime.now().astimezone().isoformat(),
        "seed": args.seed,
        "single_variable": "仅参考图的 alpha 通道；RGB 逐像素相同，节点图未改动",
        "base_image": {"path": str(base_path),
                       "sha256": _sha256(base_path.read_bytes()),
                       "size": f"{W}x{H}"},
        "variants": variants,
        "digests": digests,
        "objective_signals": {
            "A_vs_A2_inputs_identical_byte_identical_output": same_aa2,
            "A_vs_B_masks_complementary_byte_identical_output": same_ab,
            "A_and_A2_note": (
                "正对照：两次输入**完全相同**（含同一 seed）。若引擎整条执行结果被缓存，"
                "本条只是较弱的一致性证据；但 A/B 提示词相同，仅蒙版极性互补，"
                "A 与 B 的差异仍可归因于蒙版"
            ),
        },
        "region_stats": region_stats,
        "artifacts": {k: str(v) for k, v in files.items()},
        "polarity_conclusion": "NONE —— **极性结论待人工判读**",
        "how_to_judge": (
            "① 打开 probe_output_A_mask_as_is.png / probe_output_B_mask_inverted.png "
            "与 probe_input_A_mask_as_is.png（圆内为透明区）并排比较；"
            "② 看 region_stats：哪一区『被新画过』（与参考图差异大）、哪一区『被原样贴回』"
            "（与参考图差异≈0，因为 ImageCompositeMasked 把非选区贴回原图）。"
            "**数值只说明『改了哪一区』，是否与产品预期一致（透明区=待重绘）必须由人判定。**"
        ),
        "mechanism_hypothesis_not_measured": {
            "statement": (
                "假设：ComfyUI 的 LoadImage 在输入含 alpha 时输出 mask = 1 - alpha，"
                "因此**透明区**（alpha=0）→ mask=1；SetLatentNoiseMask 在 mask=1 处加噪声"
                "（重绘），ImageCompositeMasked 在 mask=1 处取重绘结果、mask=0 处贴回 destination。"
                "⟹ 即『透明区 = 待重绘区』，与 registry 的假设一致。"
            ),
            "basis": (
                "**源码推断，不是出图实测**。依据是远端只读摘录的 ComfyUI 源码"
                "（见证据文件 36_inpaint_polarity_source.txt 的 LoadImage / "
                "ImageCompositeMasked / SetLatentNoiseMask 段）。"
            ),
            "status": "待验证 —— 用本探针的客观信号核对",
        },
    }
    json_path.write_text(json.dumps(facts, ensure_ascii=False, indent=2), encoding="utf-8")
    print()
    print(f"事实记录已写入: {json_path}")
    print("⚠️ **极性结论待人工判读** —— 本探针只给客观信号，不下语义结论。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
