"""`targets.transform` 的三种取值实现（`contracts.md` §3.3）。

不填 transform = 原样注入，因此本模块只管三个具名适配器。

> **一个刻意的设计：`ref_to_filename` 不自己解析 asset_id。**
>
> 契约 §3.3 只规定「`asset_id` → ComfyUI 可见的文件名」这个**语义**，
> 而"素材存在哪、文件名怎么生成"是 A 流（素材与存储）的领域。
> 若这里硬编码一套路径规则，就会出现**两套真相**：A 流实际落盘的位置
> 与 B 流假设的位置不一致，而且**不一致时不会报错**，只会表现为
> "图片参数不生效 / 拿错图"。
>
> 所以本模块要求调用方注入 `asset_resolver` 回调；**没有 resolver 就直接报错，
> 绝不猜**（对应 `workflow_spec.md` §5.2 的约定）。
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from typing import Any

from engine.errors import RenderError

#: asset_id → ComfyUI 可见的文件名
AssetResolver = Callable[[Any], str]


def ref_to_filename(value: Any, *, resolver: AssetResolver | None = None, target: str = "") -> Any:
    """`asset_id` → 文件名。列表输入返回文件名列表。

    列表情形对应 `image_list` 类型（多参考图工作流）。
    返回列表而非拼接字符串，是因为多图节点的入参本身收 list；
    需要拼成单字段的场合应由 Schema 侧改用 `join_lines`。
    """
    if resolver is None:
        raise RenderError(
            f"target {target} 声明了 transform=ref_to_filename，但未提供 asset_resolver。"
            "该回调由 A 流（素材存储）提供；渲染器不会猜测素材路径（workflow_spec.md §5.2）"
        )

    if isinstance(value, (list, tuple)):
        return [ref_to_filename(v, resolver=resolver, target=target) for v in value]

    filename = resolver(value)
    if not isinstance(filename, str) or not filename:
        raise RenderError(
            f"target {target}: asset_resolver({value!r}) 返回了 {filename!r}，"
            "必须是非空字符串（ComfyUI 侧的文件名）"
        )
    return filename


def join_lines(value: Any, **_kw: Any) -> str:
    """数组 → 换行拼接的字符串。

    典型用途：多行提示词、批量标签。目标入参必须是 str。
    """
    if isinstance(value, str):
        return value  # 幂等：已是字符串就不动
    if not isinstance(value, (list, tuple)):
        raise RenderError(f"transform=join_lines 需要数组，实际 {type(value).__name__}")
    for item in value:
        if isinstance(item, (dict, list, tuple)):
            raise RenderError(
                f"transform=join_lines 的元素必须是标量，实际 {type(item).__name__}"
                "（嵌套结构请改用 json_str）"
            )
    return "\n".join("" if item is None else str(item) for item in value)


def json_str(value: Any, **_kw: Any) -> str:
    """对象 → JSON 字符串。目标入参必须是 str。"""
    if isinstance(value, str):
        return value  # 幂等
    try:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise RenderError(f"transform=json_str 序列化失败: {exc}") from exc


#: transform 名 → 实现。key 必须与 `schema.VALID_TRANSFORMS` 完全一致。
TRANSFORMS: dict[str, Callable[..., Any]] = {
    "ref_to_filename": ref_to_filename,
    "join_lines": join_lines,
    "json_str": json_str,
}


def apply_transform(
    transform: str | None,
    value: Any,
    *,
    resolver: AssetResolver | None = None,
    target: str = "",
) -> Any:
    """统一的 transform 入口。`transform=None` 表示原样注入。"""
    if transform is None:
        return value
    fn = TRANSFORMS.get(transform)
    if fn is None:  # Schema 解析时已挡过一次，这里是兜底
        raise RenderError(
            f"target {target} 的 transform='{transform}' 未实现，可选：{sorted(TRANSFORMS)}"
        )
    return fn(value, resolver=resolver, target=target)


def iter_transform_names() -> Iterable[str]:
    return TRANSFORMS.keys()
