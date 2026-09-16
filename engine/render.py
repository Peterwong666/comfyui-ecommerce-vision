"""工作流渲染器：把「参数 Schema + 用户 params」渲染成可提交给 ComfyUI 的 API 格式 JSON。

对应任务 **P3-02**。实现依据：`contracts.md` §3.3（`targets` 注入）+ `docs/sop/workflow_spec.md`。

---

### 渲染流水线的六步（顺序有意义，不可调换）

```
① 参数收敛   default 打底 + 传入值覆盖（None 视为未提供）
② 逐字段校验 并解析 seed（-1 → 具体整数）
③ Bypass     裁剪被关掉的分支 + 按声明重连
④ 占位符     替换所有字符串里的 {{key}}      ← 必须在 ⑤ 之前
⑤ targets    结构化注入（优先级最高）        ← 覆盖 ④ 的结果
⑥ 输出前缀   可选覆写 SaveImage.filename_prefix
```

**为什么 ③ 在 ⑤ 之前**：被 bypass 裁掉的节点不应再接收注入。
若顺序反过来，参数会先写进一个即将被删除的节点，删掉后**注入静默消失**——
用户改了参数却没生效，且没有任何报错（`workflow_spec.md` §5.1 提到的排查陷阱）。

**为什么 ④ 在 ⑤ 之前**：`{{key}}` 适合"拼在固定文案中间"，
`targets` 适合"整个入参就是一个值"。两者都写时以 `targets` 为准，
所以先做弱的、后做强覆盖。

---

### 本模块的定位

**不依赖任何后端代码**（只用标准库），因为 `engine/` 可能被 CLI、离线校验脚本、
压测脚本直接调用。与 A 流的接口只有两个：

* 入参：`params`（A 流已按契约 §3.4 合并，渲染器仍会用 default 兜底，幂等）
* 出参：`RenderResult.workflow`（直接 `POST /prompt` 的 `prompt` 字段）
"""

from __future__ import annotations

import copy
import json
import pathlib
import random
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from engine.errors import RenderError
from engine.schema import SEED_SPACE, Field, ParamSchema
from engine.transforms import AssetResolver, apply_transform

#: 仓库侧元数据段的 key。渲染时**整段剥离**，不提交给 ComfyUI（`workflow_spec.md` §2.1）
META_KEY = "_meta"

#: 占位符语法：`{{snake_case}}`。用宽松大小写匹配，好让 `{{Prompt}}` 也被识别出来报错，
#: 而不是被静默忽略（静默忽略会让"参数没生效"无从排查）。
_PLACEHOLDER_RE = re.compile(r"\{\{([A-Za-z0-9_]+)\}\}")

#: 产物输出节点类型。目前只确认 `SaveImage`（两条既有工作流都用的它）。
#: 需要扩展时在此追加，并同步在 `workflow_spec.md` §4.2 登记。
IMAGE_OUTPUT_NODES: tuple[str, ...] = ("SaveImage",)

#: 模块级默认随机源。`SystemRandom` 不参与 `random.seed()`，避免与业务侧随机互相影响。
_DEFAULT_RNG: random.Random = random.SystemRandom()


# ============================================================ 数据结构


@dataclass(frozen=True)
class Switch:
    """一个 bypass 开关声明（`workflow_spec.md` §6.2）。"""

    key: str
    enabled_when: tuple[Any, ...]
    prune: tuple[str, ...]
    rewire: Mapping[str, Mapping[str, list[Any]]]

    def is_enabled(self, value: Any) -> bool:
        return value in self.enabled_when


@dataclass
class RenderOptions:
    """渲染选项。全部可选，默认行为对应规范里的推荐用法。"""

    #: `asset_id` → ComfyUI 可见文件名。只有 Schema 里用到 `ref_to_filename` 时才必需。
    asset_resolver: AssetResolver | None = None
    #: 覆写输出前缀（如按 task 隔离）。默认 None = 沿用工作流里写的 `filename_prefix`。
    output_prefix: str | None = None
    #: seed 随机源，注入以便测试可复现。
    seed_rng: random.Random | None = None
    #: 存在未解析占位符时是否直接报错。默认 False（规范 §5.4 规则 4：保留原样 + 告警）。
    strict_placeholders: bool = False


@dataclass
class RenderResult:
    """一次渲染的完整产出。"""

    #: 可直接 `POST /prompt` 的 `prompt` 字段内容（已剥离 `_meta`）
    workflow: dict[str, Any]
    #: 本次生效的完整参数（含解析后的真实 seed）—— P3-11 写进产物元数据的就是它
    params: dict[str, Any]
    #: 解析后的真实 seed；工作流无 seed 字段时为 None
    seed: int | None
    #: 被 bypass 裁掉的节点 ID
    pruned: tuple[str, ...] = ()
    #: 被重连的位置，形如 `"41.images"`
    rewired: tuple[str, ...] = ()
    #: 未能解析的占位符（key 列表，去重保序）
    unresolved_placeholders: tuple[str, ...] = ()
    #: 需要人看一眼的提示（不阻断渲染）
    warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def node_count(self) -> int:
        return len(self.workflow)


# ============================================================ 定义加载


def load_definition(path: str | pathlib.Path) -> dict[str, Any]:
    """读取 `workflows/*.json`，返回原始定义（含 `_meta`）。"""
    p = pathlib.Path(path)
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RenderError(f"工作流定义不存在: {p}") from exc
    except json.JSONDecodeError as exc:
        raise RenderError(f"工作流定义不是合法 JSON: {p} —— {exc}") from exc
    if not isinstance(raw, dict):
        raise RenderError(f"工作流定义顶层必须是对象: {p}")
    return raw


def split_definition(definition: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """拆成 `(meta, 节点图)`。节点图已深拷贝，可安全改动。"""
    meta = definition.get(META_KEY)
    if not isinstance(meta, dict):
        raise RenderError(
            f"工作流定义缺少 `{META_KEY}` 段。该段是节点图与仓库元数据的隔离带"
            "（workflow_spec.md §2.1 规则 1）"
        )
    graph = {k: copy.deepcopy(v) for k, v in definition.items() if k != META_KEY}
    if not graph:
        raise RenderError("工作流定义里没有任何节点")
    return dict(meta), graph


def parse_switches(meta: Mapping[str, Any]) -> tuple[Switch, ...]:
    """解析 `_meta.switches`。"""
    raw = meta.get("switches")
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise RenderError(f"`{META_KEY}.switches` 必须是数组")

    out: list[Switch] = []
    for i, item in enumerate(raw):
        where = f"`{META_KEY}.switches[{i}]`"
        if not isinstance(item, dict):
            raise RenderError(f"{where} 必须是对象")
        key = item.get("key")
        if not isinstance(key, str) or not key:
            raise RenderError(f"{where} 缺少 key")

        enabled_when = item.get("enabled_when")
        if not isinstance(enabled_when, list) or not enabled_when:
            raise RenderError(
                f"{where}({key}) 缺少非空 enabled_when —— "
                "它声明'取哪些值时分支启用'，缺失会让开关的语义无法确定"
            )

        disabled = item.get("disabled")
        if not isinstance(disabled, dict):
            raise RenderError(f"{where}({key}) 缺少 disabled 段")
        prune = disabled.get("prune")
        if not isinstance(prune, list) or not prune:
            raise RenderError(f"{where}({key}).disabled 缺少非空 prune 数组")
        for nid in prune:
            if not isinstance(nid, str):
                raise RenderError(f"{where}({key}).disabled.prune 元素必须是字符串节点 ID")
        rewire = disabled.get("rewire")
        if not isinstance(rewire, dict) or not rewire:
            raise RenderError(
                f"{where}({key}).disabled 缺少非空 rewire。"
                "裁剪后必须显式声明'谁改从哪里取输入'——不做自动推断"
                "（workflow_spec.md §6.3）"
            )
        for consumer_id, mapping in rewire.items():
            if not isinstance(consumer_id, str) or not isinstance(mapping, dict):
                raise RenderError(f"{where}({key}).disabled.rewire 结构非法")

        out.append(
            Switch(
                key=key,
                enabled_when=tuple(enabled_when),
                prune=tuple(prune),
                rewire=dict(rewire),
            )
        )
    return tuple(out)


# ============================================================ 渲染主流程


def render(
    definition: Mapping[str, Any],
    schema: ParamSchema,
    params: Mapping[str, Any] | None = None,
    *,
    options: RenderOptions | None = None,
) -> RenderResult:
    """渲染一条工作流。语义见模块 docstring 的六步流水线。"""
    opts = options or RenderOptions()
    meta, graph = split_definition(definition)
    warnings: list[str] = []

    # ---------- ① 参数收敛 ----------
    provided = {k: v for k, v in (params or {}).items() if v is not None}
    effective: dict[str, Any] = schema.defaults()
    effective.update(provided)

    unknown = [k for k in provided if k not in schema]
    if unknown:
        warnings.append(
            f"传入参数 {unknown} 未在 param_schema 中声明。已保留（供元数据追溯），"
            "但它们不会被注入节点图，也不会被 A 流按 Schema 校验"
        )

    # ---------- ② 逐字段校验 + seed 解析 ----------
    seed: int | None = None
    for f in schema:
        if f.key not in effective:
            continue
        value = f.validate(effective[f.key])
        if f.type == "seed":
            value = _resolve_seed(value, opts)
            seed = value
        effective[f.key] = value

    # ---------- ③ Bypass ----------
    pruned, rewired, sw_warnings = _apply_switches(graph, meta, effective)
    warnings.extend(sw_warnings)
    pruned_set = set(pruned)

    # ---------- ④ 占位符 ----------
    unresolved = _substitute_placeholders(graph, effective)
    if unresolved:
        msg = (
            f"以下占位符未能解析，已按原样保留: {list(unresolved)}。"
            "说明 params 缺少对应值且 Schema 也没有 default —— 请检查注册表"
        )
        if opts.strict_placeholders:
            raise RenderError(msg)
        warnings.append(msg)

    # ---------- ⑤ targets 注入 ----------
    warnings.extend(_inject_targets(graph, schema, effective, opts, pruned_set))

    # ---------- ⑥ 输出前缀 ----------
    if opts.output_prefix is not None:
        _override_output_prefix(graph, opts.output_prefix)

    return RenderResult(
        workflow=graph,
        params=effective,
        seed=seed,
        pruned=pruned,
        rewired=rewired,
        unresolved_placeholders=unresolved,
        warnings=tuple(warnings),
    )


def render_file(
    definition_path: str | pathlib.Path,
    schema: ParamSchema,
    params: Mapping[str, Any] | None = None,
    *,
    options: RenderOptions | None = None,
) -> RenderResult:
    """`load_definition` + `render` 的便捷组合。"""
    return render(load_definition(definition_path), schema, params, options=options)


# ============================================================ 各步实现


def _resolve_seed(value: int, opts: RenderOptions) -> int:
    """把 `-1` 解析成具体整数。

    **必须在渲染期解析，不能交给 ComfyUI**：产物元数据要记录真实 seed，
    否则用户拿到一个 `-1`，永远复现不出那张图（FR-5.4 / FR-5.5 直接失效）。
    """
    if value != -1:
        return value
    rng = opts.seed_rng or _DEFAULT_RNG
    return rng.randrange(0, SEED_SPACE)


def derive_seed(base: int, idx: int, *, space: int = SEED_SPACE) -> int:
    """同批次内 seed 递增（`contracts.md` §2.4）。

    `base == -1` 时先随机出一个基准再递增 —— 支持「整批随机但批内递增」这个常见诉求，
    避免调用方先自己随机、再自己取模，散落两处口径。
    """
    if idx < 0:
        raise RenderError(f"derive_seed 的 idx 必须 ≥ 0，实际 {idx}")
    if base == -1:
        base = _DEFAULT_RNG.randrange(0, space)
    if not 0 <= base < space:
        raise RenderError(f"derive_seed 的 base={base} 超出 [0, {space - 1}]")
    return (base + idx) % space


def _validate_link(link: Any, graph: Mapping[str, Any], where: str) -> None:
    ok = (
        isinstance(link, list)
        and len(link) == 2
        and isinstance(link[0], str)
        and isinstance(link[1], int)
        and not isinstance(link[1], bool)
        and link[1] >= 0
    )
    if not ok:
        raise RenderError(f"{where} 的连线格式非法：{link!r}（应为 ['节点ID', 输出序号]）")
    if link[0] not in graph:
        raise RenderError(f"{where} 的连线指向不存在的节点 '{link[0]}'")


def _apply_switches(
    graph: dict[str, Any], meta: Mapping[str, Any], effective: Mapping[str, Any]
) -> tuple[tuple[str, ...], tuple[str, ...], list[str]]:
    """按 `_meta.switches` 裁剪未启用分支并重连。

    两遍处理：**先校验全部 rewire，再统一删除节点**。
    若边删边校验，一个非法的 rewire 会留下"节点已删、重连未做"的半成品图，
    报错信息也会丢失上下文。
    """
    warnings: list[str] = []
    pruned_all: list[str] = []
    rewired_all: list[str] = []

    for sw in parse_switches(meta):
        if sw.key not in effective:
            raise RenderError(
                f"开关 '{sw.key}' 声明在 `{META_KEY}.switches` 里，但 params 中没有该 key。"
                "请确认 param_schema 里存在同名字段且有 default（workflow_spec.md §6.2）"
            )
        if sw.is_enabled(effective[sw.key]):
            continue

        prune_set = set(sw.prune)
        for nid in sw.prune:
            if nid not in graph:
                raise RenderError(
                    f"开关 '{sw.key}' 要裁掉节点 '{nid}'，但工作流里没有该节点"
                    "（配置漂移：节点图被改过而 switches 没同步）"
                )
        for consumer_id, mapping in sw.rewire.items():
            where = f"开关 '{sw.key}'.disabled.rewire['{consumer_id}']"
            if consumer_id in prune_set:
                raise RenderError(f"{where}: 重连的目标节点自身也在 prune 列表里，配置矛盾")
            if consumer_id not in graph:
                raise RenderError(f"{where}: 目标节点不存在")
            for input_name, link in mapping.items():
                _validate_link(link, graph, f"{where}['{input_name}']")
                if link[0] in prune_set:
                    raise RenderError(
                        f"{where}['{input_name}'] 的连线指向被裁掉的节点 '{link[0]}'，"
                        "重连必须落到保留下来的节点上"
                    )

        for nid in sw.prune:
            graph.pop(nid)
            pruned_all.append(nid)

        for consumer_id, mapping in sw.rewire.items():
            inputs = graph[consumer_id].setdefault("inputs", {})
            for input_name, link in mapping.items():
                if input_name not in inputs:
                    warnings.append(
                        f"重连写入 {consumer_id}.{input_name}，但该入参在原图中不存在"
                        "（属新增入参，需 L3 object_info 校验确认该节点接受它）"
                    )
                inputs[input_name] = list(link)
                rewired_all.append(f"{consumer_id}.{input_name}")

    return tuple(pruned_all), tuple(rewired_all), warnings


def _substitute_placeholders(graph: dict[str, Any], effective: Mapping[str, Any]) -> tuple[str, ...]:
    """替换所有字符串里的 `{{key}}`，返回未能解析的 key（去重保序）。"""
    unresolved: dict[str, None] = {}

    def sub_string(text: str) -> str:
        def repl(m: re.Match[str]) -> str:
            key = m.group(1)
            if key not in effective:
                unresolved.setdefault(key, None)
                return m.group(0)  # 保留原样，不静默清空（规范 §5.4 规则 4）
            value = effective[key]
            if isinstance(value, (dict, list, tuple)):
                # 把结构化值插进字符串里语义不明（JSON？拼接？），交由 targets 处理
                unresolved.setdefault(key, None)
                return m.group(0)
            return str(value)

        return _PLACEHOLDER_RE.sub(repl, text)

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            return {k: walk(v) for k, v in node.items()}
        if isinstance(node, list):
            return [walk(v) for v in node]
        if isinstance(node, str):
            return sub_string(node)
        return node

    for nid in list(graph):
        graph[nid] = walk(graph[nid])

    return tuple(unresolved)


def _inject_targets(
    graph: dict[str, Any],
    schema: ParamSchema,
    effective: Mapping[str, Any],
    opts: RenderOptions,
    pruned: set[str],
) -> list[str]:
    """把每个参数的 `targets` 写入节点图。"""
    warnings: list[str] = []

    for f in schema:
        if not f.injects_into_graph or f.key not in effective:
            continue
        if _uses_placeholder(graph, f.key):
            warnings.append(
                f"字段 '{f.key}' 既声明了 targets 又出现在 `{{{{{f.key}}}}}` 占位符里。"
                "虽然行为已定义（targets 覆盖），但建议二选一（workflow_spec.md §5.4 规则 3）"
            )
        value = effective[f.key]
        for target in f.targets:
            where = target.describe()
            if target.node_id not in graph:
                if target.node_id in pruned:
                    warnings.append(
                        f"参数 '{f.key}' 的 target {where} 所在节点被 bypass 裁剪，已跳过注入"
                    )
                    continue
                raise RenderError(
                    f"参数 '{f.key}' 的 target node_id='{target.node_id}' 在工作流中不存在。"
                    "这属于注册表与工作流的配置漂移 —— 节点 ID 是 targets 的锚点，"
                    "改 ID 必须同步改 Schema（workflow_spec.md §3.1）"
                )
            node = graph[target.node_id]
            inputs = node.setdefault("inputs", {})
            injected = apply_transform(
                target.transform, value, resolver=opts.asset_resolver, target=where
            )
            if target.input not in inputs:
                warnings.append(
                    f"参数 '{f.key}' 注入到 {where}，但该入参在原图里不存在"
                    "（属新增入参，需 L3 object_info 校验确认该节点接受它）"
                )
            inputs[target.input] = injected

    return warnings


def _uses_placeholder(graph: Mapping[str, Any], key: str) -> bool:
    pattern = "{{" + key + "}}"

    def walk(node: Any) -> bool:
        if isinstance(node, str):
            return pattern in node
        if isinstance(node, dict):
            return any(walk(v) for v in node.values())
        if isinstance(node, list):
            return any(walk(v) for v in node)
        return False

    return any(walk(n) for n in graph.values())


def _override_output_prefix(graph: dict[str, Any], prefix: str) -> None:
    hits = 0
    for node in graph.values():
        if node.get("class_type") in IMAGE_OUTPUT_NODES:
            node.setdefault("inputs", {})["filename_prefix"] = prefix
            hits += 1
    if hits == 0:
        raise RenderError(
            f"要覆写输出前缀，但图里没有 {'/'.join(IMAGE_OUTPUT_NODES)} 节点"
            "（workflow_spec.md §4.2 规定每条工作流恰好一个产物输出节点）"
        )


# ============================================================ 自检辅助


def collect_target_conflicts(
    graph: Mapping[str, Any], schema: ParamSchema
) -> list[str]:
    """找出「同名入参出现在多个节点，但 Schema 只声明了其中一部分」的情况。

    这是 `workflow_spec.md` §5.1 提到的排查陷阱：漏写 target 不会报错，
    只会表现为"参数改了没生效"。返回的是提示文本，供 `engine.validate` 输出。
    """
    hints: list[str] = []
    # 入参名 → 拥有它的节点集合
    holders: dict[str, set[str]] = {}
    for nid, node in graph.items():
        if not isinstance(node, dict):
            continue
        for name in (node.get("inputs") or {}):
            holders.setdefault(name, set()).add(nid)

    for f in schema:
        if not f.targets:
            continue
        # 只关心"可调参数名"恰好也是某节点入参名的情况
        if f.key in holders:
            declared = f.target_nodes
            missing = holders[f.key] - declared
            if missing:
                hints.append(
                    f"入参名 '{f.key}' 还出现在节点 {sorted(missing)} 上，"
                    f"但字段 '{f.key}' 只声明了 targets={sorted(declared)}。"
                    "若非有意为之，请检查是否漏了 target（workflow_spec.md §5.1）"
                )
    return hints


def describe_result(result: RenderResult) -> Sequence[str]:
    """人可读的一行式摘要，供 CLI 与日志使用。"""
    lines = [
        f"节点数 {result.node_count} · seed={result.seed}",
    ]
    if result.pruned:
        lines.append(f"bypass 裁剪节点: {list(result.pruned)}")
    if result.rewired:
        lines.append(f"重连: {list(result.rewired)}")
    for w in result.warnings:
        lines.append(f"⚠ {w}")
    return lines


__all__ = [
    "Field",
    "IMAGE_OUTPUT_NODES",
    "META_KEY",
    "RenderOptions",
    "RenderResult",
    "Switch",
    "collect_target_conflicts",
    "derive_seed",
    "describe_result",
    "load_definition",
    "parse_switches",
    "render",
    "render_file",
    "split_definition",
]
