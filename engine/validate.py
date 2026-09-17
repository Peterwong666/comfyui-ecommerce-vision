"""离线校验（`workflow_spec.md` §8 的 L1 / L2 实现）。

**这个模块存在的唯一理由：把错误挡在出图之前。**
一次 GPU 出图要占机器几十秒到几分钟，而「节点 ID 写错 / targets 指错位置 /
bypass 重连漏了一个入参 / 占位符没有对应字段」这类错误**离线就能查出来**。
（`项目进展.md` #16 验证 4 的结论，本模块是它的工程化版本。）

### 能力边界（必须说清楚，不能假装）

| 级别 | 能力 | 本模块 |
|---|---|---|
| **L1** 结构自洽 | 完全离线，只依赖工作流 JSON + 注册表 | ✅ 实现 |
| **L2** 参数渲染冒烟 | 完全离线，用 default / 边界值 / 各开关组合各渲染一次 | ✅ 实现 |
| **L3** 节点合法性 | 需要 ComfyUI 的 `/object_info`（`class_type` 是否存在、入参名是否被接受、枚举取值是否合法） | ✅ **在本模块**（`check_object_info`），但**必须拿到一份 `object_info` 快照**才执行（`--object-info`，缺省自动回退到 `deploy/schemas/object_info.v0.36.0.json`）。它跑在**渲染后**的图上，因此还能覆盖 `targets` 注入的正确性 |
| **L4** 真实出图 | 需要 GPU | ❌ 不可能离线完成 |

⚠️ **L3 的能力来自快照，不是来自本模块自己**：`deploy/schemas/object_info.v0.36.0.json`
（v0.36.0，2530 个节点类）**已入库**，`python -m engine.validate` 缺省用它。
快照取不到时 L3 **被跳过**，且输出会显式打印
「未做 L3 —— 节点类型/入参名/枚举取值**未经验证**」（不会静默降级）。
**因此 L3 未执行时，本模块的输出只能证明 L1/L2 通过，绝不能据此声称"工作流已跑通"。**
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from engine.errors import RenderError, SchemaError, ValidationError
from engine.registry import DEFAULT_REGISTRY_PATH, REPO_ROOT, Registry, WorkflowEntry
from engine.render import (
    IMAGE_OUTPUT_NODES,
    META_KEY,
    RenderOptions,
    collect_target_conflicts,
    load_definition,
    parse_switches,
    render,
    split_definition,
)
from engine.schema import ParamSchema

#: 数值型字段的 type（跑边界渲染用）
_NUMERIC = {"int", "float"}

#: 契约 §3.6 不变量 1 的机械执行：**不得暴露**会让单次执行产出多张图的参数。
#: 一个参数会不会产出多张图，取决于它写进了哪个入参 —— 所以这里按**入参名**匹配，
#: 而不是按参数名匹配（叫 `n` 或 `count` 的参数照样可能是 batch_size）。
FORBIDDEN_TARGET_INPUTS: frozenset[str] = frozenset(
    {"batch_size", "batch_count", "batch", "num_images", "n_images"}
)

#: 契约 §3.6 不变量 2 涉及的、default 必须与模板字面值一致的 type。
#: `seed` 刻意排除：`-1`（随机）本来就**不应**等于模板里的历史字面值。
_DEFAULT_MUST_MATCH = {"str", "text", "enum", "int", "float"}


@dataclass
class Finding:
    """一条校验发现。"""

    level: str  # error | warn | hint
    scope: str
    message: str

    def __str__(self) -> str:
        mark = {"error": "✗", "warn": "⚠", "hint": "·"}.get(self.level, "?")
        return f"  {mark} [{self.scope}] {self.message}"


@dataclass
class Report:
    """一份校验报告。"""

    findings: list[Finding] = field(default_factory=list)
    checked: list[str] = field(default_factory=list)
    #: 实际执行了的校验级别，如 `["L1", "L2", "L3"]`。用于在输出里**如实**说明结论强度 ——
    #: "L1/L2 通过" 与 "L1/L2/L3 通过" 是两件不同的事，输出必须能区分。
    levels_run: list[str] = field(default_factory=list)

    def add(self, level: str, scope: str, message: str) -> None:
        self.findings.append(Finding(level, scope, message))

    def error(self, scope: str, message: str) -> None:
        self.add("error", scope, message)

    def warn(self, scope: str, message: str) -> None:
        self.add("warn", scope, message)

    def hint(self, scope: str, message: str) -> None:
        self.add("hint", scope, message)

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.level == "error"]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.level == "warn"]

    @property
    def ok(self) -> bool:
        return not self.errors

    def extend(self, other: Report) -> None:
        self.findings.extend(other.findings)
        for lv in other.levels_run:
            if lv not in self.levels_run:
                self.levels_run.append(lv)


# ============================================================ 图结构检查


def orphan_nodes(graph: Mapping[str, Any]) -> list[str]:
    """找出从产物节点**反向不可达**的节点（孤岛）。

    反向遍历（沿每个节点的输入连线往上走）而不是正向：我们要的是
    「哪些节点不会影响最终产物」，也就是产物节点的全部祖先集合的补集。
    """
    outputs = [nid for nid, n in graph.items() if isinstance(n, dict)
               and n.get("class_type") in IMAGE_OUTPUT_NODES]
    if not outputs:
        return []

    # deps[nid] = 该节点的输入来自哪些节点
    deps: dict[str, set[str]] = {nid: set() for nid in graph}
    for nid, node in graph.items():
        if not isinstance(node, dict):
            continue
        for value in (node.get("inputs") or {}).values():
            if (
                isinstance(value, list)
                and len(value) == 2
                and isinstance(value[0], str)
                and value[0] in deps
            ):
                deps[nid].add(value[0])

    reachable: set[str] = set()
    stack = list(outputs)
    while stack:
        cur = stack.pop()
        if cur in reachable:
            continue
        reachable.add(cur)
        stack.extend(deps.get(cur, ()))
    return sorted(set(graph) - reachable, key=_nid_sort_key)


def check_graph(
    graph: Mapping[str, Any], *, scope: str, include_orphans: bool = True
) -> list[Finding]:
    """图结构自洽：节点形状、连线有效性、产物节点唯一、可达性。"""
    out: list[Finding] = []

    for nid, node in graph.items():
        if not isinstance(nid, str) or not nid.isdigit():
            out.append(
                Finding(
                    "error",
                    scope,
                    f"节点 ID '{nid}' 不是纯数字字符串。契约 §3.3 规定 node_id 是字符串，"
                    "且规范 §2.1 要求使用短数字字符串",
                )
            )
        if not isinstance(node, dict):
            out.append(Finding("error", scope, f"节点 {nid} 不是对象"))
            continue
        ct = node.get("class_type")
        if not isinstance(ct, str) or not ct:
            out.append(Finding("error", scope, f"节点 {nid} 缺少 class_type"))
        inputs = node.get("inputs")
        if not isinstance(inputs, dict):
            out.append(Finding("error", scope, f"节点 {nid} 缺少 inputs 对象"))

    # 连线
    for nid, node in graph.items():
        if not isinstance(node, dict):
            continue
        for key, value in (node.get("inputs") or {}).items():
            if not (isinstance(value, list) and len(value) == 2):
                continue
            src, idx = value[0], value[1]
            if not isinstance(src, str):
                out.append(Finding("error", scope, f"节点 {nid}.{key} 的连线源不是字符串: {src!r}"))
                continue
            if src not in graph:
                out.append(
                    Finding("error", scope, f"节点 {nid}.{key} 的连线指向不存在的节点 '{src}'")
                )
            if isinstance(idx, bool) or not isinstance(idx, int) or idx < 0:
                out.append(Finding("error", scope, f"节点 {nid}.{key} 的输出序号非法: {idx!r}"))

    # 产物节点唯一（规范 §4.2）
    outputs = [nid for nid, n in graph.items() if isinstance(n, dict)
               and n.get("class_type") in IMAGE_OUTPUT_NODES]
    if not outputs:
        out.append(
            Finding(
                "error",
                scope,
                f"图里没有 {'/'.join(IMAGE_OUTPUT_NODES)} 节点 —— 无法收集产物"
                "（规范 §4.2 规定每条工作流恰好一个产物输出节点）",
            )
        )
    elif len(outputs) > 1:
        out.append(
            Finding(
                "warn",
                scope,
                f"图里有 {len(outputs)} 个产物输出节点 {sorted(outputs)}，"
                "会让产物归属含糊（规范 §4.2）；中间预览请用 PreviewImage 并在提交前移除",
            )
        )

    if include_orphans:
        orphans = orphan_nodes(graph)
        if orphans:
            out.append(
                Finding(
                    "warn",
                    scope,
                    f"以下节点从产物节点无法到达（孤岛）: {orphans}。"
                    "若属 bypass 分支请确认它确实出现在某个 switches 的 prune 里",
                )
            )

    # 输出前缀（规范 §4.2）
    for nid in outputs:
        node = graph[nid]
        prefix = (node.get("inputs") or {}).get("filename_prefix")
        if not isinstance(prefix, str) or not prefix:
            out.append(Finding("warn", scope, f"输出节点 {nid} 没有 filename_prefix"))
    return out


def _nid_sort_key(nid: str) -> tuple[int, str]:
    return (int(nid), nid) if nid.isdigit() else (10**9, nid)


# ============================================================ L1


def validate_entry(
    entry: WorkflowEntry,
    registry: Registry,
    settings_bounds: Mapping[str, Mapping[str, Any]] | None = None,
) -> tuple[Report, dict[str, Any] | None]:
    """对单条工作流做 L1（+ 部分 L2 前置）校验。"""
    rep = Report()
    scope = entry.id

    path = registry.definition_path(entry)
    if not path.exists():
        rep.error(scope, f"定义文件不存在: {path}")
        return rep, None

    try:
        definition = load_definition(path)
    except RenderError as exc:
        rep.error(scope, str(exc))
        return rep, None

    try:
        meta, graph = split_definition(definition)
    except RenderError as exc:
        rep.error(scope, str(exc))
        return rep, None

    # ---------- _meta 与注册表的一致性 ----------
    if meta.get("id") != entry.id:
        rep.error(scope, f"`{META_KEY}.id`='{meta.get('id')}' 与注册表 id='{entry.id}' 不一致")
    if meta.get("version") != entry.version:
        rep.error(
            scope,
            f"`{META_KEY}.version`={meta.get('version')} 与注册表 version={entry.version} 不一致",
        )
    meta_models = set(meta.get("models") or [])
    if meta_models != set(entry.models):
        rep.error(
            scope,
            f"`{META_KEY}.models`={sorted(meta_models)} 与注册表 models={sorted(entry.models)} "
            "不一致。同一事实写两处必须同步，否则产物元数据里的模型清单会失真（FR-5.4）",
        )
    if not meta.get("title"):
        rep.hint(scope, f"`{META_KEY}.title` 为空")

    rep.extend(_report_from(check_graph(graph, scope=scope)))
    rep.extend(_validate_schema_against_graph(entry.schema, graph, meta, scope))
    if settings_bounds is not None:
        rep.extend(_report_from(check_settings_bounds(entry.schema, settings_bounds, scope)))
    return rep, definition


def _report_from(findings: list[Finding]) -> Report:
    r = Report()
    r.findings.extend(findings)
    return r


def _load_object_info(path: str | pathlib.Path) -> dict[str, Any]:
    p = pathlib.Path(path)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SchemaError(f"object_info 缓存不存在: {p}") from exc
    except json.JSONDecodeError as exc:
        raise SchemaError(f"object_info 不是合法 JSON: {p} —— {exc}") from exc
    if not isinstance(data, dict):
        raise SchemaError(f"object_info 顶层必须是对象: {p}")
    return data


def _is_link(value: Any) -> bool:
    return isinstance(value, list) and len(value) == 2 and isinstance(value[0], str)


def check_object_info(
    graph: Mapping[str, Any],
    info: Mapping[str, Any],
    *,
    scope: str,
    runtime_asset_inputs: set[tuple[str, str]] | None = None,
) -> list[Finding]:
    """L3：用 ComfyUI 的 `/object_info` 校验节点类型、入参名、枚举取值、连线序号。

    **比 `26_validate_workflow.py` 多了一层用途**：那个脚本校验的是**模板原文件**，
    而这里可以对**渲染后**的图校验 —— 也就是把 `targets` 注入的结果也验一遍。
    注入错误（节点 ID 漂移、入参名拼错、枚举取值非法）只有这样才抓得到。

    ⚠️ `runtime_asset_inputs`：**由 `transform=ref_to_filename` 产生的入参值**，
    形如 `{("10", "image")}`。这些值是**运行时**由 asset_resolver 决定的
    （生产路径下素材先经 `/upload/image` 上传、文件届时才存在），
    而 `/object_info` 的枚举是**快照时刻对模型/输入目录的静态扫描** ——
    拿静态快照去比运行时文件名，**按构造就不可能匹配**。

    实测（2026-09-16）：不加这个豁免，**任何带参考图的工作流都无法通过 L1/L2/L3**
    （L2 用假 resolver 渲染出 `smoke_0.png`，L3 立刻报"取值不在允许集合内"），
    等于把 P3-04 图生图 / P3-06 局部重绘直接堵死。故此处豁免，而非放宽整个枚举检查。
    """
    out: list[Finding] = []

    for nid, node in graph.items():
        if not isinstance(node, dict):
            continue
        ct = node.get("class_type")
        if ct not in info:
            out.append(Finding("error", scope, f"节点 {nid}: class_type '{ct}' 不存在于 object_info"))
            continue

        spec = info[ct].get("input", {}) or {}
        required = spec.get("required", {}) or {}
        optional = spec.get("optional", {}) or {}
        accepted = set(required) | set(optional)
        given = node.get("inputs", {}) or {}

        for k in required:
            if k not in given:
                out.append(Finding("error", scope, f"节点 {nid} ({ct}): 缺少必填入参 '{k}'"))
        for k in given:
            if k not in accepted:
                out.append(Finding(
                    "error", scope,
                    f"节点 {nid} ({ct}): 入参 '{k}' 不被该节点接受（可选: {sorted(accepted)}）",
                ))

        for k, value in given.items():
            # 连线合法性
            if _is_link(value):
                src, idx = value[0], value[1]
                if src not in graph:
                    out.append(Finding("error", scope,
                                       f"节点 {nid} ({ct}).{k}: 连线指向不存在的节点 '{src}'"))
                else:
                    src_out = info.get(graph[src].get("class_type"), {}).get("output")
                    n_out = len(src_out) if isinstance(src_out, list) else None
                    if n_out is not None and isinstance(idx, int) and not isinstance(idx, bool) \
                            and idx >= n_out:
                        out.append(Finding(
                            "error", scope,
                            f"节点 {nid} ({ct}).{k}: 引用 {src} 的输出序号 {idx} 越界"
                            f"（该节点只有 {n_out} 个输出）",
                        ))
                continue

            # 枚举取值（object_info 里 choices 是列表的形态）
            spec_v = required.get(k, optional.get(k))
            if not isinstance(spec_v, list) or not spec_v:
                continue
            choices = spec_v[0]
            if not isinstance(choices, list):
                continue
            # ⚠️ 运行时素材引用豁免：值由 asset_resolver 在运行时决定（生产路径下先经
            #    /upload/image 上传），静态快照的枚举按构造比不中 —— 详见函数 docstring
            if runtime_asset_inputs and (nid, k) in runtime_asset_inputs:
                continue
            if value not in choices:
                hint = ""
                if isinstance(value, str):
                    near = [c for c in choices if isinstance(c, str)
                            and (value.split(".")[0] in c or c.split(".")[0] in value)]
                    if near:
                        hint = f"（是否想用 {near[:3]}？）"
                out.append(Finding(
                    "error", scope,
                    f"节点 {nid} ({ct}).{k}: 取值 {value!r} 不在允许集合内{hint}",
                ))
    return out


#: 全局红线（`backend/app/core/config.py` 的 `settings`，对齐 PRD §6.2）从哪些
#: settings 字段来。**engine 不硬编码这些数值** —— 那是 settings 与 PRD 的真相，
#: 在这里复制一份就变成了「双真相」，改一处必漏另一处。
SETTINGS_BOUNDS_MAPPING: tuple[tuple[str, str, str], ...] = (
    ("steps", "min_steps", "max_steps"),
    ("cfg", "min_cfg", "max_cfg"),
    ("width", "min_gen_side", "max_gen_side"),
    ("height", "min_gen_side", "max_gen_side"),
)


def bounds_from_settings(settings: Any) -> dict[str, dict[str, float]]:
    """从 A 流的 `settings` 对象机械地生成边界表（避免手工抄错）。

    用法（A 流侧）：

    ```python
    from app.core.config import settings
    from engine.validate import bounds_from_settings
    bounds = bounds_from_settings(settings)
    # → {"steps": {"min":1,"max":100}, "cfg": {...}, "width": {...}, "height": {...},
    #    "prompt": {"max_length": 2000}}   # max_length 由 max_prompt_length 补
    ```
    """
    out: dict[str, dict[str, float]] = {}
    for key, min_attr, max_attr in SETTINGS_BOUNDS_MAPPING:
        lo = getattr(settings, min_attr, None)
        hi = getattr(settings, max_attr, None)
        if lo is not None and hi is not None:
            out[key] = {"min": lo, "max": hi}
    max_length = getattr(settings, "max_prompt_length", None)
    if max_length is not None:
        for key in ("prompt", "negative_prompt"):
            out.setdefault(key, {})["max_length"] = max_length
    return out


def check_settings_bounds(
    schema: ParamSchema, bounds: Mapping[str, Mapping[str, Any]], scope: str
) -> list[Finding]:
    """校验 Schema 边界是否被全局红线包住（`contracts.md` §3.4）。

    规则：**Schema 可以更严，不可以更宽。**
    settings 是防炸机制（拦住 `steps=100000` 这类会让 GPU 崩掉的输入），
    工作流不得突破它 —— 所以更宽就是配置错误。

    更严是正常的：klein 是蒸馏版，`cfg` 写 `min:1, max:2` 比 settings 的 1.0–20.0 更正确。
    """
    out: list[Finding] = []
    for f in schema:
        b = bounds.get(f.key)
        if not b:
            continue
        if f.min is not None and "min" in b and f.min < b["min"]:
            out.append(Finding(
                "error", scope,
                f"字段 '{f.key}' 的 min={f.min} 比全局红线 {b['min']} 更宽松。"
                "Schema 边界只能更严，不能更宽（contracts.md §3.4）",
            ))
        if f.max is not None and "max" in b and f.max > b["max"]:
            out.append(Finding(
                "error", scope,
                f"字段 '{f.key}' 的 max={f.max} 比全局红线 {b['max']} 更宽松。"
                "Schema 边界只能更严，不能更宽（contracts.md §3.4）",
            ))
        if f.max_length is not None and "max_length" in b and f.max_length > b["max_length"]:
            out.append(Finding(
                "error", scope,
                f"字段 '{f.key}' 的 max_length={f.max_length} 超过全局上限 {b['max_length']}",
            ))
    return out


def _load_bounds(path: str | pathlib.Path) -> dict[str, dict[str, Any]]:
    p = pathlib.Path(path)
    text = p.read_text(encoding="utf-8")
    if p.suffix.lower() in (".yaml", ".yml"):
        import yaml

        data = yaml.safe_load(text)
    else:
        data = json.loads(text)
    if not isinstance(data, dict):
        raise SchemaError(f"边界文件顶层必须是对象: {p}")
    return {str(k): dict(v) for k, v in data.items() if isinstance(v, dict)}


def _validate_schema_against_graph(
    schema: ParamSchema, graph: Mapping[str, Any], meta: Mapping[str, Any], scope: str
) -> Report:
    rep = Report()

    # ---------- bypass 开关 ----------
    try:
        switches = parse_switches(meta)
    except RenderError as exc:
        rep.error(scope, f"`{META_KEY}.switches` 非法: {exc}")
        switches = ()

    all_pruned: set[str] = set()
    prune_owner: dict[str, str] = {}
    for sw in switches:
        if sw.key not in schema:
            rep.error(
                scope,
                f"开关 '{sw.key}' 在 param_schema 里没有同名字段 —— "
                "前端不会渲染出这个开关，用户无从切换（规范 §6.2）",
            )
            continue
        fld = schema.get(sw.key)
        if fld is not None and fld.targets:
            rep.warn(
                scope,
                f"开关 '{sw.key}' 同时声明了 targets {[t.describe() for t in fld.targets]}。"
                "开关的作用是控制图结构，通常不该再往图里注入值（规范 §6.2）",
            )
        for nid in sw.prune:
            if nid in prune_owner:
                rep.error(
                    scope,
                    f"节点 '{nid}' 同时被开关 '{prune_owner[nid]}' 与 '{sw.key}' 声明裁剪，"
                    "多个开关的 prune 集合必须互不相交（否则启用顺序会决定结果）",
                )
            prune_owner[nid] = sw.key
            all_pruned.add(nid)
            if nid not in graph:
                rep.error(scope, f"开关 '{sw.key}' 要裁掉节点 '{nid}'，但图中没有该节点")
        for consumer_id, mapping in sw.rewire.items():
            if consumer_id not in graph:
                rep.error(scope, f"开关 '{sw.key}' 的 rewire 目标节点 '{consumer_id}' 不存在")
                continue
            inputs = (graph[consumer_id].get("inputs") or {})
            for input_name, link in mapping.items():
                if not (isinstance(link, list) and len(link) == 2 and isinstance(link[0], str)):
                    rep.error(
                        scope,
                        f"开关 '{sw.key}' 重连 {consumer_id}.{input_name} 的连线格式非法: {link!r}",
                    )
                    continue
                if link[0] not in graph:
                    rep.error(
                        scope,
                        f"开关 '{sw.key}' 重连 {consumer_id}.{input_name} 指向不存在的节点 '{link[0]}'",
                    )
                elif link[0] in sw.prune:
                    rep.error(
                        scope,
                        f"开关 '{sw.key}' 重连 {consumer_id}.{input_name} 指向被裁节点 '{link[0]}'",
                    )
                if input_name not in inputs:
                    rep.warn(
                        scope,
                        f"开关 '{sw.key}' 重连写入 {consumer_id}.{input_name}，"
                        "该入参在原图中不存在（属新增入参，需 L3 object_info 确认该节点接受它）",
                    )

    # ---------- targets ----------
    for f in schema:
        for t in f.targets:
            exists = t.node_id in graph
            if not exists and t.node_id not in all_pruned:
                rep.error(
                    scope,
                    f"参数 '{f.key}' 的 target node_id='{t.node_id}' 在图中不存在，"
                    "且不在任何 bypass 的 prune 列表里（注册表与工作流的配置漂移）",
                )
                continue
            if exists and t.input not in (graph[t.node_id].get("inputs") or {}):
                rep.warn(
                    scope,
                    f"参数 '{f.key}' 注入 {t.describe()}，该入参在原图中不存在"
                    "（属新增入参，需 L3 object_info 确认该节点接受它）",
                )
            # transform 与 type 的匹配性
            if f.type in ("image", "image_list") and t.transform != "ref_to_filename":
                rep.error(
                    scope,
                    f"参数 '{f.key}' 是 {f.type}，但 target {t.describe()} 没有声明 "
                    "transform=ref_to_filename。素材值是 asset_id（整数），"
                    "直接写进图会让 ComfyUI 把它当文件名，报难以理解的错（契约 §3.2/§3.3）",
                )
            if f.type not in ("image", "image_list") and t.transform == "ref_to_filename":
                rep.warn(
                    scope,
                    f"参数 '{f.key}' 是 {f.type}，却对 {t.describe()} 用了 ref_to_filename",
                )
            if f.type in _NUMERIC and t.transform in ("join_lines", "json_str"):
                rep.warn(
                    scope,
                    f"参数 '{f.key}' 是数值型，却对 {t.describe()} 用了 {t.transform}",
                )

    # ---------- 契约 §3.6 不变量 1：不得暴露会产出多张图的参数 ----------
    for f in schema:
        for t in f.targets:
            if t.input in FORBIDDEN_TARGET_INPUTS:
                rep.error(
                    scope,
                    f"参数 '{f.key}' 注入 {t.describe()}，而 '{t.input}' 会让单次执行产出多张图。"
                    "契约 §3.6 不变量 1 禁止暴露这类参数："
                    "「子任务粒度 = 一张图」是断点续跑 / 单张成本核算 / 批量进度的共同基石，"
                    "一次出 N 张会让这三点同时失真",
                )

    # ---------- 契约 §3.6 不变量 2：default 必须等于模板里的实际值 ----------
    # 规范 §5.5：这条不变量让「不传参数直接渲染」≡「把模板原样提交给 ComfyUI」，
    # 从而保证 07_bench_workflow.py 这类"裸提交模板"的工具与生产渲染路径测的是同一张图。
    # **定级为 error**：模板、契约示例、registry 三处的默认值一度出现过字面差异
    # （`subtle shadow` 缺失），后果是"用户在 UI 里看到的初始状态"与"真实执行的"不是同一件事。
    for f in schema:
        if f.type not in _DEFAULT_MUST_MATCH:
            continue
        for t in f.targets:
            node = graph.get(t.node_id)
            if not isinstance(node, dict):
                continue
            literal = (node.get("inputs") or {}).get(t.input)
            if isinstance(literal, (dict, list, bool)):
                continue
            # 模板用占位符时，字面值**本来就**是 "{{key}}"，与 default 不同是正确的
            if isinstance(literal, str) and _PLACEHOLDER.search(literal):
                continue
            if isinstance(f.default, str) and isinstance(literal, str):
                if literal.strip() == f.default.strip():
                    continue
                rep.error(
                    scope,
                    f"参数 '{f.key}' 的 default 与模板 {t.describe()} 的字面值不一致 —— "
                    "渲染后会覆盖模板值，使『裸提交模板』与『渲染后提交』得到不同的图。"
                    "契约 §3.6 不变量 2 要求二者一致（若确为有意覆盖，请更新模板字面值）",
                )
            elif (
                isinstance(f.default, (int, float))
                and isinstance(literal, (int, float))
                and not isinstance(literal, bool)
                and float(f.default) != float(literal)
            ):
                rep.error(
                    scope,
                    f"参数 '{f.key}' 的 default={f.default} 与模板 {t.describe()} 的字面值"
                    f"{literal} 不一致（契约 §3.6 不变量 2）",
                )

    # ---------- 占位符 ----------
    placeholder_keys, bare = _scan_placeholders(graph)
    for key in sorted(placeholder_keys - set(schema.keys)):
        rep.error(
            scope,
            f"占位符 {{{{{key}}}}} 在 param_schema 里没有对应字段 —— "
            "它永远不会被替换，会原样出现在提示词里（规范 §5.4 规则 4）",
        )
    for key in sorted(bare):
        rep.warn(
            scope,
            f"发现疑似占位符 {{{{{key}}}}} 但语法不合法（key 只能含字母/数字/下划线）。"
            "规范 §5.4 规则 1 规定语法严格为 {{key}}",
        )

    for hint in collect_target_conflicts(graph, schema):
        rep.warn(scope, hint)

    return rep


_PLACEHOLDER = re.compile(r"\{\{([A-Za-z0-9_]+)\}\}")
_LOOSE_PLACEHOLDER = re.compile(r"\{\{([^{}]*)\}\}")


def _scan_placeholders(graph: Mapping[str, Any]) -> tuple[set[str], set[str]]:
    """返回 `(合法占位符 key 集合, 语法不合法的占位符原文集合)`。"""
    found: set[str] = set()
    loose: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, str):
            found.update(_PLACEHOLDER.findall(node))
            for raw in _LOOSE_PLACEHOLDER.findall(node):
                if not re.fullmatch(r"[A-Za-z0-9_]+", raw):
                    loose.add(raw)
        elif isinstance(node, dict):
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    for n in graph.values():
        walk(n)
    return found, loose


# ============================================================ L2


def render_smoke(
    entry: WorkflowEntry,
    definition: Mapping[str, Any],
    object_info: Mapping[str, Any] | None = None,
) -> Report:
    """L2：用多组参数各渲染一次，每次都做图结构检查（+ 有 object_info 时做 L3）。

    组合来源：
    1. **全 default** —— 最常用路径
    2. **每个 bypass 开关的两个取值** —— 覆盖"分支开/分支关"两条路径
       （bypass 的重连最容易写错，且只在特定的开关取值下才暴露）
    3. **数值字段取 min / max** —— 边界值（PRD §6.2 要求全部边界有测试用例）
    """
    rep = Report()
    rep.levels_run.extend(["L1", "L2"])
    scope = f"{entry.id}#L2"

    variants: list[tuple[str, dict[str, Any]]] = [("默认参数", {})]
    baseline_orphans: set[str] | None = None
    if object_info is not None and "L3" not in rep.levels_run:
        rep.levels_run.append("L3")

    meta, _ = split_definition(definition)
    try:
        switches = parse_switches(meta)
    except RenderError:
        switches = ()
    for sw in switches:
        fld = entry.schema.get(sw.key)
        if fld is None:
            continue
        on_value = sw.enabled_when[0]
        variants.append((f"开关 {sw.key}={on_value!r}（启用）", {sw.key: on_value}))
        try:
            off_value = _disabled_value(fld, sw.enabled_when)
        except ValueError as exc:
            rep.error(scope, f"开关 {sw.key} 无法构造关闭值: {exc}")
            continue
        variants.append((f"开关 {sw.key}={off_value!r}（关闭）", {sw.key: off_value}))

    numeric_fields = [f for f in entry.schema if f.type in _NUMERIC and f.min is not None
                      and f.max is not None]
    if numeric_fields:
        variants.append(("数值下界", {f.key: f.min for f in numeric_fields}))
        variants.append(("数值上界", {f.key: f.max for f in numeric_fields}))

    opts = RenderOptions(
        # L2 不解析素材：给一个只用于冒烟的回调，避免因缺 resolver 直接失败
        asset_resolver=lambda asset_id: f"smoke_{asset_id}.png",
    )

    # 由 `transform=ref_to_filename` 产生的入参：其值是**运行时**文件名（生产路径下
    # 素材先经 /upload/image 上传），静态快照的枚举按构造比不中 → 交给 L3 豁免。
    # 不加这一条，任何带参考图的工作流都会被 L2/L3 误判为 FAIL。
    runtime_asset_inputs: set[tuple[str, str]] = {
        (t.node_id, t.input)
        for f in entry.schema
        if f.type in ("image", "image_list")
        for t in f.targets
        if t.transform == "ref_to_filename"
    }

    for label, params in variants:
        try:
            result = render(definition, entry.schema, params, options=opts)
        except (RenderError, SchemaError) as exc:
            rep.error(scope, f"[{label}] 渲染失败: {exc}")
            continue

        if result.unresolved_placeholders:
            rep.error(
                scope,
                f"[{label}] 仍有未解析占位符 {list(result.unresolved_placeholders)}。"
                "用默认参数渲染时不该出现（说明 Schema 缺字段或缺 default）",
            )

        # 孤岛只在「相对默认参数新增」时报告：
        # 默认渲染的孤岛是静态属性，L1 已经报过一次，重复报会把真正的问题淹掉；
        # 而**新增**孤岛恰恰是 bypass 重连漏写/写错的信号，必须报。
        is_first = baseline_orphans is None
        for f in check_graph(result.workflow, scope=f"{scope} [{label}]",
                             include_orphans=is_first):
            rep.findings.append(f)
        if object_info is not None:
            # L3 跑在**渲染后**的图上 —— 这是模板校验覆盖不到的部分
            rep.findings.extend(
                check_object_info(
                    result.workflow,
                    object_info,
                    scope=f"{scope} [{label}]",
                    runtime_asset_inputs=runtime_asset_inputs,
                )
            )
        orphans = set(orphan_nodes(result.workflow))
        if is_first:
            baseline_orphans = orphans
        else:
            introduced = sorted(orphans - baseline_orphans, key=_nid_sort_key)
            if introduced:
                rep.error(
                    scope,
                    f"[{label}] 渲染后新增孤岛节点 {introduced} —— "
                    "bypass 裁剪后没有把下游重连到保留的节点上"
                    "（规范 §6.2 要求显式声明 disabled.rewire）",
                )

        # 关闭分支时，被裁节点必须真的消失
        for sw in switches:
            if label.startswith(f"开关 {sw.key}=") and "关闭" in label:
                leaked = [n for n in sw.prune if n in result.workflow]
                if leaked:
                    rep.error(scope, f"[{label}] 声明裁剪的节点仍在图中: {leaked}")
                if not result.pruned:
                    rep.error(scope, f"[{label}] 未发生任何裁剪，switches 未生效")

    return rep


def _disabled_value(field_obj: Any, enabled_when: tuple[Any, ...]) -> Any:
    """构造一个"不在 enabled_when 里"的合法取值。"""
    if field_obj.type == "bool":
        for candidate in (True, False):
            if candidate not in enabled_when:
                return candidate
        raise ValueError("bool 的两个取值都在 enabled_when 里，switch 永远启用")
    if field_obj.type == "enum":
        for candidate in field_obj.options:
            if candidate not in enabled_when:
                return candidate
        raise ValueError("所有 options 都在 enabled_when 里，switch 永远启用")
    raise ValueError(f"switch 字段的 type='{field_obj.type}' 不支持，只支持 bool / enum")


# ============================================================ 入口


def validate_registry(
    registry_path: str | pathlib.Path | None = None,
    only: str | None = None,
    settings_bounds: Mapping[str, Mapping[str, Any]] | str | pathlib.Path | None = None,
    object_info: Mapping[str, Any] | str | pathlib.Path | None = None,
) -> Report:
    """校验整份注册表。

    `settings_bounds` 传 A 流的全局红线（`contracts.md` §3.4 要求「加载注册表时告警」）。
    可用 `bounds_from_settings(settings)` 生成，或传一份 JSON/YAML 路径。
    不传则**跳过该检查并给出提示** —— 静默跳过会让"没检查"看起来像"检查通过"。

    `object_info` 传 ComfyUI `/object_info` 的内容或缓存文件路径，用于执行 **L3**。
    关键点：L3 会跑在**渲染后**的图上，因此能覆盖 `targets` 注入的正确性 ——
    这是只校验模板原文件的工具做不到的。
    """
    rep = Report()
    try:
        registry = Registry.load(registry_path)
    except SchemaError as exc:
        rep.error("registry", str(exc))
        return rep

    bounds: Mapping[str, Mapping[str, Any]] | None = None
    if isinstance(settings_bounds, (str, pathlib.Path)):
        try:
            bounds = _load_bounds(settings_bounds)
        except (OSError, SchemaError, ValueError, json.JSONDecodeError) as exc:
            rep.error("registry", f"读取 settings_bounds 失败: {exc}")
            return rep
    else:
        bounds = settings_bounds

    if bounds is None:
        rep.hint(
            "registry",
            "未提供 settings_bounds，**已跳过**「Schema 边界 ⊆ 全局红线」检查"
            "（contracts.md §3.4）。请用 --settings-bounds 提供，或调用 "
            "bounds_from_settings(settings)",
        )

    info: Mapping[str, Any] | None = None
    if isinstance(object_info, (str, pathlib.Path)):
        try:
            info = _load_object_info(object_info)
        except SchemaError as exc:
            rep.error("registry", f"读取 object_info 失败: {exc}")
            return rep
    else:
        info = object_info

    if info is None:
        rep.hint(
            "registry",
            "未提供 object_info，**已跳过 L3**（节点类型 / 入参名 / 枚举取值）。"
            "请用 --object-info 指向 deploy/schemas/object_info.v0.36.0.json",
        )

    for entry in registry:
        if only is not None and entry.id != only:
            continue
        rep.checked.append(entry.id)
        sub, definition = validate_entry(entry, registry, bounds)
        rep.extend(sub)
        if definition is None:
            continue
        rep.extend(render_smoke(entry, definition, info))
        if entry.verification != "gpu_verified":
            rep.hint(
                entry.id,
                f"verification={entry.verification} —— 仅通过离线校验（L1/L2/L3），"
                "**未在 GPU 上真实出图**。L4（出图）另需完成",
            )
    return rep


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="工作流离线校验（L1 结构自洽 + L2 渲染冒烟 + L3 节点合法性）。"
        "L3 需要 object_info（--object-info；缺省尝试 "
        "deploy/schemas/object_info.v0.36.0.json，该文件缺失时才跳过 L3）；"
        "L4（真实出图）需要 GPU，不在本命令范围内。"
    )
    ap.add_argument("--registry", default=None, help="注册表路径，默认 workflows/registry.yaml")
    ap.add_argument("--only", default=None, help="只校验某一条工作流 id")
    ap.add_argument(
        "--settings-bounds",
        default=None,
        help="全局红线（settings）边界表，JSON 或 YAML。"
        "缺省时尝试 workflows/settings_bounds.yaml；再缺则跳过检查并给出提示"
        "（contracts.md §3.4）",
    )
    ap.add_argument(
        "--object-info",
        default=None,
        help="ComfyUI /object_info 缓存路径，用于执行 L3（跑在**渲染后**的图上）。"
        "缺省时尝试 deploy/schemas/object_info.v0.36.0.json",
    )
    ap.add_argument("--json", action="store_true", help="以 JSON 输出，便于 CI 消费")
    args = ap.parse_args(argv)

    bounds_arg = args.settings_bounds
    if bounds_arg is None:
        fallback = DEFAULT_REGISTRY_PATH.parent / "settings_bounds.yaml"
        bounds_arg = fallback if fallback.exists() else None

    info_arg = args.object_info
    if info_arg is None:
        fallback_info = REPO_ROOT / "deploy" / "schemas" / "object_info.v0.36.0.json"
        info_arg = fallback_info if fallback_info.exists() else None

    report = validate_registry(args.registry, args.only, bounds_arg, info_arg)

    if args.json:
        print(json.dumps(
            {
                "ok": report.ok,
                "checked": report.checked,
                "errors": [{"scope": f.scope, "message": f.message} for f in report.errors],
                "warnings": [{"scope": f.scope, "message": f.message} for f in report.warnings],
                "hints": [
                    {"scope": f.scope, "message": f.message}
                    for f in report.findings if f.level == "hint"
                ],
            },
            ensure_ascii=False,
            indent=2,
        ))
        return 0 if report.ok else 1

    print(f"[校验] 工作流数: {len(report.checked)} · {', '.join(report.checked) or '（无）'}")
    levels = "/".join(report.levels_run) or "（无）"
    not_run = [lv for lv in ("L1", "L2", "L3", "L4") if lv not in report.levels_run]
    print(f"[校验] 已执行: {levels}"
          + (f" · **未执行**: {'/'.join(not_run)}" if not_run else ""))
    if "L3" not in report.levels_run:
        print("       ⚠ 未做 L3 —— 节点类型/入参名/枚举取值**未经验证**")
    print("       ⚠ L4（真实出图）永远需要 GPU，不在本命令范围内")
    print()

    errors = report.errors
    warnings = report.warnings
    hints = [f for f in report.findings if f.level == "hint"]

    if errors:
        print("— 错误 —")
        for f in errors:
            print(f)
        print()
    if warnings:
        print("— 告警 —")
        for f in warnings:
            print(f)
        print()
    if hints:
        print("— 提示 —")
        for f in hints:
            print(f)
        print()

    if report.ok:
        print(f"✅ {'/'.join(report.levels_run)} 通过"
              + ("（⚠ 这不代表工作流能出图：L4 未执行）" if "L4" not in report.levels_run else ""))
        print("VALIDATE=PASS")
        return 0
    print(f"❌ 发现 {len(errors)} 个错误")
    print("VALIDATE=FAIL")
    return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())


__all__ = [
    "Finding",
    "Report",
    "SETTINGS_BOUNDS_MAPPING",
    "ValidationError",
    "bounds_from_settings",
    "check_graph",
    "check_object_info",
    "check_settings_bounds",
    "main",
    "render_smoke",
    "validate_entry",
    "validate_registry",
]
