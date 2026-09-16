"""参数注入单元测试（契约 §3.3 / §3.4）。

参数注入是「用户改的参数」到「ComfyUI 节点图」的唯一通路。
它出错的表现形式特别隐蔽 —— **图能跑通，但参数没生效**，
用户看到的是"这个功能是坏的"，而日志里什么异常都没有。
所以这里对「哪一种写法会落到哪个节点」逐条钉死。
"""

from __future__ import annotations

import pytest

from app.engine.injector import SEED_RANDOM, InjectionError, inject_params

# ComfyUI API 格式的最小节点图
DEFINITION = {
    "3": {"class_type": "KSampler", "inputs": {"steps": 20, "cfg": 7.0, "seed": 1}},
    "5": {"class_type": "EmptyLatentImage", "inputs": {"width": 512, "height": 512}},
}

SCHEMA = {
    "schema_version": 1,
    "fields": [
        {
            "key": "steps",
            "type": "int",
            "default": 4,
            "targets": [{"node_id": "3", "input": "steps"}],
        },
        {
            "key": "cfg",
            "type": "float",
            "default": 1.0,
            "targets": [{"node_id": "3", "input": "cfg"}],
        },
        {
            "key": "seed",
            "type": "seed",
            "default": -1,
            "targets": [{"node_id": "3", "input": "seed"}],
        },
        # 一个参数映射到多个 target（契约 §3.3 明确允许）
        {
            "key": "width",
            "type": "int",
            "default": 1024,
            "targets": [{"node_id": "5", "input": "width"}, {"node_id": "5", "input": "height"}],
        },
        # 空 targets 合法：只参与业务逻辑，不注入图
        {"key": "sku", "type": "str", "default": "", "targets": []},
    ],
}


# --- 基本注入 -----------------------------------------------------------------


def test_inject_contract_targets_form() -> None:
    result = inject_params(DEFINITION, SCHEMA, {"steps": 30, "cfg": 3.5, "width": 768})
    assert result.definition["3"]["inputs"]["steps"] == 30
    assert result.definition["3"]["inputs"]["cfg"] == 3.5
    assert set(result.applied) == {"steps", "cfg", "width"}


def test_one_param_maps_to_multiple_targets() -> None:
    result = inject_params(DEFINITION, SCHEMA, {"width": 768})
    assert result.definition["5"]["inputs"]["width"] == 768
    assert result.definition["5"]["inputs"]["height"] == 768


def test_original_definition_is_not_mutated() -> None:
    """必须返回新图：worker 可能要用同一份 definition 重试，改坏了会污染后续任务。"""
    inject_params(DEFINITION, SCHEMA, {"steps": 99})
    assert DEFINITION["3"]["inputs"]["steps"] == 20


def test_untouched_inputs_keep_graph_values() -> None:
    """没传的参数不能覆盖图上的原值 —— 否则会把图里配好的其它入参清掉。"""
    result = inject_params(DEFINITION, SCHEMA, {"steps": 8})
    assert result.definition["3"]["inputs"]["cfg"] == 7.0


def test_none_value_does_not_clobber_graph() -> None:
    """`None` = 没传（API 合并时会把 None 滤掉，这里再兜一层）。"""
    result = inject_params(DEFINITION, SCHEMA, {"steps": None})
    assert result.definition["3"]["inputs"]["steps"] == 20
    assert "steps" not in result.applied


def test_empty_targets_is_legal_and_reported_as_skipped() -> None:
    """`sku` 只参与业务逻辑（批次分组），不该被塞进图；但它也不能算"没注入"。"""
    result = inject_params(DEFINITION, SCHEMA, {"sku": "SKU-A", "steps": 4})
    assert "sku" in result.skipped
    assert "sku" not in result.applied
    assert "sku" not in str(result.definition["3"]["inputs"])


def test_input_created_when_absent_on_node() -> None:
    """图上没有该入参时也要写进去 —— 由 ComfyUI 在提交时校验（会给出 node_errors）。"""
    schema = {
        "fields": [
            {
                "key": "denoise",
                "type": "float",
                "default": 1.0,
                "targets": [{"node_id": "3", "input": "denoise"}],
            }
        ]
    }
    result = inject_params(DEFINITION, schema, {"denoise": 0.6})
    assert result.definition["3"]["inputs"]["denoise"] == 0.6


# --- 历史形式（param_bindings） -----------------------------------------------


def test_legacy_param_bindings_form() -> None:
    """模型里的历史字段 `param_bindings`：{"steps": ["3", "inputs", "steps"]}。"""
    result = inject_params(DEFINITION, None, {"steps": 33}, {"steps": ["3", "inputs", "steps"]})
    assert result.definition["3"]["inputs"]["steps"] == 33


def test_contract_targets_take_precedence_over_bindings() -> None:
    """两种来源冲突时以**契约形式**为准（§3.3 是跨流边界，bindings 是历史遗留）。"""
    result = inject_params(DEFINITION, SCHEMA, {"steps": 50}, {"steps": ["5", "inputs", "width"]})
    assert result.definition["3"]["inputs"]["steps"] == 50
    assert result.definition["5"]["inputs"]["width"] == 512  # 没被 bindings 改掉


def test_short_binding_is_ignored_not_crashed() -> None:
    result = inject_params(DEFINITION, None, {"steps": 11}, {"steps": ["3"]})
    assert result.definition["3"]["inputs"]["steps"] == 20
    assert "steps" in result.skipped


# --- 错误必须显式抛出 ---------------------------------------------------------


def test_unknown_node_raises_with_field_key() -> None:
    """节点 id 写错时静默跳过 = 用户永远不知道参数为什么没生效。"""
    schema = {
        "fields": [
            {
                "key": "steps",
                "type": "int",
                "default": 4,
                "targets": [{"node_id": "404", "input": "steps"}],
            }
        ]
    }
    with pytest.raises(InjectionError) as exc:
        inject_params(DEFINITION, schema, {"steps": 4})
    assert exc.value.field_key == "steps"
    assert "404" in str(exc.value)


def test_unknown_transform_raises() -> None:
    schema = {
        "fields": [
            {
                "key": "steps",
                "type": "int",
                "default": 4,
                "targets": [{"node_id": "3", "input": "steps", "transform": "no_such_transform"}],
            }
        ]
    }
    with pytest.raises(InjectionError) as exc:
        inject_params(DEFINITION, schema, {"steps": 4})
    assert "no_such_transform" in str(exc.value)


def test_target_without_input_raises() -> None:
    schema = {
        "fields": [{"key": "steps", "type": "int", "default": 4, "targets": [{"node_id": "3"}]}]
    }
    with pytest.raises(InjectionError):
        inject_params(DEFINITION, schema, {"steps": 4})


# --- transform（契约 §3.3） ---------------------------------------------------


def _schema_with_transform(key: str, node: str, inp: str, transform: str) -> dict:
    return {
        "fields": [
            {
                "key": key,
                "type": "str",
                "default": "",
                "targets": [{"node_id": node, "input": inp, "transform": transform}],
            }
        ]
    }


def test_transform_join_lines() -> None:
    result = inject_params(
        {"6": {"class_type": "CLIPTextEncode", "inputs": {"text": ""}}},
        _schema_with_transform("tags", "6", "text", "join_lines"),
        {"tags": ["白瓷杯", "柔光", "白底"]},
    )
    assert result.definition["6"]["inputs"]["text"] == "白瓷杯\n柔光\n白底"


def test_transform_join_lines_accepts_string() -> None:
    result = inject_params(
        {"6": {"class_type": "CLIPTextEncode", "inputs": {"text": ""}}},
        _schema_with_transform("tags", "6", "text", "join_lines"),
        {"tags": "已经是字符串"},
    )
    assert result.definition["6"]["inputs"]["text"] == "已经是字符串"


def test_transform_json_str() -> None:
    result = inject_params(
        {"6": {"class_type": "X", "inputs": {"payload": ""}}},
        _schema_with_transform("extra", "6", "payload", "json_str"),
        {"extra": {"a": 1, "b": [2]}},
    )
    assert result.definition["6"]["inputs"]["payload"] == '{"a": 1, "b": [2]}'


def test_transform_ref_to_filename_single() -> None:
    """`asset_id` → ComfyUI 可见的文件名。"""
    result = inject_params(
        {"10": {"class_type": "LoadImage", "inputs": {"image": ""}}},
        _schema_with_transform("image", "10", "image", "ref_to_filename"),
        {"image": 42},
        resolve_filename=lambda asset_id: f"uploaded-{asset_id}.png",
    )
    assert result.definition["10"]["inputs"]["image"] == "uploaded-42.png"


def test_transform_ref_to_filename_list() -> None:
    """多图参数（`image_list`）注入数组，保持顺序。"""
    result = inject_params(
        {"10": {"class_type": "LoadImageBatch", "inputs": {"images": []}}},
        _schema_with_transform("images", "10", "images", "ref_to_filename"),
        {"images": [7, 8]},
        resolve_filename=lambda asset_id: f"f{asset_id}.png",
    )
    assert result.definition["10"]["inputs"]["images"] == ["f7.png", "f8.png"]


def test_ref_to_filename_without_resolver_raises() -> None:
    """没有 resolver 时必须报错，而不是把 asset_id 原样塞进去让引擎报看不懂的错。"""
    with pytest.raises(InjectionError) as exc:
        inject_params(
            {"10": {"class_type": "LoadImage", "inputs": {"image": ""}}},
            _schema_with_transform("image", "10", "image", "ref_to_filename"),
            {"image": 42},
        )
    assert exc.value.field_key == "image"
    assert "resolve_filename" in str(exc.value)


def test_ref_to_filename_rejects_non_numeric() -> None:
    with pytest.raises(InjectionError):
        inject_params(
            {"10": {"class_type": "LoadImage", "inputs": {"image": ""}}},
            _schema_with_transform("image", "10", "image", "ref_to_filename"),
            {"image": {"not": "an id"}},
            resolve_filename=lambda asset_id: f"f{asset_id}.png",
        )


# --- seed 实体化 --------------------------------------------------------------


def test_seed_minus_one_is_materialized() -> None:
    """`-1` = 随机。**必须在这里实体化**：ComfyUI 的图里 seed 是具体整数，
    把 -1 原样提交会让"同一提示词出多张不同图"静默失效（每次都一模一样）。"""
    result = inject_params(DEFINITION, SCHEMA, {"seed": SEED_RANDOM})
    seeded = result.definition["3"]["inputs"]["seed"]
    assert isinstance(seeded, int)
    assert seeded != SEED_RANDOM
    assert result.seed == seeded


def test_seed_negative_one_variants_are_randomized() -> None:
    """断言"两次一定是不同值"会 flaky；改动值的做法才是稳的判据。"""
    a = inject_params(DEFINITION, SCHEMA, {"seed": -1}).definition["3"]["inputs"]["seed"]
    b = inject_params(DEFINITION, SCHEMA, {"seed": -1}).definition["3"]["inputs"]["seed"]
    assert a != -1 and b != -1
    assert -1 <= a <= 2**32 - 1 and -1 <= b <= 2**32 - 1


def test_explicit_seed_is_preserved() -> None:
    result = inject_params(DEFINITION, SCHEMA, {"seed": 20260916})
    assert result.definition["3"]["inputs"]["seed"] == 20260916
    assert result.seed == 20260916


def test_top_level_seed_alias_takes_effect() -> None:
    """顶层 seed 是便捷别名（契约 §3.4），params 里没有它时也要生效。"""
    result = inject_params(DEFINITION, SCHEMA, {"steps": 4}, seed=777)
    assert result.definition["3"]["inputs"]["seed"] == 777
    assert result.seed == 777


def test_explicit_seed_beats_top_level_alias_when_both_given() -> None:
    """params["seed"] 与顶层 seed 同时给出时，顶层（显式传参）优先。"""
    result = inject_params(DEFINITION, SCHEMA, {"seed": 1}, seed=2)
    assert result.definition["3"]["inputs"]["seed"] == 2


def test_seed_not_recorded_when_workflow_does_not_expose_it() -> None:
    """工作流没暴露 seed 时不能凭空记一个随机值 —— 那是个假的可复现承诺。"""
    schema = {
        "fields": [
            {
                "key": "steps",
                "type": "int",
                "default": 4,
                "targets": [{"node_id": "3", "input": "steps"}],
            }
        ]
    }
    result = inject_params(DEFINITION, schema, {"steps": 4, "seed": -1})
    assert result.seed is None
    assert result.definition["3"]["inputs"]["seed"] == 1  # 图上的原值未被动


# --- 边界 ---------------------------------------------------------------------


@pytest.mark.parametrize("schema", [None, {}, {"fields": None}, {"fields": []}])
def test_missing_schema_is_tolerated(schema: dict | None) -> None:
    """没有 schema（例如老工作流）时不应崩，参数一律归入 skipped。"""
    result = inject_params(DEFINITION, schema, {"steps": 30})
    assert "steps" in result.skipped
    assert result.definition["3"]["inputs"]["steps"] == 20


def test_empty_params_returns_graph_unchanged() -> None:
    result = inject_params(DEFINITION, SCHEMA, {})
    assert result.definition == DEFINITION
    assert result.applied == ()
    assert result.seed is None
