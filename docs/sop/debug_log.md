# 工作流调试记录（Workflow Debug Log）

> 版本：v1.0 · 日期：2026-09-16 · 作者：Peterwong666
> 覆盖任务：**P3-12**
> 上游：`项目进展.md`（问题处置台账，项目级）· `docs/sop/workflow_spec.md`（编写规范）
>
> **本文件与 `项目进展.md` 的分工**
> - `项目进展.md` —— **项目级**问题：环境、升级、成本、流程事故（#1~#21）
> - **本文件** —— **每条工作流**的调试记录：这张图的节点为什么长这样、参数为什么取这个值
>
> 为什么要分开：半年后回看某条工作流时，需要的是「这条链路的坑」而不是
> 「整个项目发生了什么」。混在一起会让关键信息被淹没。
> 本文件里的条目会**引用** `项目进展.md` 的编号，不重复叙述。

---

## 0. 记录规矩

| 规矩 | 说明 |
|---|---|
| 格式 | **现象 → 分析 → 决策 → 处置 → 验证 → 沉淀**（与 `项目进展.md` 一致） |
| 只记**踩过的坑**，不记顺利走完的步骤 | 顺利的步骤写进 `_meta.note` 就够了 |
| 每条都要有**权威依据** | 要么是本机实测、要么是官方模板/官方文档；**不接受"我记得应该是这样"** |
| **不许把没验证的事写成已验证** | 未验证的一律标注「待 GPU 验证」，并给出验证方法 |
| 新增工作流时**先建空槽** | 见 §3 的模板 |

---

## 0.1 校验能力现状（先说清楚，避免误读本文档的结论）

| 级别 | 内容 | 现在能做吗 | 依赖 |
|---|---|---|---|
| **L1** 结构自洽 | 节点/连线/`targets`/`switches`/占位符自洽 | ✅ 能做 | `engine/validate.py`（纯离线） |
| **L2** 渲染冒烟 | 用 default/边界值/各开关组合各渲染一次 | ✅ 能做 | `engine/validate.py`（纯离线） |
| **L3** 节点合法性 | `class_type` 存在、入参名被接受、枚举取值合法 | ✅ **能做** | `deploy/schemas/object_info.v0.36.0.json`（2530 节点，已入库） |
| **L4** 真实出图 | 提交 → 出图 → 核对产物 | ❌ **做不了** | 需 GPU |

一条命令跑 L1+L2+L3（**L3 跑在渲染后的图上**，见 §0.2）：

```bash
PYTHONPATH=. backend/.venv/bin/python -m engine.validate
```

> 输出会**如实列出实际执行的级别**（如 `已执行: L1/L2/L3 · 未执行: L4`）。
> 只要 `L3` 未执行，命令会显式打印「节点类型/入参名/枚举取值**未经验证**」——
> 不允许出现"看起来全绿、其实是少做了一层"的误导。

### 0.2 L3 有两种用法，别只做一种

| 用法 | 校验对象 | 能抓到的错 |
|---|---|---|
| `26_validate_workflow.py <file> --object-info ...` | **模板原文件** | 节点类型写错、模板里的入参名/枚举值非法 |
| `engine.validate`（内置 `check_object_info`） | **渲染后的图** | 上面全部 **+ `targets` 注入的错误**（节点 ID 漂移、注入的入参名拼错、注入的枚举值非法） |

⭐ 第二种是模板校验**覆盖不到**的。`targets` 注入是整个 P3 最容易出错的地方
（节点 ID 是字符串手写的，改一次工作流就可能漂移），所以**两者都要做**。

---

## 1. 全局坑（跨工作流，适用于每一条）

这几条不属于某一条工作流，但**每新增一条都可能再撞上**，因此放在最前面。

### G1 · 引擎版本必须晚于模型发布 ⭐⭐ 代价：白下了 11GB 才发现跑不了

| | |
|---|---|
| **现象** | v0.3.75 上跑 FLUX.2 klein，`CLIPTextEncodeFlux` 报 `KeyError: 't5xxl'` |
| **分析** | 逐层读源码后链条闭合：`supported_models.py` 的 `Flux2.clip_target()` 是 `return None # TODO`（**半成品实现**）；外部 `CLIPLoader` 把 `QWEN3_4B` 路由去了 `z_image` 而不是 flux2。**根因是版本**：本机引擎 `2025-11-26`，klein 官方文档 `2026-01-15` —— 引擎比模型**早 2 个月** |
| **决策** | 升级到 v0.36.0（详见 `项目进展.md` #14/#16）。它是升级破坏面最小的时刻 |
| **沉淀** | ⭐ **引入新模型前，先用 `/object_info` 探测节点 + 读源码确认路由，再决定要不要下权重。** 本次先下了 11GB 才发现，顺序是错的。<br>⭐ 判据应是文档声明的最低版本，不是 `requirements.txt`（后者写裸 `torch` 会骗过 pip）。<br>⭐ 「版本够新」这类结论必须带**失效条件**。 |

### G2 · 大版本升级后必须做性能防回归 ⭐⭐ 代价：SDXL 悄悄劣化 1.58x

| | |
|---|---|
| **现象** | 升级后 SDXL 1024²/30 步 steady 从 median 4.60s 变成 7.26s（1.58x）。同机 klein 却有 median 1.83s —— **不是整机变慢，是 SDXL 这条路径特有** |
| **分析** | 先撞上"假因"（flash-attn/sageattention 的 ABI 失配很显眼），但硬证据否掉了它：升级前后日志同为 `Using pytorch attention`，flash-attn 只在显式传参时启用，而启动脚本从未传过。**真因是 v0.36.0 新增且 Nvidia 上默认开启的 dynamic VRAM** —— SDXL 才 6.9GB，24G 卡完全放得下，做权重流式搬运是纯开销 |
| **决策** | 单变量 A/B（四变体各 N=8）定位，定案 `--highvram` |
| **验证** | 地板 4.59s vs 升级前基线 4.54s（差 1%，回归已修复）；klein 未受影响（两档均 min 1.52s）。已固化进 `03_start_comfyui.sh` |
| **沉淀** | ⭐ **「能出图」的冒烟不等于升级成功。** 本次若只做冒烟，7.26s 会被当成基线写进 PRD，成本模型与排期一起带偏。<br>⭐ **"版本号变新" ≠ "行为不变"**：这是默认值变更而非 bug。所以 `versions.lock` 只锁包版本不够，**必须同时锁启动参数**。<br>⭐ **"可选加速装不上了"极易被误判为性能回归的原因** —— 报错很显眼，但报错不等于原本在用。 |

### G3 · 共享宿主机上必须用 min（地板）判读稳态，不能用 median ⭐

| | |
|---|---|
| **现象** | 同一配置跨轮次复测，N=12 比 N=8 整体慢约 0.3s，照 median 判读会得出"仍有 8% 残留回归"的错误结论 |
| **分析** | 原始样本是**双峰结构**：多数落在地板，每约 3 个样本一次尖峰。**关键：升级前的基线里同样有尖峰** → 是共享宿主机的周期性干扰（128 核、load ~19，多容器共用磁盘/内存带宽），不是自身回归 |
| **沉淀** | ⭐ 共享 GPU 上 **min 才是稳态能力的稳健估计**，median/P95 只作参考。<br>⭐ **同一配置跨轮次复测是必要的** —— 单轮结果不足以定论。 |

### G4 · 有官方模板就不要自己拼

| | |
|---|---|
| **处置** | 随升级装好的 `comfyui-workflow-templates` 包里有 `image_flux2_klein_text_to_image.json`。展开其 subgraph 直接得到正确节点组合，省掉若干轮 GPU 试错 |
| **沉淀** | ⭐ 引入新链路先找官方模板；有则照抄并在 `_meta.source` 写明模板文件名。 |

### G5 · 不同采样范式的 seed / steps 挂在**完全不同的节点**上

| | |
|---|---|
| **现象** | 用「按节点类型定位」的写法取 seed，在 SDXL 上正常、在 klein 上取不到 |
| **分析** | SDXL：`seed`/`steps`/`cfg` 都在 `KSampler` 一个节点上。<br>klein：`RandomNoise.noise_seed`、`Flux2Scheduler.steps`、`CFGGuider.cfg` —— **分散在三个节点，且入参名不同**（`noise_seed` 而非 `seed`） |
| **处置** | ① `07_bench_workflow.py` 改为**按入参名**匹配（兼容两种范式）② `registry.yaml` 的 `targets` 显式写明各自位置 |
| **沉淀** | ⭐ **不要假设"采样参数都在采样器节点上"。** 新增工作流时先 `08_probe_nodes.py` 查清入参挂载点，再写 Schema。 |

### G6 · `_meta` 段是"可以随时改"的安全区

| | |
|---|---|
| **要点** | `_meta` 在渲染时被**整段剥离**（`engine/render.split_definition`），不进 ComfyUI。因此修改 `_meta`（补 `id`/`version`/`models`/`node_titles`）**不会影响提交给引擎的图**，既有 GPU 验证继续有效 |
| **反之** | 改动**节点图**（增删节点、改连线、改入参字面值）会使 GPU 验证失效，必须 `version + 1` 并把 `verification` 降回 `static_only`，重跑 L4 才能改回 `gpu_verified` |
| **沉淀** | ⭐ 这条区分很重要：它让我们能安心整理元数据，而不必每改一次注释就重跑一次出图。 |

### G7 · 提示词权重语法 `(word:1.2)` 在 klein 链路上是否生效 🔴 **待验证（跨流风险，P3 未定型）**

> 来源：C 流在 `assets/prompt_lib/` 与 `docs/sop/prompt_guide.md` 里提供权重语法，
> 理由是节点白名单保留了 `ComfyUI_ADV_CLIP_emb`；team-lead 转来要求查清
> 「权重是**空转**」还是「**压根没接进 klein 图**」—— 这是两个不同的问题。

| | |
|---|---|
| **已确认的事实（静态，2026-09-16）** | `workflows/` 下两条工作流的 `class_type` 全量枚举表明：**`t2i_v1` 与 `flux2_klein_t2i_v1` 都没有 `ADV_CLIP_emb` 节点**（`grep -iE "adv_clip\|CLIP_emb" workflows/` 无匹配）。klein 的提示词走 `CLIPTextEncode`（节点 `"4"`）→ `CLIPLoader`（`type: flux2`）→ `KleinTokenizer`；SDXL 走 `CLIPTextEncode`（节点 `"6"`/`"7"`）→ `CheckpointLoaderSimple` 的 CLIP。 |
| **因此结论收窄为** | 至少在**当前图**上，问题不是"权重空转"，而是**`ADV_CLIP_emb` 根本没有接进任何一条链路**。team-lead 指出的两种情形里，这是第二种。 |
| **但还没到能下结论的程度** ⚠️ | 还差两问：<br>① **ComfyUI 核心的 `CLIPTextEncode` 自己是否解析权重语法？** 对 SD1/SDXL 链路，权重解析通常在 `comfy/sd1_clip` 的 tokenizer 里（而非仅 ADV_CLIP_emb）；对 klein 的 Qwen3 tokenizer 则**很可能不解析**，那时 `(word:1.2)` 会被当成**字面文本**送进模型 —— 那就不是"空转"，而是**主动破坏提示词**（多出括号与数字）。<br>② C 流的配方里**目前没有任何 `(word:1.2)` 写法**（已用正则扫过 `assets/prompt_lib/`，无匹配），所以此刻没有正在受损的内容，**是"先查清再上"的窗口期**。 |
| **待验证 ①（成本极低，无需 GPU）** | 在服务器上读源码确认两个 tokenizer 是否解析权重：<br>`grep -n "parse_parentheses\|escape_important\|def tokenize" /root/ComfyUI/comfy/sd1_clip.py`<br>`grep -rn "parse_parentheses\|weight" /root/ComfyUI/comfy/text_encoders/*.py \| head`（重点看 klein/Qwen3 对应的 tokenizer 文件）<br>**这一步能直接判定"字面文本"还是"被解析"。** |
| **待验证 ②（需 GPU，L4）** | 固定其它参数，同一条提示词分别以「带权重」「去权重」两种写法出图，人工比对主体是否变化；**再在 SDXL（cfg>1，可作对照组）上做同样实验**。若 SDXL 有差异而 klein 无差异 → 权重语义只在 cfg>1 链路有效。 |
| **待验证 ③（需 GPU，L4）** | 若验证 ① 判定"字面文本"，还要确认**破坏程度**：把 `(word:1.2)` 原样送进 klein，看画质相比干净提示词是否显著劣化。这决定 C 流是"删掉 weight 语义"还是"必须加节点接进去"。 |
| **对 C 流的影响** | `prompt_guide.md` 的 weight 小节暂按**待定**处理；在验证 ① 出结果前，提示词库先不要使用该语法（当前也确实未使用）。**在 SDXL 链路上则不受此问题影响与否尚需验证 ② 的对照结果。** |
| **沉淀** | ⭐ **"某能力依赖某个节点"这类判断，要落到"该节点是否真的在图上"这一层去核实** —— 白名单里保留了某个节点，不等于生产的图里挂了它。这是 `workflow_spec.md` §2.1 要求枚举 `class_type` 的实用价值。<br>⭐ 跨流能力（提示词语法 ↔ 工作流接线）必须在**工作流定型时**查清接线，否则双方各自以为对方负责。 |

### G7 · 提示词权重语法**不依赖** `ComfyUI_ADV_CLIP_emb`（已用 object_info 证伪）

| | |
|---|---|
| **起因** | C 流提出：提示词权重语法 `(word:1.2)` 依赖保留节点 `ComfyUI_ADV_CLIP_emb`；而 klein 是 `cfg=1` 的蒸馏链路，权重可能空转 |
| **核查（L3，纯离线）** | 从 `object_info.v0.36.0.json` 读出该包**只提供 4 个节点**，全部是 `BNK_` 前缀：`BNK_CLIPTextEncodeAdvanced` / `BNK_CLIPTextEncodeSDXLAdvanced` / `BNK_AddCLIPSDXLParams` / `BNK_AddCLIPSDXLRParams`。而我们两条工作流用到的条件编码节点是 **核心** `CLIPTextEncode`（`python_module=nodes`），图里**一个 `BNK_*` 都没有** |
| **结论 ①** | **C 流的前提不成立**：`(word:1.2)` 是核心 `CLIPTextEncode` 自带的能力，不是那个节点包给的。ADV_CLIP_emb 真正多出来的是两个参数 —— `token_normalization`（4 种）与 **`weight_interpretation`（5 种：`comfy` / `A1111` / `compel` / `comfy++` / `down_weight`）**。也就是说它管的是「**权重怎么解释**」，而不是「**能不能写权重**」 |
| **结论 ②** | 所以这**不是**「ADV 节点没接进图」的问题，也**不是**「权重空转」的问题 —— 而是"权重语法本来就生效，只是解释方式用的是 ComfyUI 默认语义"。想改解释方式才需要接 `BNK_*` 节点 |
| **仍未验证（L4）** | `cfg=1` 下权重变更的**效果量级**是否实用。蒸馏模型没有 guidance 放大，权重的作用可能被削弱 —— 这是**经验问题，必须实测**，不能靠推理下结论 |
| **验证方法（待 GPU）** | 同一 seed、同一构图，跑 3 组：① 无权重的基线 ② `(mug:1.5)` 加权 ③ `(mug:0.6)` 降权。**在 klein（cfg=1）与 SDXL（cfg≈6.5）上各做一遍**做对照。若 klein 三组近乎一致、SDXL 三组有明显差异 → 权重在 klein 上"结构上生效但效果量级不足"，`prompt_guide.md` 的写法需要为 klein 单独说明 |
| **沉淀** | ⭐ **"某能力依赖某自定义节点"这种判断，先用 `object_info` 查该包到底提供了哪些节点类、以及我们图里用了哪些** —— 这一步纯离线，几分钟就能把"猜测"变成"事实"。本次若不做，会带着错误前提去改提示词库 |

### G8 · `Power Lora Loader (rgthree)` **不能被 `targets` 注入**（已用真实校验器证明）

| | |
|---|---|
| **起因** | C 流从服务器日志发现 `rgthree-comfy` 与 ComfyUI 新的 Node 2.0 渲染不兼容，警告"可能**无法访问节点属性**"，而 Power Lora Loader 正是靠属性访问工作的 |
| **核查（L3，纯离线）** | A/B 探针，用真实校验器 `26_validate_workflow.py --object-info`：<br>① `Power Lora Loader (rgthree)` 注入 `lora_1` → **`入参 'lora_1' 不被该节点接受（可选: ['clip','model']）`，VALIDATE=FAIL**<br>② 核心 `LoraLoader` 注入 `lora_name`/`strength_model`/`strength_clip` → **入参名全部被接受**（唯一报错只是我们用的假文件名不在模型清单里，与注入机制无关） |
| **根因** | `object_info` 里 `Power Lora Loader (rgthree)` 的 `required` 是**空的**，`optional` 只有 `model`/`clip` —— LoRA 条目是**动态 widget 属性**，不是声明式入参。而 ComfyUI 的 prompt 校验要求入参必须落在 `required ∪ optional` 内 |
| **结论** | **C 流的担心成立，且比预想的更硬**：不是"可能访问不到属性"，而是 **LoRA 参数压根没有可注入的入参** → 该节点的 LoRA 选择/权重**无法由外部 API 驱动** |
| **处置** | P4-04 多 LoRA 叠加（FR-8.5）**用核心 `LoraLoader` 串链**（每个 LoRA 一个节点），不要依赖 Power Lora Loader。它可作为"手工在画布上调试"的便利工具，但**不能作为自动化链路的一环** |
| **遗留** | 多 LoRA 的参数 Schema 形态待定（核心节点是「一节点一 LoRA」，所以要么固定最大条数、要么动态建链）。这是 P4 的设计议题，已登记 |
| **沉淀** | ⭐ **`targets` 只能注入 `required ∪ optional` 里声明过的入参。** 凡是"参数藏在 widget 属性里"的节点（不少 rgthree / 便利类节点如此），外部 API 都驱动不了。<br>⭐ **选节点时应有一条判据：能进 `object_info` 的才可被自动化驱动。** 这条判据纯离线可查，应该在 P4 选型时就用上 |

---

## 2. 各工作流的调试记录

### 2.1 `t2i_v1`（SDXL 文生图）· version 1 · `gpu_verified`

> 链路：`CheckpointLoaderSimple` → `CLIPTextEncode`×2 + `EmptyLatentImage` → `KSampler` → `VAEDecode` → `SaveImage`

| # | 现象 | 分析 | 决策/处置 | 沉淀 |
|---|---|---|---|---|
| T1-1 | 首个"基准值"记录为 **12.0s**，但后来 30 步反而只要 4.6s —— 步数更多却快 2.6 倍 | 口径不同：12.0s 是**冷启动**（含 6.9GB 权重从磁盘读入 + 显存分配），4.6s 是**稳态**。`[warmup] 14.78s` 正好复现了冷启动量级 | 统一口径为**稳态**；在 `07_bench_workflow.py` 里把「先跑 1 次 warmup 且不计入统计」写成硬约束 | ⭐ **性能数字必须带口径**（冷启动/稳态、并发、分辨率、步数）。没有口径的数字比没有数字更危险（`项目进展.md` #15） |
| T1-2 | 升级 v0.36.0 后同一工作流从 4.60s 劣化到 7.26s | 见全局坑 **G2**（dynamic VRAM 默认开启） | 启动脚本固定 `--highvram` | 见 G2/G3 |
| T1-3 | 生产配置下复测仍有约 0.3s 波动，疑似残留回归 | 见全局坑 **G3**（宿主机周期性尖峰） | 改用 **min** 判地板：4.59s ≈ 基线 4.54s（差 1%） | 见 G3 |
| T1-4 | `SaveImage.filename_prefix` 仍是 `smoke/t2i`，不符合规范 §4.2 的 `<id>/<变体>` | 改前缀会改变节点图 → **使现有 GPU 验证失效** | **有意保留**，留到 v2 与高清修复分支一起改并重跑 L4。产物归属靠 DB 记录而非路径 | ⭐ 不要为了"干净"去改一个**已被验证过的**图的任何字节 |

**当前 GPU 依据**：`项目进展.md` #8.5（首次出图）· #16 验证 5 ③（升级后复测）· #18（生产配置标定）。
**性能参考值**（`--highvram`，1024²/30 步，N=12，warmup=1）：min **4.59s** / median 5.00s / P95 5.48s。

**待办**：L3 未做（缺 object_info）；`sampler_name`/`scheduler` 的 enum 选项集合需 L3 核对。

---

### 2.2 `flux2_klein_t2i_v1`（FLUX.2 [klein] 4B Distilled 文生图）· version 1 · `gpu_verified`

> 链路：`UNETLoader` + `VAELoader` + `CLIPLoader` → `CLIPTextEncode` → `ConditioningZeroOut`（负向）+ `EmptyFlux2LatentImage` + `Flux2Scheduler` + `RandomNoise` + `CFGGuider` + `KSamplerSelect` → `SamplerCustomAdvanced` → `VAEDecode` → `SaveImage`

**这条链路是坑最多的一条，建议新做事先完整读一遍本节。**

| # | 现象 | 分析 | 决策/处置 | 沉淀 |
|---|---|---|---|---|
| K1-1 | **`CLIPTextEncodeFlux` 报 `KeyError: 't5xxl'`** | 该节点读 `clip.tokenize(t5xxl)["t5xxl"]`，而 klein 的 tokenizer 输出键是 `qwen3_4b`。**旧版引擎写的源码里 flux2 的文本编码器接线是 `return None # TODO`** | 不用该节点。**取官方模板**得到正解：**`CLIPTextEncode` + `CFGGuider` + `ConditioningZeroOut`** | ⭐ **有官方模板时以官方模板为准**，不要自己拼（`项目进展.md` #16 验证 3） |
| K1-2 | **三件套都下完才发现编码器不兼容**：`size mismatch ... [1024,1280] vs [1024,2560]` | 当时归因为"fp4 版架构不同，换全精度版即可"。**这个归因只对了一半** —— 真因是引擎版本早于模型发布（见 G1），换全精度版会以另一种方式失败（变成 K1-1） | 放弃 fp4 版，改用全精度 `qwen_3_4b.safetensors`；但**真正解决问题的是升级引擎** | ⭐ 遇到 `shape mismatch` 不要停在"文件版本不对"，要往上追问**「这个引擎版本到底支不支持这个模型」**（`项目进展.md` #13→#14） |
| K1-3 | **我们下的是蒸馏版，不是 Base 版** | 官方模板含 Base 与 Distilled 两个子图；对照发现 `flux-2-klein-4b.safetensors`（无 `-base-` 前缀）是**蒸馏版** | 蒸馏版**只需 4 步、cfg=1**；负向用 `ConditioningZeroOut` 整体置零。`registry.yaml` 据此把 `steps` 上限收窄到 8、`cfg` 上限收窄到 2.0 | ⭐ **先确认自己是哪个变体再调参。** 用 Base 版的参数跑蒸馏版会得到"能出图但质量可疑"的结果 |
| K1-4 | `width`/`height` 改了之后画质下降，但看不出报错 | klein 的 `width`/`height` **同时**存在于 `EmptyFlux2LatentImage`（画布）与 `Flux2Scheduler`（sigma 曲线）。只改前者 → 曲线按错误分辨率生成 | Schema 里为 `width`/`height` 各声明**两个 target**（节点 `"6"` 与 `"7"`） | ⭐ **改参数"看起来没生效"时，先查是不是漏了 target**，而不是怀疑参数没传进去（规范 §5.1） |
| K1-5 | 参数改动"看起来完全没生效"且无任何报错 | 该参数指向的节点被 bypass 裁掉了 —— 注入先发生、节点后被删 | 渲染流水线把 **bypass 裁剪放在 targets 注入之前**；若仍指向被裁分支，**跳过注入并告警**而非报错 | ⭐ 见 `engine/render.py` 模块 docstring 的六步顺序说明 |
| K1-6 | 蒸馏版不需要负向提示词，但前端表单里出现了一个"填了不起作用"的字段 | 「负向被置零」是结构决定的，不是参数能改的 | **Schema 里不暴露 `negative_prompt`** | ⭐ 暴露一个不起作用的参数，比不暴露更糟 —— 用户会以为自己控制了它 |
| K1-7 | 采样参数（steps/cfg/seed）按"在采样器节点上"去找，找不到 | 见全局坑 **G5** | `targets` 显式写明：`steps→"7"`、`cfg→"9"`、`seed→"8".noise_seed` | 见 G5 |

**当前 GPU 依据**：`项目进展.md` #16 验证 5 ④（首次出图成功）· #18（生产配置复测）。
**性能参考值**（`--highvram`，1024²，4 步 / cfg=1，N=12，warmup=1）：min **1.52s** / median 1.83s / P95 2.67s；显存峰值 15.8G/24G。
**显存提示**：双底模常驻 13.1GB（见 `03_start_comfyui.sh` 的 `--highvram` 注释）。若未来同时常驻的模型总量逼近 24G，需重新评估 `--highvram`（它不卸载模型）。

**待办**：L3 未做；`filename_prefix` 仍为 `smoke/flux2_klein`（同 T1-4 的理由有意保留）；v2 计划增加参考图编辑分支。

---

### 2.3 计划中的工作流（槽位已建，定义文件尚未编写）

| id | 已识别的**预判风险**（写下来，避免重复踩） |
|---|---|
| `i2i_v1` 图生图 | ① `asset_id → 文件名` 的解析必须由 A 流以 `asset_resolver` 回调注入，**渲染器不猜路径**（规范 §5.2）② 素材上传边界（100KB–20MB / 64–2048px）要在 Schema 的 `image` 类型上对齐 PRD §6.2 |
| `upscale_v1` 高清修复 | 计划做成 `t2i_v1` 的**可选分支**而非独立 JSON，用 `_meta.switches` 声明 bypass。⚠️ 必须先确认"关闭分支时的重连"写到哪个节点 —— 这正是 K1-5 与规范 §6.3 要防的问题 |
| `inpaint_v1` 局部重绘 | 依赖 SAM / BiRefNet 分割链路；遮罩传递的入参名与类型不同（`MASK` 而非 `IMAGE`），需先 `08_probe_nodes.py` 查清 |
| `style_transfer_v1` 风格迁移 | 与 `i2i_v1` 参数面高度重叠，**先评估是否合并**，避免两条工作流各自演进（双真相） |
| `batch_v1` 批量 | ⚠️ **待 team-lead 决策：它可能不该是一条工作流。** 批量是**任意工作流的编排模式**（`POST /batches` 按 SKU 拆子任务），节点图与 `t2i_v1` 完全相同。登记成独立工作流会产生两份内容相同、版本却各自演进的 JSON |
| LoRA 相关（P4-04 / FR-8.5） | ⚠️ **多 LoRA 必须用核心 `LoraLoader`**，不能用 `Power Lora Loader (rgthree)` —— 见 G8。另需定"固定最大条数 vs 动态建链"的 Schema 形态 |
| 图生图参考图（P4 / FR-8.x） | `asset_id → 文件名` 必须由 A 流的 `asset_resolver` 回调注入；渲染器不猜路径（规范 §5.2） |

---

## 2.4 ⚠️ 两条已注册工作流**尚未通过**「渲染路径 L4」

这是当前最需要说清楚的一件事 —— **注册表里两条工作流的 `status` 都是 `disabled`**，
不是遗漏，而是如实反映验证状态：

| 概念 | 含义 | 现在的状态 |
|---|---|---|
| `verification: gpu_verified` | **节点图本身能出图**（哪怕是用裸提交模板的方式验证的） | ✅ 两条都满足（`项目进展.md` #16 验证 5 ③④ / #18） |
| `render_path_l4` | **我们的渲染路径也能出图**：`engine/render` → 真实引擎 → 核对产物 | ❌ **两条都是 false** |

**为什么这个区分是必要的**：既有 GPU 证据来自 `07_bench_workflow.py` /
`27_gpu_verify.sh`，它们把**模板 JSON 原样提交**，完全没经过 `engine/render`。
而生产走的是渲染路径 —— 中间多了 `targets` 注入、seed 解析、bypass 裁剪、
输出前缀覆写这一整套。**"裸提交能出图"不能推出"渲染路径能出图"。**
（这正是 G6 的反面：`_meta` 改动不影响验证，但**渲染逻辑**是另一条代码路径，
必须单独验证。）

**把 `render_path_l4` 变成 true 需要做的（待 GPU，一次跑完两条）**：

1. 走渲染路径提交：`render(definition, entry.schema, params)` → 提交 → 轮询 → 取图
   （比照 `07_bench_workflow.py` 的提交/轮询逻辑，但把 `wf` 换成 `RenderResult.workflow`）
2. **核对产物与预期一致**（这是关键，不是"没报错就算过"）：
   - 用**显式 seed**（不用 `-1`）跑两次，两次产物应**完全一致**
   - 校验 `width`/`height` 真的落到了两个节点（klein：`"6"` 与 `"7"`）
   - 校验产物 PNG 里有 `wf_meta`，且其中 `seed` 等于**显式传入的 seed**
     （验证 §7.1 的「seed 必须由渲染器解析并回写」真的生效）
   - 校验 ComfyUI 自己写的 `prompt` chunk 仍在（G6 之外的产物侧证据）
3. 跑通后：`status: enabled` + `render_path_l4: true`，并在 `changelog` 记一条

> 纪律：**上面 4 项全部有输出，才允许改 `render_path_l4`。**
> 只做"提交后没报错"就改标志，等于把"未验证"包装成"已验证"。

---

## 3. 新工作流的记录模板（复制这段）

```markdown
### 3.x `<workflow_id>`（标题）· version N · `verification 状态`

> 链路：<用 A → B → C 的形式写清节点顺序>

| # | 现象 | 分析 | 决策/处置 | 沉淀 |
|---|---|---|---|---|
| X-1 | | | | |

**依据来源**：<官方模板文件名 / 本机实测 / 官方文档链接>
**当前 GPU 依据**：<具体到 项目进展.md 的编号；没有就写"无">
**性能参考值**：<必须带口径：启动参数 / 分辨率 / 步数 / N / warmup / 判据>
**待验证**：<L3 / L4 状态；未验证之处必须写明验证方法>
**待办**：
```

填写要求：
1. **「沉淀」列必须写成可复用的规则**，不是"这次改了 X"。判断标准：半年后另一个人能照着它避免同类错误。
2. **性能数字必须带完整口径**（G2/T1-1 的教训）。
3. **没做过的验证不许写"通过"**，写「待 GPU 验证」+ 验证方法。

---

## 4. 变更记录

| 日期 | 版本 | 变更 |
|---|---|---|
| 2026-09-16 | v1.0 | 初稿：6 条全局坑（G1~G6）+ 两条已发布工作流的完整调试记录（T1-* / K1-*）+ 5 条计划的预判风险 + 记录模板 |
| 2026-09-16 | v1.1 | **L3 解锁**（`object_info.v0.36.0.json` 已入库）→ 更新 §0.1 并新增 §0.2「L3 的两种用法」；新增 **G7**（提示词权重不依赖 ADV_CLIP_emb，已证伪）、**G8**（Power Lora Loader 不可注入，已证明）；新增 **§2.4** 说明两条工作流为何 `status: disabled`（`render_path_l4` 未通过）及其 4 步验证清单 |
| 2026-09-16 | v1.1 | 新增 **G7**（提示词权重语法在 klein 链路是否生效）—— 由 C 流提出、team-lead 转办的跨流风险。已静态收窄结论：两条链路**都没有** `ADV_CLIP_emb` 节点，故问题不是"权重空转"而是"根本没接进去"；另列出 3 项待验证（1 项免 GPU 源码确认 + 2 项 L4） |
