"""`workflows/registry.yaml` → `workflows` 表（P2-10 配套）。

为什么这个模块必须存在
----------------------
`engine/registry.py:6` 写明「**不做写库**（那是 A 流的事）」，而 A 流从未实现 ——
后果是 `GET /api/v1/workflows` 恒返回 `[]`：动态表单没有真实数据可渲染，
P2 的 DoD「前后端互相调通一次」也无法成立。本模块补上这一环。

为什么放 `backend/` 而不是 `engine/`
----------------------------------
`engine/` 是**零第三方依赖**的独立包，且明令「不碰 `backend/`」（`engine/__init__.py:36`）。
把 SQLAlchemy 拖进内核会破坏那条边界。所以本模块属于 backend，**消费** `engine.Registry`。

⚠️ 两条不能违反的约束
--------------------
1. **`param_schema` 必须原样落库**，不得经 `ParamSchema` 再序列化。
   `engine/schema.py:126-138` 只保留 `options[].value`，**丢掉 `options[].label`**；
   一旦走 dataclass 往返，`sampler_name`(45 项) 与 `scheduler`(9 项) 的标签会被静默摧毁，
   而契约 §6 明确要求 D 端渲染 label、不得硬编码枚举选项。
   → 一律写 `entry.raw["param_schema"]`。
2. **本模块不做二次校验**。结构合法性由 `Registry.load()` → `ParamSchema.load()` 负责，
   注册表已经是一条经过校验的输入，再校验一遍就是两套规则（#22 的事故类）。

⚠️ 唯一的有损投影：`status` → `is_active`
----------------------------------------
`Workflow` **没有** `status` / `rollout_percent` / `verification` / `render_path_l4`
/ `title` / `models` 列。这些键无法落库，但**不能静默丢掉** ——
`SeedReport.dropped` 会逐条把它们报出来，CLI 也会打印。

TODO(P6/FR-6.3)：等「版本灰度/回滚」真正落地时，加一个 `registry_meta: JSONB` 列 +
第二个 alembic revision，并同步更新 `backend/tests/test_migration.py` 里的
「只允许一个迁移文件」断言。
"""

from __future__ import annotations

import pathlib
from dataclasses import dataclass, field
from typing import Any

from engine import Registry, WorkflowEntry, load_definition
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.workflow import Workflow

#: 注册表里**有**对应列的键。其余一律进 `SeedReport.dropped`。
#:
#: 用「映射集 + 差集」而不是硬编码「丢弃清单」，是为了**自动发现**新键：
#: B 流往 registry 里加字段时，报告会自动把它标成「未落库」，
#: 而不是等前端拉到空值才发现。
MAPPED_REGISTRY_KEYS: frozenset[str] = frozenset(
    {
        "id",  # → Workflow.name
        "version",  # → Workflow.version
        "display_name",  # → Workflow.display_name
        "description",  # → Workflow.description
        "definition",  # → Workflow.definition（**文件内容**，不是文件名）
        "param_schema",  # → Workflow.param_schema（原样）
        "status",  # → Workflow.is_active（有损，见 is_active_for）
    }
)


@dataclass
class SeedReport:
    """一次灌库的结果。**用于让人看见「什么没落库」**，不只是成功计数。"""

    created: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    deactivated: list[str] = field(default_factory=list)
    #: 工作流 → 该条在注册表里写过、但**表里没有列**可容纳的键
    dropped: dict[str, list[str]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.created) + len(self.updated) + len(self.unchanged)

    def as_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "created": self.created,
            "updated": self.updated,
            "unchanged": self.unchanged,
            "deactivated": self.deactivated,
            "dropped": self.dropped,
            "warnings": self.warnings,
        }


def is_active_for(status: str) -> bool:
    """`status` → `is_active`。**本模块唯一的有损投影。**

    `enabled` → True；`disabled` → False；
    `gray` → **False**（并告警）：库里没有灰度比例列，置 True 等于把未发布的工作流
    暴露给 100% 的用户。安全侧优先，宁可不可见。
    """
    return status == "enabled"


def entry_to_values(entry: WorkflowEntry, *, include_disabled: bool = False) -> dict[str, Any]:
    """把一条注册表条目映射成 `Workflow` 的列值。**纯函数，不碰 DB**（可直接单测）。

    `include_disabled` 供本地前端联调使用（见 CLI 的 SQLite 限制）：
    三个带 `type: image` 的工作流都是 `disabled`，不放它们进来，
    图片类表单控件就永远无法在浏览器里渲染出来。
    """
    # 文件不存在会抛 RenderError（engine.render.load_definition）—— 故意**不兜底**：
    # registry.yaml 已写明「写一个指向不存在文件的条目，必然让灌库脚本与前端拿到
    # 一个必然失败的配置」，这类错误必须在灌库时暴露，而不是留到运行时。
    definition = load_definition(entry.definition_path)

    return {
        "name": entry.id,
        "version": entry.version,
        "display_name": entry.display_name,
        "description": entry.description,
        "definition": definition,  # 含 `_meta` 整段：render 没有它会抛错
        "param_schema": entry.raw["param_schema"],  # ⚠️ 原样，勿改
        "is_active": True if include_disabled else is_active_for(entry.status),
    }


def _apply_update(row: Workflow, values: dict[str, Any]) -> bool:
    """写既有行，返回是否真的发生了变化。

    ⚠️ `eval_score` / `good_rate` **不在 `values` 里**（注册表没有对应字段），
    因此这里天然不会用 `None` 覆盖它们 —— 那两列归 P5-02/P7-09 的评测任务所有。
    """
    changed = False
    for column, new in values.items():
        if getattr(row, column) != new:
            setattr(row, column, new)
            changed = True
    return changed


def load_workflows(
    session: Session,
    *,
    registry_path: str | pathlib.Path | None = None,
    include_disabled: bool = False,
    dry_run: bool = False,
) -> SeedReport:
    """把注册表灌进 `workflows` 表。**幂等**：自然键 `(name, version)`。

    不用 PG 专有的 `ON CONFLICT`，保持方言可移植；唯一约束是最后一道防线。
    """
    registry = Registry.load(registry_path)
    report = SeedReport()

    for entry in registry.workflows:
        label = f"{entry.id}@v{entry.version}"
        values = entry_to_values(entry, include_disabled=include_disabled)

        _note_warnings(entry, values, report)

        existing = session.execute(
            select(Workflow).where(Workflow.name == entry.id, Workflow.version == entry.version)
        ).scalar_one_or_none()

        if existing is None:
            if not dry_run:
                session.add(Workflow(**values))
            report.created.append(label)
        elif _apply_update(existing, values):
            report.updated.append(label)
        else:
            report.unchanged.append(label)

    if not dry_run:
        # 新加的行要先 flush 才能被下面的查询看到
        session.flush()
        _deactivate_superseded(session, registry, report)

    return report


def _note_warnings(
    entry: WorkflowEntry, values: dict[str, Any], report: SeedReport
) -> None:
    """收集「不阻断、但必须让人看见」的信息。"""
    # 1) 未落库的键 —— 不让数据静默消失（本项目红线）
    dropped = sorted(set(entry.raw) - MAPPED_REGISTRY_KEYS)
    if dropped:
        report.dropped[entry.id] = dropped

    # 2) _meta 与注册表条目不一致：**告警而非报错**。
    #    报错等于给部署新增一条硬门禁，会挡住 B 流合法的图改动；
    #    但静默又会让产物元数据指向错误的 version，所以必须发声。
    meta = values["definition"].get("_meta") or {}
    if meta.get("id") != entry.id or meta.get("version") != entry.version:
        report.warnings.append(
            f"{entry.id}@v{entry.version}: definition._meta 与注册表条目不一致 "
            f"(id={meta.get('id')!r}, version={meta.get('version')!r})"
        )

    # 3) gray 无法表达灰度
    if entry.status == "gray":
        report.warnings.append(
            f"{entry.id}@v{entry.version}: status=gray，但表里没有灰度比例列 → "
            "按 is_active=False 处理（置 True 会让未发布工作流对 100% 用户可见）"
        )


def _deactivate_superseded(session: Session, registry: Registry, report: SeedReport) -> None:
    """**单活跃版本不变量**：同名工作流只允许注册表里那一个版本为 active。

    不做这一步的后果很隐蔽：registry 把 `t2i_v1` 升到 v2 之后，
    `GET /workflows` 会**同时返回 v1 与 v2**（两条都 active），
    而 `GET /workflows/{name}/schema` 会静默取 version 最大的那条 ——
    列表与详情指向不同版本，且不报错。

    ⚠️ 只处理**注册表里出现过的 name**。名字不在注册表里的工作流一律不碰：
    管理员可能通过接口手工加过。
    """
    names = {e.id for e in registry.workflows}
    if not names:
        return

    known_pairs = {(e.id, e.version) for e in registry.workflows}
    rows = session.execute(select(Workflow).where(Workflow.name.in_(names))).scalars().all()

    for row in rows:
        if (row.name, row.version) not in known_pairs and row.is_active:
            row.is_active = False
            report.deactivated.append(f"{row.name}@v{row.version}")
