"""工作流参数注入（契约 `docs/sop/contracts.md` §3.3）。

**职责边界**：把「合并好的参数 dict」写进「ComfyUI API 格式的节点图」。
参数怎么合并（default < 模板预设 < body.params < 顶层别名）由 API 层负责（契约 §3.4），
这里只负责**落到图上的哪个节点哪个入参**。

注入位置来源（两种，见 §3.3 与 `Workflow` 模型）：

| 形式 | 出处 | 说明 |
|---|---|---|
| `param_schema.fields[].targets` | 契约 §3.3，**B 流产出的权威形式** | `[{"node_id": "3", "input": "steps", "transform": ...}]` |
| `Workflow.param_bindings` | 模型里的历史字段 | `{"steps": ["3", "inputs", "steps"]}` |

两者语义重叠（⚠️ 已登记为跨流待确认项）：契约只定义了 `targets`，
但模型里还留着 `param_bindings`。为不阻断开发，这里**优先用 `targets`，
缺失时回退 `param_bindings`**，两条路径都有测试覆盖。

**为什么注入失败要抛异常而不是跳过**：工作流节点 id 写错时静默跳过，
表现为「参数改了但图没变」—— 用户看到的是"这个功能是坏的"，
而日志里什么都没有。宁可提交前就报 `INVALID_PARAM`（EX-3），也不留这种哑故障。
"""

from __future__ import annotations

import copy
import json
import random
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from app.core.logging import get_logger

log = get_logger(__name__)

# seed 的哨兵值：-1 表示「随机」。
# **必须在这里实体化**：ComfyUI 的节点图里 seed 是一个具体整数，
# 随机化是前端/调用方的责任。若把 -1 原样提交，不同批次会得到完全相同的图，
# 「同一提示词出多张不同风格」这条需求会静默失效。
SEED_RANDOM = -1
_SEED_MAX = 2**32 - 1

# `transform` 取值（契约 §3.3）
TRANSFORM_REF_TO_FILENAME = "ref_to_filename"
TRANSFORM_JOIN_LINES = "join_lines"
TRANSFORM_JSON_STR = "json_str"


class InjectionError(Exception):
    """参数注入失败。

    携带 `field_key`，便于 API/worker 把「是哪个参数出的问题」明确告诉用户
    （否则用户只看到一句"参数非法"，无从下手）。
    """

    def __init__(self, message: str, field_key: str | None = None):
        super().__init__(message)
        self.field_key = field_key


@dataclass(frozen=True)
class InjectionResult:
    """注入结果。

    同时返回 `resolved` / `seed` 是有意的：执行层要把**实际用的值**落库，
    否则 `assets.meta` 里的可复现性元数据（FR-5.4 / C1）会与实际出图不一致 ——
    客户追单时按记录的参数复现不出来，是最难查的一类问题。
    """

    definition: dict[str, Any]
    resolved: dict[str, Any] = field(default_factory=dict)
    applied: tuple[str, ...] = ()
    skipped: tuple[str, ...] = ()  # 无 targets 的业务参数（如 sku）
    seed: int | None = None


def inject_params(
    definition: Mapping[str, Any],
    param_schema: Mapping[str, Any] | None,
    params: Mapping[str, Any] | None,
    param_bindings: Mapping[str, Any] | None = None,
    *,
    resolve_filename: Callable[[int], str] | None = None,
    seed: int | None = None,
) -> InjectionResult:
    """把 `params` 注入 `definition`，返回**新图**（不修改入参）。

    `resolve_filename` 仅在遇到 `ref_to_filename` 时才会被调用：
    它负责把 `asset_id` 变成 ComfyUI 可见的文件名
    （一般实现是"上传素材到 ComfyUI 的 input 目录，拿回 name"）。
    没提供却遇到图片参数时**明确报错**，而不是塞一个 id 进去让引擎报看不懂的错。
    """
    graph = copy.deepcopy(dict(definition or {}))
    values = dict(params or {})
    # 顶层 seed 是便捷别名（契约 §3.4），params 里没有时也要能生效
    if seed is not None and "seed" not in values:
        values["seed"] = seed
    targets_by_key = _collect_targets(param_schema, param_bindings)

    resolved: dict[str, Any] = {}
    applied: list[str] = []
    skipped: list[str] = []

    # seed 归一化：顶层 seed 优先，其次 params["seed"]
    raw_seed = seed if seed is not None else values.get("seed")
    for key, raw_value in values.items():
        if raw_value is None:
            continue  # None = 没传，保持图上的原值（不覆盖成 null）
        entries = targets_by_key.get(key)
        if not entries:
            # 空 targets 是合法的：`sku` / `upload_asset_ids` 这类只参与业务逻辑
            skipped.append(key)
            continue

        value = _materialize_seed(key, raw_value, raw_seed)
        for node_id, input_name, transform in entries:
            node = graph.get(str(node_id))
            if not isinstance(node, dict):
                raise InjectionError(
                    f"工作流里没有节点 {node_id}（参数 {key} 的 targets 指向了不存在的节点）",
                    field_key=key,
                )
            inputs = node.setdefault("inputs", {})
            if not isinstance(inputs, dict):
                raise InjectionError(f"节点 {node_id} 的 inputs 不是对象", field_key=key)
            inputs[input_name] = _apply_transform(
                value, transform, key, node_id, input_name, resolve_filename
            )
        resolved[key] = value
        applied.append(key)

    resolved_seed = _seed_from(resolved)
    log.debug("inject.applied applied=%s skipped=%s seed=%s", applied, skipped, resolved_seed)
    return InjectionResult(
        definition=graph,
        resolved=resolved,
        applied=tuple(applied),
        skipped=tuple(skipped),
        seed=resolved_seed,
    )


# ---------------------------------------------------------------- 内部


_Target = tuple[str, str, str | None]


def _collect_targets(
    param_schema: Mapping[str, Any] | None,
    param_bindings: Mapping[str, Any] | None,
) -> dict[str, list[_Target]]:
    """汇总两种来源的注入目标。契约形式（`targets`）优先。"""
    out: dict[str, list[_Target]] = {}

    # `fields: null` 也是可能的（JSONB 里手写/迁移出来的脏值），兜一层 `or []`
    for field_spec in (param_schema or {}).get("fields") or []:
        if not isinstance(field_spec, dict):
            continue
        key = field_spec.get("key")
        if not isinstance(key, str):
            continue
        entries: list[_Target] = []
        for target in field_spec.get("targets") or []:
            if not isinstance(target, dict):
                continue
            node_id = target.get("node_id")
            input_name = target.get("input")
            if node_id is None or not isinstance(input_name, str):
                raise InjectionError(
                    f"Schema 里参数 {key} 的 target 缺少 node_id/input", field_key=key
                )
            transform = target.get("transform")
            entries.append((str(node_id), input_name, str(transform) if transform else None))
        if entries:
            out[key] = entries

    # 历史形式：{"steps": ["3", "inputs", "steps"]} —— 首元素是节点 id，末元素是入参名
    for key, binding in (param_bindings or {}).items():
        if key in out:
            continue  # 契约形式优先
        if isinstance(binding, str) or not isinstance(binding, Sequence) or len(binding) < 2:
            continue
        out[key] = [(str(binding[0]), str(binding[-1]), None)]
    return out


def _materialize_seed(key: str, value: Any, raw_seed: Any) -> Any:
    """把 `seed == -1` 实体化成随机整数。"""
    if key != "seed":
        return value
    candidate = raw_seed if raw_seed is not None else value
    try:
        as_int = int(candidate)
    except (TypeError, ValueError):
        return value
    if as_int != SEED_RANDOM:
        return as_int
    return random.randint(0, _SEED_MAX)


def _seed_from(resolved: Mapping[str, Any]) -> int | None:
    seed = resolved.get("seed")
    return int(seed) if isinstance(seed, int) else None


def _apply_transform(
    value: Any,
    transform: str | None,
    key: str,
    node_id: str,
    input_name: str,
    resolve_filename: Callable[[int], str] | None,
) -> Any:
    if transform is None:
        return value
    if transform == TRANSFORM_REF_TO_FILENAME:
        return _to_filename(value, key, resolve_filename)
    if transform == TRANSFORM_JOIN_LINES:
        if isinstance(value, str):
            return value
        if isinstance(value, Sequence):
            return "\n".join(str(v) for v in value)
        raise InjectionError(f"参数 {key} 需要数组/字符串才能 join_lines", field_key=key)
    if transform == TRANSFORM_JSON_STR:
        if isinstance(value, str):
            return value
        return json.dumps(value, ensure_ascii=False)
    raise InjectionError(
        f"参数 {key} 使用了未知的 transform={transform!r}"
        f"（节点 {node_id}.{input_name}；契约 §3.3 只定义了 "
        f"ref_to_filename / join_lines / json_str）",
        field_key=key,
    )


def _to_filename(value: Any, key: str, resolve_filename: Callable[[int], str] | None) -> Any:
    if resolve_filename is None:
        raise InjectionError(
            f"参数 {key} 是图片类（transform=ref_to_filename），"
            f"但本次注入没有提供 resolve_filename 回调，无法把 asset_id 变成引擎可见的文件名",
            field_key=key,
        )
    ids = value if isinstance(value, Sequence) and not isinstance(value, str) else [value]
    names: list[str] = []
    for item in ids:
        try:
            names.append(resolve_filename(int(item)))
        except (TypeError, ValueError) as exc:
            raise InjectionError(
                f"参数 {key} 的图片引用不是合法 asset_id：{item!r}", field_key=key
            ) from exc
    # 单图参数注入字符串；多图参数注入数组（与 §3.2 的 image / image_list 对应）
    return names[0] if not isinstance(value, Sequence) or isinstance(value, str) else names
