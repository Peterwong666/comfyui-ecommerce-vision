"""参数 Schema 的解析与校验（`contracts.md` §3 的代码实现）。

**职责边界**：本模块只负责「把契约 §3 的 JSON 变成一个可靠的 Python 对象，
并能校验单个参数值」。它**不做参数合并**（优先级链是 A 流的 `_merge_params`），
只提供 `defaults()` 供渲染时兜底。

> 为什么渲染器还要自己校验一遍？
> 契约 §3.4 规定 A 后端「必须二次校验」，渲染器这一层是**第三道防线**。
> 理由很实际：`engine/` 是独立模块，可能被 CLI、离线校验脚本、压测脚本直接调用
> （见 `deploy/autodl/07_bench_workflow.py` 的用法），绕过后端。
> 若这里对非法值静默放过，后果是 **ComfyUI 报一个难以理解的错**，
> 而不是"某个参数越界"这种能直接定位的错。
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any

from engine.errors import SchemaError

#: 当前支持的 Schema 版本。破坏性改动必须递增（契约 §3.1）。
SCHEMA_VERSION = 1

#: 契约 §3.2 的 type 取值全集
VALID_TYPES: frozenset[str] = frozenset(
    {"int", "float", "str", "text", "bool", "enum", "seed", "image", "image_list"}
)

#: 数值型（必须给 min / max / step）
NUMERIC_TYPES: frozenset[str] = frozenset({"int", "float", "seed"})

#: 需要 min/max 的类型（seed 的取值域由 SEED_SPACE 决定，无需 min/max）
BOUNDED_TYPES: frozenset[str] = frozenset({"int", "float"})

#: 契约 §3.3 的 transform 取值全集
VALID_TRANSFORMS: frozenset[str] = frozenset({"ref_to_filename", "join_lines", "json_str"})

#: seed 取值域上界（含）。保守取 2^32-1，保证所有节点与下游工具都接受该范围的整数。
SEED_SPACE = 2**32


@dataclass(frozen=True)
class Target:
    """一条注入位置声明（契约 §3.3）。"""

    node_id: str
    input: str
    transform: str | None = None

    @classmethod
    def parse(cls, raw: Any, *, field_key: str) -> Target:
        where = f"字段 '{field_key}' 的 targets"
        if not isinstance(raw, dict):
            raise SchemaError(f"{where} 元素必须是对象，实际是 {type(raw).__name__}")
        node_id = raw.get("node_id")
        input_name = raw.get("input")
        if not isinstance(node_id, str) or not node_id:
            raise SchemaError(
                f"{where} 缺少 node_id 或类型不是字符串"
                f"（契约 §3.3 明确要求**字符串**，写成数字会导致注入时找不到节点）"
            )
        if not isinstance(input_name, str) or not input_name:
            raise SchemaError(f"{where}（node_id={node_id}）缺少 input")
        transform = raw.get("transform")
        if transform is not None and transform not in VALID_TRANSFORMS:
            raise SchemaError(
                f"{where}（node_id={node_id}.{input_name}）的 transform='{transform}' 非法，"
                f"可选：{sorted(VALID_TRANSFORMS)}"
            )
        return cls(node_id=node_id, input=input_name, transform=transform)

    def describe(self) -> str:
        return f"{self.node_id}.{self.input}" + (f"[{self.transform}]" if self.transform else "")


@dataclass(frozen=True)
class Field:
    """一个参数的定义（契约 §3.1）。"""

    key: str
    label: str
    type: str
    default: Any
    targets: tuple[Target, ...] = ()
    min: float | None = None
    max: float | None = None
    step: float | None = None
    options: tuple[Any, ...] = ()
    group: str | None = None
    help: str | None = None
    advanced: bool = False
    max_length: int | None = None
    visible_when: dict[str, Any] | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    # ------------------------------------------------------------ 解析

    @classmethod
    def parse(cls, raw: Any) -> Field:
        if not isinstance(raw, dict):
            raise SchemaError(f"fields 元素必须是对象，实际是 {type(raw).__name__}")

        key = raw.get("key")
        if not isinstance(key, str) or not key:
            raise SchemaError("field 缺少 key（契约 §3.1 规定 key 必填且唯一）")

        # default 必填 —— 后端 _merge_params 依赖它做兜底（契约 §3.1 表格已标注）
        if "default" not in raw:
            raise SchemaError(
                f"字段 '{key}' 缺少 default。契约 §3.1 规定 default **必填**，"
                "它是参数合并的兜底值，缺失会导致后端 _merge_params 漏掉该参数"
            )

        ftype = raw.get("type")
        if ftype not in VALID_TYPES:
            raise SchemaError(
                f"字段 '{key}' 的 type='{ftype}' 非法，可选：{sorted(VALID_TYPES)}"
            )

        label = raw.get("label")
        if not isinstance(label, str) or not label:
            raise SchemaError(f"字段 '{key}' 缺少 label（前端表单显示名）")

        options: tuple[Any, ...] = ()
        if ftype == "enum":
            raw_options = raw.get("options")
            if not isinstance(raw_options, list) or not raw_options:
                raise SchemaError(f"字段 '{key}' 的 type=enum 必须提供非空 options")
            values = []
            for opt in raw_options:
                if not isinstance(opt, dict) or "value" not in opt:
                    raise SchemaError(
                        f"字段 '{key}' 的 options 元素必须是 {{'value':..., 'label':...}}"
                    )
                values.append(opt["value"])
            options = tuple(values)

        # 数值型必须给 min/max（契约 §3.1：与 PRD §6.2 边界值一致）
        fmin = raw.get("min")
        fmax = raw.get("max")
        if ftype in BOUNDED_TYPES:
            if not isinstance(fmin, (int, float)) or isinstance(fmin, bool):
                raise SchemaError(f"字段 '{key}' 是 {ftype}，必须提供数值 min")
            if not isinstance(fmax, (int, float)) or isinstance(fmax, bool):
                raise SchemaError(f"字段 '{key}' 是 {ftype}，必须提供数值 max")
            if fmin > fmax:
                raise SchemaError(f"字段 '{key}' 的 min({fmin}) > max({fmax})")
            if ftype == "float" and not isinstance(raw.get("step"), (int, float)):
                raise SchemaError(f"字段 '{key}' 是 float，契约 §3.1 要求配 step（如 0.05）")

        if ftype in ("str", "text") and "max_length" not in raw:
            # 非硬性错误（契约 §3.2 只说"配 max_length"），但缺失会让校验退化
            pass

        targets_raw = raw.get("targets")
        if targets_raw is None:
            raise SchemaError(
                f"字段 '{key}' 缺少 targets。契约 §3.3 规定 targets **必填**；"
                "只参与业务逻辑、不进图的参数请显式写空数组 []（空数组是合法的）"
            )
        if not isinstance(targets_raw, list):
            raise SchemaError(f"字段 '{key}' 的 targets 必须是数组")
        targets = tuple(Target.parse(t, field_key=key) for t in targets_raw)

        visible_when = raw.get("visible_when")
        if visible_when is not None and not isinstance(visible_when, dict):
            raise SchemaError(f"字段 '{key}' 的 visible_when 必须是对象")

        return cls(
            key=key,
            label=label,
            type=ftype,
            default=copy.deepcopy(raw["default"]),
            targets=targets,
            min=fmin,
            max=fmax,
            step=raw.get("step"),
            options=options,
            group=raw.get("group"),
            help=raw.get("help"),
            advanced=bool(raw.get("advanced", False)),
            max_length=raw.get("max_length"),
            visible_when=visible_when,
            raw=copy.deepcopy(raw),
        )

    # ------------------------------------------------------------ 属性

    @property
    def injects_into_graph(self) -> bool:
        return bool(self.targets)

    @property
    def target_nodes(self) -> set[str]:
        return {t.node_id for t in self.targets}

    # ------------------------------------------------------------ 校验

    def validate(self, value: Any) -> Any:
        """校验单个参数值，返回**已规范化**的值。

        契约 §3.4 规定 A 后端是校验主责；这里是渲染前的最后一道防线。
        校验失败一律抛 `RenderError`（EX-3，不可重试），由调用方转成 API 错误。
        """
        from engine.errors import RenderError  # 局部导入避免循环

        if value is None:
            # None 表示"未提供"，由调用方用 default 兜底，不在这里判非法
            raise RenderError(f"参数 '{self.key}' 的值为 None，调用方应先做 default 兜底")

        t = self.type

        if t == "bool":
            if not isinstance(value, bool):
                raise RenderError(f"参数 '{self.key}' 需要布尔值，实际 {type(value).__name__}")
            return value

        if t in ("int", "seed"):
            if isinstance(value, bool) or not isinstance(value, int):
                raise RenderError(
                    f"参数 '{self.key}' 需要整数，实际 {value!r}（{type(value).__name__}）"
                )
            if t == "seed":
                # -1 = 随机；其余必须在 [0, SEED_SPACE)
                if value == -1:
                    return value
                if not 0 <= value < SEED_SPACE:
                    raise RenderError(
                        f"参数 '{self.key}' 的 seed={value} 超出取值域 "
                        f"[-1, {SEED_SPACE - 1}]（-1 表示随机）"
                    )
                return value
            self._check_bounds(value)
            return value

        if t == "float":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise RenderError(
                    f"参数 '{self.key}' 需要数值，实际 {value!r}（{type(value).__name__}）"
                )
            self._check_bounds(value)
            return float(value)

        if t in ("str", "text"):
            if not isinstance(value, str):
                raise RenderError(
                    f"参数 '{self.key}' 需要字符串，实际 {value!r}（{type(value).__name__}）"
                )
            if self.max_length is not None and len(value) > self.max_length:
                raise RenderError(
                    f"参数 '{self.key}' 长度 {len(value)} 超过 max_length={self.max_length}"
                )
            return value

        if t == "enum":
            if value not in self.options:
                raise RenderError(
                    f"参数 '{self.key}' 取值 {value!r} 不在允许集合 {list(self.options)} 内"
                )
            return value

        if t == "image":
            if not isinstance(value, int) or isinstance(value, bool):
                raise RenderError(
                    f"参数 '{self.key}' 需要素材 id（整数），实际 {value!r}。"
                    "契约 §3.2 规定值经 asset_id 传递"
                )
            return value

        if t == "image_list":
            if not isinstance(value, (list, tuple)):
                raise RenderError(f"参数 '{self.key}' 需要素材 id 列表，实际 {type(value).__name__}")
            for item in value:
                if not isinstance(item, int) or isinstance(item, bool):
                    raise RenderError(f"参数 '{self.key}' 的元素需要整数 id，实际 {item!r}")
            return list(value)

        raise RenderError(f"参数 '{self.key}' 的 type='{t}' 未实现校验")  # pragma: no cover

    def _check_bounds(self, value: float) -> None:
        from engine.errors import RenderError

        if self.min is not None and value < self.min:
            raise RenderError(f"参数 '{self.key}' 的值 {value} 小于下限 {self.min}")
        if self.max is not None and value > self.max:
            raise RenderError(f"参数 '{self.key}' 的值 {value} 大于上限 {self.max}")


@dataclass
class ParamSchema:
    """一份完整的参数 Schema（契约 §3.1）。"""

    schema_version: int
    fields: tuple[Field, ...]

    @classmethod
    def load(cls, raw: Any) -> ParamSchema:
        if not isinstance(raw, dict):
            raise SchemaError(f"param_schema 必须是对象，实际是 {type(raw).__name__}")

        version = raw.get("schema_version")
        if version is None:
            raise SchemaError("param_schema 缺少 schema_version（契约 §3.1 规定必填）")
        if version != SCHEMA_VERSION:
            raise SchemaError(
                f"param_schema 的 schema_version={version}，本渲染器只支持 {SCHEMA_VERSION}。"
                "破坏性改动必须递增版本并同步升级渲染器（契约 §3.1）"
            )

        fields_raw = raw.get("fields")
        if not isinstance(fields_raw, list):
            raise SchemaError("param_schema 缺少 fields 数组")
        fields = tuple(Field.parse(f) for f in fields_raw)

        seen: set[str] = set()
        for f in fields:
            if f.key in seen:
                raise SchemaError(f"字段 key '{f.key}' 重复（契约 §3.1 规定 key 唯一）")
            seen.add(f.key)

        return cls(schema_version=version, fields=fields)

    # ------------------------------------------------------------ 查询

    def get(self, key: str) -> Field | None:
        for f in self.fields:
            if f.key == key:
                return f
        return None

    def __contains__(self, key: object) -> bool:
        return any(f.key == key for f in self.fields)

    def __iter__(self):
        return iter(self.fields)

    def __len__(self) -> int:
        return len(self.fields)

    @property
    def keys(self) -> list[str]:
        return [f.key for f in self.fields]

    def defaults(self) -> dict[str, Any]:
        """各字段的 default 组成的基础参数字典（合并优先级链的最底层）。"""
        return {f.key: copy.deepcopy(f.default) for f in self.fields}

    def validate_all(self, params: dict[str, Any]) -> list[str]:
        """批量校验，返回问题描述列表（不抛异常，供校验脚本收集全部问题）。"""
        problems: list[str] = []
        for key, value in params.items():
            f = self.get(key)
            if f is None:
                continue  # 未知 key 由渲染器单独告警，不算 Schema 问题
            try:
                f.validate(value)
            except Exception as exc:  # noqa: BLE001
                problems.append(f"参数 '{key}': {exc}")
        return problems
