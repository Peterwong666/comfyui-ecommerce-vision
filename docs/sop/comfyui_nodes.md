# ComfyUI 节点：是什么、有什么用、怎么工作

> **为什么需要这份文档**：本项目的一切——工作流、参数注入、白名单、L3 校验、性能口径——都建立在「节点」这个概念上。
> 而这个词在语境里同时指三件不同的东西（一个计算单元 / 一种能力 / 一个参数落点），不先分清会产生大量误判。
>
> 最后核实：**2026-09-16**（环境：ComfyUI v0.36.0，2530 个节点类）

---

## 1. 节点是什么

**一个节点 = 一个只做一件事的计算单元。**

ComfyUI 把「出一张图」拆成几十个小步骤，每步封装成一个节点：加载模型、编码提示词、采样、解码、保存……
节点之间用**连线**传递数据，整体连成一张**有向无环图（DAG）**。

一张最简文生图的工作流长这样：

```
CheckpointLoaderSimple ──MODEL──────────────────────────┐
        │                                               │
        ├──CLIP──> CLIPTextEncode ──CONDITIONING────────┤
        │                                               ▼
        └──VAE──────────────────────────────────>  KSampler ──LATENT──> VAEDecode ──IMAGE──> SaveImage
                                                        ▲
                                EmptyLatentImage ───────┘
```

**读图方法**：箭头是数据流方向，标签是**数据类型**。`KSampler` 要凑齐 `model`（模型）、`positive`/`negative`（条件）、
`latent_image`（初始噪声）四路输入才能跑 —— 少一路它就无法执行。

---

## 2. 四条规则（理解了这四条，就看懂了节点）

### 规则 1 · 节点有类型

每个节点有一个 `class_type`，例如 `KSampler`、`LoadImage`、`CheckpointLoaderSimple`。
**类型决定了它能做什么、要什么输入、给什么输出。**

我们环境里有 **2530 种**节点类型 —— 其中大部分由 32 个自定义节点包提供，这也是为什么需要**节点白名单**（见 §5）。

### 规则 2 · 入参分两类

| 类别 | 含义 | 例子 |
|---|---|---|
| **widget 常量** | 直接在节点上填的值 | `KSampler.steps = 30`、`LoadImage.image = "a.png"` |
| **连线输入** | 接上游节点的输出 | `KSampler.model ← CheckpointLoaderSimple 的输出 0` |

**这两类的界限是本项目的一个核心约束**：平台只能注入「widget 常量」，
且**必须该入参在 `/object_info` 的 `required ∪ optional` 里声明过**（见 §4 硬约束 ①）。

### 规则 3 · 输出带序号

一个节点可以有多个输出，用**序号**区分。连线写成 `["节点ID", 输出序号]`：

```json
"positive": ["4", 0]     // 取节点 4 的第 0 号输出
```

⚠️ 容易踩的地方：**同名节点的输出含义不同**。例如

| 节点 | 输出 0 | 输出 1 | 输出 2 |
|---|---|---|---|
| `LoadImage` | `IMAGE` | `MASK` | — |
| `LoadImageMask` | `MASK`（**只有这一个**，不产出 IMAGE） | — | — |
| `CheckpointLoaderSimple` | `MODEL` | `CLIP` | `VAE` |
| `InpaintModelConditioning` | `positive` | `negative` | `latent` |

### 规则 4 · 类型必须匹配

`MODEL` / `LATENT` / `IMAGE` / `MASK` / `CONDITIONING` / `VAE` / `CLIP` / `UPSCALE_MODEL` …各成一类。
**类型不符就连不上** —— 这是 ComfyUI 能在界面上就拦住大部分接线错误的原因，也是它比"写代码调 API"安全的地方。

> 顺带解释了一个常见困惑：为什么 `ImageInvert` **不能**用来反相蒙版？
> 因为它操作的是 `IMAGE`（RGB 颜色反相），而蒙版是 `MASK` —— 类型不同，连不上。反相蒙版要用 `InvertMask`。

---

## 3. 它怎么执行

**ComfyUI 从末端节点反向遍历依赖，按拓扑序执行，并缓存每个节点的结果。**

```
SaveImage 是终点 → 它需要 VAEDecode 的输出 → VAEDecode 需要 KSampler → …
→ 得到一张拓扑序 → 依次执行 → 结果缓存在节点上
```

三个直接后果：

| 后果 | 意义 |
|---|---|
| **改一个参数只重算受影响的节点** | 这是 ComfyUI 迭代快的原因。也解释了我们基准测试为什么必须带 `--warmup`：**第一次出图包含模型加载（约 15-20s），不是稳态**（见 `项目进展.md` #15） |
| **末端节点决定"跑什么"** | 所以规范要求工作流**恰好一个 `SaveImage`**，且提交生产版前必须移除 `PreviewImage` |
| **图是有向无环的** | 不支持循环。需要"迭代"要靠重复节点（如 `IterativeLatentUpscale`） |

---

## 4. 「节点」在本项目里的三个身份 ⭐

这是最容易混淆、也最关键的一节。**同一个词在不同的句子里指不同东西**：

| 身份 | 指什么 | 出现场景 |
|---|---|---|
| **① 能力积木** | 一种节点**类型**（`class_type`） | 「2530 个节点」·「节点白名单 keep 11 / disable 19」·「某个节点包注册了 85 个类」 |
| **② 工作流本体** | 图里**一个个具体的节点实例** | 「这条工作流有 13 个节点」·「节点 4 是 CLIPTextEncode」·「节点 ID 1-9 留给加载器」 |
| **③ 参数落点** | `targets` 指向的「**某个节点的某个入参**」 | 「`prompt` 写到节点 4 的 `text`」·「`width` 要同时写节点 6 和 7」 |

**分清这三者，很多困惑会消失**：
- 「节点缺失」通常指 ①（某个节点类型没注册成功）
- 「节点 ID 漂移」指 ②（工作流里的实例编号变了，导致 `targets` 指向错位置）
- 「参数注入不进去」指 ③（该节点的该入参不在 `required ∪ optional` 里）

### 由此推出的硬约束（每条都对应一次真实踩坑）

| # | 硬约束 | 为什么 | 出处 |
|---|---|---|---|
| **①** | **只有出现在 `/object_info` 的 `required ∪ optional` 里的入参，才能被 API 注入** | 有些节点把参数藏在 UI 的 widget 属性里，外部 API 根本没有入参可写 | `debug_log G8`：`rgthree` 的 `Power Lora Loader` 的 `required=[]`，平台**驱动不了**；同包的 `Lora Loader Stack` 有 10 个声明式入参，**可以** |
| **②** | **采样参数挂在哪个节点因模型而异** | 不同采样范式的参数不在同一个节点上 | `debug_log G5`：SDXL 的 seed 在 `KSampler.seed`；**klein 的在 `RandomNoise.noise_seed`**，steps 在 `Flux2Scheduler.steps`、cfg 在 `CFGGuider.cfg` |
| **③** | **同一个参数常常要写多个 target** | 有的参数必须同时喂给多个节点，只写一个会「能出图但质量下降且难以归因」 | 契约 §5.1：klein 的 `width` 必须**同时**写给 `EmptyFlux2LatentImage`（画布）与 `Flux2Scheduler`（sigma 曲线） |
| **④** | **枚举取值会随引擎版本变** | 枚举是引擎运行时生成的，不是固定的 | `项目进展.md` #16：`CLIPLoader.type` 从 17 项扩到 29 项。所以注册表里的枚举**必须逐项取自 `object_info` 快照**，不能凭记忆枚举 |
| **⑤** | **枚举为空 = 磁盘上没有该权重文件** | `/object_info` 的枚举是**对模型目录的实时扫描结果** | 本项目的实际应用：`UpscaleModelLoader.model_name`、`LoraLoader.lora_name`、`IPAdapterModelLoader.ipadapter_file`、`CLIPVisionLoader.clip_name` **全部为空** → 说明这些权重一个都没有，而不是"不可注入" |

---

## 5. 核心节点 vs 自定义节点

| | 核心节点 | 自定义节点 |
|---|---|---|
| 来源 | ComfyUI 本体（`nodes.py` 与 `comfy_extras/nodes_*.py`） | `custom_nodes/<包名>/` 下的第三方包 |
| 数量（本环境） | 约 1900+ | 约 600+（来自 **32 个包**） |
| 稳定性 | 随引擎版本演进，但接口相对稳 | **质量差异极大**，且是升级时的主要破坏来源 |
| 许可 | GPL-3.0（ComfyUI 本体） | **各异**，必须逐个核对 |

### 为什么不直接用全部 32 个包

`node_whitelist.yaml` 把它收敛为 **keep 11 / disable 19 / review 2**。禁用理由分三类：

| 类别 | 例子 | 理由 |
|---|---|---|
| **许可红线** | `ComfyUI-Upscaler-Tensorrt` | **CC BY-NC-SA 4.0（非商用）** —— 唯一一条真实红线 |
| **技术损坏** | `nunchaku` / `smZNodes` | 升级后 ABI 失配（**已通过卸载 sageattention 修好**）；`TeaCache` 仍坏 |
| **架构性负资产** | `ComfyUI-Easy-Use` / `ComfyUI-Coziness` | **把参数藏起来**（藏进 widget 属性 / 藏进提示词文本）→ 平台驱动不了。⚠️ 这类里 **6 个技术与许可都没问题**，禁用纯属架构原因 |

> ⭐ 最后一条是本项目与「交互式 ComfyUI 用户」的**根本取向差异**：
> 界面用户要「省事」，本平台要「**可注入、可校验、可复现**」。
> 一个功能再需要，只要参数注不进去，在这套平台里就等于不可用。

### 升级时节点为什么会坏（三种机制）

| 机制 | 例子 | 为什么 |
|---|---|---|
| **C++ 扩展 ABI 绑定 torch 大版本** | `flash-attn`、`sageattention` | 针对 torch 2.5.1 编译的 `.so`，在 torch 2.13 下 `undefined symbol` |
| **上下游 API 被移除** | `TeaCache` | 依赖的 `precompute_freqs_cis` 已从 ComfyUI 移除 |
| **模块级 import 的连锁毒化** | `smZNodes`、`nunchaku` | 一个坏包在**模块级** import 失败，会拖垮整条 `diffusers` 链，让**不相关的节点也一起挂** |

> ⚠️ 第三种最隐蔽：症状是「A 节点坏了」，真因却是「B 包坏了」。
> 所以排查纪律是：**先把同类包列全**，而不是见一个修一个（我们因此漏掉过 sageattention，代价是两个节点白坏了一天 —— 见 `项目进展.md` #23）。

---

## 6. 最小可用节点集速查表

> 只列本项目**实际用到**的节点，按工作流规范 §3.2 的 ID 分段组织。
> 入参格式：`名:类型`，`d`=默认值。标 ⚠️ 的是易错点。
> **本表是参考，权威永远是 `deploy/schemas/object_info.v0.36.0.json`（2530 节点的实时快照）。**

### 1–9 段 · 加载器

| 节点 | 关键入参 | 输出 | 备注 |
|---|---|---|---|
| `CheckpointLoaderSimple` | `ckpt_name`（枚举：当前仅 `sd_xl_base_1.0.safetensors`） | `0:MODEL` `1:CLIP` `2:VAE` | 一把出三件；SDXL 路线的入口 |
| `UNETLoader` | `unet_name` `weight_dtype` | `0:MODEL` | klein 走这个（不是 Checkpoint） |
| `VAELoader` | `vae_name` | `0:VAE` | — |
| `CLIPLoader` | `clip_name` `type` `device` | `0:CLIP` | ⚠️ klein 必须 `type=flux2` + `qwen_3_4b.safetensors`（全精度）；fp4 版不可用 |
| `UpscaleModelLoader` | `model_name` | `0:UPSCALE_MODEL` | ⚠️ **当前枚举为空** → 无放大器权重，两段式高清修复不可用 |

### 10–19 段 · 输入与条件

| 节点 | 关键入参 | 输出 | 备注 |
|---|---|---|---|
| `LoadImage` | `image`（枚举，当前仅 `example.png`） | `0:IMAGE` **`1:MASK`** | ⚠️ 一次出两路；若输入图是带 alpha 的 PNG，mask 就是它 —— **极性（是否 `1-alpha`）未实测** |
| `LoadImageMask` | `image` `channel`(`alpha/red/green/blue`) | **仅 `0:MASK`** | ⚠️ **不产出 IMAGE**，原图还得另接一个 `LoadImage` |
| `CLIPTextEncode` | `text`(multiline) `clip` | `0:CONDITIONING` | 正向/负向都靠它；klein 必须用它（不能用 `CLIPTextEncodeFlux`） |
| `ConditioningZeroOut` | `conditioning` | `0:CONDITIONING` | 把负向整体置零 —— 这就是 klein **没有负向提示词**的原因 |
| `ImageScale` | `image` `upscale_method` `width` `height` `crop` | `0:IMAGE` | method: `nearest-exact/bilinear/area/bicubic/lanczos` |
| `ImageScaleToTotalPixels` | `image` `megapixels`(d=1.0) | `0:IMAGE` | 按像素总量统一分辨率，比手算宽高更稳 |

### 20–29 段 · 采样

| 节点 | 关键入参 | 输出 | 备注 |
|---|---|---|---|
| `KSampler` | `model` `seed` `steps` `cfg` `sampler_name` `scheduler` `positive` `negative` `latent_image` **`denoise`** | `0:LATENT` | ⚠️ **`denoise` 是图生图/重绘的核心参数**（d=1.0，范围 0–1）。`steps` d=20、`cfg` d=8.0；`sampler_name` 45 项、`scheduler` 9 项 —— **枚举必须取自快照** |
| `EmptyLatentImage` | `width` `height` `batch_size` | `0:LATENT` | ⚠️ w/h **step=8**；`batch_size` **不要暴露给用户**（契约 §3.6 不变量 1） |
| `RandomNoise` | `noise_seed` | `0:NOISE` | ⚠️ klein 的 seed 挂这里，**入参名是 `noise_seed` 不是 `seed`** |
| `Flux2Scheduler` | `steps` `width` `height` | `0:SIGMAS` | ⚠️ klein 的 steps 挂这里；width/height **必须与画布节点一致** |
| `CFGGuider` | `model` `positive` `negative` `cfg` | `0:GUIDER` | klein 的 cfg 挂这里（蒸馏版固定 1.0） |
| `LatentUpscaleBy` | `samples` `upscale_method` **`scale_by`**(d=1.5) | `0:LATENT` | 免放大器模型的高清修复路线核心 |
| `SetLatentNoiseMask` | `samples` `mask` | `0:LATENT` | 局部重绘（免专用模型）的核心：只在 mask 区域加噪 |

### 30–39 段 · 后处理

| 节点 | 关键入参 | 输出 | 备注 |
|---|---|---|---|
| `VAEDecode` | `samples` `vae` | `0:IMAGE` | — |
| `VAEDecodeTiled` | `samples` `vae` `tile_size`(d=512) `overlap`(d=64) `temporal_size`(d=64) `temporal_overlap`(d=8) | `0:IMAGE` | 大图省显存。⚠️ 后两个是**为视频设计**的，静态图**也必须填** |
| `VAEEncode` | `pixels` `vae` | `0:LATENT` | 图生图入口 |
| `GrowMask` | `mask` `expand`(d=0) `tapered_corners`(**d=True**) | `0:MASK` | ⚠️ 两个默认值都会**悄悄改变 mask 形状**，JSON 里建议显式写死 |
| `InvertMask` | `mask` | `0:MASK` | 反相蒙版用它（**不是** `ImageInvert`） |
| `MaskToImage` | `mask` | `0:IMAGE` | 调试时看蒙版长什么样 |
| `ImageCompositeMasked` | `destination` `source` `x` `y` `resize_source` + 可选 `mask` | `0:IMAGE` | 把重绘结果贴回原图，保证**非选区严格不变** |
| `ImagePadForOutpaint` | `left/top/right/bottom`(step 8) `feathering`(d=40) | `0:IMAGE` **`1:MASK`** | 扩画布并自动出蒙版，比手搓简洁 |
| `DifferentialDiffusion` | `strength`(d=1.0) | `0:MODEL` | 接在 model 前，低 denoise 高清修复时减少块感 |

### 40–49 段 · 输出

| 节点 | 关键入参 | 备注 |
|---|---|---|
| `SaveImage` | `images` `filename_prefix` | ⚠️ 规范要求**恰好一个**；`filename_prefix` 固定 `<工作流id>/<变体>`，**不含 task_id** |

### 可选 · 需要额外权重的（当前**全部不可用**）

| 节点 | 需要什么权重 | 当前枚举 |
|---|---|---|
| `IPAdapterUnifiedLoader` / `IPAdapter` / `IPAdapterAdvanced` | IP-Adapter 本体 + CLIP Vision | **空** |
| `CLIPVisionLoader` | CLIP Vision 编码器 | **空** |
| `LoraLoader` / `LoraLoaderModelOnly` | LoRA 文件 | **空** |
| `ControlNetLoader` | ControlNet 权重 | **空** |
| `StyleModelLoader` | Style Model | **空** |
| `UpscaleModelLoader` | 放大器权重 | **空** |

> ⚠️ **不要用** `ComfyUI-Upscaler-Tensorrt` 的 `LoadUpscalerTensorrtModel` ——
> 它是当前唯一"枚举非空"的放大器入口，但许可为 **CC BY-NC-SA 4.0（非商用）**，白名单已 disable。

---

## 7. 一句话总结

> **节点 = 单一职责的计算单元；连线 = 带类型的数据流；工作流 = 节点的有向无环图。**
> 平台做的事，就是**把用户在界面上的输入，翻译成「某个节点的某个入参」** ——
> 所以「这个入参存不存在、叫什么名字、接受什么取值」决定了平台能不能驱动它。

---

## 相关文档

| 想深入了解 | 看这里 |
|---|---|
| 工作流怎么写（节点 ID 分段、`_meta`、bypass、校验分级） | [`workflow_spec.md`](./workflow_spec.md) |
| 参数 Schema 格式与跨流契约 | [`contracts.md`](./contracts.md) §3 |
| 哪些节点能用、为什么 | [`node_whitelist.yaml`](../../deploy/comfyui/node_whitelist.yaml) |
| 节点相关的踩坑实录 | [`debug_log.md`](./debug_log.md)（G1~G9） |
| 环境与操作 | [`runbook.md`](./runbook.md) |
