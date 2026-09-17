"""P8 两个离线校验器的测试（`check_defect_kb` / `check_golden_set`）。

运行：`PYTHONPATH=. backend/.venv/bin/python -m pytest engine/tests -q`

---

### 这些测试真正要钉住的东西

本项目最看重**「过程可证明」**，而「校验器」本身是最容易出假结论的地方
（`docs/sop/debug_log.md` **G9**：`verify_render_path` 的多 target 一致性检查曾因
用字段 key 当入参名而**假通过**）。G9 立的纪律是：

> **凡「检查 / 判据」本身，都要能被人为破坏后检出。验证工具交付前的最后一步，
> 是故意让它失败一次。**

所以本文件的核心不是「跑通了」，而是**「有鉴别力」**：
每个校验器都有一组**必须报错**的合成输入，构造方式对齐 `debug_log.md` 里
真实踩过的坑（参数越界 / 参数名不存在 / 跨工作流张冠李戴 / enum 拼错 /
seed 越界 / `targets` 注入点漂移）。少了它们，校验器「全绿」可能只说明它在空转。

⚠️ **本文件与 GPU 无关**：所有用例都是离线合成输入。
真实评测（L4）**一次都没跑过** —— 见 `tests/golden_set/README.md` §0。
"""

from __future__ import annotations

import copy
import dataclasses
import json
import pathlib
from typing import Any

import pytest

from engine.registry import REPO_ROOT, Registry
from engine.render import load_definition, split_definition
from engine.schema import ParamSchema
from engine.tools import check_defect_kb as kb
from engine.tools import check_golden_set as gs

#: 仓库侧真实产物
KB_PATH = REPO_ROOT / "docs" / "sop" / "defect_kb.md"
CASES_DIR = REPO_ROOT / "tests" / "golden_set" / "cases"
OBJECT_INFO_PATH = REPO_ROOT / "deploy" / "schemas" / "object_info.v0.36.0.json"


# ============================================================ 夹具


@pytest.fixture(scope="module")
def registry() -> Registry:
    return Registry.load()


@pytest.fixture(scope="module")
def negative_ids() -> set[str]:
    return kb.collect_negative_ids()


@pytest.fixture(scope="module")
def object_info() -> dict[str, Any]:
    return json.loads(OBJECT_INFO_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def entry_t2i(registry: Registry):
    e = registry.get("t2i_v1")
    assert e is not None, "注册表里没有 t2i_v1 —— 夹具前提不成立"
    return e


# ============================================================ KB：合成样本工具

_KB_HEADER = """# 合成缺陷库（测试用）

## 1. hand · 手部

"""


def _kb_row(
    entry_id: str = "D-01",
    *,
    symptom: str = "现象",
    cause: str = "成因",
    solution: str = "解法",
    chain: str = "sdxl",
    params: str = '{"workflow":"t2i_v1","cfg":6.0}',
    refs: str = "neg-001",
    verified: str = "l3",
    evidence: str = "依据",
) -> str:
    return f"| {entry_id} | {symptom} | {cause} | {solution} | {chain} | {params} | {refs} | {verified} | {evidence} |"


def _all_categories(rows_per_cat: int) -> list[str]:
    """六个必备分类，每类 N 行 —— 用来绕过 R6 的规模检查。"""
    out = ["## 1. hand · 手部"]
    n = 0
    for ci, cat in enumerate(kb.REQUIRED_CATEGORIES, start=1):
        out.append(f"## {ci}. {cat} · 分类 {ci}")
        for _ in range(rows_per_cat):
            n += 1
            out.append(_kb_row(f"D-{n:02d}"))
    return out


def _check_kb_text(text: str, registry: Registry, negative_ids: set[str]):
    return kb.check_kb(text, registry, negative_ids)


def _only_failure(rep: kb.Report) -> str:
    """取唯一一条失败条目的说明（便于断言「具体到条目 id 与参数名」）。"""
    assert len(rep.failures) == 1, f"期望恰好 1 条失败，实际 {len(rep.failures)}: {rep.failures}"
    return rep.failures[0].detail


# ============================================================ KB：鉴别力用例


def test_kb_rejects_out_of_range_param(registry, negative_ids) -> None:
    """① 参数越界：`cfg: 25` 超出 t2i_v1 的 max=20.0。

    对应用户可见错误「参数不合法：步数需在 1-100」（PRD EX-3）。
    """
    rep = _check_kb_text(_KB_HEADER + _kb_row(), registry, negative_ids)
    # 注意：这里只能断言「条目无失败」。只有 1 行、只覆盖 1 类的合成库
    # **本来就应该**触发规模检查（global_errors），所以不能断言 rep.ok。
    assert not rep.failures, f"基准样本本身不该有失败: {rep.failures}"
    assert rep.global_errors, "1 行 1 类的库本就该报规模不足（这是判据在起作用）"

    bad = _kb_row(params='{"workflow":"t2i_v1","cfg":25}')
    rep = _check_kb_text(_KB_HEADER + bad, registry, negative_ids)
    assert rep.failures, "cfg=25 越界必须被报出"
    detail = _only_failure(rep)
    assert "D-01" in detail or rep.failures[0].entry_id == "D-01"
    assert "cfg" in detail, f"失败说明必须具体到参数名，实际: {detail}"
    assert "上限" in detail or "20" in detail, f"应指出越界，实际: {detail}"


def test_kb_rejects_unknown_param_name(registry, negative_ids) -> None:
    """② 参数名不存在：`cfg_scale` 实际叫 `cfg`（跨文档抄错的典型）。"""
    bad = _kb_row(params='{"workflow":"t2i_v1","cfg_scale":6.0}')
    rep = _check_kb_text(_KB_HEADER + bad, registry, negative_ids)
    assert rep.failures, "不存在的参数名必须被报出"
    detail = _only_failure(rep)
    assert "cfg_scale" in detail, f"失败说明必须点名参数名，实际: {detail}"
    assert "不存在" in detail


def test_kb_rejects_cross_workflow_param(registry, negative_ids) -> None:
    """③ 跨工作流张冠李戴：`mask_expand` 属 inpaint_v1，却写在 t2i_v1 的条目里。

    这是本项目最容易犯的错 —— 参数字段是手工从注册表抄的。
    """
    bad = _kb_row(params='{"workflow":"t2i_v1","mask_expand":8}')
    rep = _check_kb_text(_KB_HEADER + bad, registry, negative_ids)
    assert rep.failures, "跨工作流参数必须被报出"
    detail = _only_failure(rep)
    assert "mask_expand" in detail
    assert "t2i_v1" in detail, f"应指出是哪个工作流的 Schema，实际: {detail}"


def test_kb_rejects_misspelled_enum(registry, negative_ids) -> None:
    """④ enum 拼错：`euler_a`（正确写法是 `euler` / `euler_ancestral`）。"""
    bad = _kb_row(params='{"workflow":"t2i_v1","sampler_name":"euler_a"}')
    rep = _check_kb_text(_KB_HEADER + bad, registry, negative_ids)
    assert rep.failures, "拼错的 enum 取值必须被报出"
    detail = _only_failure(rep)
    assert "sampler_name" in detail
    assert "允许集合" in detail or "不在" in detail


def test_kb_rejects_out_of_range_seed(registry, negative_ids) -> None:
    """⑤ seed 越界：`-2` 既不是随机（-1）也不在 [0, 2**32)。"""
    bad = _kb_row(params='{"workflow":"t2i_v1","seed":-2}')
    rep = _check_kb_text(_KB_HEADER + bad, registry, negative_ids)
    assert rep.failures, "seed=-2 必须被报出"
    assert "seed" in _only_failure(rep)


def test_kb_rejects_negative_words_on_klein_chain(registry, negative_ids) -> None:
    """⑥ **假知识**：klein（CFG=1 蒸馏）链路引用反向词。

    反向词只在 CFG>1 时有效，且 `flux2_klein_t2i_v1` 的 Schema **没有**
    `negative_prompt` 字段（`negative.yaml:5-13`）—— 不是「填了不生效」，是「无处可填」。
    """
    bad = _kb_row(chain="klein", params='{"workflow":"flux2_klein_t2i_v1","steps":4}', refs="neg-001")
    rep = _check_kb_text(_KB_HEADER + bad, registry, negative_ids)
    assert rep.failures, "klein 链路引用反向词必须被报出（这是假知识）"
    assert "klein" in _only_failure(rep)


def test_kb_rejects_sdxl_chain_without_refs(registry, negative_ids) -> None:
    """⑦ 反向对照：`sdxl` 链路却没有任何反向词 —— 链路标注与内容不符。"""
    bad = _kb_row(chain="sdxl", params='{"workflow":"t2i_v1","cfg":6.0}', refs="—")
    rep = _check_kb_text(_KB_HEADER + bad, registry, negative_ids)
    assert rep.failures, "sdxl 链路无反向词必须被报出"
    assert "negative_refs" in _only_failure(rep)


def test_kb_rejects_l3_claim_without_params(registry, negative_ids) -> None:
    """⑧ R7：`verified_by=l3` 但 `recommended_params` 为 `—`。

    `l3` 的定义是「参数已过机械校验」。没有参数就无从校验 ——
    这是拿「没有可校验内容」冒充「已机械校验过」。
    """
    bad = _kb_row(params="—", refs="neg-001", verified="l3")
    rep = _check_kb_text(_KB_HEADER + bad, registry, negative_ids)
    assert rep.failures, "l3 却没有参数必须被报出"
    assert "l3" in _only_failure(rep)


def test_kb_rejects_dangling_negative_ref(registry, negative_ids) -> None:
    """⑨ 引用完整性：指向不存在的 `neg-999`。"""
    bad = _kb_row(refs="neg-999")
    rep = _check_kb_text(_KB_HEADER + bad, registry, negative_ids)
    assert rep.failures, "悬空的反向词引用必须被报出"
    assert "neg-999" in _only_failure(rep)


def test_kb_rejects_wrong_column_count(registry, negative_ids) -> None:
    """⑩ 表格列数不对（单元格里混进 `|` 是最常见成因）。"""
    broken = "| D-01 | 现象 | 成因 | 解法 | sdxl | — | — | none | 依据 | 多出来的列 |"
    rep = _check_kb_text(_KB_HEADER + broken, registry, negative_ids)
    assert rep.parse_errors, "列数不符必须被报出"
    assert any("列数" in e for e in rep.parse_errors)


def test_kb_rejects_unknown_category(registry, negative_ids) -> None:
    """⑪ 未知分类名 —— 会被静默漏掉整类覆盖。"""
    rep = _check_kb_text(_KB_HEADER + "## 1. handd · 打错的分类", registry, negative_ids)
    assert rep.parse_errors
    assert any("未知分类" in e for e in rep.parse_errors)


def test_kb_reports_scale_shortfall(registry, negative_ids) -> None:
    """⑫ 规模不足与分类缺失必须被报出（FR-7.3 / AC-P4 要求 ≥30 条、6 类全覆盖）。"""
    rep = _check_kb_text(_KB_HEADER + _kb_row(), registry, negative_ids)
    assert rep.global_errors, "只有 1 条、只有 1 类时必须报规模问题"
    joined = "；".join(rep.global_errors)
    assert "30" in joined
    assert "分类" in joined


def test_kb_skip_is_not_counted_as_pass(registry, negative_ids) -> None:
    """⑬ `recommended_params` 为 `—` 的条目**不计入通过**（只算跳过）。

    不拿「没有可校验内容」冒充「校验通过」。
    """
    text = _KB_HEADER + _kb_row(params="—", refs="neg-001", verified="doc")
    rep = _check_kb_text(text, registry, negative_ids)
    assert rep.passed == []
    assert len(rep.skipped) == 1


def test_kb_all_six_categories_pass(registry, negative_ids) -> None:
    """⑭ 六个分类各 5 行（共 30 行）的合成库应当整体通过 —— 证明分类覆盖判据本身可用。"""
    rows = _all_categories(rows_per_cat=5)
    rep = _check_kb_text(_KB_HEADER + "\n".join(rows), registry, negative_ids)
    assert not rep.failures, f"合成库不该有失败: {rep.failures}"
    assert not rep.global_errors, f"合成库不该有规模问题: {rep.global_errors}"
    assert rep.ok


# ============================================================ KB：真实产物


def test_real_kb_exists_and_passes(registry, negative_ids) -> None:
    """⑮ **真实的** `docs/sop/defect_kb.md` 必须存在、≥30 条、六类齐、且参数全合法。"""
    assert KB_PATH.is_file(), f"缺陷库缺失: {KB_PATH}"
    rep = kb.check_kb(KB_PATH.read_text(encoding="utf-8"), registry, negative_ids)
    assert not rep.parse_errors, rep.parse_errors
    assert not rep.global_errors, rep.global_errors
    assert not rep.failures, rep.failures
    assert len(rep.entries) >= kb.MIN_ENTRIES

    by_cat: dict[str, int] = {}
    for e in rep.entries:
        by_cat[e.category] = by_cat.get(e.category, 0) + 1
    assert all(by_cat.get(c) for c in kb.REQUIRED_CATEGORIES), by_cat


def test_real_kb_claims_no_gpu_verification(registry, negative_ids) -> None:
    """⑯ 🔴 **诚实性不变量**：真实缺陷库里 `verified_by=gpu` 必须为 0 条。

    本项目**一次真实出图验证都没对缺陷条目做过**。任何 `gpu` 标注都是谎报
    （`registry.VALID_VERIFICATION` 的注释：「谎报验证状态比不验证更糟」）。
    """
    rep = kb.check_kb(KB_PATH.read_text(encoding="utf-8"), registry, negative_ids)
    counts = rep.counts_by_verified(rep.entries)
    assert counts["gpu"] == 0, f"缺陷库出现了 gpu 标注，但从未跑过真实验证: {counts}"


def test_kb_disclaimer_is_printed() -> None:
    """⑰ 输出结尾必须原样打印「只证明参数合法，不证明参数有效」。"""
    rep = kb.Report()
    text = kb.render_report(rep)
    assert kb.DISCLAIMER in text
    assert text.rstrip().count(kb.DISCLAIMER) >= 1


# ============================================================ golden set：合成用例


_VALID_CASE: dict[str, Any] = {
    "id": "gs-test-001",
    "workflow": "t2i_v1",
    "dimensions": {
        "category": "陶瓷咖啡杯",
        "material": "陶瓷釉面",
        "light": "三点布光",
        "composition": "主体70%居中",
    },
    "params": {"prompt": "a mug", "width": 1024, "height": 1024, "seed": 20260916},
    "expected": None,
    "expected_status": "pending_gpu",
    "notes": "合成用例",
}


def _write_case(tmp_path: pathlib.Path, case: dict[str, Any]) -> pathlib.Path:
    d = tmp_path / "cases"
    d.mkdir(exist_ok=True)
    p = d / f"{case.get('id', 'anon')}.json"
    p.write_text(json.dumps(case, ensure_ascii=False), encoding="utf-8")
    return d


def _run_cases(tmp_path, registry, object_info, case: dict[str, Any]) -> gs.Report:
    d = _write_case(tmp_path, case)
    return gs.check_golden_set(d, registry, object_info)


def _with(**overrides: Any) -> dict[str, Any]:
    c = copy.deepcopy(_VALID_CASE)
    for k, v in overrides.items():
        if k == "params":
            c["params"] = {**c["params"], **v}
        elif k == "dimensions":
            c["dimensions"] = {**c["dimensions"], **v}
        else:
            c[k] = v
    return c


def test_golden_set_accepts_valid_case(tmp_path, registry, object_info) -> None:
    """基准：一条合法用例在四个级别上全绿（否则下面的「必须报错」没有意义）。"""
    rep = _run_cases(tmp_path, registry, object_info, copy.deepcopy(_VALID_CASE))
    assert rep.ok, (rep.parse_errors, rep.global_errors, rep.failures)
    assert len(rep.passed) == 1
    assert set(rep.levels_run) == {"Schema", "render", "L1", "L3"}


def test_golden_set_rejects_misspelled_enum(tmp_path, registry, object_info) -> None:
    """① enum 拼错：`sampler_name: euler_a_typo`。"""
    case = _with(params={"sampler_name": "euler_a_typo"})
    rep = _run_cases(tmp_path, registry, object_info, case)
    assert rep.failures, "拼错的 enum 必须被报出"
    assert "sampler_name" in rep.failures[0].detail


def test_golden_set_rejects_out_of_range_seed(tmp_path, registry, object_info) -> None:
    """② seed 越界：`-2`（-1 才是随机）。"""
    case = _with(params={"seed": -2})
    rep = _run_cases(tmp_path, registry, object_info, case)
    assert rep.failures, "seed=-2 必须被报出"
    assert "seed" in rep.failures[0].detail


def test_golden_set_rejects_cross_workflow_param(tmp_path, registry, object_info) -> None:
    """③ 参数名跨工作流复制错：`mask_expand` 写在 `t2i_v1` 的用例里。

    用例只经「Schema 校验」这一级就该被拦住（不必走到渲染）。
    """
    case = _with(params={"mask_expand": 8})
    rep = _run_cases(tmp_path, registry, object_info, case)
    assert rep.failures, "跨工作流参数必须被报出"
    detail = rep.failures[0].detail
    assert "mask_expand" in detail
    # 参数层未通过时，后面的级别不许声称跑过
    assert set(rep.failures[0].levels_run) == {"Schema"}, rep.failures[0].levels_run


def test_golden_set_rejects_out_of_range_cfg_for_klein(tmp_path, registry, object_info) -> None:
    """④ klein 的 `cfg` 上限是 2.0（蒸馏版），给 7.0 必须报错。

    这正是「按 Base 版习惯调参」那个真实坑（`model_guide.md` §5.1 / K1-3）。
    """
    case = _with(
        workflow="flux2_klein_t2i_v1",
        params={"cfg": 7.0, "steps": 4},
    )
    case["params"] = {"prompt": "a mug", "width": 1024, "height": 1024,
                      "seed": 20260916, "cfg": 7.0, "steps": 4}
    rep = _run_cases(tmp_path, registry, object_info, case)
    assert rep.failures, "klein 上 cfg=7.0 必须被报出"
    assert "cfg" in rep.failures[0].detail


def test_golden_set_rejects_non_null_expected(tmp_path, registry, object_info) -> None:
    """⑤ 🔴 **不许编造评测结果**：`expected` 只要不是 `null` 就必须报错。"""
    case = _with(expected="reference/gs-test-001.png")
    rep = _run_cases(tmp_path, registry, object_info, case)
    assert rep.parse_errors, "expected 非 null 必须被报出"
    assert any("expected" in e for e in rep.parse_errors), rep.parse_errors


def test_golden_set_rejects_wrong_expected_status(tmp_path, registry, object_info) -> None:
    """⑥ 未跑过评测时 `expected_status` 只能是 `pending_gpu`。"""
    case = _with(expected_status="passed")
    rep = _run_cases(tmp_path, registry, object_info, case)
    assert rep.parse_errors
    assert any("pending_gpu" in e for e in rep.parse_errors), rep.parse_errors


def test_golden_set_rejects_missing_dimension(tmp_path, registry, object_info) -> None:
    """⑦ 四维矩阵缺一维（`todolist.md:408` 要求品类 × 材质 × 光影 × 构图）。"""
    case = copy.deepcopy(_VALID_CASE)
    case["dimensions"].pop("light")
    rep = _run_cases(tmp_path, registry, object_info, case)
    assert rep.parse_errors
    assert any("light" in e for e in rep.parse_errors), rep.parse_errors


def test_golden_set_rejects_unknown_workflow(tmp_path, registry, object_info) -> None:
    """⑧ 工作流 id 不存在（例如把计划中的 `style_transfer_v1` 当已实现）。"""
    case = _with(workflow="style_transfer_v1")
    rep = _run_cases(tmp_path, registry, object_info, case)
    assert rep.failures, "不存在的工作流必须被报出"
    assert "style_transfer_v1" in rep.failures[0].detail


def test_golden_set_empty_dir_is_an_error(tmp_path, registry, object_info) -> None:
    """⑨ 空用例目录是「没做」，不是「全过」。"""
    d = tmp_path / "empty"
    d.mkdir()
    rep = gs.check_golden_set(d, registry, object_info)
    assert rep.parse_errors
    assert any("没有任何" in e for e in rep.parse_errors), rep.parse_errors


def test_golden_set_skip_is_not_pass(tmp_path, registry, object_info) -> None:
    """⑩ 🔴 缺 `object_info` → L3 未执行 → **跳过不计入通过**，而且 `ok` 必须为 False。

    「少做了一层」不许装成「检查通过」。
    """
    d = _write_case(tmp_path, copy.deepcopy(_VALID_CASE))
    rep = gs.check_golden_set(d, registry, None)
    assert rep.passed == []
    assert len(rep.skipped) == 1
    assert rep.ok is False, "L3 未执行时不允许报告成功"
    assert "L3" not in rep.levels_run, f"没跑的级别不许记进 levels_run: {rep.levels_run}"


# ============================================================ golden set：注入点漂移


def _registry_with_broken_target(registry: Registry, workflow: str, field_key: str, node_id: str):
    """造一个「`targets` 指向不存在节点」的注册表副本。

    这是 `targets` 注入最典型的漂移（`workflow_spec.md` §3.1：节点 ID 是锚点，
    改 ID 必须同步改 Schema）。
    """
    entry = registry.require(workflow)
    raw = copy.deepcopy(entry.raw["param_schema"])
    hit = False
    for f in raw["fields"]:
        if f["key"] == field_key:
            for t in f["targets"]:
                t["node_id"] = node_id
                hit = True
    assert hit, f"字段 {field_key} 没有 targets，夹具前提不成立"
    patched = dataclasses.replace(entry, schema=ParamSchema.load(raw))
    return Registry(
        registry_version=registry.registry_version,
        updated_at=registry.updated_at,
        workflows=(patched,),
        planned=(),
        path=registry.path,
    )


def test_golden_set_rejects_target_node_drift(tmp_path, registry, object_info) -> None:
    """⑪ ⭐ **`targets` 注入点漂移**：`cfg` 的 target 指向不存在的节点 `999`。

    `engine/render._inject_targets` 会直接抛 `RenderError`（这是刻意设计：
    静默跳过注入会让「用户改了参数却没生效」无从排查）。
    """
    broken = _registry_with_broken_target(registry, "t2i_v1", "cfg", "999")
    rep = _run_cases(tmp_path, broken, object_info, copy.deepcopy(_VALID_CASE))
    assert rep.failures, "target 指向不存在的节点必须被报出"
    detail = rep.failures[0].detail
    assert "999" in detail, detail


def test_golden_set_rejects_injected_input_rejected_by_node(
    tmp_path, registry, object_info
) -> None:
    """⑫ ⭐ **L3 的核心价值**：注入的入参名该节点不接受。

    造法：拿真实的 `object_info` 快照，**定向篡改**某个被注入的节点类，
    让它不再接受被注入的入参名。这模拟的是「渲染图我们自己看着没问题，
    但引擎不认」—— 只有 L3 抓得到（`debug_log.md` §0.2）。
    """
    entry = registry.require("t2i_v1")
    field = entry.schema.get("cfg")
    assert field is not None and field.targets, "夹具前提：cfg 必须有 targets"
    target = field.targets[0]

    definition = load_definition(registry.definition_path(entry))
    _, graph = split_definition(definition)
    class_type = graph[target.node_id]["class_type"]

    # 定向篡改：把这个入参从该节点类的 required/optional 里删掉
    spec = object_info[class_type]["input"]
    tampered_input = {
        "required": {k: v for k, v in (spec.get("required") or {}).items()
                     if k != target.input},
        "optional": {k: v for k, v in (spec.get("optional") or {}).items()
                     if k != target.input},
    }
    tampered = {
        **object_info,
        class_type: {**object_info[class_type], "input": tampered_input},
    }

    case = copy.deepcopy(_VALID_CASE)
    case["params"] = {**case["params"], "cfg": 6.5}

    # 先确认未篡改时是好的（正对照：否则"报错"可能来自别的原因）
    ok_rep = _run_cases(tmp_path, registry, object_info, copy.deepcopy(case))
    assert not ok_rep.failures, f"未篡改时不该失败: {ok_rep.failures}"

    rep = _run_cases(tmp_path, registry, tampered, copy.deepcopy(case))
    assert rep.failures, "L3 必须抓出「入参不被该节点接受」"
    detail = rep.failures[0].detail
    assert "L3" in detail, f"失败应来自 L3，实际: {detail}"
    assert target.input in detail, detail
    assert set(rep.failures[0].levels_run) == {"Schema", "render", "L1", "L3"}


# ============================================================ golden set：真实产物


def test_real_golden_set_passes_all_levels(registry, object_info) -> None:
    """⑬ **真实的** `tests/golden_set/cases/` 必须全部通过 Schema/渲染/L1/L3。"""
    rep = gs.check_golden_set(CASES_DIR, registry, object_info)
    assert not rep.parse_errors, rep.parse_errors
    assert not rep.global_errors, rep.global_errors
    assert not rep.failures, rep.failures
    assert not rep.skipped, f"不该有跳过（object_info 已提供）: {rep.skipped}"
    assert len(rep.passed) >= 30, f"用例数不足: {len(rep.passed)}"


def test_real_golden_set_has_no_fabricated_expectations(registry, object_info) -> None:
    """⑭ 🔴 **诚实性不变量**：所有真实用例的 `expected` 必须是 `null`、
    `expected_status` 必须是 `pending_gpu`。

    本仓库**一次真实评测都没跑过** —— 任何非 null 的 `expected` 都是编造。
    """
    cases, errors = gs.load_cases(CASES_DIR)
    assert not errors, errors
    assert cases, "用例为空"
    for c in cases:
        assert c.raw["expected"] is None, f"{c.id} 的 expected 被填成了真值"
        assert c.raw["expected_status"] == gs.PENDING_STATUS, f"{c.id} 的 status 不是 pending_gpu"


def test_real_golden_set_covers_four_dimensions(registry, object_info) -> None:
    """⑮ 四维矩阵的每一维都必须有多个取值（否则矩阵名不副实）。"""
    cases, errors = gs.load_cases(CASES_DIR)
    assert not errors
    for key in gs.DIMENSION_KEYS:
        values = {c.dimensions[key] for c in cases}
        assert len(values) >= 3, f"维度 '{key}' 只有 {len(values)} 个取值，矩阵覆盖不足"


def test_golden_set_disclaimer_is_printed() -> None:
    """⑯ 输出结尾必须原样打印「不证明能出图」。"""
    rep = gs.Report()
    text = gs.render_report(rep, object_info_used=True)
    assert gs.DISCLAIMER in text
    assert "L4" in text
