"""工作流注册表的读取（任务 **P3-09** 的消费侧）。

`workflows/registry.yaml` 是**唯一的工作流清单**：A 流用它把工作流灌进
`workflows` 表、D 流用它的 `param_schema` 生成动态表单、`engine/` 用它取定义文件。

本模块只做「读 + 校验结构 + 定位定义文件」，**不做写库**（那是 A 流的事）。
"""

from __future__ import annotations

import pathlib
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any

from engine.errors import SchemaError
from engine.schema import ParamSchema

#: 仓库根目录（`engine/registry.py` 的上一级的上一级）
REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

#: 默认注册表位置
DEFAULT_REGISTRY_PATH = REPO_ROOT / "workflows" / "registry.yaml"

#: 工作流启用状态取值
VALID_STATUS: frozenset[str] = frozenset({"enabled", "gray", "disabled"})

#: 验证状态取值 —— 本项目最看重「过程可证明」，所以验证等级必须显式声明
VALID_VERIFICATION: frozenset[str] = frozenset(
    {
        "gpu_verified",  # 已在本机 GPU 上真实出图并核对产物
        "static_only",  # 仅通过离线静态校验（L1~L3），从未真实出图
        "pending_gpu",  # 定义已写、参数已声明，但连静态校验都还没过
    }
)


@dataclass(frozen=True)
class WorkflowEntry:
    """注册表里的一条工作流。"""

    id: str
    version: int
    title: str
    display_name: str
    description: str
    #: 注册表 YAML 里写的**裸文件名**（相对注册表所在目录），仅作原始记录用。
    #: 要取文件请用 `definition_path` —— 自己拼路径是已踩过一次的坑
    #: （调用方被迫知道"注册表在哪"，换个 CWD 或换个注册表就静默找不到文件）。
    definition: str
    status: str
    models: tuple[str, ...]
    schema: ParamSchema
    verification: str
    engine: str | None = None
    #: `definition` 的**已解析绝对路径**，由 `Registry.load()` 算好。
    definition_path: pathlib.Path = field(default_factory=lambda: pathlib.Path())
    rollout_percent: int = 100
    #: **渲染路径** L4 是否已通过（即 `engine/render` 渲染后提交给真实引擎并核对过产物）。
    #:
    #: 与 `verification == "gpu_verified"` 是**两件事**：后者说的是"节点图能出图"
    #: （可能来自裸提交模板），前者说的是"我们的渲染器接进去也能出图"。
    #: 只有后者为真才允许 `status: enabled`。
    render_path_l4: bool = False
    baseline: Mapping[str, Any] = field(default_factory=dict)
    changelog: tuple[Mapping[str, Any], ...] = ()
    notes: str | None = None
    raw: Mapping[str, Any] = field(default_factory=dict, repr=False)

    @property
    def is_enabled(self) -> bool:
        return self.status == "enabled"

    @property
    def is_gpu_verified(self) -> bool:
        return self.verification == "gpu_verified"


@dataclass(frozen=True)
class Registry:
    """整份注册表。"""

    registry_version: int
    updated_at: str
    workflows: tuple[WorkflowEntry, ...]
    planned: tuple[Mapping[str, Any], ...] = ()
    path: pathlib.Path | None = None

    # ---------------------------------------------------------- 加载

    @classmethod
    def load(cls, path: str | pathlib.Path | None = None) -> Registry:
        p = pathlib.Path(path) if path is not None else DEFAULT_REGISTRY_PATH
        raw = _read_yaml(p)

        if not isinstance(raw, dict):
            raise SchemaError(f"注册表顶层必须是对象: {p}")
        version = raw.get("registry_version")
        if not isinstance(version, int):
            raise SchemaError(f"注册表缺少 registry_version: {p}")
        updated_at = str(raw.get("updated_at") or "")
        if not updated_at:
            raise SchemaError(f"注册表缺少 updated_at: {p}")

        entries_raw = raw.get("workflows")
        if not isinstance(entries_raw, list) or not entries_raw:
            raise SchemaError(f"注册表缺少非空 workflows 数组: {p}")

        entries: list[WorkflowEntry] = []
        seen: set[str] = set()
        for i, item in enumerate(entries_raw):
            entry = _parse_entry(item, index=i, registry_path=p)
            if entry.id in seen:
                raise SchemaError(f"注册表里工作流 id '{entry.id}' 重复")
            seen.add(entry.id)
            entries.append(entry)

        planned_raw = raw.get("planned") or []
        if not isinstance(planned_raw, list):
            raise SchemaError("registry.planned 必须是数组")
        for item in planned_raw:
            if not isinstance(item, dict) or not item.get("id"):
                raise SchemaError("registry.planned 的每条必须含 id")

        return cls(
            registry_version=version,
            updated_at=updated_at,
            workflows=tuple(entries),
            planned=tuple(planned_raw),
            path=p,
        )

    # ---------------------------------------------------------- 查询

    def get(self, workflow_id: str) -> WorkflowEntry | None:
        for e in self.workflows:
            if e.id == workflow_id:
                return e
        return None

    def require(self, workflow_id: str) -> WorkflowEntry:
        e = self.get(workflow_id)
        if e is None:
            known = [w.id for w in self.workflows]
            raise SchemaError(f"注册表里没有工作流 '{workflow_id}'，已知: {known}")
        return e

    def enabled(self) -> tuple[WorkflowEntry, ...]:
        """对外可用的工作流。A 流的 `/workflows` 列表应当只返回这些。"""
        return tuple(e for e in self.workflows if e.is_enabled)

    def definition_path(self, entry: WorkflowEntry) -> pathlib.Path:
        """取该工作流定义文件的**绝对路径**。

        优先用 `entry.definition_path`（加载时已解析）；为兼容手工构造的
        `WorkflowEntry`（如测试夹具）才在此兜底重新拼一次。
        """
        if entry.definition_path and entry.definition_path != pathlib.Path():
            return entry.definition_path
        base = self.path.parent if self.path is not None else DEFAULT_REGISTRY_PATH.parent
        return (base / entry.definition).resolve()

    def __iter__(self) -> Iterator[WorkflowEntry]:
        return iter(self.workflows)

    def __len__(self) -> int:
        return len(self.workflows)


# ============================================================ 内部


def _read_yaml(p: pathlib.Path) -> Any:
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover
        raise SchemaError(
            "读取注册表需要 PyYAML。`engine/` 的运行时只依赖标准库，"
            "但注册表是 YAML —— 请在后端/工具链环境里安装 pyyaml"
        ) from exc
    try:
        return yaml.safe_load(p.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SchemaError(f"注册表不存在: {p}") from exc
    except yaml.YAMLError as exc:
        raise SchemaError(f"注册表不是合法 YAML: {p} —— {exc}") from exc


def _parse_entry(item: Any, *, index: int, registry_path: pathlib.Path) -> WorkflowEntry:
    where = f"{registry_path.name} workflows[{index}]"
    if not isinstance(item, dict):
        raise SchemaError(f"{where} 必须是对象")

    def need_str(key: str) -> str:
        v = item.get(key)
        if not isinstance(v, str) or not v:
            raise SchemaError(f"{where} 缺少必填字符串字段 '{key}'")
        return v

    wid = need_str("id")
    version = item.get("version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise SchemaError(f"工作流 '{wid}' 的 version 必须是 ≥1 的整数")

    status = item.get("status")
    if status not in VALID_STATUS:
        raise SchemaError(
            f"工作流 '{wid}' 的 status='{status}' 非法，可选：{sorted(VALID_STATUS)}"
        )

    verification = item.get("verification")
    if verification not in VALID_VERIFICATION:
        raise SchemaError(
            f"工作流 '{wid}' 的 verification='{verification}' 非法，可选：{sorted(VALID_VERIFICATION)}。"
            "该字段用于区分「真出过图」与「只过了静态校验」——"
            "谎报验证状态比不验证更糟（项目最看重过程可证明）"
        )
    if verification == "gpu_verified":
        baseline = item.get("baseline")
        if not isinstance(baseline, dict) or not baseline.get("measured_at"):
            raise SchemaError(
                f"工作流 '{wid}' 标了 gpu_verified，但没有 baseline.measured_at。"
                "性能数字必须带口径与测量日期（项目进展.md #15 的教训）"
            )

    rollout = item.get("rollout_percent", 100)
    if not isinstance(rollout, int) or isinstance(rollout, bool) or not 0 <= rollout <= 100:
        raise SchemaError(f"工作流 '{wid}' 的 rollout_percent 必须是 0-100 的整数")

    render_path_l4 = item.get("render_path_l4", False)
    if not isinstance(render_path_l4, bool):
        raise SchemaError(f"工作流 '{wid}' 的 render_path_l4 必须是布尔值")
    if status == "enabled" and not render_path_l4:
        raise SchemaError(
            f"工作流 '{wid}' 标了 status=enabled，但 render_path_l4=false。"
            "`enabled` 要求的是「**渲染路径**（engine/render → 真实引擎 → 核对产物）」已通过；"
            "「节点图能出图」（如裸提交模板）**不足以**放行给用户。"
            "请先跑渲染路径 L4，或把 status 改回 disabled。"
        )
    if status == "enabled" and verification != "gpu_verified":
        raise SchemaError(
            f"工作流 '{wid}' 标了 enabled，但 verification={verification}（非 gpu_verified）"
        )

    schema_raw = item.get("param_schema")
    if not isinstance(schema_raw, dict):
        raise SchemaError(f"工作流 '{wid}' 缺少 param_schema 对象")
    schema = ParamSchema.load(schema_raw)

    models_raw = item.get("models") or []
    if not isinstance(models_raw, list) or not all(isinstance(m, str) for m in models_raw):
        raise SchemaError(f"工作流 '{wid}' 的 models 必须是字符串数组")

    changelog_raw = item.get("changelog") or []
    if not isinstance(changelog_raw, list):
        raise SchemaError(f"工作流 '{wid}' 的 changelog 必须是数组")
    for i, cl in enumerate(changelog_raw):
        if not isinstance(cl, dict) or not isinstance(cl.get("version"), int):
            raise SchemaError(f"工作流 '{wid}' 的 changelog[{i}] 必须含整数 version")
    logged = {c["version"] for c in changelog_raw if isinstance(c.get("version"), int)}
    if version not in logged:
        raise SchemaError(
            f"工作流 '{wid}' 的当前 version={version} 未出现在 changelog 里。"
            "变更日志是「改坏能退回去」的依据（FR-6.3），必须与 version 同步"
        )

    baseline = item.get("baseline") or {}
    if not isinstance(baseline, dict):
        raise SchemaError(f"工作流 '{wid}' 的 baseline 必须是对象")

    notes = item.get("notes")
    if notes is not None and not isinstance(notes, str):
        raise SchemaError(f"工作流 '{wid}' 的 notes 必须是字符串")

    return WorkflowEntry(
        id=wid,
        version=version,
        title=need_str("title"),
        display_name=need_str("display_name"),
        description=str(item.get("description") or ""),
        definition=need_str("definition"),
        # 加载时就把路径解析好，调用方不必知道注册表在哪
        definition_path=(registry_path.parent / need_str("definition")).resolve(),
        status=status,
        models=tuple(models_raw),
        schema=schema,
        verification=verification,
        engine=item.get("engine"),
        rollout_percent=rollout,
        render_path_l4=render_path_l4,
        baseline=baseline,
        changelog=tuple(changelog_raw),
        notes=notes,
        raw=dict(item),
    )
