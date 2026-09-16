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

### G7 · 提示词权重语法 `(word:1.2)` 在 klein 上**不被解析、且会污染提示词** ⭐⭐ 源码级定论

> 来源：C 流在 `assets/prompt_lib/` 与 `docs/sop/prompt_guide.md` 提供权重语法，理由是节点白名单保留了 `ComfyUI_ADV_CLIP_emb`。
> team-lead 要求查清是「权重**空转**」还是「**压根没接进 klein 图**」。
> **两轮核查下来，结论都不在这两个选项里。**

**第一轮 · 静态核查（`object_info`）—— 排除"依赖 ADV_CLIP_emb"这个前提**

| | |
|---|---|
| 事实 | 该包**只提供 4 个节点**，全部是 `BNK_` 前缀（`BNK_CLIPTextEncodeAdvanced` / `BNK_CLIPTextEncodeSDXLAdvanced` / `BNK_AddCLIPSDXLParams` / `BNK_AddCLIPSDXLRParams`）。我们两条工作流的条件编码节点是**核心** `CLIPTextEncode`（`python_module=nodes`），图里**一个 `BNK_*` 都没有** |
| 该包真正的职责 | 多出 `token_normalization`（4 种）与 `weight_interpretation`（`comfy` / `A1111` / `compel` / `comfy++` / `down_weight`）—— 它管「权重**怎么解释**」，不管「**能不能写**权重」 |

**第二轮 · 源码级核查（team-lead 在 `/root/ComfyUI` 读源码）—— 定论**

```python
# comfy/sd1_clip.py:585-588  —— SD1Tokenizer.tokenize_with_weights 内部
text = escape_important(text)
if kwargs.get("disable_weights", self.disable_weights):
    parsed_weights = [(text, 1.0)]              # ← 跳过权重解析
else:
    parsed_weights = token_weights(text, 1.0)   # ← 解析 (word:1.2)

# comfy/text_encoders/flux.py:153,169 —— KleinTokenizer
class KleinTokenizer(sd1_clip.SD1Tokenizer):
    def tokenize_with_weights(self, text, return_word_ids=False, llama_template=None, **kwargs):
        tokens = super().tokenize_with_weights(llama_text, return_word_ids=return_word_ids,
                                               disable_weights=True, **kwargs)   # ← 关键
```

| # | 结论 | 说明 |
|---|---|---|
| ① | klein **有**解析能力，但被**显式关掉** | `KleinTokenizer` 继承 `SD1Tokenizer`（本来能解析），却传了 `disable_weights=True` |
| ② | ⚠️ **不是"空转"，是"主动破坏"** | 走 `parsed_weights = [(text, 1.0)]` 这一支时，**原样的 `(word:1.2)` 字符串会进入分词** —— 括号、冒号、数字全变成 token 喂给模型 |
| ③ | ⚠️ **我原先关于 cfg 的推断是错的** | 我曾推断「`cfg=1` → 正负向相减为 0 → 权重无效果」。**机制不对**：权重靠 tokenizer 产出 `(token, weight)` 对、再由 `encode_token_weights` 作用到 **embedding** 上，**与 CFG 无关**。所以只要解析开着，`cfg=1` 也照样有加权效果 |
| ④ | SDXL 链路**正常** | `t2i_v1` 走 SD1 tokenizer 且未 disable → 权重正常解析（仍值得按待验证 ② 做对照，但机制上成立） |

**处置**

| 对象 | 动作 |
|---|---|
| `prompt_guide.md` / 前端提示文案（C 流侧） | 口径必须是 **「klein 链路上**严禁**使用权重语法」**，而**不是**"权重无效" —— 后者会让用户以为"写了也没关系"，而实际是污染提示词 |
| 提示词库现状 | 配方里**目前没有** `(word:1.2)` 写法（已用正则扫过），所以此刻没有正在受损的内容，属"先查清再上" |
| 工作流本身 | klein 图**不改**。若要让 klein 支持权重，见待验证 ③ |

**待验证（全部需 GPU，并入 P4）· 已备好可执行探针**

```bash
# 离线预检（不需要 GPU）：三种模式都能构造 + 通过 L3
PYTHONPATH=. python -m engine.tools.probe_g7_weight --mode all --dry-run

# GPU 上真跑（固定 seed；产物落在 g7_probe_out/，供并排看图）
PYTHONPATH=. python -m engine.tools.probe_g7_weight --mode all --seed 20260916
```

⚠️ **探针只给一个客观信号：同一 seed 下「带权重 vs 不带权重」的产物像素（IDAT）是否逐字节一致。**

⚠️ **并且它自带两道自证 —— 否则「相同」可能是个假通过**（「相同 = 完全无影响」这个决定性结论，
隐含前提是"两次确实都真的执行了、且输入确实不同"）：

| # | 自证 | 不通过意味着什么 |
|---|---|---|
| 1 | **输入确实不同**（离线）：baseline 与 weighted 的图必须逐字节不同 | 相同 ⇒ 等于"拿自己跟自己比"，比对毫无意义 |
| 2 | **执行确实发生**（GPU 正对照）：同提示词、**只改 seed** 的那一组必须像素不同 | 仍相同 ⇒ 根本没真跑（缓存/提前返回/提交了同一份），**该模式所有"相同"结论作废** |

> 第 1 道自证**上线即抓到我自己代码里的一个真 bug**：变体构造只做了浅拷贝，
> 导致所有变体共享同一批节点 dict，后一次迭代写 `text` 时把已构造好的 baseline
> **追溯性改掉**了 —— 两个变体指向同一对象，比对恒为"相同"。
> 若没有这道自证，探针会把"根本没比对"报成"完全无影响"，**方向恰好是最危险的那种假通过**。
> 已改为深拷贝，并加了锁定该行为的回归测试。这也再次印证：**判据本身也要能被检出。**

| 结果 | 能得出的结论 |
|---|---|
| **相同** | 提示词改动对模型**完全没有影响**（**决定性**：权重既没生效、也没污染）——**前提是两道自证都通过** |
| **不同** | 提示词改动**确实进了模型**，但**不能推出"权重按预期生效"** —— 被当字面文本同样会改变结果。**必须人工看图**区分「主体更突出/更弱」还是「画面出现括号文字感/构图崩坏」|

**把"像素不同"说成"权重生效"，就是本节已经犯过两次的那类机制推断。** 探针脚本刻意不打印"生效/无效"这种结论。

**回填口径已由 C 流预先约定**（`docs/sop/prompt_guide.md` §1.1.1，四种结果各自的措辞与连带动作都定死了）：
回填时**只给字母 A/B/C/D**，语义表述由该文件负责 —— 这样避免"拿到结果后再往已有结论上靠"。
A = `klein-bnk` 像素相同（③ 已证伪）· B = 不同且人工判为权重生效 · C = 不同且判为字面污染 · D = 不同但人工无法判定（保守按 C）。
若出现口径表未预置的情形（如 `sdxl` 对照组反而"相同"），**先不回填**，先补表。

| # | 内容 | 目的 |
|---|---|---|
| ① | `klein-core` 模式：把 `(mug:1.5)` **原样**送进核心 `CLIPTextEncode` | 量化"主动破坏"的程度（是否显著劣化） |
| ② | `sdxl` 模式：SDXL 上「带权重 vs 去权重」对照 | 验证机制（对照组，预期有差异） |
| ③ | `klein-bnk` 模式：把 klein 的文本编码节点**从核心 `CLIPTextEncode` 换成 `BNK_CLIPTextEncodeAdvanced`**（注意：不是"接在它之前" —— `BNK_*` 自己就是文本编码节点，入参是 `clip`+`text`、输出 CONDITIONING，没有 conditioning 输入），看权重是否恢复生效 | 若能生效，klein 就能支持权重语法 |

> 探针的 `klein-bnk` 变体在**运行时**由注册表里那份 klein 工作流派生（只替换提示词编码节点，
> 连线与其余节点逐字节不动），**不落单独的 JSON** —— 否则注册表一改，探针副本就悄悄过期，
> 实验结论会建立在过期的图上。变体构造另有单测保证"只改了该改的那个节点"。

⚠️ **对 ③ 的预期要克制 —— 不要重犯本节 ③ 那条错。**
先前的写法是「这条路改的是 embedding、不依赖 CFG，`cfg=1` 下**应该有效**」。这又是一次
**未经验证的机制推断**，而且它站不住的地方很具体：

`KleinTokenizer.tokenize_with_weights()` **在内部硬编码了 `disable_weights=True`**（源码见上）。
也就是说，**任何**调用 `clip.tokenize*(...)` 的节点 —— 包括 `BNK_CLIPTextEncodeAdvanced` ——
拿到的都可能是**已经丢掉权重**的 token 序列。除非该节点**自己重新解析提示词并把权重直接作用到 embedding**，
否则换节点也救不回来。

**所以 ③ 的正确表述是「值得实测，但机制上并不保证有效」**，而不是"应该有效"。
把它列为待验证项的价值在于：**若它无效，"klein 支持权重"这条路就被证伪了，不必再投入**。

**沉淀（补充 ③ 的教训）**
⭐ **"这样改应该能行"和"我推断的机制是对的"是同一类错误** —— 本条目里已经犯过一次 cfg 推断，
不要在同一个条目里再犯一次。**待验证项要写成开放问题，不要夹带预期结论**：
`"X 能否恢复生效（未验证，且存在 Y 这个反例机制）"` 比 `"X 应该能生效"` 有用得多 ——
前者后人会去验，后者后人会**直接采信**。

**沉淀**

⭐ **推断出的机制必须用源码或数据验证后才能写进文档。** 我这次的 cfg 推断听起来很合理，实际是错的；若照它写进 `prompt_guide`，后人在 SDXL 上调权重时会被同样的逻辑误导。这正是 `项目进展.md` #14 那条教训的又一次复现（"不要停留在看起来合理的那一层"）。
⭐ **「该能力被禁用」与「该能力无效」是两种不同的用户指引**：前者要写"别写"，后者会被理解成"写了无害"。**先查清是哪一种，再写文案。**
⭐ **"某能力依赖某个节点"这类判断，要落到"该节点是否真的在图上"这一层核实** —— 白名单保留了某节点 ≠ 生产的图里挂了它。
⭐ 跨流能力（提示词语法 ↔ 工作流接线）必须在**工作流定型时**查清接线，否则双方各自以为对方负责。

### G8 · `Power Lora Loader (rgthree)` **不能被 `targets` 注入**（已用真实校验器证明）

| | |
|---|---|
| **起因** | C 流从服务器日志发现 `rgthree-comfy` 与 ComfyUI 新的 Node 2.0 渲染不兼容，警告"可能**无法访问节点属性**"，而 Power Lora Loader 正是靠属性访问工作的 |
| **核查（L3，纯离线）** | A/B 探针，用真实校验器 `26_validate_workflow.py --object-info`：<br>① `Power Lora Loader (rgthree)` 注入 `lora_1` → **`入参 'lora_1' 不被该节点接受（可选: ['clip','model']）`，VALIDATE=FAIL**<br>② 核心 `LoraLoader` 注入 `lora_name`/`strength_model`/`strength_clip` → **入参名全部被接受**（唯一报错只是我们用的假文件名不在模型清单里，与注入机制无关） |
| **根因** | `object_info` 里 `Power Lora Loader (rgthree)` 的 `required` 是**空的**，`optional` 只有 `model`/`clip` —— LoRA 条目是**动态 widget 属性**，不是声明式入参。而 ComfyUI 的 prompt 校验要求入参必须落在 `required ∪ optional` 内 |
| **结论** | **C 流的担心成立，且比预想的更硬**：不是"可能访问不到属性"，而是 **LoRA 参数压根没有可注入的入参** → 该节点的 LoRA 选择/权重**无法由外部 API 驱动** |
| **处置** | P4-04 多 LoRA 叠加（FR-8.5）**用核心 `LoraLoader` 串链**（每个 LoRA 一个节点），不要依赖 Power Lora Loader。它可作为"手工在画布上调试"的便利工具，但**不能作为自动化链路的一环** |
| **遗留** | 多 LoRA 的参数 Schema 形态待定。**已由 C 流给出更优方案，见下 §G8.1** |
| **沉淀** | ⭐ **`targets` 只能注入 `required ∪ optional` 里声明过的入参。** 凡是"参数藏在 widget 属性里"的节点（不少 rgthree / 便利类节点如此），外部 API 都驱动不了。<br>⭐ **选节点时应有一条判据：能进 `object_info` 的才可被自动化驱动。** 这条判据纯离线可查，应该在 P4 选型时就用上 |

### G8.1 ⭐ 同一个包里就有一个**能用**的多 LoRA 节点 —— `Lora Loader Stack (rgthree)`

C 流指出：`rgthree-comfy` 里除了那个不能用的 `Power Lora Loader`，还有一个**完全可注入**的多 LoRA 节点。
我独立复核（不轻信结论，自己查 `object_info` + 跑真实校验器）：

| 节点 | `required` | 可注入 | A/B 探针的实际报错 |
|---|---|---|---|
| `Power Lora Loader (rgthree)` | **`[]`**（空） | ❌ | `入参 'lora_01' 不被该节点接受（可选: ['clip','model']）` —— **报的是入参名** |
| **`Lora Loader Stack (rgthree)`** | **10 个**：`model` `clip` `lora_01` `strength_01` … `lora_04` `strength_04` | ✅ | 只有 `取值 'PLACEHOLDER.safetensors' 不在允许集合内` —— **报的是取值，入参名全部被接受** |
| 核心 `LoraLoader` | 5 个：`model` `clip` `lora_name` `strength_model` `strength_clip` | ✅ | 同上（只有文件名报错） |

⭐ **这个 A/B 是"逐节点查 `required`"这条规则最好的教材**：**同一个包、名字高度相似的两个节点，注入结果完全相反。**
`Power Lora Loader` 报的是**入参名不被接受**（根本没这个入参），
`Lora Loader Stack` 报的是**取值不在枚举内**（入参存在，只是我们用了占位文件名）。
⚠️ **必须能区分这两类报错** —— 前者是"驱动不了"，后者是"文件没装"，处置完全不同。

**处置（P4-04 多 LoRA 叠加 / FR-8.5 的选型建议）**

| 方案 | 节点数 | 连线 | Schema 稳定性 |
|---|---|---|---|
| 核心 `LoraLoader` × 4 串链 | 4 | 需自建 3 条连线 | 4 组 `targets`，且**增删 LoRA 数会改变链长 → Schema 得跟着改** |
| **`Lora Loader Stack`** | **1** | 无 | 1 组 `targets`，**槽位固定 4 → Schema 稳定** |

→ **优先 `Lora Loader Stack`**：槽位数固定意味着 **Schema 不随模板变**，对"参数 Schema 驱动"最友好。
代价是硬上限 4 个 LoRA（V1 的 FR-8.5 是 Should 级，够用）。
`rgthree` 是 **MIT** 且在白名单 keep 档，用它无许可障碍。

⚠️ **但有一个前置依赖，与节点无关**：`object_info` 里 `LoraLoader.lora_name` 的枚举**是空的** ——
**服务器上目前一个 LoRA 权重都没有**。所以 FR-8.5 的阻塞不只是"选对节点"，
还需要**先下载并登记 LoRA 权重**（且每一个都要过许可证审查，见 `license_matrix.md`）。
排查时先看这一条，别在节点接线里绕。

**沉淀（补充）**
⭐ **报错要读到"是入参名错还是取值错"这一层。** 两者都表现为 `VALIDATE=FAIL`，
但一个是"这个节点驱动不了"，一个是"少了个文件"。**只看 FAIL 不看原因，会得出完全相反的结论。**
⭐ **同一个包里节点的可注入性可能完全相反** → 判据必须落到**单个节点类**，不能按包下结论
（C 流量化后也印证了这点：rgthree 24 个类里 **12 个（50%）**`required` 为空，而 controlnet_aux / IPAdapter_plus / BiRefNet 等关键路径包是 **0%**）。

### G9 · ⭐⭐ **判据本身也要能被检出** —— "检查通过了"不等于"检查真的在检查"

| | |
|---|---|
| **现象** | 同一轮里出现**两次**假结论：① `verify_render_path.py` 的多 target 一致性检查用字段 key 当入参名取值，遇 `seed`→`noise_seed` 这类不同名取到 `None`，于是 `{None, None}` 被判"一致" ② G7 探针的变体构造只做浅拷贝 → 两个变体共享同一批节点 dict → 比对恒为"相同" |
| **分析** | 两次**方向相反**：① 是**假通过**（结论偏乐观："多 target 一致"）② 是**假阴性**（结论偏保守："提示词完全无影响"）。**但特征完全一样：错误结论不会报错，只会显得更稳妥 / 更成功。** 因此人工复核与测试都抓不到 —— 测试全绿、输出好看、没有任何异常。<br>按这个特征回溯，同一族在本项目已出现**至少四次**：<br>· `项目进展.md` **#21** `.gitignore` 误挡源码 → "工作区绿 ≠ 仓库完整"<br>· `项目进展.md` **#20** 未提交 / 未登记 → "做完了但无痕"<br>· 本例 ① 假通过 · ② 假阴性<br>C 流也识别出**同构陷阱**：白名单的禁用验收若断言「目标节点类**不在** `/object_info` 里」，那么请求失败 / 服务没起 / 返回被截断时，这条断言会**天然通过** —— 于是"全都禁用成功了"实际是"什么都没读到" |
| **决策** | 立一条纪律：**凡"检查 / 判据"本身，都要能被人为破坏后检出。验证工具交付前的最后一步，是故意让它失败一次。** |
| **处置** | ① `verify_render_path.py`：改用每个 target 自己的入参名 + `None` 守卫；新增"人为破坏一个节点的 width 后必须检出不一致"的测试<br>② G7 探针：加**两道自证** —— 离线断言 baseline≠weighted（防"拿自己跟自己比"）＋ GPU 上**正对照**（同提示词只改 seed，像素必须不同，否则该模式所有"相同"结论**作废**）；并加"除 seed 节点外不得有节点不同"的单变量纯度检查<br>③ C 流在其白名单 `disable_mechanism.positive_control` 加了同款硬断言（含"keep 档节点类 ⊆ 禁用后集合"这条正对照） |
| **验证** | 探针的自证**上线即报错**并抓出浅拷贝别名 bug（若没有它，会带着"完全无影响"这个自称"决定性"的错误结论去回填 G7 ①）。两处都已补"人为破坏必须检出"的测试 |
| **沉淀** | ⭐⭐ **判据要有正对照。** 一个"通过"如果没有"能失败"的证明，它就不是证据。<br>⭐ **"作废"必须比"提示"显眼。** 正对照失败时只打一行 warning，跑的人会当成环境噪声继续用；必须写成「该模式所有结论一律作废 + 先排查运行时 + 不要回填文档」才拦得住。<br>⭐ **别把"该不该采信"交给当场的心情判断** —— 与 C 流把回填口径**在拿到结果之前**预先定死（`prompt_guide.md` §1.1.1）是同一个道理：事后定措辞会不自觉往已有结论上靠。<br>⭐ **越像"更稳妥 / 更成功"的结论越要怀疑**：假结论的伪装方向恰好是让人放心的那一侧。 |

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

> 已实现的条目已移出本段。当前只剩 `style_transfer_v1` 一条。

| id | 已识别的**预判风险 / 阻塞原因** |
|---|---|
| `style_transfer_v1` 风格迁移 | 🔴 **缺权重**：`ipadapter/*` 与 `clip_vision/*` 枚举均为空，LoRA 目录也为空 → 三条实现路径全断（2026-09-16 基于 `/object_info` 快照核实）。必须先下载权重并过许可审查（`license_matrix §7`）。<br>⚠️ **不做「img2img + 风格提示词」的降级版** —— 那不是真正的风格迁移（无参考图驱动），命名成 `style_transfer` 是「功能名不副实」 |
| LoRA 相关（P4-04 / FR-8.5） | ⚠️ **多 LoRA 用 `Lora Loader Stack (rgthree)`**（单节点、4 个固定槽位、10 个声明式入参全部可注入），比"核心 `LoraLoader` 串链"更优 —— 槽位固定意味着 **Schema 不随模板变**。**不能用 `Power Lora Loader`**（见 G8 / G8.1）。<br>⚠️ **前置依赖**：服务器上**一个 LoRA 权重都没有**（`lora_name` 枚举为空） |

> **`batch_v1` 已定论：它不是工作流**（批量是**任意工作流的编排模式**，节点图与 `t2i_v1` 完全相同；
> 登记成独立工作流会产生两份内容相同、版本各自演进的 JSON = 双真相）。
> ⚠️ 并与契约 §3.6 不变量 1 显式澄清：那里的「单次执行只产出 1 张图」说的是**一条工作流跑一次**，
> 批量是靠父任务拆 N 个**独立子任务**达成的 —— **两者不矛盾**。

---

### 2.4 「渲染路径 L4」的状态（两条已通过，三条待跑）

**为什么要有这个概念**：`verification: gpu_verified` 只说明**节点图本身**能出图
（可能是用裸提交模板的方式验证的）。但生产走的是**渲染路径** ——
`engine/render` → 真实引擎 → 核对产物，中间多了 `targets` 注入、seed 解析、
bypass 裁剪、输出前缀覆写这一整套。**"裸提交能出图"不能推出"渲染路径能出图"。**

| 工作流 | `render_path_l4` | `status` | 说明 |
|---|---|---|---|
| `t2i_v1` | ✅ **true** | enabled | 2026-09-16 实机通过（通过 11 项 / 失败 0） |
| `flux2_klein_t2i_v1` | ✅ **true** | enabled | 同上（通过 13 项 / 失败 0） |
| `i2i_v1` | ⬜ false | disabled | 本轮新写，**L4 未跑** |
| `inpaint_v1` | ⬜ false | disabled | 同上；且**蒙版极性**必须先实测 |
| `upscale_v1` | ⬜ false | disabled | 同上 |

**已通过的判定依据**（`2026-09-16`，RTX 4090，生产配置 `--highvram`，显式 `seed=20260916`）：

| # | 检查项 | 结果 |
|---|---|---|
| 1 | 走渲染路径提交并出图 | ✅ 两条均成功 |
| 2 | **同一显式 seed 两次出图，产物 IDAT 逐字节一致** | ✅ 一致（确定性成立） |
| 3 | `targets` 注入落点正确（含多 target 一致性） | ✅ 含 klein 的 `width`/`height` 双 target |
| 4 | 产物 `wf_meta.seed` == 传入的显式 seed | ✅ |
| 5 | 写元数据未改动像素数据 | ✅ |
| 6 | ComfyUI 自己写的 `prompt` chunk 仍保留 | ✅ |

> ⭐ **可执行工具**：`engine/tools/verify_render_path.py`（本文档本节的可执行版本）
> ```bash
> python -m engine.tools.verify_render_path --workflow <id> --seed 20260916 --dry-run  # 离线预检
> python -m engine.tools.verify_render_path --workflow <id> --seed 20260916            # 真实 L4
> ```
> 退出码：`0` 通过 · `1` 有检查失败 · `2` 用法错。
> ⚠️ 工具**不会**自动改注册表 —— `render_path_l4`/`status` 必须人工确认后再改，
> 避免"脚本跑绿了就自动放行"。
>
> ⚠️ 该工具**不**证明画质与性能达标（分别属 golden set 与稳态基准 `#18`）。
> PASS 的唯一含义是：**渲染路径打通了。**

**纪律**：上面各项**全部有输出**，才允许改 `render_path_l4`。
只做"提交后没报错"就改标志，等于把"未验证"包装成"已验证"。
另外 **两个底模都要各跑一遍** —— 修 SDXL 不能拿 klein 当代价（`项目进展.md` #18）。

---

### 2.5 `i2i_v1`（SDXL 图生图）· version 1 · `pending_gpu`

**链路**：`CheckpointLoaderSimple` → `LoadImage` → `ImageScale` → `VAEEncode`
→ `KSampler`（**denoise 是核心参数**）→ `VAEDecode` → `SaveImage`

| # | 现象 | 分析 | 决策/处置 | 沉淀 |
|---|---|---|---|---|
| I1-1 | `check_object_info` 报「节点 10 (LoadImage).image: 取值 'smoke_0.png' 不在允许集合内」 | L2 的冒烟渲染用**假 resolver** 产出 `smoke_0.png`，而 L3 的枚举来自 `/object_info` 快照（**快照时刻对输入目录的静态扫描**）—— 拿静态快照比运行时文件名，**按构造就不可能匹配**。**后果：任何带参考图的工作流都无法通过 L1/L2/L3**（i2i 与 inpaint 会被直接堵死） | 给 `check_object_info` 加 `runtime_asset_inputs` 参数，由 `render_smoke` 从 Schema 推导（`type ∈ {image,image_list}` 且 `transform == ref_to_filename` 的 target），**只豁免这些入参**，不是放宽整个枚举检查 | ⭐ **校验器要对「运行时才确定的值」留豁免口**。这类值拿静态快照校验必然是假阳性 —— 且它表现为「工作流写不出来」，很容易被误判成"自己写错了"而不是"校验器有缺陷"。修完按 G9 补了正对照测试：不豁免必须报错、标记后不报错、标记别的入参/节点仍必须报错 |
| I1-2 | `image` 类型的 `default` 该写什么？ | 实测四种组合：`default=null` + 省略该参数 → **渲染直接抛 RenderError**；`default=0` + 省略 → 注入 `asset_0.png`（不存在的文件）。两条路都不完美 | 取 `default: 0` 占位 + `help` 写明「必须提供」+ 要求后端提交时校验。**立场是「响亮失败优于静默用错图」** | ⭐ **契约缺口**：`contracts.md §3.1` 的 `default` 对 `image` 类型**无法表达「必填」**。已登记待补 `required` 语义。当前用 0 占位是因为 L2 冒烟必须能用 default 渲染通过 |
| I1-3 | `ImageScale.crop=disabled` 会拉伸非等比参考图 | 等比裁切（`crop=center`）会裁掉商品边缘 | V1 取「不裁切」的保守选择，**已知局限**写进 registry `notes`，待 golden set 评测后定 | 参数取舍要**写下取舍理由**，否则后人只会看到"这里写着 disabled"而不知道是权衡的结果 |

**依据来源**：节点/入参经 L3 对 `deploy/schemas/object_info.v0.36.0.json` 校验
**当前 GPU 依据**：无（**L4 未跑**）
**性能参考值**：无（未出图，不填）
**待验证**：① L4 渲染路径 ② `LoadImage` 的 MASK 极性（与 inpaint 同源）
**待办**：等放大器/IP-Adapter 资产到位后评估是否接入 ControlNet 强化结构保持（P4）

---

### 2.6 `inpaint_v1`（SDXL 局部重绘）· version 1 · `pending_gpu`

**链路**：`LoadImage`（取 `0:IMAGE` + `1:MASK`）→ `ImageScale` → `GrowMask`
→ `VAEEncode` → `SetLatentNoiseMask` → `KSampler`(denoise=1.0) → `VAEDecode`
→ `ImageCompositeMasked`（**非选区贴回原图**）→ `SaveImage`

| # | 现象 | 分析 | 决策/处置 | 沉淀 |
|---|---|---|---|---|
| IP-1 | 免专用 inpaint 模型的路线是否成立？ | 侦察确认 `SetLatentNoiseMask` 在核心节点里，让采样**只作用于蒙版区**，任何 SDXL 底模都能用 → **零新增权重** | 走这条路线。专用模型（`InpaintModelConditioning`）路线留到权重到位 | ⭐ **先找「零新增权重」的可行路线**：本项目多处被权重缺口卡住（放大器/IP-Adapter/LoRA），但同一个需求往往有核心节点就能做的实现 —— 先做能做的，把"名不副实"的降级版排除掉 |
| IP-2 | 为什么还要 `ImageCompositeMasked` 把原图贴回？ | 看似多余（`SetLatentNoiseMask` 已保证非选区不被采样修改），但 **VAE 编解码往返会轻微劣化整张图** | 保留这一步，保证**非选区严格等于原图像素** | ⭐ 电商场景的硬要求：**不该动的地方一个像素都不能动**。这类"看起来多余但保证语义"的步骤，理由必须写进 `notes`，否则后人会当冗余删掉 |
| IP-3 | `GrowMask.tapered_corners` 默认 `True`、`expand` 默认 0 | 快照给的默认值会**悄悄改变蒙版形状**，跨版本可能漂移 | 两个值都**在 JSON 里显式写死**（`tapered_corners: true` / `expand: 8`），`expand` 暴露成参数 | 凡是"默认值会影响结果"的入参，**都显式写出来**，不依赖默认值 |

**依据来源**：节点/入参经 L3 校验；免模型路线经侦察确认
**当前 GPU 依据**：无（**L4 未跑**）
**性能参考值**：无
**待验证** ⚠️ **本工作流有一个必须先实测才能定稿的前提**：
**`LoadImage` 的 MASK 输出极性**（是否为 `1 - alpha`）—— 它直接决定「重绘哪一块」，
极性相反会去重绘**非**选区。当前按常见约定假设「透明区 = 待重绘区」。
验证方法：① 读 `/root/ComfyUI/nodes.py` 的 `LoadImage` 实现确认是否 `1. - alpha`
② 用「透明区 = 待重绘区」的图跑一次，看 `MaskToImage` 预览是否为白
③ 若相反，修复 = 在 `GrowMask` 前插 `InvertMask`（**不预先插**：那会在假设正确时反过来做错，
两全不可能，只能实测后定）
**待办**：权重到位后增加专用 inpaint 模型分支（用 `_meta.switches` 声明 bypass，**不新建 JSON**）

---

### 2.7 `upscale_v1`（SDXL 高清修复）· version 1 · `pending_gpu`

**链路**：`LoadImage` → `VAEEncode` → `LatentUpscaleBy` → `KSampler`(denoise=0.4)
→ `VAEDecodeTiled`（**分块解码防显存爆**）→ `SaveImage`

| # | 现象 | 分析 | 决策/处置 | 沉淀 |
|---|---|---|---|---|
| U1-1 | `UpscaleModelLoader.model_name` 枚举为空 | 服务器上**一个放大器权重都没有**；而唯一非空的候选 `LoadUpscalerTensorrtModel` 属 `ComfyUI-Upscaler-Tensorrt`，许可 **CC BY-NC-SA 4.0（非商用）**，白名单已 disable | 走**免放大器模型**路线：`LatentUpscaleBy` 潜空间插值放大 + 低 denoise 重绘 | ⚠️ **"唯一可用的那个恰好是红线"**：许可禁用会连带砍掉功能入口，选型时必须把「许可」与「能力」一起看，否则会得到一个"技术可行但不能用"的方案 |
| U1-2 | 原计划把高清修复做成 `t2i_v1` 的**可选分支**（用 `_meta.switches`），我做成了**独立工作流** | 分支方案只能放大 `t2i_v1` 刚生成的图；独立工作流能放大**任意来源**的图（含其他工作流的产物），更贴合「高清修复」语义 | 做成独立工作流，**并把这次偏离记在这里** | ⭐ **偏离原计划要写明** —— 否则后人看到 `planned` 里写着"做成可选分支"而实际是独立工作流，会以为是漏做。语义差异（"修复任意图" vs "修复刚生成的图"）是决策依据 |
| U1-3 | `VAEDecodeTiled` 的 `temporal_size` / `temporal_overlap` 是**为视频设计**的 | 静态图也必须填这两个参数（快照显示为 required） | 显式填 `64` / `8`，并在 `verification_note` 标注「其对静态图的影响未实测」 | 节点参数带"视频语义"却出现在静态图链路里时，**不要猜它无影响** —— 标为待验证 |

**依据来源**：节点/入参经 L3 校验；权重缺口经侦察确认
**当前 GPU 依据**：无（**L4 未跑**）
**性能参考值**：无
**待验证**：① L4 渲染路径 ② **放大后的画质增益是否成立**（潜空间插值本身不引入新信息，
增益全部来自低 denoise 重绘，需实看图；**本项不声称画质达标**）③ `upscale_method=bislerp`
与其他取值的差异 ④ `temporal_*` 对静态图的影响
**待办**：放大器权重到位后增加「两段式」分支（`UpscaleModelLoader` → `ImageUpscaleWithModel`
→ `VAEEncode` → `KSampler`），用 `_meta.switches` 声明 bypass

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

## 3.5 本地无 PG 起库 + 前端联调（P2-10，2026-09-16 晚）

> 这一节记录的是**可复现的命令与实测输出**，供下一个会话直接对照。
> 背景：本机没有 PostgreSQL 也没有 docker daemon，而后端默认连 PG —— 此前**根本跑不起来**。

### 3.5.1 起库与灌库

```bash
cd backend
export DATABASE_URL='sqlite+pysqlite:////tmp/cui_dev.db'
.venv/bin/python -m app.cli.init_db
#  → ✅ 已建表（幂等）：sqlite+pysqlite:////tmp/cui_dev.db
#  → ⚠️ 这是本地开发用的 SQLite —— 它**不能**替代 PostgreSQL 验证

.venv/bin/python -m app.cli.seed_workflows
#  → 灌库完成：共 5 条
#  → 新增 5：t2i_v1@v1, inpaint_v1@v1, i2i_v1@v1, upscale_v1@v1, flux2_klein_t2i_v1@v1
#  → ⚠️ 下列键在 `workflows` 表里没有对应列，因此未落库：
#     （baseline / changelog / engine / models / notes / render_path_l4 / rollout_percent / title / verification / verification_note）

# 幂等性：再跑一次
.venv/bin/python -m app.cli.seed_workflows
#  → 未变 5：...
```

**库内状态核对**

```
  flux2_klein_t2i_v1       v1 active=True  fields=6
  i2i_v1                   v1 active=False fields=11
  inpaint_v1               v1 active=False fields=12
  t2i_v1                   v1 active=True  fields=10
  upscale_v1               v1 active=False fields=11
  total rows = 5
```

> `active=True` 恰为 **2 条**，与注册表里 `status: enabled` 的条目一致
> —— 另 3 条是 `disabled`（它们的 `render_path_l4: false`）。

### 3.5.2 接口实测（端到端证据）

```bash
.venv/bin/python -m uvicorn app.main:app --port 8010
curl -s localhost:8010/health          # → 200 {"status":"ok",...}
curl -s localhost:8010/health/ready    # → database:{ok:true}  comfyui:{ok:false}(预期：本地无 ComfyUI)
```

| 请求 | 结果 |
|---|---|
| `POST /api/v1/auth/register` | **201** + `access_token` |
| `GET /api/v1/workflows` | **200**，返回 **2 条**（`flux2_klein_t2i_v1` / `t2i_v1`）—— **修复前恒为 `[]`** |
| `GET /api/v1/workflows/flux2_klein_t2i_v1/schema` | **200**，顶层键含 `param_schema`（**嵌套**）、**不含** `definition`（后端有意不暴露节点图） |
| klein schema 字段 | `prompt`(text) / `width`(int,1024) / `height`(int,1024) / `seed`(seed,**-1**) / `steps`(int,4,advanced) / `cfg`(float,1.0,advanced) |
| **CORS 预检** `OPTIONS` + `Origin: http://localhost:5173` | **200** + `access-control-allow-origin: http://localhost:5173` |
| **跨域 GET**（同上 Origin） | **200** + 同样的 allow-origin 头 |

> ⚠️ 本机 **8000 已被另一个项目占用**（ToyVerse Cloud），所以本轮一律用 **8010**。
> 前端通过 `web/.env.local` 的 `VITE_API_BASE_URL` 指向它（该文件已被 `.gitignore` 排除）。

### 3.5.3 前端实测

```bash
cd web
corepack pnpm exec vitest run      # → Test Files 6 passed / Tests 72 passed
corepack pnpm typecheck            # → 通过
corepack pnpm build                # → ✓ built in 24.29s（3114 modules）
corepack pnpm exec eslint .        # → 0 error（1 warning：router.tsx 的 react-refresh 提示）
curl -s -o /dev/null -w '%{http_code}' localhost:5173/            # → 200
curl -s -o /dev/null -w '%{http_code}' localhost:5173/src/main.tsx # → 200（Vite 能转译入口）
```

`pnpm` 的获取**没有失败**（原计划担心网络）：`corepack pnpm --version` → **12.4.2**（自动下载）。
⚠️ 但 pnpm 12 默认**禁止依赖执行安装脚本**，`esbuild` 的 postinstall 会被拦下 →
已用 `web/pnpm-workspace.yaml` 的 `allowBuilds: {esbuild: true}` 声明式放行
（**注意**：pnpm 10+ 起该设置不再从 `package.json` 的 `pnpm` 字段读，写在那里只留一条 WARN）。

### 3.5.4 ⚠️ 本节的边界：**这些没有被验证**

| 项 | 状态 |
|---|---|
| 任务走到 `succeeded` / 重试 / 取消往返 | **待 Redis + ComfyUI + GPU** —— 无 Redis 时任务停在 `queued` |
| 图片上传与 `params.reference_image` | **待 MinIO** —— `POST /assets` 写对象存储 |
| `str` / `bool` / `image_list` 控件 | **待真实数据** —— 注册表里出现 0 次 |
| 浏览器里的人工走查 | **待人工验证** —— 已验的是构建/单测/CORS/接口响应，不是"人在浏览器里点通了" |
| 生产 PostgreSQL 行为 | **待 PG 验证** —— 本机无 PG 二进制也无 docker daemon |
| `alembic upgrade head` 在 SQLite 上建的表能否 INSERT | **待验证** —— 迁移里硬编码 `sa.text("now()")`，SQLite 无该函数；本轮本地引导**刻意走 `create_all`** 绕开它 |

---

## 4. 变更记录

| 日期 | 版本 | 变更 |
|---|---|---|
| 2026-09-16 | v1.0 | 初稿：6 条全局坑（G1~G6）+ 两条已发布工作流的完整调试记录（T1-* / K1-*）+ 计划中的预判风险 + 记录模板 |
| 2026-09-16 | v1.1 | **L3 解锁**（`object_info.v0.36.0.json` 入库）→ 更新 §0.1，新增 §0.2「L3 的两种用法」（对模板 / 对**渲染后的图**）。新增 **G7**（提示词权重在 klein 链路的跨流风险，由 C 流提出、team-lead 转办）、**G8**（`Power Lora Loader` 不可注入，已用真实校验器 A/B 证明）。新增 **§2.4**：说明两条工作流为何 `status: disabled`（`render_path_l4` 未通过），并给出 4 步验证清单 |
| 2026-09-16 | v1.2 | **G7 改写为源码级定论**（team-lead 读 `/root/ComfyUI` 源码）：`KleinTokenizer` 显式传 `disable_weights=True` → klein 上权重**不被解析**，且 `(word:1.2)` 的**字面字符会进入分词（主动污染提示词）**，不是「空转」；**我原先的 cfg 推断（权重靠正负向相减）机制错误**，已在 G7 内明确标注并纠正；新增待验证 ③（换用 `BNK_CLIPTextEncodeAdvanced` 是否恢复权重）。同时**合并了并发编辑产生的重复 G7 标题** |
| 2026-09-16 | v1.3 | 新增 **G8.1**：同一 rgthree 包里的 `Lora Loader Stack (rgthree)` **是可注入的**（10 个声明式入参）。用真实校验器做 A/B，证明「同包、类名高度相似的两个节点，注入结果完全相反」—— P4-04 多 LoRA 建议改用它（单节点、槽位固定 4 → **Schema 不随模板变**），不再需要核心 `LoraLoader` 串链；并记录前置依赖「服务器上目前一个 LoRA 权重都没有」。新增 **§2.4 的可执行工具** `engine/tools/verify_render_path.py`（含离线 `--dry-run`）。收敛 G7 待验证 ③ 的表述（去掉「应该有效」这类夹带预期结论的写法） |
| 2026-09-16 | v1.4 | G7 三项待验证**已备可执行探针** `engine/tools/probe_g7_weight.py`（三种模式：`klein-core` / `klein-bnk` / `sdxl`），并明确「探针只给**客观信号**（同 seed 像素是否逐字节相同），**语义判断必须人工看图**」—— 避免把"像素不同"读成"权重生效"。探针的 BNK 变体**运行时由注册表派**（不落第二份 JSON，防双真相），且定位提示词节点用的是**注册表 Schema 的 `targets`** 而非硬编码 ID。修复 `verify_render_path.py` 多 target 检查的一个**假通过**缺陷：原实现用字段 key 当入参名取值，遇 `seed`→`noise_seed` 这类不同名会取到 `None`，使"一致性检查"退化为"两个 None 相等" |
| 2026-09-16 | v1.5 | 探针补**两道自证**（由 C 流提出，「像素相同 = 决定性结论」隐含"两次真跑了 + 输入真不同"这个前提）：① 离线校验 baseline 与 weighted 的图必须不同 ② GPU 上加**正对照**（同提示词、只改 seed）必须像素不同，否则该模式所有"相同"结论**作废**。**自证 ① 上线即抓到真 bug**：变体构造只做浅拷贝 → 所有变体共享同一批节点 dict → 后一次迭代把已构造的 baseline 追溯性改写 → 比对恒为"相同"（方向最危险的假通过）。已改深拷贝 + 加回归测试。回填口径改为**只给字母 A/B/C/D**（`prompt_guide.md` §1.1.1 已预先约定四种措辞，避免事后合理化） |
| 2026-09-16 | v1.6 | 新增 **G9**「**判据本身也要能被检出**」：把"检查通过 ≠ 检查真的在检查"立为独立全局坑。该族已出现**至少四次**（#20 无痕 / #21 工作区绿 ≠ 仓库完整 / 本轮假通过 + 假阴性 / C 流白名单禁用验收的同构陷阱），两次方向相反但**特征一致：错误结论不报错，只显得更稳妥或更成功**。纪律：**凡判据都要能被人为破坏后检出；验证工具交付前故意让它失败一次**。附带两条：**"作废"要比"提示"显眼**、**别把"该不该采信"交给当场心情判断** |
| 2026-09-16 | v1.7 | **新增三条工作流的调试记录**（§2.5 `i2i_v1` / §2.6 `inpaint_v1` / §2.7 `upscale_v1`），并把踩到的坑写成可复用规则。**§2.4 更新**：`t2i_v1` 与 `flux2_klein_t2i_v1` 的「渲染路径 L4」**已于 2026-09-16 实机通过**（通过 11 / 13 项，失败 0），两条已 `status: enabled`；三条新工作流待跑。**§2.3 清理**：已实现的条目移出，只剩 `style_transfer_v1`（缺 IP-Adapter + CLIP Vision + LoRA 三类权重）；`batch_v1` 定论为「不是工作流」并与契约 §3.6 不变量 1 显式澄清不矛盾。**同时记录一个校验器缺陷的修复**：`check_object_info` 会把 `transform=ref_to_filename` 产生的**运行时文件名**也拿去比静态快照枚举 → **任何带参考图的工作流都无法通过 L1/L2/L3**，已加 `runtime_asset_inputs` 豁免（只豁免这些入参）并按 G9 补正对照测试 |
| **2026-09-16 晚** | v1.8 | 新增 **§3.5「本地无 PG 起库 + 前端联调（P2-10）」**：把 `init_db` / `seed_workflows` / uvicorn / CORS 预检 / 前端四项校验的**实测命令与输出**逐条记下，供下个会话直接对照；并单列「本节的边界」表（6 项**未验证**，含"浏览器人工走查"与"SQLite 上的 alembic 迁移能否 INSERT"）。<br>同时记入一条**本轮踩到的环境事实**：pnpm 12 默认禁止依赖执行安装脚本，`esbuild` 的 postinstall 被拦 → 必须用 `pnpm-workspace.yaml` 的 `allowBuilds` 放行，且该设置**在 pnpm 10+ 已不再从 `package.json` 读**。<br>另记：`pkill -f "vite"` **自匹配杀掉自己的 shell**（旧坑复发，已记入 `项目进展.md` #29）—— 同一条纪律写进文档并未阻止它复发 |
