"""缺陷知识库（`docs/sop/defect_kb.md`）的**机械**校验器（任务 **P8-06** 的离线验证）。

    PYTHONPATH=. backend/.venv/bin/python -m engine.tools.check_defect_kb

---
### 它验什么

对 `defect_kb.md` 里每一条**带 `recommended_params` 的条目**，把该 JSON 拿去和
`workflows/registry.yaml` 里**该条目自己声明的那条工作流的真实 `param_schema`** 对照：

| # | 判据 | 能抓出的错 |
|---|---|---|
| R1 | 结构：id 格式/唯一、四要素非空、`链路`/`verified_by` 枚举、`依据`非空 | 漏填、拼错枚举、复制粘贴留下的空列 |
| R2 | **反向词 × 链路**：`链路=klein` 的条目不得引用 `neg-*` | 给 CFG=1 的蒸馏链路开反向词（**假知识**） |
| R3 | **工作流 × 链路一致性**：参数所属工作流的 Schema 必须**有/没有** `negative_prompt` 字段 | 跨链路张冠李戴 |
| R4 | **参数机械合法性**：参数名存在于该工作流 Schema、类型/边界/enum/seed 域全部通过 `Field.validate` | `cfg: 25` 越界、`cfg_scale` 名字不存在、`sampler_name: euler_a_typo`、`mask_expand` 出现在 `t2i_v1` |
| R5 | 引用完整性：`negative_refs` 的每个 id 存在于 `assets/prompt_lib/negative.yaml` | 指向已删除/写错的词条 |
| R6 | 规模：条目 ≥30（FR-7.3 / AC-P4）、6 类均有条目（JD 行 9） | 未达验收标准 |
| R7 | **`verified_by=l3` 必须带 `recommended_params`**（`l3` 的含义就是「参数已过本脚本的机械校验」） | 拿「没有可校验内容」冒充「已机械校验过」 |

> ⚠️ 本脚本**不核对** `verified_by` 是否属实（那是文档自述）。R7 是唯一一条能机械执行的
> 自洽性检查：`l3` 至少要**真的有一组参数**被本脚本验过。`none` / `doc` 没有这个约束。

R2 与 R3 用的是**推导**而非硬编码工作流 id：判据是「该工作流的 Schema 里到底有没有
`negative_prompt` 字段」—— 这与 `assets/prompt_lib/negative.yaml:5-13` 的口径同源，
换工作流时不需要改本脚本。

---
### ⚠️ 它**不**证明什么（必须与结论一起读）

**本校验只证明参数合法，不证明参数有效。**

* 它证明的是「`cfg: 6.5` 在 `t2i_v1` 的 Schema 里合法」，**不是**
  「改成 6.5 就能修掉过曝」。参数**有效性**只能靠真实出图 + 人工/自动质检（P8-03/P8-04）。
* 它**完全看不到**画质。`verified_by` 列是本脚本唯一能读到的"验证等级"信号，
  但它读的是**文档自己写的字**，本脚本**无法核实**该标注是否属实。
* 它不校验 `negative_prompt` 的**文本内容**是否真的对得上 `negative_refs`
  （那是语义，不是机械关系）。

---
### 依赖说明

本脚本自身只用标准库 + `engine/` 既有模块。读取 `workflows/registry.yaml` 需要 PyYAML ——
这是 `engine/registry.py` **既有的**惰性导入路径（`engine/` 运行时本身仍零依赖）。

---
### 知识库里的机器可读格式（与本文档的解析器一一对应）

每个分类一个二级标题，形如 `## 2. text · 文字`；每条一行表格，**恰好 9 列且单元格内不得出现 `|`**：

```
| id | 瑕疵（现象） | 成因 | 解法 | 链路 | recommended_params | negative_refs | verified_by | 依据 |
```

* `链路` ∈ `sdxl` / `klein` / `all` / `post`
* `recommended_params`：`—` 表示无关联参数（跳过，**不计入通过**）；否则为 JSON 对象，
  **必须含 `workflow` 键**（指明所属工作流），其余键为该工作流的参数
* `negative_refs`：`neg-001,neg-028` 形式，或 `—`
* `verified_by` ∈ `none`（推演）/ `doc`（抄自既有文档）/ `l3`（参数合法性已机械校验通过）/ `gpu`（真实出图验证过）
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
from dataclasses import dataclass, field
from typing import Any

from engine.errors import RenderError, SchemaError
from engine.registry import REPO_ROOT, Registry, WorkflowEntry
from engine.schema import ParamSchema

#: 仓库侧默认路径
DEFAULT_KB_PATH = REPO_ROOT / "docs" / "sop" / "defect_kb.md"
DEFAULT_NEGATIVE_PATH = REPO_ROOT / "assets" / "prompt_lib" / "negative.yaml"

#: 本文档输出的**固定措辞**。不许改写 —— 它是结论强度的一部分。
DISCLAIMER = "本校验只证明参数合法，不证明参数有效。"

#: 6 个必备分类（`todolist.md:52` JD 行 9：手部/文字/畸变/过曝/材质失真/结构漂移）
REQUIRED_CATEGORIES: tuple[str, ...] = (
    "hand",
    "text",
    "distortion",
    "exposure",
    "material",
    "drift",
)

#: `链路` 取值。语义写在 KB 文首，这里只做枚举。
VALID_CHAINS: frozenset[str] = frozenset({"sdxl", "klein", "all", "post"})

#: `verified_by` 取值。**`gpu` 只在真实出图验证过时才允许**。
VALID_VERIFIED_BY: frozenset[str] = frozenset({"none", "doc", "l3", "gpu"})

#: 反向词字段名。链路判据是「该工作流的 Schema 有没有这个字段」，不是硬编码 id。
NEGATIVE_FIELD = "negative_prompt"

#: 验收规模（FR-7.3 / AC-P4）
MIN_ENTRIES = 30

_TABLE_ROW = re.compile(r"^\|\s*(D-\d+)\s*\|")
_HEADING = re.compile(r"^##\s+(\d+)\.\s*([a-z_]+)\s*(?:·.*)?$")
_NEG_ID = re.compile(r"^neg-\d{3}$")
_DEFECT_ID = re.compile(r"^D-\d{2,}$")

#: 表格列数（不含首尾的空字段）
COLUMNS: tuple[str, ...] = (
    "id",
    "symptom",
    "cause",
    "solution",
    "chain",
    "recommended_params",
    "negative_refs",
    "verified_by",
    "evidence",
)
N_COLS = len(COLUMNS)

#: 表示"无"的单元格写法
_EMPTY_CELLS = frozenset({"—", "-", "--", "无", ""})


# ============================================================ 数据结构


@dataclass
class KBEntry:
    """表格里的一行。"""

    id: str
    category: str
    symptom: str
    cause: str
    solution: str
    chain: str
    params_raw: str
    negative_refs_raw: str
    verified_by: str
    evidence: str
    line_no: int = 0

    @property
    def has_params(self) -> bool:
        return self.params_raw not in _EMPTY_CELLS

    def params(self) -> dict[str, Any]:
        """解析 `recommended_params`；不是合法 JSON 对象时抛 `SchemaError`。"""
        data = json.loads(self.params_raw)
        if not isinstance(data, dict):
            raise SchemaError(
                f"recommended_params 必须是 JSON 对象（形如 "
                f'{{"workflow":"t2i_v1","cfg":6.5}}），实际是 {type(data).__name__}'
            )
        return data

    def negative_refs(self) -> tuple[str, ...]:
        if self.negative_refs_raw in _EMPTY_CELLS:
            return ()
        return tuple(
            part.strip()
            for part in re.split(r"[,，]", self.negative_refs_raw)
            if part.strip()
        )


@dataclass
class Outcome:
    """一条条目的校验结果。`status` 为 `pass` / `fail` / `skip`。"""

    entry_id: str
    category: str
    status: str
    detail: str = ""


@dataclass
class Report:
    entries: list[KBEntry] = field(default_factory=list)
    outcomes: list[Outcome] = field(default_factory=list)
    #: 解析期（表格之外）的问题：表头列数不对、分类名未知、文件结构错等
    parse_errors: list[str] = field(default_factory=list)
    #: 全局问题：条目总数不足、某分类缺失
    global_errors: list[str] = field(default_factory=list)
    #: 实际参与校验的工作流 id（去重保序），供输出说明校验面
    workflows_touched: list[str] = field(default_factory=list)

    def added(self, entry: KBEntry, problems: list[str], *, skipped: str = "") -> None:
        if problems:
            self.outcomes.append(
                Outcome(entry.id, entry.category, "fail", "；".join(problems))
            )
        elif skipped:
            self.outcomes.append(Outcome(entry.id, entry.category, "skip", skipped))
        else:
            self.outcomes.append(Outcome(entry.id, entry.category, "pass"))

    @property
    def failures(self) -> list[Outcome]:
        return [o for o in self.outcomes if o.status == "fail"]

    @property
    def skipped(self) -> list[Outcome]:
        return [o for o in self.outcomes if o.status == "skip"]

    @property
    def passed(self) -> list[Outcome]:
        return [o for o in self.outcomes if o.status == "pass"]

    @property
    def ok(self) -> bool:
        return not (self.failures or self.parse_errors or self.global_errors)

    def counts_by_verified(self, entries: list[KBEntry]) -> dict[str, int]:
        out = {k: 0 for k in sorted(VALID_VERIFIED_BY)}
        for e in entries:
            if e.verified_by in out:
                out[e.verified_by] += 1
        return out

    def counts_by_chain(self, entries: list[KBEntry]) -> dict[str, int]:
        out = {k: 0 for k in sorted(VALID_CHAINS)}
        for e in entries:
            if e.chain in out:
                out[e.chain] += 1
        return out


# ============================================================ 解析


def parse_kb(text: str) -> tuple[list[KBEntry], list[str]]:
    """从 markdown 文本里抽出表格行。返回 `(条目, 解析错误)`。"""
    entries: list[KBEntry] = []
    errors: list[str] = []
    category: str | None = None
    seen_ids: dict[str, int] = {}

    for lineno, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.rstrip()
        heading = _HEADING.match(line)
        if heading:
            category = heading.group(2)
            if category not in REQUIRED_CATEGORIES:
                errors.append(
                    f":{lineno} 未知分类 '{category}'，可选：{list(REQUIRED_CATEGORIES)}"
                )
            continue
        if not _TABLE_ROW.match(line):
            continue
        if category is None:
            errors.append(f":{lineno} 表格行出现在任何分类标题之前，无法判定归属分类")
            continue

        cells = line.split("|")
        # 首尾各一个空字段
        if len(cells) != N_COLS + 2:
            errors.append(
                f":{lineno} 表格列数为 {len(cells) - 2}，应为 {N_COLS}（{list(COLUMNS)}）。"
                "常见原因：单元格文本里出现了 `|`，或漏填了一列"
            )
            continue

        values = [c.strip() for c in cells[1:-1]]
        entry = KBEntry(
            id=values[0],
            category=category,
            symptom=values[1],
            cause=values[2],
            solution=values[3],
            chain=values[4],
            params_raw=values[5],
            negative_refs_raw=values[6],
            verified_by=values[7],
            evidence=values[8],
            line_no=lineno,
        )
        if entry.id in seen_ids:
            errors.append(
                f":{lineno} 条目 id '{entry.id}' 重复（首次出现在 :{seen_ids[entry.id]}）"
            )
            continue
        seen_ids[entry.id] = lineno
        entries.append(entry)

    return entries, errors


def collect_negative_ids(path: str | pathlib.Path = DEFAULT_NEGATIVE_PATH) -> set[str]:
    """从 `negative.yaml` 取 `neg-xxx` id 集合。

    刻意用正则而不是 YAML 解析：**本脚本自身保持零第三方依赖**，
    而这里要的只是"引用完整性"这一件事，正则足够且不会因 YAML 结构变动而崩。
    """
    p = pathlib.Path(path)
    if not p.is_file():
        raise SchemaError(f"反向词库不存在: {p}")
    ids = set(re.findall(r"^\s*-\s*id:\s*(neg-\d{3})\s*$", p.read_text(encoding="utf-8"), re.M))
    if not ids:
        raise SchemaError(
            f"从 {p} 里没解析出任何 neg-xxx id —— 要么文件格式变了，"
            "要么该文件被清空。**不许把这种情况当成'没有引用需要校验'**"
        )
    return ids


# ============================================================ 单条校验


def workflow_has_negative_field(entry: WorkflowEntry) -> bool:
    """该工作流的 Schema 里有没有 `negative_prompt` 字段。

    这是"链路"判据的**唯一来源** —— 不硬编码工作流 id：
    CFG=1 的蒸馏链路（klein）根本没有这个字段，所以反向词**无处可填**
    （`assets/prompt_lib/negative.yaml:5-13`）。
    """
    return NEGATIVE_FIELD in entry.schema


def check_entry(
    entry: KBEntry,
    registry: Registry,
    negative_ids: set[str],
) -> tuple[list[str], str]:
    """校验一条条目。返回 `(问题列表, 跳过原因)`。

    问题列表非空 → 该条失败；问题为空且跳过原因非空 → 该条跳过（不计入通过）。
    """
    problems: list[str] = []

    # ---------- R1 结构 ----------
    if not _DEFECT_ID.match(entry.id):
        problems.append(f"id '{entry.id}' 不合规范（应为 D-01 / D-02 …）")
    for name, value in (
        ("瑕疵（现象）", entry.symptom),
        ("成因", entry.cause),
        ("解法", entry.solution),
        ("依据", entry.evidence),
    ):
        if value in _EMPTY_CELLS:
            problems.append(f"列「{name}」为空 —— 四要素（现象→成因→解法→参数）必须齐备")
    if entry.chain not in VALID_CHAINS:
        problems.append(
            f"链路 '{entry.chain}' 非法，可选：{sorted(VALID_CHAINS)}"
            "（链路必须写明，否则无法判断解法在哪些工作流上成立）"
        )
    if entry.verified_by not in VALID_VERIFIED_BY:
        problems.append(
            f"verified_by '{entry.verified_by}' 非法，可选：{sorted(VALID_VERIFIED_BY)}"
        )

    # ---------- R7 l3 自洽性 ----------
    if entry.verified_by == "l3" and not entry.has_params:
        problems.append(
            "verified_by=l3 但 recommended_params 为 — —— `l3` 的定义是"
            "「该条的参数**已过本脚本的机械校验**」，没有参数就无从校验。"
            "这种写法是拿「没有可校验内容」冒充「已机械校验过」，请改为 none 或 doc"
        )

    refs = entry.negative_refs()

    # ---------- R5 引用完整性 ----------
    for ref in refs:
        if not _NEG_ID.match(ref):
            problems.append(f"negative_refs 里的 '{ref}' 不是 neg-xxx 形式")
        elif ref not in negative_ids:
            known_min = min(negative_ids) if negative_ids else "（无）"
            problems.append(
                f"negative_refs 指向不存在的词条 '{ref}'（词库现有 {len(negative_ids)} 条，"
                f"最小 id {known_min}）"
            )

    # ---------- R2 反向词 × 链路 ----------
    if entry.chain == "klein" and refs:
        problems.append(
            "链路=klein 的条目引用了反向词 "
            f"{list(refs)}。CFG=1 的蒸馏链路反向词**无效**"
            "（negative.yaml:5-13），且 flux2_klein_t2i_v1 的 Schema 里**没有 "
            "negative_prompt 字段** —— 不是「填了不生效」，是「无处可填」。"
            "请把解法改成不含反向词的手段（参数/结构/后处理），并把 negative_refs 置为 —"
        )

    # ---------- R4 / R3 参数机械合法性 ----------
    skip_reason = ""
    if not entry.has_params:
        skip_reason = "recommended_params 为 —（该条无可机械校验的关联参数）"
    else:
        try:
            params = entry.params()
        except (json.JSONDecodeError, SchemaError) as exc:
            problems.append(f"recommended_params 不是合法 JSON 对象：{exc}")
        else:
            name = params.get("workflow")
            if not isinstance(name, str) or not name:
                problems.append(
                    "recommended_params 缺少 'workflow' 键 —— 参数必须指明属于哪条工作流，"
                    "否则无法对照真实 Schema 校验（这正是抓「跨工作流张冠李戴」的前提）"
                )
            else:
                wf = registry.get(name)
                if wf is None:
                    problems.append(
                        f"recommended_params.workflow='{name}' 不在注册表里，"
                        f"已知：{[w.id for w in registry]}"
                    )
                else:
                    problems.extend(_check_params_against(wf, params))
                    problems.extend(_check_chain_consistency(entry, wf, refs))

    return problems, skip_reason


def _check_params_against(wf: WorkflowEntry, params: dict[str, Any]) -> list[str]:
    """把 `recommended_params` 里除 `workflow` 外的键逐项对照该工作流的 Schema。"""
    problems: list[str] = []
    schema: ParamSchema = wf.schema
    for key, value in params.items():
        if key == "workflow":
            continue
        fld = schema.get(key)
        if fld is None:
            problems.append(
                f"参数 '{key}' 不存在于工作流 '{wf.id}' 的 Schema "
                f"（该工作流可用参数：{schema.keys}）"
            )
            continue
        try:
            fld.validate(value)
        except RenderError as exc:
            problems.append(f"参数 '{key}' 非法：{exc}")
    return problems


def _check_chain_consistency(
    entry: KBEntry, wf: WorkflowEntry, refs: tuple[str, ...]
) -> list[str]:
    """`链路` 声明与"该工作流到底有没有 negative_prompt"必须自洽。"""
    problems: list[str] = []
    has_neg = workflow_has_negative_field(wf)

    if entry.chain == "sdxl" and not has_neg:
        problems.append(
            f"链路=sdxl 但参数所属工作流 '{wf.id}' 的 Schema **没有** negative_prompt 字段"
            "（它属于 CFG=1 的蒸馏链路）—— 链路标注与工作流不符"
        )
    if entry.chain == "klein" and has_neg:
        problems.append(
            f"链路=klein 但参数所属工作流 '{wf.id}' **有** negative_prompt 字段"
            "（它属于 CFG>1 的链路）—— 链路标注与工作流不符"
        )
    if refs and not has_neg:
        problems.append(
            f"negative_refs={list(refs)} 但参数所属工作流 '{wf.id}' 的 Schema 里"
            "**没有** negative_prompt 字段 —— 这些反向词在该链路上无处可填"
        )
    if entry.chain == "sdxl" and not refs:
        problems.append(
            "链路=sdxl 却没有任何 negative_refs —— SDXL 链路的常见解法就是反向词，"
            "若本条确实不靠反向词，请把链路改为 all"
        )
    return problems


# ============================================================ 整体校验


def check_kb(
    text: str,
    registry: Registry,
    negative_ids: set[str],
    *,
    min_entries: int = MIN_ENTRIES,
) -> Report:
    """校验整份知识库。**纯函数** —— 输入是文本，便于测试构造合成样本。"""
    rep = Report()
    entries, parse_errors = parse_kb(text)
    rep.parse_errors.extend(parse_errors)
    rep.entries = entries

    for entry in entries:
        problems, skip_reason = check_entry(entry, registry, negative_ids)
        rep.added(entry, problems, skipped=skip_reason)

        if entry.has_params:
            try:
                name = entry.params().get("workflow")
            except (json.JSONDecodeError, SchemaError):
                name = None
            if isinstance(name, str) and name and name not in rep.workflows_touched:
                rep.workflows_touched.append(name)

    # ---------- R6 规模 ----------
    if len(entries) < min_entries:
        rep.global_errors.append(
            f"条目数 {len(entries)} < {min_entries}（FR-7.3 / AC-P4 要求 ≥{min_entries} 条）"
        )
    by_cat: dict[str, int] = {}
    for e in entries:
        by_cat[e.category] = by_cat.get(e.category, 0) + 1
    missing = [c for c in REQUIRED_CATEGORIES if not by_cat.get(c)]
    if missing:
        rep.global_errors.append(
            f"以下必备分类没有任何条目：{missing}"
            "（todolist.md:52 要求覆盖 手部/文字/畸变/过曝/材质失真/结构漂移）"
        )
    return rep


def render_report(rep: Report) -> str:
    """人可读输出。**结尾固定打印 DISCLAIMER**。"""
    lines: list[str] = []
    lines.append(
        f"[缺陷库] 解析出条目 {len(rep.entries)} 条"
        f" · 分类 {len({e.category for e in rep.entries})} 个"
        f" · 涉及工作流 {len(rep.workflows_touched)} 条"
        f"{'（' + ', '.join(rep.workflows_touched) + '）' if rep.workflows_touched else ''}"
    )
    lines.append(
        f"[校验] 通过 {len(rep.passed)} · 失败 {len(rep.failures)} · 跳过 {len(rep.skipped)}"
    )
    lines.append(
        "       ⚠ 「跳过」= recommended_params 为 —，**不计入通过**"
        "（不拿「无可校验内容」冒充「校验通过」）"
    )
    if rep.entries:
        v = rep.counts_by_verified(rep.entries)
        c = rep.counts_by_chain(rep.entries)
        lines.append("       verified_by 分布: " + " · ".join(f"{k}={n}" for k, n in v.items()))
        lines.append("       链路分布: " + " · ".join(f"{k}={n}" for k, n in c.items()))
    lines.append("")

    if rep.parse_errors:
        lines.append("— 解析错误 —")
        lines.extend(f"  ✗ {e}" for e in rep.parse_errors)
        lines.append("")
    if rep.global_errors:
        lines.append("— 规模/覆盖问题 —")
        lines.extend(f"  ✗ {e}" for e in rep.global_errors)
        lines.append("")
    if rep.failures:
        lines.append("— 条目失败（具体到条目 id 与参数名）—")
        for o in rep.failures:
            lines.append(f"  ✗ [{o.entry_id}] ({o.category}) {o.detail}")
        lines.append("")
    if rep.skipped:
        lines.append("— 跳过 —")
        for o in rep.skipped:
            lines.append(f"  · [{o.entry_id}] {o.detail}")
        lines.append("")

    lines.append("-" * 72)
    if rep.ok:
        lines.append(
            f"✅ 缺陷库机械校验通过（{len(rep.passed)} 条带参数条目全部合法）"
        )
    else:
        lines.append(
            f"❌ 发现 {len(rep.failures)} 条失败、"
            f"{len(rep.parse_errors)} 个解析错误、{len(rep.global_errors)} 个规模/覆盖问题"
        )
    lines.append(f"⚠️ {DISCLAIMER}")
    lines.append(
        "   —— 参数「有效」只能靠真实出图 + 人工/自动质检（P8-03/P8-04）来证明，"
        "本仓库**至今未对任何一条缺陷条目做过真实出图验证**"
        "（KB 里 verified_by=gpu 的条目数应为 0）。"
    )
    return "\n".join(lines)


# ============================================================ CLI


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m engine.tools.check_defect_kb",
        description="缺陷知识库的机械校验器（P8-06 离线验证）。"
        "只证明参数合法，不证明参数有效。",
    )
    ap.add_argument("--kb", default=None, help=f"知识库路径，默认 {DEFAULT_KB_PATH}")
    ap.add_argument("--registry", default=None, help="注册表路径，默认 workflows/registry.yaml")
    ap.add_argument(
        "--negative",
        default=None,
        help=f"反向词库路径，默认 {DEFAULT_NEGATIVE_PATH}",
    )
    ap.add_argument(
        "--min-entries", type=int, default=MIN_ENTRIES, help=f"最少条目数，默认 {MIN_ENTRIES}"
    )
    args = ap.parse_args(argv)

    kb_path = pathlib.Path(args.kb) if args.kb else DEFAULT_KB_PATH
    try:
        text = kb_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        print(f"[fatal] 知识库不存在: {kb_path}")
        return 2

    try:
        registry = Registry.load(args.registry)
    except SchemaError as exc:
        print(f"[fatal] 注册表加载失败: {exc}")
        return 2

    try:
        negative_ids = collect_negative_ids(args.negative or DEFAULT_NEGATIVE_PATH)
    except SchemaError as exc:
        print(f"[fatal] {exc}")
        return 2

    rep = check_kb(text, registry, negative_ids, min_entries=args.min_entries)
    print(render_report(rep))
    return 0 if rep.ok else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())


__all__ = [
    "COLUMNS",
    "DEFAULT_KB_PATH",
    "DEFAULT_NEGATIVE_PATH",
    "DISCLAIMER",
    "KBEntry",
    "MIN_ENTRIES",
    "Outcome",
    "REQUIRED_CATEGORIES",
    "Report",
    "VALID_CHAINS",
    "VALID_VERIFIED_BY",
    "check_entry",
    "check_kb",
    "collect_negative_ids",
    "main",
    "parse_kb",
    "render_report",
    "workflow_has_negative_field",
]
