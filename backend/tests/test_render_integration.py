"""A 流 ↔ B 流渲染接口的集成测试（**不需要 GPU**）。

**这条测试在守什么**：worker 的真实渲染调用是

```python
render(workflow.definition, ParamSchema.load(workflow.param_schema), task.params)
```

其中 `definition` / `param_schema` 来自数据库的 JSONB 列（不是文件）。
也就是说生产路径是「**库里的 JSON → 内核渲染**」，而 B 流的工具链平时走的是
「**仓库里的文件 → 内核渲染**」。两条路都叫 `render`，但数据来源不同 ——
本文件就是钉住"库里的那份 JSON 也能渲染成功"。

为什么值得单独测：它**不需要 GPU** 就能覆盖生产路径的绝大部分失败面
（Schema 不合契约、`targets` 指向不存在的节点、定义里有非 JSON 可序列化的值、
占位符解析不了……）。这些问题如果留到 GPU 窗口才发现，代价是机时 + 排查时间。

⚠️ **跨流耦合提醒**：本文件读的是 B 流的 `workflows/`。它红了有两种可能：
① 我们的集成断了；② B 流正在改工作流。**两种情况都需要有人看一眼**，
所以这里不做"失败就跳过"的处理 —— 那等于把守卫拆掉（见 `项目进展.md` #21 的同源教训）。
`workflows/registry.yaml` 不存在时（例如只检出 `backend/`）整文件跳过。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

engine = pytest.importorskip("engine", reason="engine 包未安装（仓库根 pip install -e .）")

from engine import ParamSchema, load_definition, render  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
REGISTRY = REPO_ROOT / "workflows" / "registry.yaml"

pytestmark = pytest.mark.skipif(
    not REGISTRY.exists(), reason="只检出 backend/ 时没有 B 流的工作流，跳过"
)


@pytest.fixture(scope="module")
def registry():  # type: ignore[no-untyped-def]
    return engine.Registry.load(str(REGISTRY))


def test_every_enabled_workflow_renders(registry) -> None:  # type: ignore[no-untyped-def]
    """每条已放行的工作流，用**空参数**都必须能渲染出干净结果。

    空参数是最保守的输入（全靠 Schema 的 default 兜底）——
    连它都不能干净渲染，说明生产路径有问题。
    """
    entries = registry.enabled()
    assert entries, "注册表里没有 enabled 的工作流，本测试失去意义"

    for entry in entries:
        definition = json.loads(json.dumps(load_definition(registry.definition_path(entry))))
        result = render(definition, entry.schema, {})

        assert result.node_count > 0, f"{entry.id} 渲染后没有任何节点"
        assert result.warnings == (), f"{entry.id} 渲染告警：{result.warnings}"
        assert result.unresolved_placeholders == (), (
            f"{entry.id} 有未解析占位符：{result.unresolved_placeholders}"
        )


def test_definition_survives_database_json_roundtrip(registry) -> None:  # type: ignore[no-untyped-def]
    """定义经过 `json.dumps`/`loads` 往返（模拟 JSONB 存取）后仍能渲染。

    库里存的是 JSONB，**读回来的是纯 JSON 类型**。若定义里混入了非 JSON 值，
    或某段逻辑依赖 Python 对象同一性，往返之后就会出问题 —— 而这类问题
    在"直接吃文件"的离线校验里永远暴露不出来。
    """
    for entry in registry.enabled():
        raw = load_definition(registry.definition_path(entry))
        roundtripped = json.loads(json.dumps(raw, ensure_ascii=False))
        assert roundtripped == raw, f"{entry.id} 的定义不是纯 JSON（往返后变了）"

        a = render(roundtripped, entry.schema, {})
        assert a.node_count > 0


def test_rendering_with_schema_defaults_is_warning_free(registry) -> None:  # type: ignore[no-untyped-def]
    """把每个字段的 default 显式传进去（生产里 API 会这么做）也不能有告警。

    API 层会把 `fields[].default` 合并进 `params` 再落库（契约 §3.4），
    所以 worker 拿到的 `task.params` 是**带着默认值**的。这条测的就是那个形状 ——
    与上一条（空参数）覆盖的是不同的输入，是刻意分开的两条。
    """
    for entry in registry.enabled():
        defaults = entry.schema.defaults()
        definition = load_definition(registry.definition_path(entry))
        result = render(definition, entry.schema, defaults)
        assert result.warnings == (), f"{entry.id} 带默认值渲染告警：{result.warnings}"


def test_seed_typed_workflows_resolve_seed(registry) -> None:  # type: ignore[no-untyped-def]
    """声明了 `seed` 字段的工作流，渲染后必须拿到**具体整数**。

    这是可复现性（FR-5.4/5.5）的前提：`-1` 若原样落进元数据，
    用户永远复现不出那张图 —— 而且**不会报错**，只是"复现不出来"。
    """
    checked = 0
    for entry in registry.enabled():
        if "seed" not in entry.schema:
            continue
        result = render(load_definition(registry.definition_path(entry)), entry.schema, {})
        assert isinstance(result.seed, int), f"{entry.id} 未解析出 seed"
        assert result.seed != -1, f"{entry.id} 的 seed 仍是随机哨兵"
        checked += 1
    assert checked > 0, "没有任何 enabled 工作流声明 seed 字段 —— 本测试失去意义"


def test_schema_load_rejects_missing_targets() -> None:
    """`targets` 必填（契约 §3.3）—— 这条是 A 流依赖的行为，钉住它以免被放松。

    历史上 `param_bindings` 与 `targets` 并存过一段时间（前者已删除）。
    若 `targets` 变成可选，那种"两套绑定机制"的隐患就有了回来的缝隙。
    """
    from engine import SchemaError

    with pytest.raises(SchemaError, match="targets"):
        ParamSchema.load(
            {
                "schema_version": 1,
                "fields": [
                    {
                        "key": "steps",
                        "label": "步数",
                        "type": "int",
                        "default": 4,
                        "min": 1,
                        "max": 10,
                    }
                ],
            }
        )
