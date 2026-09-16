"""`app.services.workflow_models` 纯函数单测（C1 可复现性 / FR-5.4）。

为什么单独一个文件（而不是并进 `test_worker.py`）：`extract_models` 是**纯函数**，
不碰 DB / 存储 / 引擎，不该为了测它去拖 worker 那一整套假依赖。
这里只测判据本身（什么算模型、顺序、坏形状），端到端的落库断言放在
`test_worker.py`（那里才有 `FakeDriver` / `make_executor` 等骨架）。

判据的出处是 `deploy/schemas/object_info.v0.36.0.json`（ComfyUI v0.36.0 的
`/object_info` 导出）—— 下面用到“几个槽位”的地方，数字都直接取自那份快照。
"""

from __future__ import annotations

import pytest

from app.services.workflow_models import extract_models

# ---------------------------------------------------------------- 基本提取


def test_extracts_checkpoint_from_simple_loader() -> None:
    """最典型的一条：`CheckpointLoaderSimple.ckpt_name`。"""
    definition = {
        "_meta": {},
        "4": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": "sd_xl_base_1.0.safetensors"},
        },
    }

    assert extract_models(definition) == [
        {
            "node": "4",
            "class_type": "CheckpointLoaderSimple",
            "input": "ckpt_name",
            "name": "sd_xl_base_1.0.safetensors",
        }
    ]


def test_extracts_every_loader_family_used_by_this_project() -> None:
    """本项目会用到的各 loader 族都要覆盖（入参名逐条对照 `object_info` 快照）。"""
    definition = {
        "_meta": {},
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": "flux-2-klein-4b.safetensors"}},
        "2": {"class_type": "VAELoader", "inputs": {"vae_name": "flux2-vae.safetensors"}},
        "3": {"class_type": "CLIPLoader", "inputs": {"clip_name": "qwen_3_4b.safetensors"}},
        "4": {"class_type": "ControlNetLoader", "inputs": {"control_net_name": "canny.safetensors"}},
        "5": {"class_type": "IPAdapterModelLoader", "inputs": {"ipadapter_file": "ip.bin"}},
        "6": {"class_type": "UpscaleModelLoader", "inputs": {"model_name": "4x.pth"}},
        "7": {"class_type": "LoraLoaderModelOnly", "inputs": {"lora_name": "only.safetensors"}},
    }

    assert [entry["name"] for entry in extract_models(definition)] == [
        "flux-2-klein-4b.safetensors",
        "flux2-vae.safetensors",
        "qwen_3_4b.safetensors",
        "canny.safetensors",
        "ip.bin",
        "4x.pth",
        "only.safetensors",
    ]


# ---------------------------------------------------------------- 顺序与去信息损失


def test_multiple_loras_are_all_kept_and_deterministically_ordered() -> None:
    """多 LoRA 一个都不能丢，且顺序必须**确定**。

    用列表而不是 dict 就是为了这条：dict 的 `lora_name` 只能留下最后一个。
    """
    definition = {
        "_meta": {},
        "7": {"class_type": "LoraLoader", "inputs": {"lora_name": "b_style.safetensors"}},
        "3": {"class_type": "LoraLoader", "inputs": {"lora_name": "a_style.safetensors"}},
        "12": {"class_type": "LoraLoaderModelOnly", "inputs": {"lora_name": "c_style.safetensors"}},
    }

    first = extract_models(definition)
    assert [entry["name"] for entry in first] == [
        "a_style.safetensors",
        "b_style.safetensors",
        "c_style.safetensors",
    ]
    # 节点 id 是数字串时按**数值**排：3 < 7 < 12（纯字典序会得到 12 < 3 < 7）
    assert [entry["node"] for entry in first] == ["3", "7", "12"]

    # 同一输入跑两次结果一致
    assert extract_models(definition) == first

    # 且与 dict 的键插入顺序**无关**（顺序来自排序，不是来自 JSON 书写顺序）
    reordered = {key: definition[key] for key in reversed(list(definition))}
    assert extract_models(reordered) == first


def test_non_numeric_node_ids_still_sort_deterministically() -> None:
    """非数字节点 id 也要有稳定落位 —— 不能依赖 dict 插入顺序。"""
    definition = {
        "_meta": {},
        "beta": {"class_type": "VAELoader", "inputs": {"vae_name": "b.safetensors"}},
        "2": {"class_type": "VAELoader", "inputs": {"vae_name": "num.safetensors"}},
        "alpha": {"class_type": "VAELoader", "inputs": {"vae_name": "a.safetensors"}},
    }

    assert [entry["node"] for entry in extract_models(definition)] == ["2", "alpha", "beta"]
    reordered = {key: definition[key] for key in reversed(list(definition))}
    assert extract_models(reordered) == extract_models(definition)


# ---------------------------------------------------------------- 多槽位 CLIP


@pytest.mark.parametrize(
    ("class_type", "inputs", "expected_inputs"),
    [
        # 入参名逐条取自 deploy/schemas/object_info.v0.36.0.json
        ("CLIPLoader", {"clip_name": "one.safetensors"}, ["clip_name"]),
        (
            "DualCLIPLoader",
            {"clip_name1": "a.safetensors", "clip_name2": "b.safetensors", "type": "sdxl"},
            ["clip_name1", "clip_name2"],
        ),
        (
            "TripleCLIPLoader",
            {"clip_name1": "a.safetensors", "clip_name2": "b.safetensors", "clip_name3": "c.safetensors"},
            ["clip_name1", "clip_name2", "clip_name3"],
        ),
        (
            "QuadrupleCLIPLoader",
            {
                "clip_name1": "a.safetensors",
                "clip_name2": "b.safetensors",
                "clip_name3": "c.safetensors",
                "clip_name4": "d.safetensors",
            },
            ["clip_name1", "clip_name2", "clip_name3", "clip_name4"],
        ),
    ],
)
def test_multi_slot_clip_inputs_are_not_missed(
    class_type: str, inputs: dict, expected_inputs: list[str]
) -> None:
    """`clip_name1`/`clip_name2`… **不以 `_name` 结尾**，是最容易漏的一类。

    只按 `str.endswith("_name")` 过滤会静默漏掉整组 CLIP 权重，
    而 CLIP 权重换一个就是另一张图 —— 属于 C1 的硬缺口。
    """
    result = extract_models({"_meta": {}, "2": {"class_type": class_type, "inputs": inputs}})

    assert [entry["input"] for entry in result] == expected_inputs
    assert [entry["name"] for entry in result] == [
        inputs[name] for name in expected_inputs
    ]


# ---------------------------------------------------------------- 边界：空


def test_workflow_without_loaders_yields_empty_list() -> None:
    """没有 loader 节点时是**空列表**，不是缺键、不是 None。

    前端按 `meta["models"]` 直接渲染列表；`None` 会让它崩，缺键会让它要写兜底分支。
    """
    definition = {
        "_meta": {},
        "1": {"class_type": "KSampler", "inputs": {"steps": 20, "seed": 1}},
        "2": {"class_type": "SaveImage", "inputs": {"filename_prefix": "smoke/t2i"}},
    }

    assert extract_models(definition) == []


def test_empty_definition_yields_empty_list() -> None:
    assert extract_models({}) == []


# ---------------------------------------------------------------- 边界：非模型入参


def test_non_model_inputs_of_a_loader_are_not_collected() -> None:
    """loader 节点上的非模型入参不能进 `models`。

    这条是判据的**精确性**一侧：`strength_model` / `seed` 是数字，
    `model` / `clip` 是连线条目，而 `prompt` 是**字符串**却也不是模型 ——
    只有名字判据（`_name` / `_file`）能挡住它。
    """
    definition = {
        "_meta": {},
        "5": {
            "class_type": "LoraLoader",
            "inputs": {
                "model": ["4", 0],
                "clip": ["4", 1],
                "lora_name": "cups_v2.safetensors",
                "strength_model": 0.8,
                "strength_clip": 0.8,
                "prompt": "一只白瓷杯，柔光，电商主图",
                "seed": 42,
            },
        },
    }

    assert [entry["input"] for entry in extract_models(definition)] == ["lora_name"]


def test_loader_with_unselected_slot_records_nothing() -> None:
    """槽位没选（值为 `None` / 空串）不记 —— 记了就是在元数据里承诺一个不存在的模型。"""
    definition = {
        "_meta": {},
        "1": {"class_type": "LoraLoader", "inputs": {"lora_name": None}},
        "2": {"class_type": "VAELoader", "inputs": {"vae_name": ""}},
    }

    assert extract_models(definition) == []


def test_non_loader_node_with_model_shaped_input_is_ignored() -> None:
    """类名不含 `Loader` 的节点一律不看（判据的第一个条件）。

    反面例子：`KSampler` 上有个叫 `sampler_name` 的入参 —— 它选的是采样器名字，
    不是权重文件。若把"类名含 Loader"这个条件去掉，它就会被误收。
    """
    definition = {
        "_meta": {},
        "1": {"class_type": "KSampler", "inputs": {"sampler_name": "dpmpp_2m"}},
        "2": {"class_type": "CLIPTextEncode", "inputs": {"text": "一只白瓷杯"}},
    }

    assert extract_models(definition) == []


def test_known_over_inclusion_is_deliberate_not_a_regression() -> None:
    """判据**刻意偏向召回**：形状吻合但并非权重的入参会一起被记进来。

    与 `object_info` 快照逐条交叉核对后，确认 `ConditioningLoader.conditioning_name`
    属于此类（它是 conditioning 类型名，不是权重文件）。

    本测试**锁住这个取舍**，而不是把它当 bug 修掉：漏记一个真实权重的代价
    （照元数据复现失败）远大于多记一个无关字符串（多一行、可事后审计）。
    将来若收紧判据，这条会变红，迫使改动者同步更新
    `workflow_models` 模块 docstring 里的说明 —— 判据本身也要能被检出。
    """
    definition = {
        "_meta": {},
        "1": {"class_type": "ConditioningLoader", "inputs": {"conditioning_name": "SDXL"}},
    }

    assert extract_models(definition) == [
        {
            "node": "1",
            "class_type": "ConditioningLoader",
            "input": "conditioning_name",
            "name": "SDXL",
        }
    ]


# ---------------------------------------------------------------- 边界：坏形状不抛异常


@pytest.mark.parametrize(
    "definition",
    [
        None,
        [],
        "不是字典",
        42,
        {"1": {"class_type": "KSampler"}},  # 节点没有 inputs
        {"2": None},  # 节点不是字典
        {"3": {"class_type": "X", "inputs": "不是字典"}},  # inputs 不是字典
        {"4": {"class_type": "LoraLoader", "inputs": {"lora_name": None}}},
        {"5": {"class_type": 123, "inputs": {"ckpt_name": "a.safetensors"}}},  # 类名不是字符串
        {"6": {"class_type": "UNETLoader", "inputs": {"unet_name": 123}}},  # 值不是字符串
        {"7": {"class_type": "VAELoader", "inputs": {1: "a.safetensors"}}},  # 入参名不是字符串
        {"8": {"class_type": "VAELoader", "inputs": ["vae_name", "a.safetensors"]}},
        {"9": {"class_type": "UNETLoader"}},  # 连 inputs 键都没有
        {"_meta": None},
        {"_meta": {}, "10": {"inputs": {"ckpt_name": "a.safetensors"}}},  # 没有 class_type
    ],
)
def test_malformed_shapes_never_raise(definition: object) -> None:
    """任何坏形状都只是"跳过"，**绝不允许抛异常**。

    它跑在出图成功后的收尾路径上（`TaskExecutor._finish_success`）：
    一旦抛出去，一张已经出好的图会被判成失败任务 —— 这是本模块的硬要求。
    """
    assert extract_models(definition) == []


def test_malformed_nodes_are_skipped_without_losing_the_good_ones() -> None:
    """坏节点跳过、好节点照记 —— 不能因为一个坏节点就整体放弃。"""
    definition = {
        "_meta": {},
        "1": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": "sd_xl_base_1.0.safetensors"},
        },
        "2": None,
        "3": {"class_type": "KSampler"},
        "4": {"class_type": "LoraLoader", "inputs": "不是字典"},
        "5": {"class_type": "VAELoader", "inputs": {"vae_name": None}},
        "6": {"class_type": "UNETLoader", "inputs": {"unet_name": 123}},
        "7": {"class_type": "CLIPLoader", "inputs": {"clip_name": "qwen.safetensors"}},
    }

    assert [entry["name"] for entry in extract_models(definition)] == [
        "sd_xl_base_1.0.safetensors",
        "qwen.safetensors",
    ]
