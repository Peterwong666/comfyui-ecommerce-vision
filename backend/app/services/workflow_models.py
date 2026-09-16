"""从节点图里提取「这张图实际用了哪些模型」（C1 可复现性 / FR-5.4）。

为什么需要它
------------
`Asset.meta` 已经记了 `seed` / `params` / 工作流版本 / 产物自己的 `sha256`，
**唯独没有模型**。客户追单要能出一样的图，少这一项就复现不出来：
换个底模、或把 LoRA 从 v1 换成 v2，seed 与参数全都一样，出来的却是另一张图。
`docs/prototype/wireframes.md` §6 的画廊元数据抽屉明确要展示
`模型 sd_xl_base_1.0 (sha256:a1b2..)` —— 前端实现到那一步时后端还没有这个字段，
只能在抽屉里写「（后端 meta 未记录模型名）」。本模块补上这个缺口。

判据（什么算「一个模型引用」）
------------------------------
一条记录必须**同时**满足：

1. 节点类名含 `Loader`；
2. 入参名匹配 ``_(name|file)\\d*$``（即 `_name` / `_file`，允许**尾随数字槽位**）；
3. 值是非空字符串。

**出处**（`deploy/schemas/object_info.v0.36.0.json`，ComfyUI v0.36.0 的 `/object_info`
导出，2530 个节点类）：该快照里会加载权重文件的节点类，类名**一律含 `Loader`**
（`CheckpointLoaderSimple.ckpt_name`、`UNETLoader.unet_name`、`VAELoader.vae_name`、
`CLIPLoader.clip_name`、`LoraLoader.lora_name`、`UpscaleModelLoader.model_name`、
`ControlNetLoader.control_net_name`、`IPAdapterModelLoader.ipadapter_file` …），
而模型文件名入参一律以 `_name` / `_file` 收尾。判据不是猜的，是从这份快照反推的。

⚠️ **尾随数字槽位不能漏**：快照里 `DualCLIPLoader` 的入参是
`['clip_name1', 'clip_name2', 'type']`、`TripleCLIPLoader` 是
`['clip_name1', 'clip_name2', 'clip_name3']`、`QuadrupleCLIPLoader` 到 `clip_name4`。
它们**不以 `_name` 结尾** —— 只按 `str.endswith("_name")` 过滤会静默漏掉整组 CLIP 权重。
所以判据里带 `\\d*`。

⚠️ **刻意偏向召回（宁可多记，不可漏记）**：两类错误的代价不对称 ——
多记一条只是元数据里多一行、对复现无害且可事后审计；漏记一条会让「照元数据复现」
直接失败，而那正是本功能存在的理由。已知的过度包含（与快照逐条交叉核对得出，
见 `docs/` 的记录）：
`ConditioningLoader.conditioning_name`、`Text File History Loader.dictionary_name`
—— 它们不是权重文件，但形状与判据完全一致，按上述取舍保留。

⚠️ **刻意不覆盖 rgthree 的 `Lora Loader Stack (rgthree)`**：它是文档钦定的多 LoRA
首选节点（`docs/sop/debug_log.md`），但入参是 `lora_01..lora_04`，**不匹配本判据**。
今天零影响：仓库里 5 个工作流都没用它，且服务器上 `LoraLoader.lora_name` 枚举为空
（一个 LoRA 权重都没有，P4-04/FR-8.5 未启动）。真接入 LoRA 时必须一并扩展判据。
TODO(P4-04/FR-8.5)：支持 `lora_NN` 槽位式入参。

为什么是纯函数
--------------
不碰 DB / 存储 / 网络，因此可以便宜地单测，也能被别处复用
（例如提交前预估显存、管理后台展示「这个工作流依赖哪些模型」）。

⚠️ **绝不抛异常**：调用点 `TaskExecutor._finish_success` 在**出图成功之后**的收尾路径上。
定义形状异常、节点没有 `inputs`、`inputs` 不是字典……一律跳过。
让「元数据提取失败」把一张已经出好的图变成失败任务，是绝对不能接受的。
"""

from __future__ import annotations

import re
from typing import Any

#: 节点类名里必须出现的标记。快照里所有加载权重的节点类都含它（见模块 docstring）。
_LOADER_MARKER = "Loader"

#: 模型文件名入参的名字判据。
#:
#: `\d*` 不能省：`DualCLIPLoader` → `clip_name1`/`clip_name2`、
#: `TripleCLIPLoader` → `clip_name1..3`、`QuadrupleCLIPLoader` → `clip_name1..4`
#: （均出自 `deploy/schemas/object_info.v0.36.0.json`）。
_MODEL_INPUT_RE = re.compile(r"_(?:name|file)\d*$")


def extract_models(definition: Any) -> list[dict[str, str]]:
    """提取节点图里的模型引用，按 (节点 id, 入参名) **确定性排序**。

    返回列表而不是字典（`dict` 会丢信息）：一个工作流可能有多个同类型 loader，
    多 LoRA 场景下 `{"lora_name": ...}` 只能留下最后一个。

    每个元素的形状::

        {"node": "4", "class_type": "CheckpointLoaderSimple",
         "input": "ckpt_name", "name": "sd_xl_base_1.0.safetensors"}

    `definition` 形状异常时返回 `[]`，**任何情况下都不抛异常**（见模块 docstring）。
    """
    if not isinstance(definition, dict):
        return []

    found: list[tuple[tuple[int, int, str, str], dict[str, str]]] = []
    for node_id, node in definition.items():
        # 节点值必须是 dict —— 顺带挡掉 `{"_meta": {...}}` 这类非节点段，
        # 以及 `{"2": None}` 这种坏形状。
        if not isinstance(node, dict):
            continue

        class_type = node.get("class_type")
        if not isinstance(class_type, str) or _LOADER_MARKER not in class_type:
            continue

        inputs = node.get("inputs")
        if not isinstance(inputs, dict):
            continue

        raw_id = str(node_id)
        for input_name, value in inputs.items():
            if not isinstance(input_name, str) or not _MODEL_INPUT_RE.search(input_name):
                continue
            # 值必须是**非空字符串**才是模型文件名。`None`（槽位未选 LoRA）、
            # 数字、被注入成 asset_id 的图片参数都在这里被挡掉。
            if not isinstance(value, str) or not value:
                continue
            found.append(
                (
                    (*_node_order(raw_id), input_name),
                    {
                        "node": raw_id,
                        "class_type": class_type,
                        "input": input_name,
                        "name": value,
                    },
                )
            )

    found.sort(key=lambda pair: pair[0])
    return [entry for _, entry in found]


def _node_order(node_id: str) -> tuple[int, int, str]:
    """节点 id 的排序键：数字 id 按**数值**排（`2` 在 `10` 前面），其余按字典序排在其后。

    纯字典序会把 `"10"` 排在 `"2"` 前面，人看着别扭。但**确定性是硬要求**
    （同一输入跑两次必须得到同一顺序），所以非数字 id 也要有稳定的落位 ——
    不能依赖 `dict` 的插入顺序（它反映的是 JSON 里的书写顺序，不是语义）。
    """
    if node_id.isdigit():
        return (0, int(node_id), "")
    return (1, 0, node_id)
