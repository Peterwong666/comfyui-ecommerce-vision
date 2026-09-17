"""golden set 用例的**可执行性**校验器（任务 **P8-01** 的离线验证）。

    # 完整跑（Schema → 渲染 → L1 → L3），不需要 GPU
    timeout 600 backend/.venv/bin/python -m engine.tools.check_golden_set

    # 只看结构，不跑 L3
    timeout 600 backend/.venv/bin/python -m engine.tools.check_golden_set --no-l3

    # 只看某一条工作流的用例
    timeout 600 backend/.venv/bin/python -m engine.tools.check_golden_set --workflow t2i_v1

---
### 它验什么

对 `tests/golden_set/cases/*.json` 的**每一个**用例：

| # | 判据 | 能抓出的错 |
|---|---|---|
| C1 | **结构**：`id` 唯一 · `workflow`/`notes` 非空 · `dimensions` 四维（品类/材质/光影/构图）齐备 · `expected` **必须为 `null`** · `expected_status` 必须是 `pending_gpu` | 漏填维度、把没跑过的评测**编造**成期望值 |
| C2 | **参数名存在于该工作流的真实 Schema** | ⭐ **参数名跨工作流复制错**（把 `inpaint_v1` 的 `mask_expand` 写进 `t2i_v1` 的用例） |
| C3 | **参数取值合法**（类型 / 边界 / enum / seed 域，走 `Field.validate`） | `seed: -2` 越界、`sampler_name: euler_a` 拼错枚举、`cfg: 25` 越界 |
| C4 | **能渲染**（`engine/render`，用桩 asset_resolver） | `targets` 指向**不存在的节点**（`engine/render._inject_targets` 会直接抛 `RenderError`）、占位符没对应字段 |
| C5 | **L1 图结构自洽**（`engine.validate.check_graph`，跑在**渲染后**的图上） | 连线指向不存在的节点、产物节点缺失/不唯一、渲染后新增孤岛 |
| C6 | **L3 节点合法性**（`engine.validate.check_object_info`，跑在**渲染后**的图上） | ⭐ **`targets` 注入点漂移**：注入的入参名该节点不接受、注入值不在枚举内、连线序号越界 |

**C6 是本工具的核心价值**：L1/L2 只看得见「我们自己写的图」，
而 L3 拿真实的 `/object_info` 快照去核对「引擎到底认不认这张图」——
`targets` 的节点 ID 与入参名全是**手写字符串**，改一次工作流就可能漂移，只有 L3 抓得到
（`docs/sop/debug_log.md` §0.2）。

---
### ⚠️ 它**不**证明什么（必须与结论一起读）

**本校验不证明能出图。**

* 它**不**证明「期望输出正确」—— 用例的 `expected` 全是 `null`，
  本仓库**一次真实评测都没跑过**（见 `tests/golden_set/README.md`）。
* 它**不**证明画质、不证明良品率、**不**产生任何通过率数字。
* 它跑在**桩 asset_resolver** 上（`verify_render_path._stub_resolver`），产出的文件名
  `dryrun_asset_<id>.png` **在引擎里并不存在** —— 所以带参考图的三条工作流
  （`i2i_v1` / `inpaint_v1` / `upscale_v1`）在这里只验「渲染器结构 + 注入落点」，
  **不**验「素材真能被引擎读到」。真实素材上传链路属 L4（需要 GPU，见 `verify_render_path.py`）。
* **L4（真实出图）无法离线完成。** 本工具的输出无论多绿，都**不等于**「这些用例能出图」。

> 与 `engine/validate.py:16-19` 的能力边界声明同源：**只能证明 L1/L2/L3，
> 不能据此声称「工作流已跑通」。**

---
### 用例的机器可读格式

`tests/golden_set/cases/*.json`，一个文件一个用例：

```json
{
  "id": "gs-t2i-001",
  "workflow": "t2i_v1",
  "dimensions": {"category": "…", "material": "…", "light": "…", "composition": "…"},
  "params": {"prompt": "…", "cfg": 6.5, "seed": 20260916},
  "expected": null,
  "expected_status": "pending_gpu",
  "notes": "…"
}
```

* `expected` **必须为 `null`** —— 这是硬约束，不是占位符。谁填了真值谁就在编造评测结果。
* `params` 只需写**该用例要覆盖的参数**；其余由渲染器用 Schema 的 `default` 兜底。
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from engine.errors import RenderError, SchemaError
from engine.registry import REPO_ROOT, Registry, WorkflowEntry
from engine.render import RenderOptions, load_definition, render
from engine.schema import ParamSchema

# ⚠️ 复用而不是重造：`verify_render_path.py` 的离线桩 resolver 已经处理了
# 「不解析路径、只让渲染走通」这条语义，且它的 docstring 明确写了
# 「产出的文件名在引擎里不存在，只说明渲染器结构正确」。这正是不需要 GPU 的那部分。
# 它是私有名（不在 `__all__` 里），这里直接导入是有意为之 —— 本文件与它同属
# `engine/tools/` 的校验工具族，重复实现一份桩 resolver 才是真正的问题。
from engine.tools.verify_render_path import _stub_resolver
from engine.validate import check_graph, check_object_info

#: 用例目录与默认 object_info 快照
DEFAULT_CASES_DIR = REPO_ROOT / "tests" / "golden_set" / "cases"
DEFAULT_OBJECT_INFO_PATH = REPO_ROOT / "deploy" / "schemas" / "object_info.v0.36.0.json"

#: 本文件输出的**固定措辞**。不许改写 —— 它是结论强度的一部分。
DISCLAIMER = "本校验不证明能出图。"

#: `expected_status` 唯一允许的值：没有 GPU 评测前，用例只能停在这里。
PENDING_STATUS = "pending_gpu"

#: 四维矩阵的键（`todolist.md:408`：品类 × 材质 × 光影 × 构图）
DIMENSION_KEYS: tuple[str, ...] = ("category", "material", "light", "composition")

#: 用例文件的必备键
REQUIRED_KEYS: tuple[str, ...] = (
    "id",
    "workflow",
    "dimensions",
    "params",
    "expected",
    "expected_status",
    "notes",
)

#: 单条用例的检查结果取值
STATUS_PASS = "pass"
STATUS_FAIL = "fail"
STATUS_SKIP = "skip"


# ============================================================ 数据结构


@dataclass
class GoldenCase:
    """一个用例。`expected` 恒为 `None`（见模块 docstring）。"""

    id: str
    workflow: str
    dimensions: Mapping[str, Any]
    params: Mapping[str, Any]
    notes: str
    path: pathlib.Path
    raw: Mapping[str, Any] = field(default_factory=dict, repr=False)


@dataclass
class Outcome:
    """一条用例的校验结果。`status` 为 `pass` / `fail` / `skip`。"""

    case_id: str
    workflow: str
    status: str
    levels_run: tuple[str, ...] = ()
    detail: str = ""


@dataclass
class Report:
    cases: list[GoldenCase] = field(default_factory=list)
    outcomes: list[Outcome] = field(default_factory=list)
    #: 文件层面的问题（JSON 不合法、缺键、id 重复…）：连用例都构造不出来
    parse_errors: list[str] = field(default_factory=list)
    #: 规模/覆盖问题
    global_errors: list[str] = field(default_factory=list)
    #: 实际执行到的级别（取所有用例的并集），用于**如实**说明结论强度
    levels_run: list[str] = field(default_factory=list)

    def add(self, outcome: Outcome) -> None:
        self.outcomes.append(outcome)
        for lv in outcome.levels_run:
            if lv not in self.levels_run:
                self.levels_run.append(lv)

    @property
    def failures(self) -> list[Outcome]:
        return [o for o in self.outcomes if o.status == STATUS_FAIL]

    @property
    def skipped(self) -> list[Outcome]:
        return [o for o in self.outcomes if o.status == STATUS_SKIP]

    @property
    def passed(self) -> list[Outcome]:
        return [o for o in self.outcomes if o.status == STATUS_PASS]

    @property
    def ok(self) -> bool:
        """⚠️ **跳过不算通过。**

        golden set 里的 `skip` 只可能来自「L3 未执行」（缺 `object_info`），
        那意味着该用例**没有被完整校验**。若此处忽略跳过、返回成功，
        CI 会把「少做了一层」当成全绿 —— 这正是本项目明令禁止的误导
        （`engine/validate.py` 的能力边界声明同源）。
        """
        return not (self.failures or self.skipped or self.parse_errors or self.global_errors)

    def workflows_touched(self) -> list[str]:
        out: list[str] = []
        for c in self.cases:
            if c.workflow and c.workflow not in out:
                out.append(c.workflow)
        return out

    def counts_by_status(self) -> dict[str, int]:
        return {
            STATUS_PASS: len(self.passed),
            STATUS_FAIL: len(self.failures),
            STATUS_SKIP: len(self.skipped),
        }


# ============================================================ 用例加载


def load_cases(cases_dir: str | pathlib.Path) -> tuple[list[GoldenCase], list[str]]:
    """读 `cases/*.json`。返回 `(用例, 问题)`。**纯文件 IO，不碰注册表。**"""
    d = pathlib.Path(cases_dir)
    if not d.is_dir():
        raise SchemaError(f"用例目录不存在: {d}")

    files = sorted(p for p in d.glob("*.json") if p.is_file())
    cases: list[GoldenCase] = []
    errors: list[str] = []
    seen: dict[str, pathlib.Path] = {}

    for p in files:
        rel = p.name
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            errors.append(f"{rel}: 不是合法 JSON —— {exc}")
            continue
        if not isinstance(raw, dict):
            errors.append(f"{rel}: 顶层必须是对象，实际是 {type(raw).__name__}")
            continue

        missing = [k for k in REQUIRED_KEYS if k not in raw]
        if missing:
            errors.append(
                f"{rel}: 缺少必备键 {missing}（契约见本模块 docstring 的用例格式）"
            )
            continue

        case_id = raw.get("id")
        if not isinstance(case_id, str) or not case_id.strip():
            errors.append(f"{rel}: id 必须是非空字符串")
            continue
        if case_id in seen:
            errors.append(f"{rel}: 用例 id '{case_id}' 重复（首次出现在 {seen[case_id].name}）")
            continue
        seen[case_id] = p

        dims = raw.get("dimensions")
        params = raw.get("params")
        problems = _shape_problems(raw, dims, params)
        if problems:
            errors.extend(f"{rel}: {m}" for m in problems)
            continue

        cases.append(
            GoldenCase(
                id=case_id,
                workflow=str(raw["workflow"]),
                dimensions=dims,
                params=params,
                notes=str(raw["notes"]),
                path=p,
                raw=raw,
            )
        )

    if not files:
        errors.append(f"{d} 下没有任何 *.json 用例 —— 这是「没做」，不是「全过」")
    return cases, errors


def _shape_problems(
    raw: Mapping[str, Any], dims: Any, params: Any
) -> list[str]:
    """C1：与注册表无关的结构判据。"""
    out: list[str] = []
    wf = raw.get("workflow")
    if not isinstance(wf, str) or not wf.strip():
        out.append("workflow 必须是非空字符串")

    notes = raw.get("notes")
    if not isinstance(notes, str) or not notes.strip():
        out.append("notes 必须是非空字符串（写清这个用例想覆盖什么失败模式）")

    # 🔴 expected 必须为 null —— 一次真实评测都没跑过，不许编造期望值
    if "expected" in raw and raw["expected"] is not None:
        out.append(
            f"expected 必须为 null，实际是 {raw['expected']!r}。"
            "本仓库**一次真实评测都没跑过** —— 填任何非 null 值都是在编造评测结果。"
            "等 golden set 在 GPU 上跑完并归档产物后，再另立字段记录真实期望"
        )

    status = raw.get("expected_status")
    if status != PENDING_STATUS:
        out.append(
            f"expected_status 必须是 '{PENDING_STATUS}'，实际是 {status!r}"
            f"（每一条用例在跑过真实评测前都只能停在 '{PENDING_STATUS}'）"
        )

    if not isinstance(dims, dict):
        out.append(f"dimensions 必须是对象，实际是 {type(dims).__name__}")
    else:
        miss = [k for k in DIMENSION_KEYS if not str(dims.get(k) or "").strip()]
        if miss:
            out.append(
                f"dimensions 缺少四维取值 {miss}（todolist.md:408 要求覆盖 品类 × 材质 × 光影 × 构图）"
            )

    if not isinstance(params, dict):
        out.append(f"params 必须是对象，实际是 {type(params).__name__}")
    elif not params:
        out.append("params 为空 —— 一个不指定任何参数的用例没有覆盖力（全 default 另有冒烟用例）")
    return out


# ============================================================ 单条校验


def check_case(
    case: GoldenCase,
    registry: Registry,
    object_info: Mapping[str, Any] | None,
) -> Outcome:
    """校验一条用例。返回 `Outcome`（`status` 为 pass/fail/skip）。

    ⚠️ **不抛异常** —— 所有失败都收集成问题列表，便于一次看到全部。
    """
    problems: list[str] = []
    levels: list[str] = []

    entry = registry.get(case.workflow)
    if entry is None:
        known = [w.id for w in registry]
        return Outcome(
            case.id,
            case.workflow,
            STATUS_FAIL,
            (),
            f"workflow='{case.workflow}' 不在注册表里，已知：{known}",
        )

    # ---------- C2 / C3 参数名 + 取值 ----------
    schema: ParamSchema = entry.schema
    for key, value in case.params.items():
        fld = schema.get(key)
        if fld is None:
            problems.append(
                f"参数 '{key}' 不存在于工作流 '{case.workflow}' 的 Schema "
                f"（该工作流可用参数：{schema.keys}）—— 跨工作流复制参数名是最常见的错法"
            )
            continue
        try:
            fld.validate(value)
        except RenderError as exc:
            problems.append(f"参数 '{key}' 非法：{exc}")
    levels.append("Schema")

    if problems:
        # 参数层就错了，后面三级没有意义。
        # ⚠️ **不要把没跑的级别记进 levels_run** —— 那会让输出声称「L1/L3 已执行」，
        # 与「少做一层不许装成全绿」的纪律直接冲突。
        return Outcome(
            case.id,
            case.workflow,
            STATUS_FAIL,
            tuple(levels),
            "；".join(problems) + "（参数层未通过 → 渲染/L1/L3 **未执行**）",
        )

    # ---------- C4 渲染（带参考图的三条用桩 resolver）----------
    try:
        definition = load_definition(registry.definition_path(entry))
    except RenderError as exc:
        return Outcome(
            case.id, case.workflow, STATUS_FAIL, tuple(levels), f"定义文件读取失败：{exc}"
        )

    try:
        result = render(
            definition,
            schema,
            dict(case.params),
            options=RenderOptions(asset_resolver=_stub_resolver),
        )
    except (RenderError, SchemaError) as exc:
        return Outcome(
            case.id,
            case.workflow,
            STATUS_FAIL,
            tuple(levels),
            f"渲染失败：{exc}（渲染层未通过 → L1/L3 **未执行**）",
        )
    levels.append("render")

    if result.unresolved_placeholders:
        problems.append(
            f"渲染后仍有未解析占位符 {list(result.unresolved_placeholders)} —— "
            "说明该键既没有传给用例、Schema 里也没有 default"
        )

    if result.warnings:
        # 警告不阻断，但必须显示：`_inject_targets` 的"入参在原图里不存在"就在这里
        problems.append(
            "渲染期告警（不阻断，但需确认不是配置漂移）：" + "；".join(result.warnings)
        )

    # ---------- C5 L1：跑在渲染后的图上 ----------
    for f in check_graph(result.workflow, scope=f"{case.id}#L1"):
        if f.level == "error":
            problems.append(f"L1 {f.scope}: {f.message}")
    levels.append("L1")

    # ---------- C6 L3：跑在渲染后的图上（本工具的核心价值）----------
    if object_info is None:
        # ⚠️ 这里**不**把 "L3" 记进 levels_run：它没跑。
        # 输出里的「**未执行**: L3」由 render_report 从 levels_run 反推，如实列出。
        return Outcome(
            case.id,
            case.workflow,
            STATUS_SKIP,
            tuple(levels),
            "未提供 object_info → **L3 未执行**，节点类型/入参名/枚举取值、"
            "以及 targets 注入落点**均未经验证**。不计入通过"
            + ("；另有问题：" + "；".join(problems) if problems else ""),
        )

    for f in check_object_info(
        result.workflow,
        object_info,
        scope=f"{case.id}#L3",
        runtime_asset_inputs=_runtime_asset_inputs(entry),
    ):
        if f.level == "error":
            problems.append(f"L3 {f.scope}: {f.message}")
    levels.append("L3")

    if problems:
        return Outcome(case.id, case.workflow, STATUS_FAIL, tuple(levels), "；".join(problems))
    return Outcome(case.id, case.workflow, STATUS_PASS, tuple(levels))


def _runtime_asset_inputs(entry: WorkflowEntry) -> set[tuple[str, str]]:
    """由 `transform=ref_to_filename` 产生的入参位置，供 L3 豁免。

    ⚠️ 这段推导与 `engine/validate.render_smoke` 里那一段**语义相同**，但本文件
    **不导入**它 —— `render_smoke` 把推导写在函数体内、没有暴露成可复用的辅助函数，
    而本轮作业面只允许改 `engine/validate.py` 的**顶部 docstring**（不许加函数）。
    故此处就地推导，并在此显式登记这条重复，避免后人以为是漏了复用。

    为什么必须豁免：这些入参的值是**运行时**由 asset_resolver 决定的（生产路径下素材先经
    `/upload/image` 上传，文件名届时才存在），而 `/object_info` 的枚举是**快照时刻对输入目录的
    静态扫描** —— 拿静态快照比运行时文件名，**按构造就不可能匹配**
    （`docs/sop/debug_log.md` I1-1）。不豁免会让**任何**带参考图的工作流必然失败。
    """
    return {
        (t.node_id, t.input)
        for f in entry.schema
        if f.type in ("image", "image_list")
        for t in f.targets
        if t.transform == "ref_to_filename"
    }


# ============================================================ 整体校验


def check_golden_set(
    cases_dir: str | pathlib.Path,
    registry: Registry,
    object_info: Mapping[str, Any] | None = None,
    *,
    only: str | None = None,
) -> Report:
    """校验整个 golden set。**核心是纯函数** —— 便于测试注入合成注册表 / object_info。"""
    rep = Report()
    cases, parse_errors = load_cases(cases_dir)
    rep.parse_errors.extend(parse_errors)
    rep.cases = cases

    for case in cases:
        if only is not None and case.workflow != only:
            continue
        rep.add(check_case(case, registry, object_info))

    if rep.cases and not rep.outcomes:
        rep.global_errors.append(f"没有 workflow='{only}' 的用例")
    return rep


def render_report(rep: Report, *, object_info_used: bool) -> str:
    """人可读输出。**结尾固定打印 DISCLAIMER**。"""
    lines: list[str] = []
    touched = rep.workflows_touched()
    lines.append(
        f"[golden set] 用例 {len(rep.cases)} 条"
        f" · 涉及工作流 {len(touched)} 条"
        f"{'（' + ', '.join(touched) + '）' if touched else ''}"
    )
    c = rep.counts_by_status()
    lines.append(
        f"[校验] 通过 {c[STATUS_PASS]} · 失败 {c[STATUS_FAIL]} · 跳过 {c[STATUS_SKIP]}"
    )
    lines.append(
        "       ⚠ 「跳过」= L3 未执行，**不计入通过**"
        "（不拿「少做了一层」冒充「检查通过」）"
    )
    levels = "/".join(rep.levels_run) or "（无）"
    not_run = [lv for lv in ("Schema", "render", "L1", "L3") if lv not in rep.levels_run]
    lines.append(
        f"       已执行: {levels}" + (f" · **未执行**: {'/'.join(not_run)}" if not_run else "")
    )
    if not object_info_used:
        lines.append(
            "       🔴 未提供 object_info → **L3 全部未执行**。"
            "节点类型/入参名/枚举取值、以及 targets 注入落点均**未经验证**。"
            "请用 --object-info deploy/schemas/object_info.v0.36.0.json"
        )
    lines.append("       ⚠ L4（真实出图）永远需要 GPU，**不在本命令范围内**")
    lines.append("")

    if rep.parse_errors:
        lines.append("— 结构/文件问题 —")
        lines.extend(f"  ✗ {e}" for e in rep.parse_errors)
        lines.append("")
    if rep.global_errors:
        lines.append("— 覆盖问题 —")
        lines.extend(f"  ✗ {e}" for e in rep.global_errors)
        lines.append("")
    if rep.failures:
        lines.append("— 用例失败（具体到用例 id）—")
        for o in rep.failures:
            lines.append(f"  ✗ [{o.case_id}] ({o.workflow}) {o.detail}")
        lines.append("")
    if rep.skipped:
        lines.append("— 跳过 —")
        for o in rep.skipped:
            lines.append(f"  · [{o.case_id}] ({o.workflow}) {o.detail}")
        lines.append("")

    lines.append("-" * 72)
    if rep.ok:
        lines.append(f"✅ golden set 可执行性校验通过（{c[STATUS_PASS]} 条用例全绿）")
    else:
        lines.append(
            f"❌ 发现 {c[STATUS_FAIL]} 条用例失败、"
            f"{len(rep.parse_errors)} 个结构问题、{len(rep.global_errors)} 个覆盖问题"
        )
    lines.append(f"⚠️ {DISCLAIMER}")
    lines.append(
        "   —— 本工具只证明「用例**能被渲染并通过 L1/L3**」。"
        "它**不**证明「期望输出正确」（用例的 expected 全为 null），"
        "更**不**证明画质与良品率。"
    )
    lines.append(
        "   —— 真实出图（L4）需要 GPU；本仓库**至今没有跑过任何一次 golden set 评测**。"
    )
    return "\n".join(lines)


# ============================================================ CLI


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m engine.tools.check_golden_set",
        description="golden set 用例的可执行性校验器（P8-01 离线验证）。"
        "只证明用例能被渲染并通过 L1/L3，不证明能出图。",
    )
    ap.add_argument("--cases", default=None, help=f"用例目录，默认 {DEFAULT_CASES_DIR}")
    ap.add_argument("--registry", default=None, help="注册表路径，默认 workflows/registry.yaml")
    ap.add_argument(
        "--object-info",
        default=None,
        help=f"ComfyUI /object_info 快照路径，默认 {DEFAULT_OBJECT_INFO_PATH}。"
        "缺省且快照不存在时 **L3 全部跳过（不计入通过）**",
    )
    ap.add_argument("--no-l3", action="store_true", help="显式跳过 L3（结果里会标为跳过）")
    ap.add_argument("--workflow", default=None, help="只校验某一条工作流的用例")
    ap.add_argument("--json", action="store_true", help="以 JSON 输出，便于 CI 消费")
    args = ap.parse_args(argv)

    cases_dir = pathlib.Path(args.cases) if args.cases else DEFAULT_CASES_DIR
    try:
        registry = Registry.load(args.registry)
    except SchemaError as exc:
        print(f"[fatal] 注册表加载失败: {exc}")
        return 2

    object_info: Mapping[str, Any] | None = None
    if not args.no_l3:
        info_path = pathlib.Path(args.object_info) if args.object_info else DEFAULT_OBJECT_INFO_PATH
        if info_path.is_file():
            try:
                loaded = json.loads(info_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                print(f"[fatal] object_info 不是合法 JSON: {info_path} —— {exc}")
                return 2
            if not isinstance(loaded, dict):
                print(f"[fatal] object_info 顶层必须是对象: {info_path}")
                return 2
            object_info = loaded
        else:
            print(f"[warn] object_info 快照不存在: {info_path} → **L3 将全部跳过**（不计入通过）")

    try:
        rep = check_golden_set(cases_dir, registry, object_info, only=args.workflow)
    except SchemaError as exc:
        print(f"[fatal] {exc}")
        return 2

    if args.json:
        print(json.dumps(
            {
                "ok": rep.ok,
                "counts": rep.counts_by_status(),
                "levels_run": rep.levels_run,
                "cases": [
                    {
                        "id": o.case_id,
                        "workflow": o.workflow,
                        "status": o.status,
                        "levels_run": list(o.levels_run),
                        "detail": o.detail,
                    }
                    for o in rep.outcomes
                ],
                "parse_errors": rep.parse_errors,
                "global_errors": rep.global_errors,
                "disclaimer": DISCLAIMER,
            },
            ensure_ascii=False,
            indent=2,
        ))
        return 0 if rep.ok else 1

    print(render_report(rep, object_info_used=object_info is not None))
    return 0 if rep.ok else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())


__all__ = [
    "DEFAULT_CASES_DIR",
    "DEFAULT_OBJECT_INFO_PATH",
    "DIMENSION_KEYS",
    "DISCLAIMER",
    "GoldenCase",
    "Outcome",
    "PENDING_STATUS",
    "REQUIRED_KEYS",
    "Report",
    "STATUS_FAIL",
    "STATUS_PASS",
    "STATUS_SKIP",
    "check_case",
    "check_golden_set",
    "load_cases",
    "main",
    "render_report",
]
