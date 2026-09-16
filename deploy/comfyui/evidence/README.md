# 证据索引（P2-05 节点白名单的一手依据）

> 本目录存放**原始证据**，用于追溯 `deploy/comfyui/node_whitelist.yaml` 每一处判定的来源。
> 采集时间：**2026-09-16 16:59–17:01（UTC+8）**，实例处于**无卡模式**，全部操作**只读**。

## 采集时的实例状态

| 项 | 值 |
|---|---|
| 实例 | autoDL `connect.westc.seetacloud.com:22910`（西北B区/329机） |
| 模式 | 无卡模式（cgroup 限内存 / 0.5 核） |
| ComfyUI | **v0.36.0**，HEAD `ee71d5c4993f29086b27fde1629a945ae48425bf`（与 `deploy/versions.lock` 一致 ✅） |
| 系统盘 | `/` overlay 30G，已用 20G，**剩 11G** |
| 数据盘 | `/root/autodl-tmp` 50G，已用 28G，**剩 23G** |
| 宿主负载 | load average 16.60 / 15.94 / 16.31（共享宿主机，与台账 #18 的「周期性干扰尖峰」一致） |

## 文件清单

| 文件 | 来源 | 用途 |
|---|---|---|
| `comfyui_cpu_audit.log` | `/root/autodl-tmp/comfyui_cpu_audit.log`（40771 B，**2026-09-16 10:01**） | **核心证据（第一次）**：CPU 模式（`--cpu`）启动日志，含**每个自定义节点的 import 结果**与完整失败 traceback |
| `comfyui_cpu_startup.log` | 同一次 CPU 模式审计的**第二次运行**（40893 B，**2026-09-16 17:01**） | **核心证据（第二次，独立复现）**：⚠️ **provenance 未确认** —— 该文件在我（C 流）落盘后被写入本目录（17:05），非我采集。**内容已核验为可信**：`Set vram state to: DISABLED` / `Device: cpu` → 确是 CPU 模式审计；**import 结论与第一次完全一致**（同样是 3 个 IMPORT FAILED）。两次独立运行同结论 → **证据强度提升** |
| `custom_nodes_git_status.txt` | `custom_nodes/` 下逐目录 `git rev-parse HEAD` + `status --porcelain` | 核对 `versions.lock` 的 32 个 commit 是否与实际一致 |
| `custom_nodes_inventory.txt` | ⚠️ **同上（provenance 未确认）**，格式为 `name commit=... dirty_files=N` | 与 `custom_nodes_git_status.txt` **同源同结论**（32 个零漂移），互为交叉验证 |
| `custom_nodes_licenses.txt` | `custom_nodes/` 下逐目录 `LICENSE*` 文件首行 | 节点许可登记（P2-05 子任务） |
| `node_import_failures.txt` | ⚠️ **provenance 未确认** —— 从审计日志中抽取的失败片段汇总 | 便于快速查阅失败清单；**原始依据仍以两份完整审计日志为准** |
| `custom_nodes_install_errors.log` | `custom_nodes/install_errors.log`（186018 B） | ComfyUI-Manager 的**依赖安装**期错误（与运行期 import 故障是两回事） |
| `object_info_v0.3.75_classnames.txt` | 由 `/root/autodl-tmp/object_info.json`（2394710 B，**2025-09-15 21:46**）提取的**类名清单**（2102 行） | ⚠️ **升级前**（v0.3.75）的节点类清单 = 2102 类。**不是当前状态**，用作台账 #16「2102 → 2530」的基线锚点与未来 diff 的依据。原始 2.4MB JSON **未入库**（体积考虑），如需比对可重新采集 |

> ℹ️ **关于 provenance**：`comfyui_cpu_startup.log`、`custom_nodes_inventory.txt`、`node_import_failures.txt`
> 三个文件的时间戳为 **17:05**，晚于我（C 流）的落盘（17:01）。它们的内容与我的采集**互相印证**，
> 故予保留并在此标明来源不明。**已上报团队负责人核实是否有并行采集者**
> —— `deploy/comfyui/` 是 C 流独占目录，若存在并行写入需协调以避免互相覆盖。

## 关键结论（每条都有行号可查）

### 1. 三节点 import 失败（审计日志实证，**两次独立运行同结论**）

| 节点 | 第一次（10:01）行号 | 第二次（17:01）行号 | 失败信息（原文摘要） |
|---|---|---|---|
| `ComfyUI-nunchaku` | 111–191、290–292、473 | 474（+ 同段 traceback） | `ImportError: nunchaku/_C.cpython-312-x86_64-linux-gnu.so: undefined symbol: _ZN3c106detail23torchInternalAssertFailEPKcS2_jS2_RKSs`；**并叠加 diffusers 导入失败**（见下） |
| `ComfyUI-TeaCache` | 309–320、450 | 447 | `ImportError: cannot import name 'precompute_freqs_cis' from 'comfy.ldm.lightricks.model'` |
| `ComfyUI_smZNodes` | 325–429、458 | 461 | 经 `diffusers.pipelines.flux.pipeline_flux` 间接失败，**根因见下** |

汇总位置：两份日志的 `[INFO] ... (IMPORT FAILED)` 三行。
✅ **两次运行（相隔 7 小时）给出完全相同的 3 个失败节点** → 不是偶发，是稳定复现的环境缺陷。

### 2. ⚠️ smZNodes 的真实根因不是 diffusers，而是 **sageattention 的 ABI 失配**

审计日志 192–260 行的完整调用链：

```
diffusers/models/transformers/transformer_flux.py:29
  → from ..attention_dispatch import dispatch_attention_fn
diffusers/models/attention_dispatch.py:72
  → from sageattention import (...)
sageattention/core.py:47 → quant.py:20 → from . import _fused
ImportError: sageattention-2.2.0-...egg/sageattention/_fused.cpython-312-x86_64-linux-gnu.so:
           undefined symbol: _ZN3c106detail14torchCheckFailEPKcS2_jRKSs
```

**含义**：`sageattention==2.2.0`（针对 torch 2.5.1 编译的 C++ 扩展）**仍安装在环境中**
（见 `deploy/versions.lock` `[pip]`），而它的坏 `.so` 经 diffusers 的
`attention_dispatch` 被**模块级无条件 import** → **整条 diffusers 导入链被毒化**。

**推论（待验证）**：**卸载 sageattention 极可能让 `smZNodes` 恢复**（并可消掉 `nunchaku` 的第一层报错）。
这与 todo 中「smZNodes 非必需，直接禁用」的处置并列 —— 但现在我们知道**它损坏的原因是可修的**。
另见审计日志 430 行：`Could not load sageattention: ... undefined symbol`（ComfyUI 侧也放弃了它）。

> ⚠️ 本项目此前已因同类原因卸载过 `flash-attn`（`项目进展.md` #16 处置 10，判据相同：
> `undefined symbol: _ZN3c106detail14torchCheckFailEPKcS2_jRKSs`）。
> **sageattention 是同一类问题的漏网之鱼** —— 它当时被「回退」（不使用），但**没有被卸载**。

### 3. 32 个节点的 commit 与实际**零漂移**

`custom_nodes_git_status.txt` 里 32 个节点的 HEAD 与 `deploy/versions.lock [custom_nodes]`
**逐条完全一致**（无一处不同）。这排除了「ComfyUI-Manager 偷偷更新过节点」的担心（R2 风险的一项）。

⚠️ 但有 4 个节点工作区**有未提交改动**（`dirty` 计数非 0），需注意「锁的是 commit，运行的是 commit+改动」：

| 节点 | dirty 文件数 |
|---|---|
| `ComfyUI-nunchaku` | 1 |
| `ComfyUI_ADV_CLIP_emb` | 1 |
| `comfyui-supersave` | 1 |
| `efficiency-nodes-comfyui` | 2 |

### 4. ⚠️ 许可红线：一个节点为**非商用**许可

`custom_nodes_licenses.txt` 显示 `ComfyUI-Upscaler-Tensorrt` 的许可是
**Attribution-NonCommercial-ShareAlike 4.0 International（CC BY-NC-SA 4.0）= 非商用**。
**它现在装在环境里** → 已在白名单里改为 `disable`（依据 `docs/sop/license_matrix.md` 的红线纪律）。

另有 3 个节点**仓库内无 LICENSE 文件**（`Derfuu_ComfyUI_ModdedNodes`、`comfyui-browser`、
`comfyui-supersave`）→ 按「未知即不用」处理。

### 5. 依赖安装期错误（与运行期无关）

`custom_nodes_install_errors.log` 里最实质的一条：`comfyui_controlnet_aux` 安装时
**`pycairo` 编译失败**（`meson.build:31:12: ERROR: Dependency "cairo" not found`，行 1044–1103）。
但 `comfyui_controlnet_aux` **运行期 import 正常**（审计日志：1.3 秒加载成功）
→ 说明 `pycairo` 是**可选依赖**，不影响 V1 关键路径。**不要为此去装系统级 cairo**（会占系统盘）。

另有 `comfyui_segment_anything` 出现在安装日志（行 1109）但**不在当前 `custom_nodes/` 中**
→ 历史上装过又移除（该节点与 `ComfyUI-Impact-Pack` 功能重叠）。

### 6. 两处环境配置瑕疵（非阻塞）

| # | 发现 | 影响 |
|---|---|---|
| 1 | `ComfyUI-Impact-Pack` 警告：`custom_wildcards path not found: /root/autodl-tmp/ComfyUI/custom_nodes/...`（第一次审计行 102） | **陈旧路径** —— 该路径指向 ComfyUI 曾在 `/root/autodl-tmp/ComfyUI` 的旧布局。当前 ComfyUI 在 `/root/ComfyUI`，故通配词目录失效（自动回落默认路径，不报错） |
| 2 | `extra_model_paths.yaml` 配置了 `base_path: /root/autodl-tmp/models` 与 `/root/models`、`/root/autodl-fs/models`，但**这些目录都不存在** | 审计日志前 20 行会打印一批 `Adding extra search path ...` 指向**空路径**。模型实际都在 `/root/autodl-tmp/comfyui-data/models/`（`/root/ComfyUI` 侧为软链）。**死配置容易误导后人以为模型在别处** |
| 3 | ⚠️ **`rgthree-comfy` 自述与新 UI 不兼容**（第二次审计 17:01 的日志，非 `[WARNING]` 前缀的裸输出）：<br>*"ComfyUI's new Node 2.0 rendering may be incompatible with some rgthree-comfy nodes and features, breaking some rendering as well as losing the ability to access a node's properties... It also appears to run MUCH more slowly spiking CPU usage and causing jankiness and unresponsiveness, especially with large workflows."* | **这是 `rgthree-comfy`（已列入 keep）的功能性风险**：它可能与我们保留它的理由（Power Lora Loader，P4-04）直接冲突 —— 节点属性不可访问意味着**参数可能无法从外部注入**，这会破坏 contracts.md §3.3 的 `targets` 注入契约。<br>**处置**：P4-04 接入时必须**实测 Power Lora Loader 是否可被参数注入**；若不可，改用核心 `LoraLoader` 并把该节点降为 review |
| 4 | `comfyui_controlnet_aux` 的 DWPose 在 CPU 模式告警（第二次审计行 307）：`Onnxruntime not found or doesn't come with acceleration providers, switch to OpenCV with CPU device. DWPose might run very slowly` | **仅 CPU 模式的现象**（无卡审计时必然出现），GPU 模式下 onnxruntime 有 CUDA provider（同日志行 306 已列出 `CUDAExecutionProvider`）→ **不是缺陷**，勿据此误判 |

### 7. 反向发现：`Import times` 段的计数自校验

第二次审计的 `Import times` 段共 **33** 行 = **32 个节点目录 + 1 个 `websocket_image_save.py`**
（后者是 ComfyUI 自带的单文件节点，不是我们的节点）。两次审计均为 33 行
→ 说明**没有节点在采集期间被增删**，两份日志可比。

## 复现方式（只读）

```bash
export SSHPASS='<向团队负责人索取，不要写进任何文件>'
sshpass -e ssh -o StrictHostKeyChecking=no -p 22910 root@connect.westc.seetacloud.com \
  'cd /root/ComfyUI/custom_nodes; ls -1; for d in */; do n=${d%/}; git -C "$n" rev-parse HEAD 2>/dev/null; done'
# 节点审计（CPU 模式，不耗 GPU）：用 deploy/autodl/23_cpu_mode_node_audit.sh
```

## 尚未取得的证据

| # | 缺口 | 为什么需要 | 获取方式 |
|---|---|---|---|
| 1 | **当前版本（v0.36.0）的 `/object_info` 节点类清单** | 量化裁剪收益；台账 #16 记的 2530 目前**无文件证据**（手上的 2102 是升级前基线） | 需启动 ComfyUI 后 `curl 127.0.0.1:8188/object_info`（**需实例可达且能起服务**，无卡模式可起 CPU 模式） |
| 2 | 各节点 `README` / `NODE_CLASS_MAPPINGS` 的**节点类名清单** | 用于「禁用后确认目标节点类消失」的验收 | 同上，或直接读各节点 `__init__.py` |
| 3 | 禁用动作的**前后对比**（节点类总数变化） | 白名单执行的验收判据 | 执行白名单时采集 |
