# 模型与依赖商用许可矩阵（P5-10）

> **这是商用项目的红线文档。** 状态：初稿 2026-09-15 ｜ **2026-09-16 补充实际在用模型与 V1 计划模型**
>
> 免责声明：以下信息为整理时的公开信息，**不具法律效力**。
> 每个模型下载后必须以仓库中的 LICENSE 原文为准，并登记到 `engine/model_registry.yaml`。
>
> ⚠️ **「待核实」不等于「可用」**：凡标「待核实」的条目，在拿到 LICENSE 原文前**不得进入任何交付链路**。
> DoD 要求的「许可矩阵无未知项」，其达成方式是**把「未知」逐条转为「已核实」**，不是把未知写成已知。

---

## 三条铁律

1. **下载即登记** —— 拿到模型立刻记录：来源 URL、许可证名称、LICENSE 文件原文路径、hash。
2. **未知即不用** —— 找不到明确许可证的模型一律不用，找替代品；不存侥幸心理。
3. **发布前复查** —— 模型许可证会变更（尤其是社区模型），V1 发布前全量复查一次（P11-11）。

---

## 0. 当前模型合规总览（2026-09-16）

> 与 [`../../engine/model_registry.yaml`](../../engine/model_registry.yaml) 的 `license_summary` 段保持一致。改一边必须改另一边。

| 模型 | 类型 | 许可证 | 可商用 | LICENSE 原文已入库 | 状态 | 阻塞 V1 |
|---|---|---|---|---|---|---|
| `sd_xl_base_1.0` | checkpoint | CreativeML OpenRAIL++-M | ✅ | ❌ **待入库** | active | 否 |
| `flux-2-klein-4b` | unet | Apache-2.0 | ✅ | ❌ **待入库** | active | 否 |
| `flux2-vae` | vae | Apache-2.0 | ✅ | ❌ **待入库** | active | 否 |
| `qwen_3_4b` | text_encoder | Apache-2.0（上游 Qwen3-4B） | ✅ | ❌ **待入库**（两层） | active | 否 |
| `sdxl_vae_fp16fix` | vae | **待核实**（社区发布） | ⚠️ unknown | ❌ | standby | 否（未进链路） |
| `qwen_3_4b_fp4_flux2` | text_encoder | **待核实**（量化派生作品） | ⚠️ unknown | ❌ | disabled | 否 |

| 指标 | 值 |
|---|---|
| 模型总数 | 6 |
| 结论可商用 | 4 |
| 许可未知 | 2（**均已 standby/disabled，不进交付链路**） |
| **LICENSE 原文已入库** | **0 / 6** ⚠️ |
| 当前阻塞 V1 的项 | **无** |

> ⚠️ **最大的合规缺口是「原文未入库」而不是「结论不清」**：
> 4 个 active 模型的许可结论有 ADR-005 与上游仓库页面支撑，但**没有任何一份 LICENSE 原文进了归档**。
> 这违反了铁律 1，且 P11-11 无法在此基础上复查。**列为 V1 发布前的必修项。**
>
> 🚩 **P11-11 门禁**：**`§7` 第 1 条（6 个模型的 LICENSE 原文入库，当前 0/6）不结清，P11-11 不得判定通过。**
> 它是本文件唯一一条「已知不阻塞 V1、但**必须在上线前结清**」的项 —— 放在此处是为了**防止它因为"不阻塞"而被遗忘**。
>
> 🚩 **P11-11 必修标记（team-lead 2026-09-16 指定）**：本项**不阻塞 V1**（4 个 active 模型无 unknown 许可），
> 但它**必须出现在 P11-11 的验收清单里，且不得被遗忘**。验收方式：6 条逐一给出 LICENSE 原文的归档路径。
> **本行未结清 → P11-11 不得判定通过。**

---

## 1. 引擎与框架

| 组件 | 许可证 | 可商用 | 说明 |
|---|---|---|---|
| **ComfyUI** | GPL-3.0 | ⚠️ 视使用方式 | 不改源码、不分发、仅以独立进程 API 调用 → 一般不触发传染；若 fork 修改并分发必须开源 |
| 自定义节点（32 个） | 各异 | ⚠️ 逐个核对 | **已于 2026-09-16 逐条实测登记**，见下表 §1.1 |
| FastAPI / SQLAlchemy / Celery | MIT / BSD / BSD-3 | ✅ | 常规开源依赖 |
| React / Ant Design | MIT | ✅ | — |
| `insightface==0.7.3`（pip，**已装**） | 代码 MIT，**预训练权重非商用** | ❌ | **上游 PyPI 原文（2026-09-16 已核实，逐字引用）**：<br>“The library code is released under the **MIT License**, for academic and commercial use. **The pretrained models provided with this library are for non-commercial research only**, whether downloaded automatically or manually.”<br>同页另注：“**Model licenses apply separately from the library's MIT license.**”<br>→ **关键含义：库 MIT ≠ 权重可商用**，而权重下载是 `pip install` 之后的**自动行为**，很容易在无人察觉时完成。商用授权需向 insightface.ai 单独购买（buffalo_l / antelopev2 等模型包）。<br>本项目已确认其权重**落在磁盘上**：`models/insightface/models/buffalo_l/*.onnx` → **任何链路都不得引用该目录** |
| `ultralytics`（由 Impact-Pack 引入） | **AGPL-3.0** | ⚠️ **需单独评估** | AGPL 传染性强于 GPL。仅进程内调用、不以库形式分发通常不触发，但**必须记录判断**并跟踪上游（若改为交付镜像则风险上升） |
| `sageattention==2.2.0`（pip，**已装但损坏**） | Apache-2.0 | ✅（许可） | 许可无问题；**技术上有 ABI 失配缺陷，建议卸载**（见 §1.2） |
| `flash-attn` | BSD-3-Clause | ✅（许可） | 与许可无关；已因 torch ABI 失配卸载（`项目进展.md` #16/#17） |

### 1.1 自定义节点许可实测登记（2026-09-16，32 个）

> 来源：各节点仓库内 `LICENSE*` 文件首行（原始输出见 `deploy/comfyui/evidence/custom_nodes_licenses.txt`）
> ⚠️ 这一节把 P2-05 与 P5-10 连起来了：**节点的许可与模型的许可同等重要**。

| 许可 | 数量 | 判定 |
|---|---|---|
| MIT | 10 | ✅ 可保留 |
| Apache-2.0 | 5 | ✅ 可保留 |
| **GPL-3.0** | **13** | ⚠️ 可保留，但**构成商业模式约束**（见 §1.2） |
| **CC BY-NC-SA 4.0（非商用）** | **1** | ❌ **必须禁用** |
| **无 LICENSE 文件（未知）** | **3** | ❌ **必须禁用**（未知即不用） |

**红线条目（已列入 P2-05 白名单的禁用档）**

| 节点 | 许可 | 处置 |
|---|---|---|
| `ComfyUI-Upscaler-Tensorrt` | **CC BY-NC-SA 4.0** | ❌**禁用** —— 非商用 + ShareAlike。**本轮采集最重要的合规发现：它当前装在环境里**。功能可由常规放大器或 Impact-Pack 替代 |
| `Derfuu_ComfyUI_ModdedNodes` | 无 LICENSE 文件 | ❌禁用（待上游核实后可回落） |
| `comfyui-browser` | 无 LICENSE 文件 | ❌禁用（待核实） |
| `comfyui-supersave` | 无 LICENSE 文件 | ❌禁用（待核实；且产物元数据建议自研） |

#### 1.1.1 交叉验证：两个独立来源的一致性（2026-09-16）

> 上面那份是**服务器端读各仓库 `LICENSE*` 原文**得出的。
> 为降低「单一来源读错」的风险，C 流另用 **GitHub License API**（`/repos/{owner}/{repo}/license`，读的是 GitHub 检测结果）独立跑了一遍 32 个节点，两个来源**互不依赖**。

| 项 | 服务器读 LICENSE 原文 | GitHub License API（独立第二来源） | 是否一致 |
|---|---|---|---|
| MIT | 10 | 9 | ✅ 差 1 项为 `ComfyUI_BiRefNet_ll`（API 报 `NOASSERTION`，实为 MIT，见下） |
| Apache-2.0 | 5 | 5 | ✅ 完全一致 |
| GPL-3.0 | 13 | 13（含 `ComfyUI_IPAdapter_plus` / `Impact-Pack` / `KJNodes` / `ADV_CLIP_emb` / `Advanced-ControlNet` / `Manager` 等） | ✅ 完全一致 |
| **无 LICENSE** | 3 | 3（**同样**是 `Derfuu_ComfyUI_ModdedNodes` / `comfyui-browser` / `comfyui-supersave`） | ✅ **逐条指名一致** |
| NC 许可 | 1（`ComfyUI-Upscaler-Tensorrt` = CC BY-NC-SA 4.0） | 1 报 `NOASSERTION`（GitHub 认不出该文本） | ✅ 可对上（`NOASSERTION` 常意味着非标准/自定义条款，值得警惕） |
| 合计 | 32 | 32 | ✅ 无遗漏、无多出 |

**结论**：两个来源完全吻合，**§1.1 的统计可以采信**。
**副产物**：本次跑出了 **32/32 个节点的「节点目录名 → GitHub 仓库 slug → SPDX」映射**（28 个直接命中 API，4 个经仓库检索确认：`Coziness` / `Dev-Utils` / `Derfuu` / `comfyui-browser` / `comfyui-supersave`），已登记进 `deploy/comfyui/node_whitelist.yaml` 的 `license_cross_validation.repo_slugs`，可直接用于 §8 登记流程里的 `source.repo` 字段 —— 以前白名单里只能写「待核实」。<br>⚠️ 这些 slug 是**候选值**，尚未与服务器上各节点的 `git remote get-url origin` 逐条对账（已登记为新缺口）。

**顺带修正了两条**：

| 节点 | 修正 | 依据 |
|---|---|---|
| `ComfyUI_BiRefNet_ll` | GitHub API 报 `NOASSERTION`，但**实际 LICENSE 是 MIT**（Copyright (c) 2024 lldacing） | 以服务器读到的原文为准 → 这也说明 **API 不能替代读原文** |
| `ComfyUI-Upscaler-Tensorrt` | GitHub API 报 `NOASSERTION` → 实为 **CC BY-NC-SA 4.0** | 同上。**若只信 API 而不读原文，就会漏掉这个非商用红线** |

> 💡 **方法论沉淀**：**API 适合做「批量筛查 + 交叉验证」，原文适合做「判定」**。
> API 报 `NOASSERTION` / 空 时必须回落读原文 —— 本次那个 CC BY-NC-SA 的节点就藏在 `NOASSERTION` 里。
> 这与 §1.1 的「未知即不用」是同一逻辑：**没读到原文就等于未知**。

### 1.2 ⚠️ GPL-3.0 与「交付形态」的强关联（新结论）

13 个节点为 GPL-3.0（含 `ComfyUI_IPAdapter_plus`、`ComfyUI-Impact-Pack`、`ComfyUI-KJNodes` 等
**V1 关键路径节点**）。由于 **ComfyUI 本体也是 GPL-3.0**，进程内组合不产生额外约束 ——
**关键分界线是「是否构成分发（distribution）」**：

| 交付形态 | 是否构成分发 | GPL-3.0 义务 |
|---|---|---|
| **SaaS / 仅以 API 提供服务**（本项目 V1 形态，ADR-001） | 否 | 一般无义务 ✅ |
| **交付 docker 镜像 / 本地化部署给客户** | **是** | **须履行**（提供完整对应源码 + 保留许可声明）⚠️ |

> ⚠️ **这直接约束 ADR-001 的交付形态决策**：若未来从 SaaS 转为「镜像交付」，
> GPL 合规工作量会显著上升。**该结论需同步给 P11-11 与产品决策。**

---

## 2. 底模（Checkpoint）

| 模型 | 许可证 | 可商用 | 说明 |
|---|---|---|---|
| Stable Diffusion 1.5 | CreativeML OpenRAIL-M | ✅ | 附使用限制条款（禁非法内容、禁冒充、禁未经同意的肖像等） |
| **SDXL 1.0 / Turbo** | CreativeML OpenRAIL++-M | ✅ | 同上。**本项目主底模**（ADR-005） |
| SD 3 / 3.5 | Stability AI Community License | ⚠️ 有条件 | 年收入 <100 万美元可免费商用，超出需商业许可 |
| **FLUX.1 [schnell]** | Apache-2.0 | ✅（许可）| 商用最省心，但 **BFL 官方仓库为 gated（403）**，本项目实际取不到，已改用 FLUX.2 klein |
| FLUX.1 [dev] | FLUX.1-dev Non-Commercial License | ❌ 禁止 | 需单独授权；**V1 不得用于商业交付** |
| **FLUX.2 [klein] 4B** | **Apache-2.0** | ✅ | ✅ **本项目采用**（ADR-005）。未 gated，仓库自带 VAE 与文本编码器。**⚠️ 待核实**：项目取的是 `Comfy-Org/flux2-klein-4B` 打包版，需核对该仓库自身的 LICENSE 声明与上游 BFL 许可一致 |
| **FLUX.2 [klein] 4B Base** | **Apache-2.0** | ✅ | V2 微调候选。⚠️ 与蒸馏版是**两个不同的文件**，混用会导致步数/cfg 用错（`docs/sop/model_guide.md` §5.1） |
| FLUX.2 [klein] 9B / 9B KV / 9B Base | FLUX.2-dev Non-Commercial | ❌ 禁止 | 参数更大但**不可商用**，一律不碰 |
| FLUX.2 [dev]（32B） | FLUX.2-dev Non-Commercial | ❌ 禁止 | 且仓库 gated（403） |
| FLUX.2 autoencoder (VAE) | **Apache-2.0** | ✅ | 随 klein 仓库提供（`flux2-vae.safetensors`） |
| **Qwen3-4B**（klein 文本编码器） | **Apache-2.0** | ✅ | 上游 `Qwen/Qwen3-4B`。**⚠️ 待核实**：① 上游 LICENSE 原文 ② `Comfy-Org/vae-text-encorder-for-flux-klein-4b` 打包仓库的声明 |
| Qwen3-4B **fp4 量化版** | **待核实** | ⚠️ unknown | 量化属**派生作品**，量化方可能有独立许可声明。当前状态 disabled（且技术上 shape mismatch，见 `项目进展.md` #13） |
| **`sdxl_vae_fp16fix`**（SDXL fp16 修复 VAE） | **待核实**（社区常用标注 MIT） | ⚠️ unknown | 来源为社区个人仓库，**未核对原文前保持 standby**。⚠️ 需先确认来源仓库（下载渠道未在 `deploy/` 留痕） |
| 各类社区合并模型 / 蒸馏模型 | 多为 OpenRAIL 系或作者自定 | ⚠️ 高风险 | 社区模型常附加「禁转售」条款，必须逐个核对 |

> ⚠️ **重要经验（2026-09-15）**：`HF_ENDPOINT=https://hf-mirror.com` 能解决**网络不通**，
> 但**绕不过 gated 仓库**的授权校验 —— 会报 `GatedRepoError: ... you are not in the authorized list`。
> 选模型时必须把「能否真正下载到」当成硬指标，许可证可商用但取不到 = 等于不可用。
>
> ⚠️ **镜像站的额外风险（2026-09-16 补充）**：经 `hf-mirror.com` 取到的文件**无法证明与上游逐字节一致**。
> 缓解手段：`deploy/versions.lock` 的 `sha256` 若与上游公布的 hash 一致即可确认；**未比对过的需标注**。

---

## 3. 控制与适配（V1 P4 阶段重点）

| 组件 | 许可证 | 可商用 | 说明 |
|---|---|---|---|
| ControlNet 代码（lllyasviel） | Apache-2.0 | ✅ | 代码可商用 |
| **ControlNet SDXL 权重**（各来源） | 多为 OpenRAIL 系 / 部分 Apache-2.0 | ⚠️ **逐模型待核实** | ⚠️ 同一「ControlNet」名下不同权重来源许可差异极大。**首选标注 Apache-2.0 的权重（如 xinsir 系），并核对原文**；P5-04 建立模型集时必须逐条登记 |
| ControlNet Union / ProMax 系列 | 多为 OpenRAIL-M | ⚠️ 逐个核对 | — |
| **IP-Adapter**（`h94/IP-Adapter`） | 代码 Apache-2.0 | ⚠️ **待核实** | ⚠️ 两处需核：① **权重仓库**的许可声明（与代码未必同）② **依赖的 CLIP 图像编码器**权重许可（常被忽略）。FR-8.3 的保真主力，**必须在 P4-03 接入前核实完毕** |
| 🚨 **IP-Adapter *FaceID* 系列** | 权重"待核实"，**但依赖是硬红线** | ❌ **禁止用于交付** | **新发现（2026-09-16，来源：`cubiq/ComfyUI_IPAdapter_plus` README 原文）**：<br>“**FaceID** models require `insightface`, you need to install it in your ComfyUI environment.”<br>且 README 明确列出 `Kolors-IP-Adapter-FaceID-Plus.bin` 的说明：“Kolors is trained on InsightFace **antelopev2** model, you need to manually download it”<br>→ **凡走 FaceID 的 IP-Adapter 变体（`ip-adapter-faceid*`）都会拉起 InsightFace 权重 = 触非商用红线**。<br>**且 FaceID 模型还需配套同名 LoRA**（权重清单见该 README）→ 风险面更大。<br>**结论：FR-8.3 只允许使用非 FaceID 的 base / plus / plusv2 变体**（它们用 CLIP image embedding，不碰 InsightFace） |
| IP-Adapter 的 CLIP Vision 编码器 | 待核实 | ⚠️ unknown | 单独登记，不要与 IP-Adapter 本体混为一条。**注意**：非 FaceID 变体正是依赖它，所以这一条是 base/plus 能否商用的**关键路径项** |
| T2I-Adapter | Apache-2.0 | ✅ | — |
| **InstantID** | 代码 Apache-2.0，但依赖 InsightFace 预训练模型 | ❌ **禁止用于交付** | InsightFace 权重为**非商用/研究用途**。本项目明确排除整条 InstantID 链路 |
| **PuLID** | 节点代码 Apache-2.0；**人脸分析后端可选** | 🔶 **部分路径可用（待核实细节）** | **新发现（2026-09-16，来源：`lldacing/ComfyUI_PuLID_Flux_ll` README 原文）** —— PuLID 有**两条互斥的人脸分析路径**：<br>**路径 A（❌ 禁止）** `PulidFluxInsightFaceLoader` → 依赖 InsightFace + facexlib（`antelopev2` / `parsing_bisenet` / `Resnet50`）→ README 自己标注为 “For Non-Commercial/Research Use”<br>**路径 B（✅ 商用候选）** `PulidFluxFaceNetLoader` → README 标注 “**Commercial-friendly FaceNet implementation** — Alternative to InsightFace for commercial usage without licensing restrictions”，依赖 `facenet-pytorch` + VGGFace2 预训练权重，且 “No additional model downloads required”（自动下载到 `~/.cache/torch/checkpoints/20180402-114759-vggface2.pt`）<br>**🔶 仍需核实的两点**：① `facenet-pytorch` 库与 `20180402-114759` 权重的许可原文（VGGFace2 **数据集**本身有研究用途条款 → 权重是否继承该限制需确认）② **本项目环境里当前没有任何 PuLID 节点**（32 节点清单中无）→ 若 V2 要用，需新引入该节点并重新过白名单<br>**⚠️ 在①②核实前，PuLID 不得写作"已验证可用的一致性方案"** |
| **关于 PuLID 作为 InstantID 替代的结论修正** | — | 🔶 **未决** | 原结论「PuLID 是 InstantID 的商用替代（Apache-2.0）」**过粗**：它取决于**选了哪条人脸分析路径**。<br>→ 已回写 `项目记忆` 需修正的口径：**「PuLID 的 FaceNet 路径是商用候选，InsightFace 路径禁止；细节待核」**。<br>→ **归属：V2 启动前（W11）** 由 C 流完成核实。 |
| SAM / SAM 2（Meta） | Apache-2.0 | ✅（**原文待入库**） | 代码与权重均为 Apache-2.0；已随 pip 装 `SAM-2`（`versions.lock` `[pip]`）。FR-8.6 自动遮罩方案 |
| Grounding DINO | Apache-2.0 | ✅（**原文待入库**） | — |
| **Florence-2（Microsoft）** | MIT | ✅ | 自动遮罩方案的优选（许可比 DINO 更清爽） |
| **DWPose** | ⚠️ **待核实** | ⚠️ | 部分姿态权重仅研究用途。FR-8.7 定为 Could 级，可延后；**引入前必须核到具体权重文件** |
| **BiRefNet**（节点 `ComfyUI_BiRefNet_ll` 已装） | **MIT**（节点许可） | ✅（节点） | ✅ **已核实**（2026-09-16）：节点仓库 LICENSE 为 MIT（Copyright (c) 2024 lldacing）。⚠️ 但仍需单独核**它下载的 BiRefNet 权重**许可（`models/BiRefNet/` 目前为空）—— **节点许可 ≠ 权重许可** |
| BrushNet / PowerPaint | 需逐个核对 | ⚠️ | FR-8.4 重绘链路候选，P3-06 时核实 |

---

## 4. 放大（Upscale，P5-05）

| 模型 | 许可证 | 可商用 | 说明 |
|---|---|---|---|
| Real-ESRGAN（代码与官方权重） | BSD-3-Clause | ✅（**原文待入库**） | ⚠️ 需区分：**代码 BSD-3** 与**社区再训练的 4x 权重**是两回事 |
| **`4x-UltraSharp` / `4x_NMKD` 等社区放大模型** | **待核实**（多数无明确声明） | ⚠️ **高风险** | 来源集中在 OpenModelDB / Civitai，**大量条目无许可证声明**。无声明 = 未知 = 不用 |
| **GFPGAN**（人脸修复） | 代码 Apache-2.0 | ⚠️ 待核实 | 权重训练数据与用途限制需单独核 |
| **CodeFormer**（人脸修复） | **S-Lab License 1.0**（非商用，商用需单独授权） | ❌ **禁止** | ⚠️ 在放大/修复管线里极易被顺手引入 —— **列入禁用清单**，除非取得书面授权 |
| TAESD 系（预览用低阶 VAE） | MIT | ✅ | 仅用于预览，不影响交付画质 |

> ⚠️ **放大器是「顺手引入」的重灾区**：`upscale_models/` 目录里塞一个下载来的 4x 权重，
> 没人会去看它的许可。**P5-05 建立模型集时必须逐条登记，包括来源 URL 与许可原文。**

---

## 5. LoRA（风险最高的一类）

| 来源 | 常见许可证 | 可商用 | 说明 |
|---|---|---|---|
| Civitai | CreativeML OpenRAIL-M / 作者自定义 | ⚠️ 高风险 | **大量模型标注「禁止商用 / 禁止转售 / 需署名」**，是踩坑重灾区 |
| HuggingFace | 各异（Apache-2.0 / MIT / OpenRAIL / 无声明） | ⚠️ 逐个核对 | — |
| 官方 / 厂商发布 | 各异 | ⚠️ 逐个核对 | — |
| **自训练** | 自有 | ✅ | V2 微调闭环后可彻底规避此风险（E6） |

**LoRA 准入的最小检查表**（P5-03 执行）：

| # | 检查 | 不过则 |
|---|---|---|
| 1 | 模型页是否**明确写出**许可名称？ | 无声明 → 不用 |
| 2 | 是否含 `NC` / `Non-Commercial` / 「禁止商用」/「禁止转售」/「需授权」字样？ | 有 → 不用 |
| 3 | 训练数据是否声明含真人肖像 / 品牌素材？ | 有且未授权 → 不用 |
| 4 | 底模是否与我们的底模同族（SDXL / klein）？ | 不同族 → 不用（技术原因） |
| 5 | 许可原文是否已归档？ | 否则标「待核实」，**不得进交付链路** |

> **V1 的策略**：LoRA 只作为 **Should 级**（FR-8.5），且**优先走「自训练」路线**（E6）。
> 在自训练闭环建成前，**V1 交付链路可以完全不含第三方 LoRA** —— 这是最省心的合规选择。
>
> ⚠️ **当前的硬阻塞是「零资产」而不是「选不对模型」**（B 流 `debug_log.md` G8.1 实测）：
> `object_info` 里 `LoraLoader.lora_name` 的**枚举为空** → **服务器上一个 LoRA 权重都没有**。
> 所以 FR-8.5 的真实前置依赖是「**先下载 LoRA 并过许可审查**」，不是「选对节点」。
> **排查 FR-8.5 时先看模型有没有，别在节点接线里绕。**
> 该阻塞已登记进 `engine/model_registry.yaml` 的 `planned` 段（LoRA 条目，含 `blocker` 与 `v1_policy`）。
>
> 💡 平台侧已就绪、只缺资产的部分：多 LoRA 的注入节点已定为 **`Lora Loader Stack (rgthree)`**
> （4 槽位固定、10 个声明式入参、Schema 稳定）—— **资产一到即可接入，无需再改工作流结构**。

---

## 6. 输出内容本身的合规（不只是模型）

| 风险点 | 说明 | 应对 |
|---|---|---|
| 生成物版权 | 多数司法辖区下纯 AI 生成物不受著作权保护 | 商用前提示客户，不做版权承诺 |
| 商标与品牌 | 生成图中出现他人商标 / 卡通 IP | 提示词黑名单 + 上传素材授权声明。**反向词包里已有 `neg-027`（伪造品牌标识），但反向词在 cfg=1 链路无效 → 必须靠人工核查** |
| 肖像权 | 使用真人照片做参考 / 生成真人形象 | 需授权声明；V1 内置上传即声明的流程 |
| 儿童形象 | 儿童出现在商业化内容中 | 法律与平台双重红线，**列入生成内容黑名单** |
| 平台规范 | 电商主图有尺寸、水印、夸大宣传等限制 | 模板设计时遵循主流平台规范 |
| 生成标识 | 部分监管要求 AI 生成内容需标识 | V2 规划 C2PA 水印（E11） |

---

## 7. 待核实清单（P11-11 的输入，按优先级排序）

> **这份清单就是「许可矩阵无未知项」的施工图。** 每核掉一条，就更新 §0 总览与 `model_registry.yaml`。

| # | 待核实项 | 现状 | 风险 | 归属 | 何时 |
|---|---|---|---|---|---|
| 1 | 🔴 **6 个 active/standby 模型的 LICENSE 原文入库** | **0/6 已入库** | 中（结论有支撑但无原始凭证）；**不阻塞 V1** | C 流 | 🚩 **P11-11 必修项 —— 本行不结清，P11-11 不得判定通过**。日常跟进：V1 发布前复查时**逐条打勾**（6 条：sd_xl_base_1.0 / flux-2-klein-4b / flux2-vae / qwen_3_4b / sdxl_vae_fp16fix / qwen_3_4b_fp4_flux2） |
| 2 | `sdxl_vae_fp16fix` 的来源仓库与许可 | unknown | 中（当前 standby，未进链路） | C 流 | 下次服务器可达时回查下载记录 |
| 3 | `qwen_3_4b_fp4_flux2` 量化派生作品的独立许可 | unknown | 低（同时技术上不可用，已 disabled） | C 流 | P11-11 |
| 4 | `Comfy-Org/flux2-klein-4B` 打包仓库与上游 BFL 许可的一致性 | 待核 | 中（**klein 是核心底模**） | C 流 | **P5-02 评测前（W3）** |
| 5 | `qwen_3_4b` 的两层许可（上游 Qwen3-4B + 打包仓库） | 待核 | 中（klein 链路唯一可用 TE） | C 流 | P5-02 评测前 |
| 6 | `sd_xl_base_1.0` 的实际下载渠道 | 未留痕 | 低 | C 流 | 下次服务器可达时 |
| 7 | **ControlNet SDXL 各权重**的许可（P5-04 逐条） | 未开始 | **高**（P4 控制体系的基础） | C 流 | P4-01/P4-02 之前 |
| 8 | **IP-Adapter 权重 + CLIP Vision 编码器**许可 | 未开始 | **高**（FR-8.3 保真主力） | C 流 | P4-03 之前 |
| 9 | **放大器模型集**（含社区 4x 权重）许可 | 未开始 | 中高（顺手引入的重灾区） | C 流 | P5-05 |
| 10 | **PuLID 的商用可行性（路径已定位，细节待核）** | 🔶 **部分解决**：已明确 **FaceNet 路径可用 / InsightFace 路径禁止** | 中（决定 V2 一致性方案） | C 流 | ① 待核 `facenet-pytorch` + VGGFace2 权重许可 → **V2 启动前（W11）**；② 若采用需新引入节点并重过 P2-05 白名单 |
| 11 | ~~BiRefNet 节点（已装）的许可~~ | ✅ **已核实：MIT** | — | — | ✅ 2026-09-16 完成 |
| 12 | ~~32 个自定义节点的许可逐条登记~~ | ✅ **已完成（32/32）** | — | — | ✅ 2026-09-16 完成（发现 1 个 NC、3 个无许可） |
| 13 | `hf-mirror.com` 取到的文件与上游 hash 是否一致 | 部分未比对 | 中（完整性不等于许可，但影响可复现） | C 流 | P11-11 |
| 14 | **`ultralytics`（AGPL-3.0）的传染性评估** | 未评估 | 中高（由 Impact-Pack 引入，V1 关键路径） | C 流 | **P4-05（自动遮罩）之前** |
| 15 | **`models/ultralytics/face_yolov8n.pt` 的权重许可** | 未登记 | 中（已在磁盘上） | C 流 | P4-05 之前 |
| 16 | **各 ControlNet 预处理器下载的辅助权重**（DWPose 等） | 未登记 | 中高 | C 流 | P4-01 之前 |

**明确禁用清单（不得出现在任何交付链路）**

| 项 | 原因 |
|---|---|
| InstantID 全链路 | 依赖 InsightFace 非商用权重 |
| InsightFace 预训练权重（**已下载到 `models/insightface/models/buffalo_l/`**） | 非商用/研究用途 |
| FLUX.1 [dev]、FLUX.2 [dev]（32B）、FLUX.2 [klein] 9B 系 | 非商用许可 |
| **CodeFormer** | S-Lab License 1.0，非商用 |
| **`ComfyUI-Upscaler-Tensorrt` 节点** | CC BY-NC-SA 4.0，非商用 + ShareAlike |
| 无许可证声明的社区 LoRA / 放大器权重 | 未知即不用 |
| `Derfuu_ComfyUI_ModdedNodes` / `comfyui-browser` / `comfyui-supersave` | 仓库内无 LICENSE 文件 → 未知即不用 |

---

## 8. 登记流程（每次引入模型时执行）

```bash
# 1. 记录来源：来源 URL、下载日期、发布者
# 2. 保存并核对 LICENSE 原文（不要只看许可证名称）
# 3. 计算 hash 与体积
sha256sum model.safetensors && stat -c %s model.safetensors
# 4. 写入 engine/model_registry.yaml
#    name / kind / version / file_path / file_size / sha256
#    source{repo,url,channel} / license{name,url,commercial_ok,verified,note}
#    eval{score,good_rate} / status / vram
# 5. 回写本文件的 §0 总览与「复查记录」
# 6. 若 commercial_ok = unknown → 立即置 status: standby|disabled，不得启用
# 7. 更新 deploy/versions.lock（重跑 01_lock_versions.sh）
```

> **注意**：`commercial_ok` 只有 `true` / `false` / `unknown` 三态，**没有「大概可以」**。
> 拿不准就写 `unknown` + `verified: false`，并在 §7 待核实清单里登记一条。

---

## 9. 复查记录

| 日期 | 复查范围 | 结论 | 处理 |
|---|---|---|---|
| 2026-09-15 | 建立矩阵初稿 | — | 待模型引入后逐条填充 |
| **2026-09-16** | **6 个实际在用模型的许可逐条核对（P5-10 主体工作）** | 4 个结论可商用（SDXL / klein / flux2-vae / qwen3-4b）；2 个 unknown（`sdxl_vae_fp16fix` / `qwen_3_4b_fp4_flux2`），**均已 standby/disabled，不进交付链路 → 不阻塞 V1** | ① 新增 §0 总览（与 `model_registry.yaml` 联动）② 新增 §7 待核实清单 **13 条**（当时；后随节点侧与 P4/P5 计划模型核实**扩至 16 条**，权威数量以 §7 实际行数为准）③ 明确 §5 禁用清单（InstantID / InsightFace / FLUX dev 系 / CodeFormer）④ **暴露关键缺口：0/6 的 LICENSE 原文已入库** → 列为 P11-11 必修 |
| 2026-09-16 | V1 计划引入模型（ControlNet / IP-Adapter / 放大器 / LoRA / SAM / PuLID） | 见 §3–§5；其中 **ControlNet 权重、IP-Adapter、放大器权重**为高风险待核实项 | 已排入 §7 清单 #7/#8/#9，要求在各阶段接入前核实完毕 |
| **2026-09-16** | **32 个自定义节点的许可逐条实测登记（P2-05 与 P5-10 的交叉项）** | 来源为各仓库 `LICENSE*` 文件首行（`deploy/comfyui/evidence/custom_nodes_licenses.txt`）。结果：MIT 10 / Apache-2.0 5 / **GPL-3.0 13** / **CC BY-NC-SA 4.0（非商用）1** / **无 LICENSE 文件 3** | ① 新增 §1.1 节点许可表与红线条目 ② 新增 §1.2 **GPL-3.0 与交付形态的强关联结论**（约束 ADR-001）③ §7 #11/#12 标记完成、新增 #14/#15/#16（AGPL 传染性、face_yolov8n 权重、预处理器辅助权重）④ 禁用清单新增 4 项 ⑤ **发现 `ComfyUI-Upscaler-Tensorrt` 为非商用许可且当前装在环境里** → 已由 P2-05 白名单判定禁用 |
| 2026-09-16 | 模型与磁盘的实测核对（`engine/model_registry.yaml` 配套） | ✅ 6 个已登记模型的 `file_size` 与 `versions.lock [models]` **逐条完全一致**；模型实际路径确认在 `/root/autodl-tmp/comfyui-data/models/` | 在 registry 头部登记实测结果；补登两类此前未登记的磁盘文件（`ultralytics/face_yolov8n.pt`、`insightface/buffalo_l/*.onnx`） |
| **2026-09-16** | **节点许可的第二来源交叉验证 + 人脸链路红线细化（C 流）** | ① **交叉验证通过**：GitHub License API 独立跑 32 节点，结果与服务器读原文**逐条吻合**（GPL-3.0 13、无 LICENSE 3 指名一致）；② **新增人脸链路红线**：IP-Adapter **FaceID** 系列因强制 `insightface` → **FR-8.3 只允许 base/plus 变体**；③ **PuLID 结论修正**：找到商用路径（`PulidFluxFaceNetLoader`），InsightFace 路径明确禁止；④ 逐字引用了 `insightface` PyPI 的许可原文 | ① 新增 §1.1.1（交叉验证 + 方法论：**API 适合筛查，原文才行判定**；`NOASSERTION` 会藏 NC 条款）② §3 新增 IP-Adapter FaceID 禁用行、PuLID 双路径行、结论修正行 ③ §1 `insightface` 行补逐字引用 ④ §7 #10 更新为「部分解决」 ⑤ 产出 28/32 节点的「目录名→仓库 slug→SPDX」映射，供 §8 的 `source.repo` 字段直接使用 |
