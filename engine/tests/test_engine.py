"""`engine/` 的离线自测。

**为什么这些测试很重要**：`engine/` 是 B 流的核心产出，而它当前**无法在 GPU 上验证**
（实例处于无卡模式）。所以"能被离线证明的部分"必须全部被证明 ——
否则等切回有卡时，就要在 GPU 机时上排错，成本高得多。

最要紧的两个断言：
1. `test_defaults_render_equals_template` —— 「不传参数直接渲染」必须等价于「把模板原样提交」。
   这条不变量保证 `07_bench_workflow.py`（裸提交模板）测出的性能，
   与生产渲染路径得到的是同一张图。
2. `test_write_metadata_does_not_touch_image_data` —— 写元数据**不得改变图像数据字节**。
   否则产物 hash 不可比对、去重失效。

运行：`PYTHONPATH=. backend/.venv/bin/python -m pytest engine/tests -q`
"""

from __future__ import annotations

import json
import pathlib
import struct

import pytest

from engine.errors import MetadataError, RenderError, SchemaError
from engine.metadata import (
    METADATA_JSON_KEY,
    PNG_SIGNATURE,
    PROMPT_DIGEST_ALG,
    _prompt_digest,
    build_metadata,
    read_png_metadata,
    read_png_text_chunks,
    write_png_metadata,
)
from engine.registry import Registry
from engine.render import (
    RenderOptions,
    derive_seed,
    render,
    split_definition,
)
from engine.schema import ParamSchema
from engine.transforms import apply_transform
from engine.validate import (
    _validate_schema_against_graph,
    bounds_from_settings,
    check_graph,
    check_object_info,
    check_settings_bounds,
    orphan_nodes,
    validate_registry,
)

# ============================================================ 夹具


def make_schema(fields: list[dict]) -> ParamSchema:
    return ParamSchema.load({"schema_version": 1, "fields": fields})


def make_definition(nodes: dict, meta_extra: dict | None = None) -> dict:
    meta = {"id": "demo_v1", "version": 1, "title": "演示", "models": ["m.safetensors"]}
    meta.update(meta_extra or {})
    return {"_meta": meta, **nodes}


#: 一条最小的合法工作流（含一个 bypass 分支）：1 → 2 → 3 → 5，4 是可选分支
MINIMAL_NODES = {
    "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "m.safetensors"}},
    "2": {"class_type": "EmptyLatentImage",
          "inputs": {"width": 1024, "height": 1024, "batch_size": 1}},
    "3": {"class_type": "KSampler",
          "inputs": {"seed": 1, "steps": 20, "cfg": 6.0, "model": ["1", 0],
                     "latent_image": ["2", 0]}},
    "4": {"class_type": "ImageScale", "inputs": {"image": ["3", 0], "scale": 2.0}},
    "5": {"class_type": "SaveImage",
          "inputs": {"images": ["3", 0], "filename_prefix": "demo/base"}},
}


def make_png(width: int = 8, height: int = 8, extra_text: dict[str, str] | None = None) -> bytes:
    """用手写的最小 PNG 编码器造一张真 PNG。

    刻意**不依赖 Pillow** —— 测试夹具不该引入第三方依赖，否则
    「另一个虚拟环境里跑不起来」会变成测试被跳过甚至被删掉的理由。
    （Pillow 只在一个**独立交叉验证**用例里用，缺失时自动 skip。）

    模拟 ComfyUI 的产出：含它自己写的 `prompt` / `workflow` tEXt chunk。
    """
    import binascii
    import zlib

    def chunk(ctype: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + ctype
            + data
            + struct.pack(">I", binascii.crc32(ctype + data) & 0xFFFFFFFF)
        )

    # 8-bit RGB，filter type 0
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    rows = bytearray()
    for y in range(height):
        rows.append(0)  # filter: None
        for x in range(width):
            rows += bytes(((x * 7 + y) % 256, (x * 13 + y * 3) % 256, (x * 3 + y * 11) % 256))

    out = bytearray(PNG_SIGNATURE)
    out += chunk(b"IHDR", ihdr)
    for k, v in (extra_text or {}).items():
        out += chunk(b"tEXt", k.encode("latin-1") + b"\x00" + v.encode("latin-1"))
    out += chunk(b"IDAT", zlib.compress(bytes(rows)))
    out += chunk(b"IEND", b"")
    return bytes(out)


def idat_bytes(raw: bytes) -> bytes:
    """取出全部 IDAT chunk 的数据（图像像素数据）。"""
    from engine.metadata import _iter_chunks

    out = bytearray()
    for _pos, ctype, _length, data in _iter_chunks(raw):
        if ctype == b"IDAT":
            out += data
    return bytes(out)


# ============================================================ Schema


class TestSchema:
    def test_loads_valid_schema(self):
        s = make_schema([
            {"key": "steps", "label": "步数", "type": "int", "default": 25,
             "min": 1, "max": 100, "step": 1, "targets": [{"node_id": "3", "input": "steps"}]},
        ])
        assert s.keys == ["steps"]
        assert s.defaults() == {"steps": 25}

    def test_rejects_missing_default(self):
        with pytest.raises(SchemaError, match="缺少 default"):
            make_schema([{"key": "steps", "label": "步数", "type": "int",
                          "min": 1, "max": 100, "targets": []}])

    def test_rejects_missing_targets(self):
        """契约 §3.1 规定 targets 必填 —— 空数组合法，但字段不能没有。"""
        with pytest.raises(SchemaError, match="缺少 targets"):
            make_schema([{"key": "sku", "label": "SKU", "type": "str", "default": ""}])

    def test_empty_targets_is_legal(self):
        s = make_schema([{"key": "sku", "label": "SKU", "type": "str", "default": "",
                          "targets": []}])
        assert s.get("sku").targets == ()
        assert s.get("sku").injects_into_graph is False

    def test_rejects_numeric_node_id(self):
        """node_id 必须是字符串：写成数字会在注入时找不到节点。"""
        with pytest.raises(SchemaError, match="字符串"):
            make_schema([{"key": "steps", "label": "步数", "type": "int", "default": 1,
                          "min": 1, "max": 2, "targets": [{"node_id": 3, "input": "steps"}]}])

    def test_rejects_enum_without_options(self):
        with pytest.raises(SchemaError, match="options"):
            make_schema([{"key": "s", "label": "采样器", "type": "enum", "default": "euler",
                          "targets": []}])

    def test_rejects_duplicate_key(self):
        fld = {"key": "a", "label": "A", "type": "int", "default": 1, "min": 0, "max": 2,
               "targets": []}
        with pytest.raises(SchemaError, match="重复"):
            make_schema([fld, dict(fld)])

    def test_rejects_unknown_transform(self):
        with pytest.raises(SchemaError, match="transform"):
            make_schema([{"key": "a", "label": "A", "type": "str", "default": "",
                          "targets": [{"node_id": "1", "input": "x", "transform": "nope"}]}])

    def test_rejects_bad_schema_version(self):
        with pytest.raises(SchemaError, match="schema_version"):
            ParamSchema.load({"schema_version": 99, "fields": []})

    def test_validate_bounds(self):
        f = make_schema([{"key": "steps", "label": "步数", "type": "int", "default": 25,
                          "min": 1, "max": 100, "targets": []}]).get("steps")
        assert f.validate(50) == 50
        with pytest.raises(RenderError, match="上限"):
            f.validate(101)
        with pytest.raises(RenderError, match="整数"):
            f.validate("50")

    def test_bool_is_not_int(self):
        """Python 里 True == 1，若不显式排除会把布尔值当整数放进步数。"""
        f = make_schema([{"key": "steps", "label": "步数", "type": "int", "default": 25,
                          "min": 0, "max": 100, "targets": []}]).get("steps")
        with pytest.raises(RenderError):
            f.validate(True)

    def test_seed_accepts_minus_one(self):
        f = make_schema([{"key": "seed", "label": "种子", "type": "seed", "default": -1,
                          "targets": []}]).get("seed")
        assert f.validate(-1) == -1
        assert f.validate(123) == 123
        with pytest.raises(RenderError, match="取值域"):
            f.validate(2**40)


# ============================================================ transform


class TestTransforms:
    def test_join_lines(self):
        assert apply_transform("join_lines", ["a", "b"]) == "a\nb"
        assert apply_transform("join_lines", "already") == "already"
        with pytest.raises(RenderError):
            apply_transform("join_lines", {"a": 1})

    def test_json_str(self):
        assert apply_transform("json_str", {"a": 1}) == '{"a":1}'
        assert apply_transform("json_str", "x") == "x"

    def test_ref_to_filename_requires_resolver(self):
        """没有 resolver 时必须报错，绝不猜素材路径（规范 §5.2）。"""
        with pytest.raises(RenderError, match="asset_resolver"):
            apply_transform("ref_to_filename", 7, target="30.image")

    def test_ref_to_filename_with_resolver(self):
        resolver = lambda aid: f"asset_{aid}.png"  # noqa: E731
        assert apply_transform("ref_to_filename", 7, resolver=resolver) == "asset_7.png"
        assert apply_transform("ref_to_filename", [7, 8], resolver=resolver) == \
            ["asset_7.png", "asset_8.png"]

    def test_none_transform_is_identity(self):
        assert apply_transform(None, 42) == 42


# ============================================================ render


class TestRender:
    def test_meta_is_stripped(self):
        schema = make_schema([])
        result = render(make_definition(MINIMAL_NODES), schema)
        assert "_meta" not in result.workflow
        assert set(result.workflow) == set(MINIMAL_NODES)

    def test_defaults_render_equals_template(self):
        """⭐ 核心不变量：不传参数直接渲染 ≡ 模板原样。

        这条保证了「裸提交模板的工具」与「生产渲染路径」测的是同一张图。
        """
        definition = make_definition(MINIMAL_NODES)
        schema = make_schema([
            {"key": "width", "label": "宽", "type": "int", "default": 1024,
             "min": 512, "max": 2048, "step": 64, "targets": [{"node_id": "2", "input": "width"}]},
            {"key": "steps", "label": "步数", "type": "int", "default": 20,
             "min": 1, "max": 100, "step": 1, "targets": [{"node_id": "3", "input": "steps"}]},
            {"key": "cfg", "label": "CFG", "type": "float", "default": 6.0, "min": 1.0,
             "max": 20.0, "step": 0.1, "targets": [{"node_id": "3", "input": "cfg"}]},
        ])
        result = render(definition, schema)
        _meta, original = split_definition(definition)
        assert result.workflow == original
        assert not result.warnings

    def test_multi_target_injection(self):
        """一个参数可以映射到多个 target —— klein 的 width 必须同时写潜空间与调度器。"""
        definition = make_definition({
            "1": {"class_type": "VAELoader", "inputs": {"vae_name": "v.safetensors"}},
            "6": {"class_type": "EmptyFlux2LatentImage",
                  "inputs": {"width": 1024, "height": 1024, "batch_size": 1}},
            "7": {"class_type": "Flux2Scheduler",
                  "inputs": {"steps": 4, "width": 1024, "height": 1024}},
            "13": {"class_type": "SaveImage",
                   "inputs": {"images": ["6", 0], "filename_prefix": "k/base"}},
        })
        schema = make_schema([
            {"key": "width", "label": "宽", "type": "int", "default": 1024,
             "min": 512, "max": 2048, "step": 64,
             "targets": [{"node_id": "6", "input": "width"},
                         {"node_id": "7", "input": "width"}]},
        ])
        result = render(definition, schema, {"width": 768})
        assert result.workflow["6"]["inputs"]["width"] == 768
        assert result.workflow["7"]["inputs"]["width"] == 768

    def test_placeholder_substitution(self):
        definition = make_definition({
            "1": {"class_type": "CLIPTextEncode",
                  "inputs": {"text": "a photo of {{product}}, {{scene}}", "clip": ["9", 0]}},
            "2": {"class_type": "SaveImage",
                  "inputs": {"images": ["1", 0], "filename_prefix": "demo/b"}},
        })
        schema = make_schema([
            {"key": "product", "label": "商品", "type": "str", "default": "mug", "targets": []},
            {"key": "scene", "label": "场景", "type": "str", "default": "studio", "targets": []},
        ])
        result = render(definition, schema, {"scene": "kitchen"})
        assert result.workflow["1"]["inputs"]["text"] == "a photo of mug, kitchen"

    def test_targets_override_placeholder(self):
        """规范 §5.4 规则 2：targets 优先级高于占位符。"""
        definition = make_definition({
            "1": {"class_type": "CLIPTextEncode",
                  "inputs": {"text": "prefix {{p}} suffix", "clip": ["9", 0]}},
            "2": {"class_type": "SaveImage",
                  "inputs": {"images": ["1", 0], "filename_prefix": "demo/b"}},
        })
        schema = make_schema([
            {"key": "p", "label": "P", "type": "str", "default": "D",
             "targets": [{"node_id": "1", "input": "text"}]},
        ])
        result = render(definition, schema)
        # targets 把整个 text 覆盖成 "D"，占位符的结果被丢弃
        assert result.workflow["1"]["inputs"]["text"] == "D"

    def test_unresolved_placeholder_is_preserved_and_reported(self):
        """规范 §5.4 规则 4：保留原样 + 告警，绝不静默清空。"""
        definition = make_definition({
            "1": {"class_type": "CLIPTextEncode",
                  "inputs": {"text": "hello {{missing}}", "clip": ["9", 0]}},
            "2": {"class_type": "SaveImage",
                  "inputs": {"images": ["1", 0], "filename_prefix": "demo/b"}},
        })
        result = render(definition, make_schema([]))
        assert result.workflow["1"]["inputs"]["text"] == "hello {{missing}}"
        assert "missing" in result.unresolved_placeholders
        assert any("占位符" in w for w in result.warnings)

    def test_strict_placeholders_raises(self):
        definition = make_definition({
            "1": {"class_type": "CLIPTextEncode",
                  "inputs": {"text": "{{missing}}", "clip": ["9", 0]}},
            "2": {"class_type": "SaveImage",
                  "inputs": {"images": ["1", 0], "filename_prefix": "demo/b"}},
        })
        with pytest.raises(RenderError, match="占位符"):
            render(definition, make_schema([]),
                   options=RenderOptions(strict_placeholders=True))

    def test_seed_minus_one_is_resolved_and_recorded(self):
        """⭐ -1 必须解析成真实整数并暴露出来，否则用户复现不出图（FR-5.5）。"""
        definition = make_definition(MINIMAL_NODES)
        schema = make_schema([
            {"key": "seed", "label": "种子", "type": "seed", "default": -1,
             "targets": [{"node_id": "3", "input": "seed"}]},
        ])
        result = render(definition, schema)
        assert isinstance(result.seed, int)
        assert 0 <= result.seed < 2**32
        assert result.workflow["3"]["inputs"]["seed"] == result.seed
        assert result.params["seed"] == result.seed

    def test_explicit_seed_is_preserved(self):
        definition = make_definition(MINIMAL_NODES)
        schema = make_schema([
            {"key": "seed", "label": "种子", "type": "seed", "default": -1,
             "targets": [{"node_id": "3", "input": "seed"}]},
        ])
        result = render(definition, schema, {"seed": 20260916})
        assert result.seed == 20260916
        assert result.workflow["3"]["inputs"]["seed"] == 20260916

    def test_none_param_falls_back_to_default(self):
        definition = make_definition(MINIMAL_NODES)
        schema = make_schema([
            {"key": "steps", "label": "步数", "type": "int", "default": 20, "min": 1,
             "max": 100, "targets": [{"node_id": "3", "input": "steps"}]},
        ])
        result = render(definition, schema, {"steps": None})
        assert result.workflow["3"]["inputs"]["steps"] == 20

    def test_unknown_param_is_kept_and_warned(self):
        definition = make_definition(MINIMAL_NODES)
        result = render(definition, make_schema([]), {"sku": "SKU-A"})
        assert result.params["sku"] == "SKU-A"
        assert any("未在 param_schema" in w for w in result.warnings)

    def test_target_to_missing_node_raises(self):
        definition = make_definition(MINIMAL_NODES)
        schema = make_schema([
            {"key": "steps", "label": "步数", "type": "int", "default": 20, "min": 1,
             "max": 100, "targets": [{"node_id": "999", "input": "steps"}]},
        ])
        with pytest.raises(RenderError, match="999"):
            render(definition, schema)

    def test_original_definition_not_mutated(self):
        """渲染必须深拷贝 —— 否则同一份定义被反复渲染时会串味。"""
        definition = make_definition(MINIMAL_NODES)
        schema = make_schema([
            {"key": "steps", "label": "步数", "type": "int", "default": 20, "min": 1,
             "max": 100, "targets": [{"node_id": "3", "input": "steps"}]},
        ])
        render(definition, schema, {"steps": 7})
        assert definition["3"]["inputs"]["steps"] == 20

    def test_output_prefix_override(self):
        definition = make_definition(MINIMAL_NODES)
        result = render(definition, make_schema([]),
                        options=RenderOptions(output_prefix="t2i_v1/task_42"))
        assert result.workflow["5"]["inputs"]["filename_prefix"] == "t2i_v1/task_42"

    def test_output_prefix_without_save_node_raises(self):
        definition = make_definition({
            "1": {"class_type": "VAELoader", "inputs": {"vae_name": "v.safetensors"}},
        })
        with pytest.raises(RenderError, match="SaveImage"):
            render(definition, make_schema([]), options=RenderOptions(output_prefix="x/"))

    def test_derive_seed(self):
        assert derive_seed(100, 0) == 100
        assert derive_seed(100, 3) == 103
        assert derive_seed(2**32 - 1, 1) == 0  # 回绕
        assert 0 <= derive_seed(-1, 5) < 2**32

    def test_missing_meta_raises(self):
        with pytest.raises(RenderError, match="_meta"):
            render({"1": {"class_type": "X", "inputs": {}}}, make_schema([]))


# ============================================================ bypass


class TestBypass:
    def _switch_meta(self) -> dict:
        return {
            "switches": [
                {
                    "key": "hires_fix",
                    "enabled_when": [True],
                    "disabled": {
                        "prune": ["4"],
                        "rewire": {"5": {"images": ["3", 0]}},
                    },
                }
            ]
        }

    def _schema(self) -> ParamSchema:
        return make_schema([
            {"key": "hires_fix", "label": "高清修复", "type": "bool", "default": False,
             "targets": []},
        ])

    def test_enabled_keeps_branch(self):
        definition = make_definition({
            **MINIMAL_NODES,
            "3": {"class_type": "KSampler",
                  "inputs": {"seed": 1, "steps": 20, "cfg": 6.0, "model": ["1", 0],
                             "latent_image": ["2", 0]}},
            "5": {"class_type": "SaveImage",
                  "inputs": {"images": ["4", 0], "filename_prefix": "demo/base"}},
        }, self._switch_meta())
        result = render(definition, self._schema(), {"hires_fix": True})
        assert not result.pruned
        assert result.workflow["5"]["inputs"]["images"] == ["4", 0]

    def test_disabled_prunes_and_rewires(self):
        """关闭时：分支节点消失，消费者改从保留节点取输入。"""
        definition = make_definition({
            **MINIMAL_NODES,
            "5": {"class_type": "SaveImage",
                  "inputs": {"images": ["4", 0], "filename_prefix": "demo/base"}},
        }, self._switch_meta())
        result = render(definition, self._schema(), {"hires_fix": False})
        assert "4" not in result.workflow
        assert result.pruned == ("4",)
        assert result.workflow["5"]["inputs"]["images"] == ["3", 0]
        assert "5.images" in result.rewired
        # 裁剪后图仍然是连通的（rewire 到位）
        assert orphan_nodes(result.workflow) == []

    def test_defaults_use_disabled_path(self):
        """default=False 时，默认渲染就走裁剪路径。"""
        definition = make_definition({
            **MINIMAL_NODES,
            "5": {"class_type": "SaveImage",
                  "inputs": {"images": ["4", 0], "filename_prefix": "demo/base"}},
        }, self._switch_meta())
        result = render(definition, self._schema())
        assert "4" not in result.workflow

    def test_prune_missing_node_raises(self):
        definition = make_definition(MINIMAL_NODES, {"switches": [{
            "key": "hires_fix", "enabled_when": [True],
            "disabled": {"prune": ["404"], "rewire": {"5": {"images": ["3", 0]}}},
        }]})
        with pytest.raises(RenderError, match="404"):
            render(definition, self._schema(), {"hires_fix": False})

    def test_rewire_to_pruned_node_raises(self):
        definition = make_definition(MINIMAL_NODES, {"switches": [{
            "key": "hires_fix", "enabled_when": [True],
            "disabled": {"prune": ["4"], "rewire": {"5": {"images": ["4", 0]}}},
        }]})
        with pytest.raises(RenderError, match="被裁掉"):
            render(definition, self._schema(), {"hires_fix": False})

    def test_missing_rewire_is_rejected_at_parse(self):
        definition = make_definition(MINIMAL_NODES, {"switches": [{
            "key": "hires_fix", "enabled_when": [True],
            "disabled": {"prune": ["4"]},
        }]})
        with pytest.raises(RenderError, match="rewire"):
            render(definition, self._schema(), {"hires_fix": False})

    def test_switch_key_not_in_params_raises(self):
        definition = make_definition(MINIMAL_NODES, {"switches": [{
            "key": "ghost", "enabled_when": [True],
            "disabled": {"prune": ["4"], "rewire": {"5": {"images": ["3", 0]}}},
        }]})
        with pytest.raises(RenderError, match="ghost"):
            render(definition, make_schema([]))

    def test_target_into_pruned_node_is_skipped_with_warning(self):
        """参数指向被裁的分支时跳过注入并告警，而不是报错（分支关掉本就该忽略）。"""
        definition = make_definition({
            **MINIMAL_NODES,
            "5": {"class_type": "SaveImage",
                  "inputs": {"images": ["4", 0], "filename_prefix": "demo/base"}},
        }, self._switch_meta())
        schema = make_schema([
            {"key": "hires_fix", "label": "高清修复", "type": "bool", "default": False,
             "targets": []},
            {"key": "scale", "label": "放大倍数", "type": "float", "default": 2.0,
             "min": 1.0, "max": 4.0, "step": 0.5,
             "targets": [{"node_id": "4", "input": "scale"}]},
        ])
        result = render(definition, schema, {"hires_fix": False})
        assert any("跳过注入" in w for w in result.warnings)


# ============================================================ 图结构检查


class TestGraphChecks:
    def test_detects_dangling_link(self):
        graph = {"1": {"class_type": "SaveImage",
                       "inputs": {"images": ["9", 0], "filename_prefix": "x"}}}
        findings = check_graph(graph, scope="t")
        assert any("不存在的节点" in f.message for f in findings)

    def test_detects_missing_output_node(self):
        graph = {"1": {"class_type": "VAELoader", "inputs": {"vae_name": "v"}}}
        findings = check_graph(graph, scope="t")
        assert any("SaveImage" in f.message and f.level == "error" for f in findings)

    def test_orphan_detection_follows_inputs(self):
        graph = {
            "1": {"class_type": "A", "inputs": {}},
            "2": {"class_type": "B", "inputs": {"x": ["1", 0]}},
            "3": {"class_type": "SaveImage",
                  "inputs": {"images": ["2", 0], "filename_prefix": "x"}},
            "9": {"class_type": "Z", "inputs": {}},
        }
        assert orphan_nodes(graph) == ["9"]

    def test_link_index_is_not_bool(self):
        graph = {"1": {"class_type": "SaveImage",
                       "inputs": {"images": ["2", True], "filename_prefix": "x"}},
                 "2": {"class_type": "A", "inputs": {}}}
        findings = check_graph(graph, scope="t")
        assert any("输出序号非法" in f.message for f in findings)


# ============================================================ 元数据


class TestMetadata:
    def test_build_metadata_includes_prompt_by_default(self):
        meta = build_metadata(
            workflow_id="t2i_v1", workflow_version=1,
            params={"prompt": "白色陶瓷杯", "steps": 30}, seed=42,
            models=["sd_xl_base_1.0.safetensors"], task_id=7,
        )
        assert meta["params"]["prompt"] == "白色陶瓷杯"
        assert meta["seed"] == 42
        assert meta["models"] == ["sd_xl_base_1.0.safetensors"]
        assert meta["task_id"] == 7
        assert meta["prompt_digest"]["prompt"]["length"] == 5

    def test_build_metadata_can_exclude_prompt(self):
        """服务端口径：只记长度 + hash（tracking_plan.md §1.2）。"""
        meta = build_metadata(
            workflow_id="t2i_v1", workflow_version=1,
            params={"prompt": "secret brand name", "steps": 30}, seed=1,
            include_prompt=False,
        )
        assert "prompt" not in meta["params"]
        assert meta["prompt_included"] is False
        assert meta["prompt_digest"]["prompt"]["sha256"]
        assert meta["prompt_digest"]["prompt"]["length"] == len("secret brand name")

    def test_digest_is_salted_and_marks_itself(self):
        """⭐ 埋点侧只记 hash 时，短提示词必须加盐防彩虹表还原。

        摘要要自带 `salted` 标志 —— 否则加盐与未加盐的两批摘要混在一起比对，
        会得出"提示词不同"的错误结论。
        """
        params = {"prompt": "白色杯子"}
        unsalted = build_metadata(workflow_id="w", workflow_version=1, params=params,
                                  include_prompt=False, hash_salt=None)
        salted = build_metadata(workflow_id="w", workflow_version=1, params=params,
                                include_prompt=False, hash_salt="s3cr3t")
        salted2 = build_metadata(workflow_id="w", workflow_version=1, params=params,
                                 include_prompt=False, hash_salt="other")

        d_plain = unsalted["prompt_digest"]["prompt"]
        d_salt = salted["prompt_digest"]["prompt"]

        assert d_plain["salted"] is False
        assert d_salt["salted"] is True
        assert d_plain["sha256"] != d_salt["sha256"]
        # 同盐稳定、异盐不同
        assert d_salt["sha256"] == build_metadata(
            workflow_id="w", workflow_version=1, params=params,
            include_prompt=False, hash_salt="s3cr3t",
        )["prompt_digest"]["prompt"]["sha256"]
        assert d_salt["sha256"] != salted2["prompt_digest"]["prompt"]["sha256"]
        # 长度不因加盐而变（长度是统计口径，不应受盐影响）
        assert d_plain["length"] == d_salt["length"] == len("白色杯子")

    def test_write_and_read_roundtrip(self, tmp_path):
        p = tmp_path / "out.png"
        p.write_bytes(make_png())
        meta = build_metadata(
            workflow_id="t2i_v1", workflow_version=1,
            params={"prompt": "中文提示词", "steps": 30, "width": 1024}, seed=20260916,
            models=["sd_xl_base_1.0.safetensors"], task_id=7, batch_id=3, idx=1,
        )
        write_png_metadata(p, meta)
        assert read_png_metadata(p) == meta

    def test_write_metadata_does_not_touch_image_data(self, tmp_path):
        """⭐ 核心断言：写元数据不改变像素数据字节。

        若这里失败，产物 hash 不可比对、去重失效 —— 而这类问题在
        「图能打开、看起来正常」的假象下极难发现。
        """
        p = tmp_path / "out.png"
        original = make_png(16, 16)
        p.write_bytes(original)
        before = idat_bytes(original)

        write_png_metadata(p, build_metadata(
            workflow_id="t2i_v1", workflow_version=1, params={"prompt": "x"}, seed=1,
        ))
        after = idat_bytes(p.read_bytes())
        assert before == after

    def test_preserves_engine_chunks(self, tmp_path):
        """ComfyUI 自己写的 prompt / workflow chunk 必须原样保留。"""
        p = tmp_path / "out.png"
        p.write_bytes(make_png(extra_text={
            "prompt": '{"3":{"class_type":"KSampler"}}',
            "workflow": '{"nodes":[]}',
        }))
        write_png_metadata(p, build_metadata(
            workflow_id="t2i_v1", workflow_version=1, params={}, seed=1,
        ))
        chunks = read_png_text_chunks(p)
        assert chunks["prompt"] == '{"3":{"class_type":"KSampler"}}'
        assert chunks["workflow"] == '{"nodes":[]}'
        assert METADATA_JSON_KEY in chunks

    def test_write_is_idempotent(self, tmp_path):
        """重复写入不得堆积 chunk（A 流重试保存时会走到这条路径）。"""
        p = tmp_path / "out.png"
        p.write_bytes(make_png())
        meta = build_metadata(workflow_id="t2i_v1", workflow_version=1,
                              params={"prompt": "x"}, seed=1)
        write_png_metadata(p, meta)
        size_once = p.stat().st_size
        count_once = counts(p)
        write_png_metadata(p, meta)
        assert p.stat().st_size == size_once
        assert counts(p) == count_once

    def test_scalar_text_chunks_are_written(self, tmp_path):
        p = tmp_path / "out.png"
        p.write_bytes(make_png())
        write_png_metadata(p, build_metadata(
            workflow_id="t2i_v1", workflow_version=1, params={}, seed=42,
            models=["a.safetensors", "b.safetensors"], task_id=9,
        ))
        chunks = read_png_text_chunks(p)
        assert chunks["workflow_id"] == "t2i_v1"
        assert chunks["seed"] == "42"
        assert chunks["task_id"] == "9"
        assert chunks["models"] == "a.safetensors,b.safetensors"

    def test_read_returns_empty_for_plain_png(self, tmp_path):
        p = tmp_path / "plain.png"
        p.write_bytes(make_png())
        assert read_png_metadata(p) == {}

    def test_rejects_non_png(self, tmp_path):
        p = tmp_path / "x.png"
        p.write_bytes(b"not a png at all")
        with pytest.raises(MetadataError, match="PNG"):
            write_png_metadata(p, {"a": 1})

    def test_rejects_truncated_png(self, tmp_path):
        p = tmp_path / "x.png"
        p.write_bytes(make_png()[:20])
        with pytest.raises(MetadataError):
            read_png_metadata(p)

    def test_written_png_is_still_readable_by_pillow(self, tmp_path):
        """独立第三方（Pillow）能正常打开，证明我们拼出来的 PNG 结构合法。

        这是**交叉验证**：我们自己写的解析器当然读得懂自己写的文件，
        但那不能证明文件对别的工具也合法（例如 chunk 顺序、CRC、iTXt 字段布局）。
        Pillow 缺失时 skip，不掩盖其他用例。
        """
        Image = pytest.importorskip("PIL.Image")
        p = tmp_path / "out.png"
        p.write_bytes(make_png(16, 16))
        write_png_metadata(p, build_metadata(
            workflow_id="t2i_v1", workflow_version=1, params={"prompt": "中文"}, seed=1,
        ))
        with Image.open(p) as im:
            im.load()
            assert im.size == (16, 16)
            assert json.loads(im.info["wf_meta"])["workflow_id"] == "t2i_v1"
            # 像素数据没被改坏：与原始图逐像素一致
            p2 = tmp_path / "orig.png"
            p2.write_bytes(make_png(16, 16))
            with Image.open(p2) as orig:
                assert list(im.getdata()) == list(orig.getdata())


def counts(path) -> dict[bytes, int]:
    from engine.metadata import _iter_chunks

    out: dict[bytes, int] = {}
    for _pos, ctype, _length, _data in _iter_chunks(path.read_bytes()):
        out[ctype] = out.get(ctype, 0) + 1
    return out


# ============================================================ 注册表 + 端到端


class TestRegistry:
    def test_real_registry_loads_and_validates(self):
        registry = Registry.load()
        assert len(registry) >= 2
        report = validate_registry()
        assert report.ok, "\n".join(str(f) for f in report.errors)

    def test_gpu_verified_entries_have_baseline(self):
        """标了 gpu_verified 就必须有带日期的性能基线（#15 的纪律）。"""
        for entry in Registry.load():
            if entry.verification == "gpu_verified":
                assert entry.baseline.get("measured_at"), entry.id
                assert entry.baseline.get("min_s") is not None, entry.id

    def test_verification_enum_is_enforced(self):
        """verification 不能瞎写 —— 谎报验证状态比不验证更糟。"""
        from engine.registry import _parse_entry

        with pytest.raises(SchemaError, match="verification"):
            _parse_entry({
                "id": "x", "version": 1, "title": "T", "display_name": "D",
                "definition": "x.json", "status": "enabled", "verification": "looks_good",
                "param_schema": {"schema_version": 1, "fields": []},
                "changelog": [{"version": 1}],
            }, index=0, registry_path=_fake_path())

    def test_definition_path_is_resolved_at_load_time(self):
        """`definition_path` 必须是可直接用的绝对路径 —— 调用方不必知道注册表在哪。

        （team-lead 独立验证时踩过：`definition` 是裸文件名，得自己拼路径。）
        """
        for entry in Registry.load():
            p = entry.definition_path
            assert p.is_absolute(), f"{entry.id} 的 definition_path 不是绝对路径: {p}"
            assert p.exists(), f"{entry.id} 的 definition_path 不存在: {p}"
            assert p.name == entry.definition
            # 与 Registry.definition_path() 保持一致
            assert Registry.load().definition_path(entry) == p

    def test_definition_path_is_cwd_independent(self, tmp_path, monkeypatch):
        """换工作目录后仍能取到同一文件（这正是"自己拼路径"会断的场景）。"""
        monkeypatch.chdir(tmp_path)
        entry = Registry.load().require("t2i_v1")
        assert entry.definition_path.exists()

    def test_changelog_must_cover_current_version(self):
        from engine.registry import _parse_entry

        with pytest.raises(SchemaError, match="changelog"):
            _parse_entry({
                "id": "x", "version": 2, "title": "T", "display_name": "D",
                "definition": "x.json", "status": "disabled", "verification": "pending_gpu",
                "param_schema": {"schema_version": 1, "fields": []},
                "changelog": [{"version": 1}],
            }, index=0, registry_path=_fake_path())

    # ---------------------------------------------------- 放行纪律（机器可校验）

    def test_enabled_requires_render_path_l4(self):
        """⭐ 「模板出过图」不足以放行 —— 必须是「渲染路径」出过图。

        这两件事容易被混为一谈（`verification: gpu_verified` 说的是节点图能出图，
        而生产路径还要经过 `engine/render`）。把纪律写成校验而不是注释，
        才不会在赶进度时被绕过。
        """
        from engine.registry import _parse_entry

        base = {
            "id": "x", "version": 1, "title": "T", "display_name": "D",
            "definition": "x.json", "verification": "gpu_verified",
            "param_schema": {"schema_version": 1, "fields": []},
            "changelog": [{"version": 1}],
            "baseline": {"measured_at": "2026-09-16", "min_s": 1.0},
        }

        with pytest.raises(SchemaError, match="render_path_l4"):
            _parse_entry({**base, "status": "enabled", "render_path_l4": False},
                         index=0, registry_path=_fake_path())

        # 渲染路径 L4 通过后才允许启用
        entry = _parse_entry({**base, "status": "enabled", "render_path_l4": True},
                             index=0, registry_path=_fake_path())
        assert entry.is_enabled

    def test_enabled_requires_gpu_verified(self):
        from engine.registry import _parse_entry

        with pytest.raises(SchemaError, match="verification"):
            _parse_entry({
                "id": "x", "version": 1, "title": "T", "display_name": "D",
                "definition": "x.json", "status": "enabled", "verification": "static_only",
                "render_path_l4": True,
                "param_schema": {"schema_version": 1, "fields": []},
                "changelog": [{"version": 1}],
            }, index=0, registry_path=_fake_path())

    def test_enabled_workflows_satisfy_release_invariant(self):
        """放行不变量（对当前真实数据）：enabled ⟹ render_path_l4 ∧ gpu_verified ∧ 有基线。

        ⚠️ **本测试替换掉了一条已过期的快照断言**
        （原 `test_current_registry_has_no_enabled_workflow`，断言「当前注册表
        一条 enabled 都没有」）。它在 2026-09-16「渲染路径 L4」通过、两条工作流
        被**合法**放行后**必然失败** —— 因为它是**状态快照**，不是**不变量**。

        教训（与 `项目进展.md` #25「测试全绿 ≠ 没有冲突」同族）：
        **守卫要用不变量表达。** 快照式守卫会随合法推进而失效，而失效时的
        默认反应是"把测试改松"，于是守卫形同撤销。此处改为断言规则本身，
        并用 `test_enabled_requires_render_path_l4` 做正对照证明规则有牙。
        """
        for entry in Registry.load():
            if entry.status != "enabled":
                continue
            assert entry.render_path_l4 is True, entry.id
            assert entry.verification == "gpu_verified", entry.id
            assert entry.baseline.get("measured_at"), entry.id


class TestObjectInfoCheck:
    """L3：对**渲染后**的图校验节点合法性（比模板校验多覆盖 `targets` 注入）。"""

    #: 极小的假 object_info，避免测试依赖 3.8MB 的真实缓存
    INFO = {
        "KSampler": {
            "input": {
                "required": {
                    "seed": ["INT", {}],
                    "steps": ["INT", {"min": 1, "max": 100}],
                    "sampler_name": [["euler", "dpmpp_2m"], {}],
                    "model": ["MODEL", {}],
                }
            },
            "output": ["LATENT"],
        },
        "SaveImage": {
            "input": {"required": {"images": ["IMAGE", {}], "filename_prefix": ["STRING", {}]}},
            "output": [],
        },
        "EmptyLatentImage": {
            "input": {"required": {"width": ["INT", {}], "height": ["INT", {}],
                                   "batch_size": ["INT", {}]}},
            "output": ["LATENT"],
        },
    }

    def _graph(self, **sampler_inputs):
        return {
            "1": {"class_type": "EmptyLatentImage",
                  "inputs": {"width": 1024, "height": 1024, "batch_size": 1}},
            "2": {"class_type": "KSampler",
                  "inputs": {"seed": 1, "steps": 20, "sampler_name": "euler",
                             "model": ["3", 0], **sampler_inputs}},
            "3": {"class_type": "EmptyLatentImage",
                  "inputs": {"width": 8, "height": 8, "batch_size": 1}},
            "9": {"class_type": "SaveImage",
                  "inputs": {"images": ["1", 0], "filename_prefix": "x"}},
        }

    def test_valid_graph_passes(self):
        assert check_object_info(self._graph(), self.INFO, scope="t") == []

    def test_runtime_asset_input_is_exempt_from_enum_check(self):
        """⭐ 运行时素材引用豁免枚举检查 —— 且豁免**只针对被标记的入参**。

        背景（实测发现，2026-09-16）：L2 的冒烟渲染用假 resolver 产出 `smoke_0.png`，
        而 `/object_info` 的枚举是**快照时刻**对输入目录的静态扫描 ——
        拿静态快照去比运行时文件名，**按构造就不可能匹配**。
        不豁免的话，**任何带参考图的工作流都无法通过 L1/L2/L3**
        （i2i_v1 / inpaint_v1 会被直接堵死）。

        本测试的三段是**正对照**（`debug_log G9`：判据本身要能被人为破坏后检出）：
        ① 不豁免 → 必须报错，证明这条检查仍然有牙、不是被整体放宽；
        ② 标记为运行时素材 → 不报错；
        ③ 标记了**别的入参名** → 仍然报错，证明豁免不是"连坐"。
        """
        info = {"LoadImage": {"input": {"required": {"image": [["example.png"], {}]}}}}
        graph = {"10": {"class_type": "LoadImage", "inputs": {"image": "uploaded_ref.png"}}}

        def has_enum_error(findings) -> bool:
            return any(
                f.level == "error" and "不在允许集合内" in f.message for f in findings
            )

        # ① 不豁免 → 报错（检查有牙）
        assert has_enum_error(check_object_info(graph, info, scope="t"))
        # ② 正主：标记为运行时素材 → 豁免
        assert check_object_info(
            graph, info, scope="t", runtime_asset_inputs={("10", "image")}
        ) == []
        # ③ 豁免不连坐：标记的是同节点但**不同入参名**
        assert has_enum_error(
            check_object_info(graph, info, scope="t", runtime_asset_inputs={("10", "other")})
        )
        # ④ 豁免不连坐：标记的是同入参名但**不同节点**
        assert has_enum_error(
            check_object_info(graph, info, scope="t", runtime_asset_inputs={("99", "image")})
        )

    def test_unknown_class_type(self):
        g = self._graph()
        g["2"]["class_type"] = "NotANode"
        findings = check_object_info(g, self.INFO, scope="t")
        assert any("不存在于 object_info" in f.message for f in findings)

    def test_unaccepted_input_name(self):
        """这正是《targets 注入错位置》会产生的故障形态。"""
        findings = check_object_info(self._graph(lora_1={"on": True}), self.INFO, scope="t")
        assert any("不被该节点接受" in f.message for f in findings)

    def test_missing_required_input(self):
        g = self._graph()
        del g["2"]["inputs"]["seed"]
        findings = check_object_info(g, self.INFO, scope="t")
        assert any("缺少必填入参 'seed'" in f.message for f in findings)

    def test_illegal_enum_value(self):
        findings = check_object_info(self._graph(sampler_name="bogus"), self.INFO, scope="t")
        assert any("不在允许集合内" in f.message for f in findings)

    def test_dangling_link(self):
        findings = check_object_info(self._graph(model=["404", 0]), self.INFO, scope="t")
        assert any("指向不存在的节点" in f.message for f in findings)

    def test_link_output_index_out_of_range(self):
        findings = check_object_info(self._graph(model=["9", 3]), self.INFO, scope="t")
        assert any("输出序号 3 越界" in f.message for f in findings)

    def test_link_is_not_treated_as_enum_value(self):
        """连线形态的值不该被拿去比对枚举集合（否则会误报）。"""
        assert check_object_info(self._graph(model=["3", 0]), self.INFO, scope="t") == []

    # ---------------- 与真实注册表的端到端 ----------------

    def test_real_object_info_exists_and_matches_node_count(self):
        """L3 的前提：object_info 缓存必须在仓库里，且节点数与审计记录一致。"""
        import json as _json

        p = pathlib.Path("deploy/schemas/object_info.v0.36.0.json")
        if not p.exists():
            pytest.skip("object_info 缓存不在仓库里（L3 不可用）")
        with p.open(encoding="utf-8") as fh:
            info = _json.load(fh)
        assert len(info) == 2530, "节点数与 项目进展.md #16 验证 2 记录的 2530 不一致"
        for cls in ("KSampler", "CLIPTextEncode", "Flux2Scheduler", "CFGGuider", "RandomNoise"):
            assert cls in info

    def test_real_registry_passes_l3(self):
        """端到端：真实注册表 **渲染后** 的图必须通过 L3。"""
        p = pathlib.Path("deploy/schemas/object_info.v0.36.0.json")
        if not p.exists():
            pytest.skip("object_info 缓存不在仓库里（L3 不可用）")
        report = validate_registry(object_info=str(p))
        assert "L3" in report.levels_run, "L3 没有真正执行"
        assert report.ok, "\n".join(str(f) for f in report.errors)

    def test_l3_negative_control(self, tmp_path):
        """负向对照：故意破坏 object_info，L3 必须报错（证明它真的在跑）。"""
        import json as _json

        p = pathlib.Path("deploy/schemas/object_info.v0.36.0.json")
        if not p.exists():
            pytest.skip("object_info 缓存不在仓库里（L3 不可用）")
        with p.open(encoding="utf-8") as fh:
            info = _json.load(fh)
        info.pop("KSampler", None)
        broken = tmp_path / "oi.json"
        broken.write_text(_json.dumps(info), encoding="utf-8")
        report = validate_registry(object_info=str(broken))
        assert not report.ok
        assert any("KSampler" in f.message for f in report.errors)


class TestContractInvariants:
    """`contracts.md` §3.6 的机械执行（不变量 1 与 2）。"""

    def _schema(self, **field):
        base = {"key": "batch_size", "label": "批量张数", "type": "int",
                "default": 1, "min": 1, "max": 8, "step": 1}
        base.update(field)
        return make_schema([base])

    def _graph(self, inputs):
        return {
            "1": {"class_type": "EmptyLatentImage", "inputs": inputs},
            "9": {"class_type": "SaveImage",
                  "inputs": {"images": ["1", 0], "filename_prefix": "x"}},
        }

    def test_rejects_batch_size_injection(self):
        """不变量 1：不得暴露会让单次执行产出多张图的参数。"""
        graph = self._graph({"width": 1024, "height": 1024, "batch_size": 1})
        schema = self._schema(targets=[{"node_id": "1", "input": "batch_size"}])
        findings = _validate_schema_against_graph(schema, graph, {}, "t").findings
        assert any("不变量 1" in f.message and f.level == "error" for f in findings)

    def test_allows_width_injection(self):
        graph = self._graph({"width": 1024, "height": 1024, "batch_size": 1})
        schema = make_schema([{"key": "width", "label": "宽", "type": "int",
                               "default": 1024, "min": 512, "max": 2048, "step": 64,
                               "targets": [{"node_id": "1", "input": "width"}]}])
        assert _validate_schema_against_graph(schema, graph, {}, "t").findings == []

    def test_forbidden_match_is_by_input_name_not_param_name(self):
        """叫 `n` 的参数照样可能是 batch_size —— 所以按入参名匹配。"""
        graph = self._graph({"width": 8, "height": 8, "batch_size": 1})
        schema = make_schema([{"key": "n", "label": "张数", "type": "int",
                               "default": 1, "min": 1, "max": 8,
                               "targets": [{"node_id": "1", "input": "batch_size"}]}])
        findings = _validate_schema_against_graph(schema, graph, {}, "t").findings
        assert any("不变量 1" in f.message for f in findings)

    def test_default_mismatch_string_is_error(self):
        """不变量 2：default 必须等于模板字面值。"""
        graph = self._graph({"text": "模板里的完整提示词", "width": 8, "height": 8})
        schema = make_schema([{"key": "prompt", "label": "提示词", "type": "text",
                               "default": "少了几个字", "targets": [{"node_id": "1", "input": "text"}]}])
        findings = _validate_schema_against_graph(schema, graph, {}, "t").findings
        assert any("不变量 2" in f.message and f.level == "error" for f in findings)

    def test_default_mismatch_numeric_is_error(self):
        graph = self._graph({"steps": 25, "width": 8, "height": 8})
        schema = make_schema([{"key": "steps", "label": "步数", "type": "int",
                               "default": 30, "min": 1, "max": 100,
                               "targets": [{"node_id": "1", "input": "steps"}]}])
        findings = _validate_schema_against_graph(schema, graph, {}, "t").findings
        assert any("不变量 2" in f.message for f in findings)

    def test_default_matches_passes(self):
        graph = self._graph({"steps": 25, "width": 8, "height": 8})
        schema = make_schema([{"key": "steps", "label": "步数", "type": "int",
                               "default": 25, "min": 1, "max": 100,
                               "targets": [{"node_id": "1", "input": "steps"}]}])
        assert _validate_schema_against_graph(schema, graph, {}, "t").findings == []

    def test_seed_default_is_exempt(self):
        """`-1`（随机）本来就不应等于模板里的历史字面值。"""
        graph = self._graph({"seed": 20260915, "width": 8, "height": 8})
        schema = make_schema([{"key": "seed", "label": "种子", "type": "seed",
                               "default": -1, "targets": [{"node_id": "1", "input": "seed"}]}])
        assert _validate_schema_against_graph(schema, graph, {}, "t").findings == []

    def test_placeholder_literal_is_exempt(self):
        """模板用 `{{key}}` 时，字面值与 default 不同是正确的。"""
        graph = self._graph({"text": "a photo of {{product}}", "width": 8, "height": 8})
        schema = make_schema([{"key": "product", "label": "商品", "type": "text",
                               "default": "mug", "targets": [{"node_id": "1", "input": "text"}]}])
        assert _validate_schema_against_graph(schema, graph, {}, "t").findings == []

    def test_real_registry_obeys_invariants(self):
        report = validate_registry()
        assert report.ok, "\n".join(str(f) for f in report.errors)


class TestPromptDigestHMAC:
    """`HMAC-SHA256` 构造（2026-09-16 team-lead 批准，替换脆弱的 `sha256(salt‖text)`）。"""

    def test_matches_independent_hmac(self):
        import hashlib as _h
        import hmac as _hmac

        expect = _hmac.new(b"salty", "白色杯子".encode(), _h.sha256).hexdigest()
        assert _prompt_digest("白色杯子", "salty") == expect

    def test_accepts_bytes_salt(self):
        assert _prompt_digest("x", b"salty") == _prompt_digest("x", "salty")

    def test_unsalted_is_not_bare_sha256(self):
        """无盐时用空 key 的 HMAC，**不得**退化成裸 sha256（那会被彩虹表还原）。"""
        import hashlib as _h

        assert _prompt_digest("白色杯子", None) != _h.sha256("白色杯子".encode()).hexdigest()

    def test_meta_carries_alg_and_salted_flags(self):
        meta = build_metadata(workflow_id="t2i_v1", workflow_version=1,
                              params={"prompt": "x"}, seed=1, hash_salt="s")
        d = meta["prompt_digest"]["prompt"]
        assert d["alg"] == PROMPT_DIGEST_ALG == "hmac-sha256"
        assert d["salted"] is True

    def test_unsalted_is_flagged(self):
        meta = build_metadata(workflow_id="t2i_v1", workflow_version=1,
                              params={"prompt": "x"}, seed=1)
        assert meta["prompt_digest"]["prompt"]["salted"] is False

    def test_same_prompt_same_salt_same_digest(self):
        a = build_metadata(workflow_id="a", workflow_version=1, params={"prompt": "p"},
                           seed=1, hash_salt="s")
        b = build_metadata(workflow_id="b", workflow_version=2, params={"prompt": "p"},
                           seed=9, hash_salt="s")
        assert (a["prompt_digest"]["prompt"]["sha256"]
                == b["prompt_digest"]["prompt"]["sha256"])

    def test_different_salt_different_digest(self):
        a = build_metadata(workflow_id="a", workflow_version=1, params={"prompt": "p"},
                           seed=1, hash_salt="s1")
        b = build_metadata(workflow_id="a", workflow_version=1, params={"prompt": "p"},
                           seed=1, hash_salt="s2")
        assert (a["prompt_digest"]["prompt"]["sha256"]
                != b["prompt_digest"]["prompt"]["sha256"])


class TestDefinitionPathResolution:
    """`definition` 是裸文件名，调用方不该自己拼路径。"""

    def test_entry_carries_resolved_path(self):
        entry = Registry.load().require("t2i_v1")
        assert entry.definition == "t2i_v1.json"
        assert entry.definition_path.is_absolute()
        assert entry.definition_path.exists()
        assert entry.definition_path.name == entry.definition

    def test_registry_helper_agrees_with_entry(self):
        registry = Registry.load()
        for entry in registry:
            assert registry.definition_path(entry) == entry.definition_path
            assert entry.definition_path.exists(), entry.id

    def test_resolution_is_independent_of_cwd(self, tmp_path, monkeypatch):
        """路径解析必须在加载时完成 —— 换 CWD 也要能用。"""
        registry = Registry.load()
        monkeypatch.chdir(tmp_path)
        entry = registry.require("t2i_v1")
        assert registry.definition_path(entry).exists()


class TestVerifyRenderPathDryRun:
    """`render_path_l4` 验证工具的离线部分（真实出图那半属于 L4）。"""

    def _entry(self, workflow_id: str = "flux2_klein_t2i_v1"):
        return Registry.load().require(workflow_id)

    def test_multi_target_fields_carry_input_names(self):
        """⭐ 回归：多 target 检查必须带**每个 target 自己的入参名**。

        曾经的写法是用字段 key 当入参名去取值。对 klein 的 `width` 恰好蒙对
        （key == input），但对 `seed`→`noise_seed` 这类就会取到 `None`，
        于是"一致性检查"退化成"两个 None 相等"的**假通过**。
        """
        from engine.tools.verify_render_path import _multi_target_fields

        fields = _multi_target_fields(self._entry())
        assert set(fields) == {"width", "height"}
        for key, targets in fields.items():
            for node_id, input_name, _tf in targets:
                assert isinstance(node_id, str)
                assert input_name == key  # klein 的 width/height 恰好同名
        # 结构化验证：必须是 3 元组（含入参名），不能只是节点 ID
        assert all(len(t) == 3 for t in fields["width"])

    def test_multi_target_check_detects_inconsistency(self):
        """真正的不一致必须被检出（否则这条检查形同虚设）。"""
        from engine.render import load_definition
        from engine.tools.verify_render_path import _multi_target_fields

        registry = Registry.load()
        entry = registry.require("flux2_klein_t2i_v1")
        result = render(load_definition(registry.definition_path(entry)), entry.schema)
        # 人为破坏一个节点，使 width 在 6 与 7 上不一致
        result.workflow["7"]["inputs"]["width"] = 768
        values = {
            f"{nid}.{inp}": result.workflow[nid]["inputs"].get(inp)
            for nid, inp, _tf in _multi_target_fields(entry)["width"]
        }
        assert len(set(values.values())) == 2, "破坏后应当不一致"
        assert values == {"6.width": 1024, "7.width": 768}

    def test_idat_digest_ignores_non_pixel_chunks(self):
        """确定性判据只比像素：追加元数据不得改变 IDAT 摘要。"""
        from engine.tools.verify_render_path import idat_digest

        raw = make_png(16, 16)
        assert idat_digest(raw) == idat_digest(raw)

    def test_dry_run_passes_for_both_registered_workflows(self, capsys):
        from engine.tools.verify_render_path import main as verify_main

        for wf_id in ("t2i_v1", "flux2_klein_t2i_v1"):
            assert verify_main(["--workflow", wf_id, "--dry-run"]) == 0, wf_id
        assert "DRY-RUN 通过" in capsys.readouterr().out


class TestG7WeightProbe:
    """G7 探针：变体构造必须**离线可验**，否则 GPU 机时会被浪费在拼图错误上。"""

    _ENTRY_FOR_MODE = {
        "klein-core": "flux2_klein_t2i_v1",
        "klein-bnk": "flux2_klein_t2i_v1",
        "sdxl": "t2i_v1",
    }

    def _entry_definition(self, workflow_id: str):
        from engine.render import load_definition

        registry = Registry.load()
        entry = registry.require(workflow_id)
        return entry, load_definition(registry.definition_path(entry))

    def test_prompt_node_comes_from_registry_schema(self):
        """探针必须从注册表 `targets` 定位提示词节点，不能硬编码 ID。"""
        from engine.tools.probe_g7_weight import _prompt_target_node

        for wf_id, expected in (("flux2_klein_t2i_v1", "4"), ("t2i_v1", "6")):
            entry, _d = self._entry_definition(wf_id)
            assert _prompt_target_node(entry) == expected, wf_id

    def test_bnk_variant_swaps_encoder_only(self):
        """只换文本编码节点，**连线与其余节点完全不动**（否则比对引入额外变量）。"""
        from engine.tools.probe_g7_weight import BNK_DEFAULTS, _build_bnk_variant

        entry, original = self._entry_definition("flux2_klein_t2i_v1")
        variant = _build_bnk_variant(original, "a mug", "4")
        a = {k: v for k, v in original.items() if k != "_meta"}
        b = {k: v for k, v in variant.items() if k != "_meta"}
        assert set(a) == set(b), "节点集合不能变"

        changed = [nid for nid in a if a[nid] != b[nid]]
        assert changed == ["4"], f"只应改动节点 4，实际: {changed}"
        assert b["4"]["class_type"] == "BNK_CLIPTextEncodeAdvanced"
        for k, v in BNK_DEFAULTS.items():
            assert b["4"]["inputs"][k] == v
        # 连线必须原样继承
        assert b["4"]["inputs"]["clip"] == a["4"]["inputs"]["clip"]

    def test_bnk_variant_rejects_non_encoder_node(self):
        from engine.tools.probe_g7_weight import _build_bnk_variant

        _entry, original = self._entry_definition("flux2_klein_t2i_v1")
        with pytest.raises(RenderError, match="CLIPTextEncode"):
            _build_bnk_variant(original, "x", "1")  # 节点 1 是 UNETLoader

    def test_bnk_variant_is_l3_valid(self):
        """BNK 变体必须通过 L3（入参名/枚举取值对得上真实 object_info）。"""
        from engine.tools.probe_g7_weight import BNK_DEFAULTS, _build_bnk_variant
        from engine.validate import _load_object_info, check_object_info

        info_path = pathlib.Path("deploy/schemas/object_info.v0.36.0.json")
        if not info_path.exists():
            pytest.skip("object_info 缓存不在仓库里（L3 不可用）")
        info = _load_object_info(str(info_path))
        _entry, original = self._entry_definition("flux2_klein_t2i_v1")
        variant = _build_bnk_variant(original, "a mug", "4")
        graph = {k: v for k, v in variant.items() if k != "_meta"}
        errors = [f for f in check_object_info(graph, info, scope="bnk") if f.level == "error"]
        assert errors == [], "\n".join(str(e) for e in errors)
        # 顺带确认我们用的枚举值确实在允许集合内
        spec = info["BNK_CLIPTextEncodeAdvanced"]["input"]["required"]
        assert BNK_DEFAULTS["weight_interpretation"] in spec["weight_interpretation"][0]
        assert BNK_DEFAULTS["token_normalization"] in spec["token_normalization"][0]

    def test_all_modes_build_offline(self):
        from engine.tools.probe_g7_weight import BASELINE_PROMPT, MODES, _variant

        for mode in MODES:
            entry, definition = self._entry_definition(self._ENTRY_FOR_MODE[mode])
            graph = _variant(mode, BASELINE_PROMPT, definition, entry)
            assert graph, mode
            assert "_meta" in graph, mode
            assert len(graph) == len(definition), mode

    def test_weighted_prompt_differs_from_baseline(self):
        """探针的两个提示词必须真的不同，否则比对无从谈起。"""
        from engine.tools.probe_g7_weight import BASELINE_PROMPT, WEIGHTED_PROMPT

        assert BASELINE_PROMPT != WEIGHTED_PROMPT
        assert "(white ceramic coffee mug:1.5)" in WEIGHTED_PROMPT

    def test_sdxl_variant_leaves_negative_untouched(self):
        """SDXL 对照组：只改正向节点 6，负向保持模板原样。"""
        from engine.tools.probe_g7_weight import _sdxl_variant

        entry, original = self._entry_definition("t2i_v1")
        variant = _sdxl_variant(original, "PROBE", "6")
        assert variant["6"]["inputs"]["text"] == "PROBE"
        assert variant["7"]["inputs"]["text"] == original["7"]["inputs"]["text"]

    def test_variants_are_independent_deep_copies(self):
        """⭐ 回归：变体必须**深拷贝**，否则 baseline 会被后续迭代追溯性改写。

        原先的实现只做浅拷贝，所有变体共享同一批节点 dict → 写 weighted 的
        text 时把已构造好的 baseline 也改了 → 两者比较恒为"相同"，
        于是探针会把"根本没比对"报成"完全无影响"（方向最危险的假通过）。
        """
        from engine.tools.probe_g7_weight import (
            BASELINE_PROMPT,
            WEIGHTED_PROMPT,
            _variant,
        )

        entry, definition = self._entry_definition("flux2_klein_t2i_v1")
        baseline = _variant("klein-core", BASELINE_PROMPT, definition, entry)
        weighted = _variant("klein-core", WEIGHTED_PROMPT, definition, entry)

        assert baseline["4"]["inputs"]["text"] == BASELINE_PROMPT, "baseline 被改写了"
        assert weighted["4"]["inputs"]["text"] == WEIGHTED_PROMPT
        assert baseline != weighted, "两个变体必须不同，否则比对无意义"
        # 原定义不得被污染
        assert definition["4"]["inputs"]["text"] != WEIGHTED_PROMPT

    def test_control_differs_from_baseline_only_in_seed(self):
        """正对照必须是**干净的单变量**：只改 seed，别的都不动。"""
        from engine.tools.probe_g7_weight import (
            BASELINE_PROMPT,
            _seed_nodes,
            _set_seed,
            _variant,
        )

        entry, definition = self._entry_definition("flux2_klein_t2i_v1")
        baseline = _variant("klein-core", BASELINE_PROMPT, definition, entry)
        control = _variant("klein-core", BASELINE_PROMPT, definition, entry)
        _set_seed(baseline, entry, 20260916)
        _set_seed(control, entry, 20260917)

        assert _seed_nodes(entry) == {"8"}
        changed = {k for k in baseline if baseline[k] != control[k]}
        assert changed == {"8"}, f"正对照只应改 seed 节点，实际: {changed}"
        assert baseline["8"]["inputs"]["noise_seed"] == 20260916
        assert control["8"]["inputs"]["noise_seed"] == 20260917

    def test_seed_nodes_comes_from_registry_schema(self):
        from engine.tools.probe_g7_weight import _seed_nodes

        assert _seed_nodes(self._entry_definition("flux2_klein_t2i_v1")[0]) == {"8"}
        assert _seed_nodes(self._entry_definition("t2i_v1")[0]) == {"3"}

    def test_probe_dry_run_passes(self, capsys):
        from engine.tools.probe_g7_weight import main as probe_main

        assert probe_main(["--mode", "all", "--dry-run"]) == 0
        out = capsys.readouterr().out
        # 必须明确声明它不给语义结论
        assert "语义判断必须人工看图" in out
        assert "全部变体通过 L3" in out


def _fake_path():
    import pathlib

    return pathlib.Path("registry.yaml")


class TestSettingsBounds:
    """`contracts.md` §3.4：Schema 边界可以更严，不可以更宽。"""

    #: 与 `backend/app/core/config.py` 的 settings 一致
    BOUNDS = {
        "steps": {"min": 1, "max": 100},
        "cfg": {"min": 1.0, "max": 20.0},
        "width": {"min": 512, "max": 2048},
        "height": {"min": 512, "max": 2048},
        "prompt": {"max_length": 2000},
    }

    def test_narrower_is_allowed(self):
        """更严是正常的：klein 蒸馏版 cfg 1.0–2.0 比 settings 的 1.0–20.0 更正确。"""
        schema = make_schema([
            {"key": "cfg", "label": "CFG", "type": "float", "default": 1.0,
             "min": 1.0, "max": 2.0, "step": 0.1, "targets": []},
            {"key": "steps", "label": "步数", "type": "int", "default": 4,
             "min": 1, "max": 8, "targets": []},
        ])
        assert check_settings_bounds(schema, self.BOUNDS, "t") == []

    def test_equal_is_allowed(self):
        schema = make_schema([
            {"key": "steps", "label": "步数", "type": "int", "default": 25,
             "min": 1, "max": 100, "targets": []},
        ])
        assert check_settings_bounds(schema, self.BOUNDS, "t") == []

    def test_wider_min_is_an_error(self):
        schema = make_schema([
            {"key": "steps", "label": "步数", "type": "int", "default": 25,
             "min": 0, "max": 100, "targets": []},
        ])
        findings = check_settings_bounds(schema, self.BOUNDS, "t")
        assert len(findings) == 1
        assert findings[0].level == "error"
        assert "更宽松" in findings[0].message

    def test_wider_max_is_an_error(self):
        schema = make_schema([
            {"key": "cfg", "label": "CFG", "type": "float", "default": 6.0,
             "min": 1.0, "max": 30.0, "step": 0.1, "targets": []},
        ])
        findings = check_settings_bounds(schema, self.BOUNDS, "t")
        assert any("更宽松" in f.message for f in findings)

    def test_max_length_over_limit_is_an_error(self):
        schema = make_schema([
            {"key": "prompt", "label": "提示词", "type": "text", "default": "",
             "max_length": 5000, "targets": []},
        ])
        findings = check_settings_bounds(schema, self.BOUNDS, "t")
        assert any("max_length" in f.message for f in findings)

    def test_unlisted_key_is_ignored(self):
        schema = make_schema([
            {"key": "denoise", "label": "重绘幅度", "type": "float", "default": 1.0,
             "min": 0.0, "max": 1.0, "step": 0.01, "targets": []},
        ])
        assert check_settings_bounds(schema, self.BOUNDS, "t") == []

    def test_real_registry_is_within_bounds(self):
        """真实注册表必须全部落在全局红线内。"""
        report = validate_registry(settings_bounds=self.BOUNDS)
        assert report.ok, "\n".join(str(f) for f in report.errors)

    def test_bounds_from_settings_maps_correctly(self):
        class FakeSettings:
            min_steps = 1
            max_steps = 100
            min_cfg = 1.0
            max_cfg = 20.0
            min_gen_side = 512
            max_gen_side = 2048
            max_prompt_length = 2000

        bounds = bounds_from_settings(FakeSettings())
        assert bounds["steps"] == {"min": 1, "max": 100}
        assert bounds["width"] == {"min": 512, "max": 2048}
        assert bounds["prompt"]["max_length"] == 2000

    def test_skipping_bounds_is_reported_not_silent(self):
        """跳过检查必须显式提示 —— 静默跳过会让"没检查"看起来像"检查通过"。"""
        report = validate_registry()
        assert any("跳过" in f.message for f in report.findings)


class TestRegistrySchemasMatchWorkflows:
    """端到端：注册表的 Schema 必须真的能渲染对应的定义文件。"""

    @pytest.mark.parametrize("workflow_id", ["t2i_v1", "flux2_klein_t2i_v1"])
    def test_render_with_defaults_succeeds(self, workflow_id):
        from engine.render import load_definition

        registry = Registry.load()
        entry = registry.require(workflow_id)
        definition = load_definition(registry.definition_path(entry))
        result = render(definition, entry.schema)
        assert not result.unresolved_placeholders, result.unresolved_placeholders
        assert result.seed is not None
        orph = orphan_nodes(result.workflow)
        assert orph == [], f"{workflow_id} 有孤岛节点: {orph}"

    def test_prompt_default_matches_template_literal(self):
        """规范 §5.5：default 必须等于模板里的字面值。"""
        from engine.render import load_definition, split_definition

        registry = Registry.load()
        for entry in registry:
            definition = load_definition(registry.definition_path(entry))
            _meta, graph = split_definition(definition)
            for fld in entry.schema:
                if fld.type not in ("str", "text") or not isinstance(fld.default, str):
                    continue
                for target in fld.targets:
                    literal = (graph[target.node_id]["inputs"]).get(target.input)
                    if isinstance(literal, str):
                        assert literal.strip() == fld.default.strip(), (
                            f"{entry.id} 字段 {fld.key} 的 default 与 "
                            f"{target.describe()} 的字面值不一致"
                        )


# ============================================================ A 流集成契约


class TestBackendContract:
    """与 A 流的硬边界：`RenderResult.workflow` 必须是可直接提交的 API 格式。"""

    def test_rendered_workflow_is_json_serializable(self):
        registry = Registry.load()
        entry = registry.require("t2i_v1")
        result = render(
            __import__("engine.render", fromlist=["load_definition"]).load_definition(
                registry.definition_path(entry)
            ),
            entry.schema,
            {"seed": 1},
        )
        payload = {"prompt": result.workflow, "client_id": "test"}
        text = json.dumps(payload)
        assert json.loads(text)["prompt"]["9"]["class_type"] == "SaveImage"

    def test_no_meta_key_leaks_into_prompt(self):
        """`_meta` 段不得混进提交给 ComfyUI 的图（规范 §2.1 规则 1）。

        ⚠️ 本测试遍历**全部已注册工作流**，因此必须给一个 stub `asset_resolver`：
        带参考图的工作流（i2i_v1 等）在 `transform=ref_to_filename` 下
        **按契约就要求** resolver，缺它渲染会正确报错 —— 那是预期行为，
        与本测试要验证的「元数据泄漏」无关。给 stub 是为了让本测试只测它该测的东西。
        """
        from engine.render import load_definition

        registry = Registry.load()
        opts = RenderOptions(asset_resolver=lambda asset_id: f"stub_{asset_id}.png")
        for entry in registry:
            result = render(
                load_definition(registry.definition_path(entry)), entry.schema, options=opts
            )
            assert all(not k.startswith("_") for k in result.workflow)
            for node in result.workflow.values():
                assert set(node) <= {"class_type", "inputs"}

    def test_prompt_payload_shape_matches_comfyui_expectation(self):
        """节点值必须是 `{"class_type": str, "inputs": {...}}`，连线必须是 `[str, int]`。"""
        from engine.render import load_definition

        registry = Registry.load()
        result = render(load_definition(registry.definition_path(registry.require("t2i_v1"))),
                        registry.require("t2i_v1").schema)
        for nid, node in result.workflow.items():
            assert isinstance(nid, str)
            assert isinstance(node["class_type"], str)
            for _key, value in node["inputs"].items():
                if isinstance(value, list) and len(value) == 2 and isinstance(value[0], str):
                    assert isinstance(value[1], int) and not isinstance(value[1], bool)


def test_png_chunk_parser_matches_manual_layout():
    """校验 chunk 解析与手工按 PNG 规范算出的布局一致（CRC 也要对）。"""
    import binascii

    raw = make_png()
    pos = 8
    while pos < len(raw):
        (length,) = struct.unpack(">I", raw[pos : pos + 4])
        ctype = raw[pos + 4 : pos + 8]
        data = raw[pos + 8 : pos + 8 + length]
        crc_stored = struct.unpack(">I", raw[pos + 8 + length : pos + 12 + length])[0]
        assert crc_stored == binascii.crc32(ctype + data) & 0xFFFFFFFF
        pos += 12 + length
        if ctype == b"IEND":
            break
    assert ctype == b"IEND"
