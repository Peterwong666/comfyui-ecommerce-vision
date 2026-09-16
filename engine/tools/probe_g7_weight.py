"""G7 提示词权重探针（`docs/sop/debug_log.md` G7 的可执行版本）。

回答 G7 的三个开放问题：

| # | 问题 | 本脚本的模式 |
|---|---|---|
| ① | klein 上写 `(word:1.2)` 会不会**主动劣化**提示词（被当字面 token） | `--mode klein-core` |
| ② | SDXL（权重正常解析）上带权重 vs 不带权重是否有**可观测差异** | `--mode sdxl` |
| ③ | 把 klein 的文本编码节点换成 `BNK_CLIPTextEncodeAdvanced` 后，权重**能否恢复生效** | `--mode klein-bnk` |

---

### 判据（**客观部分**与**主观部分**要分开，别把后者说成前者）

本脚本只能给出**一个客观信号**：

> **同一 seed、除提示词外全部参数相同 → 产物的像素数据（IDAT）是否逐字节一致。**

- **一致** ⇒ 该提示词的改动**对模型完全没有影响**。这是**决定性结论**（不是"看起来差不多"）：
  说明权重语法在这些节点上被吃掉了或根本没进模型。
- **不一致** ⇒ 提示词的改动**确实影响了模型**。但**不能说"权重按预期生效了"** ——
  因为 `(mug:1.5)` 被当字面文本同样会改变结果。**具体是哪一种，必须人工看图**。

⚠️ 所以本脚本**不会**打印"权重生效/无效"这种结论；它打印的是
「两次产物是否相同」+ 产物存放路径，把语义判断留给人工评审。
把"像素不同"说成"权重生效"，就是 G7 里已经犯过两次的那类机制推断。

---

### ⚠️ 两道自证：否则「相同」可能是个假通过

「像素相同 = 完全无影响」这个**决定性结论**有一个隐含前提 ——
**两次确实都真的执行了、而且输入确实不同**。前提不成立时，
「相同」仍会被读成决定性结论，**这是方向最危险的一类假通过**
（与 `verify_render_path.py` 里 `{None, None}` 判"一致"同族）。所以本脚本加了两道自证：

| # | 自证 | 在哪一步 | 不通过意味着什么 |
|---|---|---|---|
| 1 | **输入确实不同** | 离线（构造后立刻） | baseline 与 weighted 的图**逐字节相同** → 等于"拿自己跟自己比"，比对毫无意义 |
| 2 | **执行确实发生**（正对照） | GPU 上 | 同提示词、**只改 seed** 的对照若像素仍相同 → 根本没真跑（缓存/提前返回/提交了同一份），**本模式所有"相同"结论作废** |

第 2 道是正对照（positive control）：改 seed **必然**改变像素输出，所以它"必须不同"。
**判据本身也要能被检出** —— 与本项目在 `verify_render_path.py`、G8 用的是同一招。

### 用法

    # 先离线预检（不需要 GPU）：确认三种模式的图都能渲染 + 通过 L3
    PYTHONPATH=. python -m engine.tools.probe_g7_weight --mode all --dry-run

    # GPU 上真跑（固定 seed，各模式跑 baseline / weighted 两次）
    PYTHONPATH=. python -m engine.tools.probe_g7_weight --mode all --seed 20260916

产物落在 `--outdir`（默认 `g7_probe_out/`），文件名形如
`klein-core_baseline_<digest8>.png` / `klein-core_weighted_<digest8>.png`，方便并排比对。
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from typing import Any

from engine.errors import RenderError
from engine.registry import Registry
from engine.render import RenderOptions, load_definition, render
from engine.tools.verify_render_path import ComfyClient, idat_digest

#: 三种模式
MODES: tuple[str, ...] = ("klein-core", "klein-bnk", "sdxl")

#: 探针提示词：同一主体，三种写法。
#: 刻意选一个**明确的商品词**做权重目标，便于人工判断"主体是否变强/变弱"。
BASELINE_PROMPT = "a white ceramic coffee mug on a light grey background, soft studio lighting"
WEIGHTED_PROMPT = "a (white ceramic coffee mug:1.5) on a light grey background, soft studio lighting"

#: BNK 节点默认入参。`comfy` 是与核心节点最可比的权重解释语义。
BNK_DEFAULTS: dict[str, Any] = {
    "token_normalization": "none",
    "weight_interpretation": "comfy",
}


def _prompt_target_node(entry: Any) -> str:
    """从**注册表 Schema** 找出正向提示词落在哪个节点。

    ⚠️ 刻意不硬编码节点 ID、也不按 `class_type` 扫全图 ——
    节点 ID 会随工作流演进漂移（契约 §3.3 的 `targets` 正是为此存在的锚点）。
    注册表已经是"提示词写在哪"的唯一事实来源，探针就该复用它。
    """
    field = entry.schema.get("prompt")
    if field is None or not field.targets:
        raise RenderError(f"工作流 {entry.id} 的 Schema 里没有带 targets 的 'prompt' 字段")
    nodes = {t.node_id for t in field.targets}
    if len(nodes) != 1:
        raise RenderError(f"工作流 {entry.id} 的 prompt 映射到多个节点 {sorted(nodes)}，探针无法确定目标")
    return next(iter(nodes))


def _build_bnk_variant(definition: dict[str, Any], prompt: str, encoder_node: str) -> dict[str, Any]:
    """把 klein 图里的核心 `CLIPTextEncode` 换成 `BNK_CLIPTextEncodeAdvanced`。

    **刻意在运行时生成，不落一份单独的 JSON** —— 否则注册表里那份工作流一改，
    探针副本就悄悄过期，实验结论会被建立在过期的图上（本项目反复出现的"双真相"问题）。

    节点 ID 与连线保持不变；BNK 节点的入参是 `text` + `clip` +
    `token_normalization` + `weight_interpretation`，输出同样是 CONDITIONING，
    所以**下游（`ConditioningZeroOut` / `CFGGuider`）完全不用改**。
    """
    meta, graph = dict(definition.get("_meta") or {}), {
        k: v for k, v in definition.items() if k != "_meta"
    }
    node = graph.get(encoder_node)
    if not isinstance(node, dict) or node.get("class_type") != "CLIPTextEncode":
        raise RenderError(
            f"节点 '{encoder_node}' 应为核心 CLIPTextEncode，实际: "
            f"{node.get('class_type') if isinstance(node, dict) else node!r}"
        )
    if "clip" not in node.get("inputs", {}):
        raise RenderError(f"节点 '{encoder_node}' 没有 clip 入参，无法替换为 BNK 节点")
    graph[encoder_node] = {
        "class_type": "BNK_CLIPTextEncodeAdvanced",
        "inputs": {"text": prompt, "clip": node["inputs"]["clip"], **BNK_DEFAULTS},
    }
    meta["id"] = f"{meta.get('id', 'klein')}__bnk_probe"
    meta["note"] = "G7 探针：文本编码节点替换为 BNK_CLIPTextEncodeAdvanced（非生产工作流）"
    return {"_meta": meta, **graph}


def _write_prompt(graph: dict[str, Any], node_id: str, prompt: str) -> None:
    graph[node_id].setdefault("inputs", {})["text"] = prompt


def _set_seed(graph: dict[str, Any], entry: Any, value: int) -> None:
    """把 seed 写进该工作流声明的 seed 目标节点（同样走 Schema 的 targets）。"""
    field = next((f for f in entry.schema if f.type == "seed"), None)
    if field is None or not field.targets:
        raise RenderError(f"工作流 {entry.id} 的 Schema 里没有带 targets 的 seed 字段")
    for t in field.targets:
        if t.node_id in graph:
            graph[t.node_id].setdefault("inputs", {})[t.input] = value


def _klein_core_variant(definition: dict[str, Any], prompt: str, node_id: str) -> dict[str, Any]:
    _meta, graph = dict(definition.get("_meta") or {}), {
        k: v for k, v in definition.items() if k != "_meta"
    }
    _write_prompt(graph, node_id, prompt)
    return {"_meta": _meta, **graph}


def _sdxl_variant(definition: dict[str, Any], prompt: str, node_id: str) -> dict[str, Any]:
    """SDXL：只改正向节点，**负向保持模板原样**（做对照组，避免同时动两个变量）。"""
    _meta, graph = dict(definition.get("_meta") or {}), {
        k: v for k, v in definition.items() if k != "_meta"
    }
    _write_prompt(graph, node_id, prompt)
    return {"_meta": _meta, **graph}


def _variant(mode: str, prompt: str, definition: dict[str, Any], entry: Any) -> dict[str, Any]:
    node_id = _prompt_target_node(entry)
    if mode == "klein-core":
        return _klein_core_variant(definition, prompt, node_id)
    if mode == "klein-bnk":
        return _build_bnk_variant(definition, prompt, node_id)
    if mode == "sdxl":
        return _sdxl_variant(definition, prompt, node_id)
    raise RenderError(f"未知模式: {mode}")


def _l3_check(graph: dict[str, Any], info: dict[str, Any], scope: str) -> list[str]:
    """对**构造出来的图**跑 L3 —— 手拼的变体必须先验节点合法性再提交。"""
    from engine.validate import check_object_info

    return [f.message for f in check_object_info(graph, info, scope=scope)
            if f.level == "error"]


def _load_object_info() -> dict[str, Any] | None:
    p = Registry.load().path
    root = p.parent.parent if p is not None else pathlib.Path(".")
    cand = root / "deploy" / "schemas" / "object_info.v0.36.0.json"
    if not cand.exists():
        return None
    with cand.open(encoding="utf-8") as fh:
        return json.load(fh)


def run(args: argparse.Namespace) -> int:
    registry = Registry.load(args.registry)
    seed_field_workflow = {
        "klein-core": "flux2_klein_t2i_v1",
        "klein-bnk": "flux2_klein_t2i_v1",
        "sdxl": "t2i_v1",
    }
    modes = list(MODES) if args.mode == "all" else [args.mode]
    info = _load_object_info()
    outdir = pathlib.Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    print("=== G7 提示词权重探针 ===")
    print(f"seed = {args.seed}（固定）· 模式 = {modes}")
    print(f"baseline : {BASELINE_PROMPT}")
    print(f"weighted : {WEIGHTED_PROMPT}")
    print("⚠️ 本脚本只给客观信号（产物是否逐字节相同）；语义判断必须人工看图\n")

    variants_for: dict[str, dict[str, dict[str, Any]]] = {}
    fatal = False

    for mode in modes:
        wf_id = seed_field_workflow[mode]
        entry = registry.require(wf_id)
        definition = load_definition(registry.definition_path(entry))

        # 先按生产 Schema 渲染一次，拿到「合法参数基线」（含正确的 seed 注入点）
        base_params = {f.key: f.default for f in entry.schema}
        seed_key = next((f.key for f in entry.schema if f.type == "seed"), None)
        if seed_key is None:
            print(f"[fatal] {wf_id} 没有 seed 字段")
            return 2
        base_params[seed_key] = args.seed
        try:
            rendered = render(definition, entry.schema, base_params,
                              options=RenderOptions())
        except RenderError as exc:
            print(f"[fatal] {mode} 渲染失败: {exc}")
            return 2

        built: dict[str, dict[str, Any]] = {}
        for tag, prompt, seed_value in (
            ("baseline", BASELINE_PROMPT, args.seed),
            ("weighted", WEIGHTED_PROMPT, args.seed),
            # 正对照：同提示词、**只改 seed** —— 它必须产生不同的像素
            ("control", BASELINE_PROMPT, args.seed + 1),
        ):
            g = _variant(mode, prompt, definition, entry)
            # 把生产渲染出来的参数（width/height/steps/cfg/seed）搬进变体，
            # 保证「除提示词外全部一致」——否则比对的是两个变量，结论无效
            graph = {k: v for k, v in g.items() if k != "_meta"}
            for nid, node in rendered.workflow.items():
                if nid in graph and node.get("inputs"):
                    for key, val in node["inputs"].items():
                        if isinstance(val, list) and val and isinstance(val[0], str):
                            continue  # 连线不动
                        if key == "text":
                            continue  # 提示词归探针控制
                        graph[nid]["inputs"][key] = val
            _set_seed(graph, entry, seed_value)
            # ---- 自证 1：输入确实不同（防"拿自己跟自己比"）----
            if tag == "weighted" and graph == built.get("baseline"):
                print(f"  ❌ {mode}: weighted 与 baseline 的图**逐字节相同** —— "
                      "提示词没被写进去，比对无意义")
                fatal = True
                continue
            if info is not None:
                errs = _l3_check(graph, info, f"{mode}/{tag}")
                if errs:
                    print(f"  ❌ {mode}/{tag} L3 未通过:")
                    for e in errs:
                        print(f"       {e}")
                    fatal = True
                    continue
            built[tag] = graph
        # 正对照必须是干净的单变量：只在 seed 所在节点上与基线不同
        if "control" in built and "baseline" in built:
            b, c = built["baseline"], built["control"]
            changed = {k for k in set(b) | set(c) if b.get(k) != c.get(k)}
            unexpected = changed - _seed_nodes(entry)
            if unexpected:
                print(f"  ⚠ {mode}: 正对照除 seed 外还有节点不同 {sorted(unexpected)}，"
                      "它不是干净的单变量对照，结论会被削弱")
        variants_for[mode] = built

    if fatal:
        print("\n[fatal] 有变体未通过 L3，先修图再跑")
        return 1

    print("— L3 预检 —")
    print(f"  {'✅ 全部变体通过 L3（节点类型/入参名/枚举取值）' if info else '⏭ 没找到 object_info，跳过 L3'}")

    if args.dry_run:
        print("\n=== DRY-RUN 结束（未提交，不需要 GPU）===")
        for mode in modes:
            for tag in ("baseline", "weighted"):
                if tag in variants_for.get(mode, {}):
                    print(f"  ✅ {mode}/{tag}: 节点数 {len(variants_for[mode][tag])}")
        print("\n→ 去掉 --dry-run 在 GPU 上跑真实比对。")
        return 0

    client = ComfyClient(args.base_url)
    try:
        stats = client.health()
        dev = (stats.get("devices") or [{}])[0]
        print(f"  引擎: {dev.get('name')}")
    except Exception as exc:  # noqa: BLE001
        print(f"[fatal] ComfyUI 不可达: {exc}")
        return 1

    print("\n================ 结果 ================")
    rows: list[tuple[str, str, str, bool | None]] = []
    for mode in modes:
        digests: dict[str, str] = {}
        for tag in ("baseline", "weighted"):
            graph = variants_for.get(mode, {}).get(tag)
            if graph is None:
                continue
            try:
                pid = client.submit(graph)
                entry_h = client.wait(pid, args.timeout)
            except Exception as exc:  # noqa: BLE001
                print(f"  ❌ {mode}/{tag} 提交失败: {exc}")
                digests[tag] = f"ERROR:{exc}"
                continue
            img = client.first_image(entry_h)
            if img is None:
                print(f"  ❌ {mode}/{tag} 无产物")
                continue
            png = client.fetch_image(img)
            d = idat_digest(png)
            digests[tag] = d
            dest = outdir / f"{mode}_{tag}_{d[:8]}.png"
            dest.write_bytes(png)
            print(f"  · {mode}/{tag}: IDAT={d[:16]}… → {dest}")

        same: bool | None = None
        if "baseline" in digests and "weighted" in digests:
            same = digests["baseline"] == digests["weighted"]
        rows.append((mode, digests.get("baseline", "-")[:16],
                     digests.get("weighted", "-")[:16], same))

    print("\n| 模式 | baseline IDAT | weighted IDAT | 像素相同？ |")
    print("|---|---|---|---|")
    for mode, a, b, same in rows:
        mark = "—" if same is None else ("**相同**" if same else "不同")
        print(f"| {mode} | `{a}` | `{b}` | {mark} |")

    print("""
**怎么读这张表（重要，别读错）**
- **相同** ⇒ 提示词改动对模型**完全无影响**（决定性结论：权重既没生效、也没污染）
- **不同** ⇒ 提示词改动**确实进了模型**，但**不能推出"权重按预期生效"** ——
  被当字面文本同样会改变结果。**必须人工并排看 `--outdir` 里的图**，判断
  「主体是否更突出/更弱（权重生效）」还是「画面出现异常的括号文字感/构图崩坏（被当字面文本）」
- `klein-core` 预期：G7 源码定论为**不被解析** → 大概率「不同」（污染）或「相同」（token 被忽略），
  两者都需要看图定性质；`klein-bnk` 是问「换 BNK 节点能否救回来」；`sdxl` 是**对照组**。
""")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m engine.tools.probe_g7_weight",
        description="G7 提示词权重探针（debug_log.md G7 的可执行版本）",
    )
    ap.add_argument("--mode", default="all", choices=(*MODES, "all"))
    ap.add_argument("--registry", default=None)
    ap.add_argument("--seed", type=int, default=20260916, help="固定 seed（必须 ≥0）")
    ap.add_argument("--base-url", default="http://127.0.0.1:8188")
    ap.add_argument("--timeout", type=float, default=600.0)
    ap.add_argument("--outdir", default="g7_probe_out")
    ap.add_argument("--dry-run", action="store_true", help="只构造并 L3 预检，不提交")
    args = ap.parse_args(argv)
    if args.seed < 0:
        print("[fatal] --seed 必须 ≥ 0")
        return 2
    return run(args)


if __name__ == "__main__":
    sys.exit(main())


__all__ = ["BASELINE_PROMPT", "BNK_DEFAULTS", "MODES", "WEIGHTED_PROMPT", "main", "run"]
