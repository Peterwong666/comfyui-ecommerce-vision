"""registry → `workflows` 表装载器（P2-10 配套）。

这个装载器补的是一个**平台级空洞**：`engine/registry.py:6` 明说「不做写库（那是 A 流的事）」，
而 A 流从未实现 —— `GET /api/v1/workflows` 恒返回 `[]`。

测试重点不是"能插入"，而是几条**静默出错**的边界：
- `options[].label` 会不会在往返中丢掉（`engine/schema.py:126-138` 只存 value）
- 库里的 `definition` 能不能真的被渲染器吃下去（否则"灌进去了"毫无意义）
- 同名多版本会不会同时 active（列表与详情指向不同版本且不报错）
"""

from __future__ import annotations

import dataclasses
import pathlib

import pytest
import yaml
from engine import ParamSchema, Registry, RenderError, RenderOptions, load_definition, render
from engine.registry import WorkflowEntry
from sqlalchemy import select

from app.models.workflow import Workflow
from app.services.registry_loader import (
    MAPPED_REGISTRY_KEYS,
    entry_to_values,
    is_active_for,
    load_workflows,
)

REAL_REGISTRY = Registry.load()


@pytest.fixture
def fake_resolver():  # type: ignore[no-untyped-def]
    """`ref_to_filename` 用的假素材解析器（A 流的真实实现要连存储）。"""

    def _resolve(asset_id: object) -> str:
        return f"asset_{asset_id}.png"

    return _resolve


def _write_temp_registry(
    tmp_path: pathlib.Path,
    *,
    base: WorkflowEntry,
    version: int,
    status: str = "enabled",
) -> pathlib.Path:
    """用真实条目做蓝本生成一份临时注册表。

    `definition` 写成**绝对路径**：`registry_path.parent / <绝对路径>` 在 pathlib 里
    会直接采用绝对路径，所以临时目录不必复制定义文件。
    """
    raw = dict(base.raw)
    raw["version"] = version
    raw["status"] = status
    raw["changelog"] = [{"version": version, "note": "test"}]
    raw["definition"] = str(base.definition_path)
    if status == "enabled":
        raw["verification"] = "gpu_verified"
        raw["render_path_l4"] = True
        raw["baseline"] = {"measured_at": "2026-09-16", "note": "test"}

    doc = {"registry_version": 1, "updated_at": "2026-09-16", "workflows": [raw]}
    path = tmp_path / "registry.yaml"
    path.write_text(yaml.safe_dump(doc, allow_unicode=True), encoding="utf-8")
    return path


# ---------------------------------------------------------------- status → is_active


@pytest.mark.parametrize(
    ("status", "expected"),
    [("enabled", True), ("disabled", False), ("gray", False)],
)
def test_is_active_for(status: str, expected: bool) -> None:
    """`gray` 必须是 False：表里没有灰度比例可存，置 True = 对 100% 用户可见。"""
    assert is_active_for(status) is expected


def test_gray_status_is_inactive_in_values() -> None:
    entry = REAL_REGISTRY.require("t2i_v1")
    gray = dataclasses.replace(entry, status="gray")
    assert entry_to_values(gray)["is_active"] is False


# ---------------------------------------------------------------- param_schema 原样落库


def test_param_schema_persisted_verbatim_with_option_labels(db) -> None:  # type: ignore[no-untyped-def]
    """**本文件最重要的一条**：`options[].label` 必须活下来。

    若图省事存 `entry.schema`（解析后的 dataclass），`engine/schema.py:126-138`
    只保留 `value`，全部 54 个采样器/调度器选项的标签会被静默摧毁，
    而契约 §6 明确要求 D 端渲染 label、不许硬编码枚举。
    """
    load_workflows(db, registry_path=REAL_REGISTRY.path)

    for entry in REAL_REGISTRY.workflows:
        row = db.execute(
            select(Workflow).where(Workflow.name == entry.id, Workflow.version == entry.version)
        ).scalar_one()
        # 逐字节相等：不是"包含"，是"一模一样"
        assert row.param_schema == entry.raw["param_schema"], f"{entry.id} 的 param_schema 被改动过"

    # 至少有一条真实存在 label ≠ value 的选项，否则这条测试是空转
    t2i = db.execute(select(Workflow).where(Workflow.name == "t2i_v1")).scalar_one()
    sampler = next(f for f in t2i.param_schema["fields"] if f["key"] == "sampler_name")
    labeled = [o for o in sampler["options"] if o.get("label") and o["label"] != o["value"]]
    assert labeled, "采样器选项里应有 label≠value 的条目（如 dpmpp_2m → DPM++ 2M（推荐））"


# ---------------------------------------------------------------- definition 可用性


def test_definition_keeps_meta_and_is_renderable_from_db(db, fake_resolver) -> None:  # type: ignore[no-untyped-def]
    """闭环：**从库里读出来的** `definition` + `param_schema` 必须能真的渲染。

    只验"插入成功"是不够的 —— 若 `_meta` 被剥掉，`render` 会抛
    `RenderError`（`engine/render.py:138-149`），而 worker 走的正是
    `render(workflow.definition, ...)` 这条路。
    """
    load_workflows(db, registry_path=REAL_REGISTRY.path, include_disabled=True)

    for entry in REAL_REGISTRY.workflows:
        row = db.execute(
            select(Workflow).where(Workflow.name == entry.id, Workflow.version == entry.version)
        ).scalar_one()

        assert "_meta" in row.definition, f"{entry.id} 的 definition 丢了 _meta"

        schema = ParamSchema.load(row.param_schema)
        # 带图工作流的 reference_image 默认值 0 是"必填占位符"，
        # 真提交前必须替换成真实 asset_id —— 这里给一个假的，走通渲染链路即可。
        params = {f.key: 7 for f in schema if f.type == "image"}
        result = render(
            row.definition,
            schema,
            params,
            options=RenderOptions(asset_resolver=fake_resolver),
        )
        assert result.workflow, f"{entry.id} 渲染出的节点图为空"
        assert result.unresolved_placeholders == (), f"{entry.id} 有未解析占位符"
        assert result.seed is not None, f"{entry.id} 的 seed 未被解析成真实整数"


# ---------------------------------------------------------------- 幂等


def test_seeding_is_idempotent(db) -> None:  # type: ignore[no-untyped-def]
    first = load_workflows(db, registry_path=REAL_REGISTRY.path)
    db.commit()
    assert first.created, "第一次应当有新增"

    second = load_workflows(db, registry_path=REAL_REGISTRY.path)
    db.commit()

    assert not second.created, "第二次不应再新增（否则会撞 uq_workflows_name_version）"
    assert not second.updated, "内容未变时不应报告更新"
    assert sorted(second.unchanged) == sorted(first.created)

    count = db.execute(select(Workflow)).scalars().all()
    assert len(count) == len(REAL_REGISTRY.workflows), "行数不应因重复灌库增长"


def test_dry_run_writes_nothing(db) -> None:  # type: ignore[no-untyped-def]
    report = load_workflows(db, registry_path=REAL_REGISTRY.path, dry_run=True)
    db.commit()
    assert report.created
    assert db.execute(select(Workflow)).scalars().all() == []


# ---------------------------------------------------------------- 单活跃版本不变量


def test_superseded_version_is_deactivated(db, tmp_path) -> None:  # type: ignore[no-untyped-def]
    """同名多版本只能有一个 active。

    不修这条的后果很隐蔽：`/workflows` 返回两条同名记录，
    而 `/workflows/{name}/schema` 按 version 倒序静默取高的那条 ——
    列表与详情指向不同版本，且**不报错**。
    """
    base = REAL_REGISTRY.require("t2i_v1")

    v1 = _write_temp_registry(tmp_path, base=base, version=1)
    load_workflows(db, registry_path=v1)
    db.commit()

    # 模拟旧版本此前是 active 的（比如曾手工激活过）
    stale = Workflow(
        name="t2i_v1",
        version=99,
        display_name="旧版本",
        definition=load_definition(base.definition_path),
        param_schema=base.raw["param_schema"],
        is_active=True,
    )
    db.add(stale)
    db.commit()

    v2 = _write_temp_registry(tmp_path, base=base, version=2)
    report = load_workflows(db, registry_path=v2)
    db.commit()

    rows = db.execute(select(Workflow).where(Workflow.name == "t2i_v1")).scalars().all()
    active = [r.version for r in rows if r.is_active]
    assert active == [2], f"应当只剩 v2 active，实际 {active}"
    assert "t2i_v1@v99" in report.deactivated


def test_untouched_workflows_are_left_alone(db, tmp_path) -> None:  # type: ignore[no-untyped-def]
    """注册表里没有的 name 一律不碰 —— 管理员可能手工加过。"""
    db.add(
        Workflow(
            name="handmade_v1",
            version=1,
            display_name="手工加的工作流",
            definition={"_meta": {}, "1": {"class_type": "KSampler", "inputs": {}}},
            param_schema={"schema_version": 1, "fields": []},
            is_active=True,
        )
    )
    db.commit()

    base = REAL_REGISTRY.require("t2i_v1")
    load_workflows(db, registry_path=_write_temp_registry(tmp_path, base=base, version=1))
    db.commit()

    row = db.execute(select(Workflow).where(Workflow.name == "handmade_v1")).scalar_one()
    assert row.is_active is True, "不在注册表里的工作流被误停用了"


# ---------------------------------------------------------------- 状态与丢弃报告


def test_disabled_workflows_become_inactive(db) -> None:  # type: ignore[no-untyped-def]
    load_workflows(db, registry_path=REAL_REGISTRY.path)
    db.commit()

    active = {r.name for r in db.execute(select(Workflow)).scalars().all() if r.is_active}
    # 与注册表真实 enabled() 集合比较，不 hardcode 数量，避免改 verification 状态导致测试崩。
    assert active == {e.id for e in REAL_REGISTRY.enabled()}


def test_include_disabled_activates_them(db) -> None:  # type: ignore[no-untyped-def]
    load_workflows(db, registry_path=REAL_REGISTRY.path, include_disabled=True)
    db.commit()
    active = {r.name for r in db.execute(select(Workflow)).scalars().all() if r.is_active}
    assert active == {e.id for e in REAL_REGISTRY.workflows}


def test_dropped_keys_are_reported(db) -> None:  # type: ignore[no-untyped-def]
    """表里没有列的键必须被报出来，不能静默消失。"""
    report = load_workflows(db, registry_path=REAL_REGISTRY.path, dry_run=True)
    dropped = set(report.dropped["t2i_v1"])
    # 这些是已知无法落库的（表里没有对应列）
    assert {"title", "models", "verification", "render_path_l4", "changelog"} <= dropped
    # 反过来：有列可落的键**不得**出现在丢弃清单里
    assert not (set(MAPPED_REGISTRY_KEYS) & dropped)


def test_meta_mismatch_warns_but_does_not_fail(db, tmp_path) -> None:  # type: ignore[no-untyped-def]
    """`_meta` 与条目不符只告警。

    报错会给部署加一条新硬门禁，会挡住 B 流合法的图改动；
    但静默会让产物元数据指向错误 version，所以必须发声。
    """
    base = REAL_REGISTRY.require("t2i_v1")
    # version=2 的注册表条目，却指向 _meta.version=1 的定义文件
    report = load_workflows(
        db, registry_path=_write_temp_registry(tmp_path, base=base, version=2), dry_run=True
    )
    assert any("_meta" in w for w in report.warnings), report.warnings


# ---------------------------------------------------------------- 失败要响


def test_missing_definition_file_raises(db, tmp_path) -> None:  # type: ignore[no-untyped-def]
    """定义文件缺失必须**报错**，而不是灌进一条必然失败的配置。"""
    base = REAL_REGISTRY.require("t2i_v1")
    raw = dict(base.raw)
    raw["definition"] = str(tmp_path / "does_not_exist.json")
    path = tmp_path / "registry.yaml"
    path.write_text(
        yaml.safe_dump(
            {"registry_version": 1, "updated_at": "2026-09-16", "workflows": [raw]},
            allow_unicode=True,
        ),
        encoding="utf-8",
    )

    with pytest.raises(RenderError, match="不存在"):
        load_workflows(db, registry_path=path)
