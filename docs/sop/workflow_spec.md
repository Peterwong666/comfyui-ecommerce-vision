# 统一工作流规范（Workflow Spec）

> 版本：v1.0 · 日期：2026-09-16 · 作者：Peterwong666
> 覆盖任务：**P3-01**
> 上游：`contracts.md` §3（参数 Schema 格式，**已冻结**）· `PRD_v1.md` §3 M6 · §6.2 边界值
> 下游：`workflows/*.json`（工作流文件）· `workflows/registry.yaml`（注册表）· `engine/`（渲染器）
>
> **本规范的作用**：让「新增一条工作流」变成**填空题而不是设计题**。
> 六条标准工作流若各写各的，参数注入、bypass、元数据三件事就会各有一套做法，
> 后端与前端也无法用同一套逻辑处理 —— 这正是 ADR-004 约束 2
>「新增工作流不改后端与前端代码」要防住的事。

---

## 0. 规范的法律地位

| 项 | 说明 |
|---|---|
| **强制** | §2 文件结构 · §3 节点 ID · §5 参数注入（`targets`） · §7 元数据字段 |
| **推荐** | §4 命名与分组 · §6 Bypass 用法 |
| **受契约保护** | 参数 Schema 的字段名与语义以 `contracts.md` §3 为准，本规范**不得与之冲突** |
| **冲突时** | 以上游契约与代码为准，并把本规范改过来 |

本文只规定「怎么写工作流」，不规定「工作流里该有哪些节点」—— 节点选型是各工作流的自由
（`contracts.md` §0.2），但**有官方模板时必须以官方模板为准**（见 §9）。

---

## 1. 工作流的三个文件

一条工作流在仓库里由三处描述，职责不重叠：

| 文件 | 内容 | 谁读它 |
|---|---|---|
| `workflows/<id>.json` | ComfyUI API 格式节点图 + `_meta` 段 | B 流（编写/校验）、`engine/` 渲染器 |
| `workflows/registry.yaml` → `param_schema` | 对外暴露的参数（契约 §3 格式） | A 流后端（校验）、D 流前端（动态表单） |
| `workflows/registry.yaml` → 版本与变更日志 | 版本号、启用/灰度、历史 | B 流、A 流（选版本）、运维 |

> **为什么节点图与 Schema 分离**：节点图是「实现」，Schema 是「对外承诺」。
> 实现可以换（换采样器、加高清分支），只要 Schema 不变，前端与后端就完全不用动。
> 这也是 FR-6.4「新增工作流不改代码」的落点。

---

## 2. `workflows/<id>.json` 文件结构

```jsonc
{
  "_meta": {
    "id": "t2i_v1",                 // 与 registry 的 id、文件名一致
    "version": 1,                    // 整数，单调递增；回滚靠 A 流选旧版本
    "title": "SDXL 文生图",
    "engine": "ComfyUI v0.36.0",     // 编写时所用的引擎版本（信息性）
    "models": ["sd_xl_base_1.0.safetensors", "sdxl_vae_fp16fix.safetensors"],
    "license_note": "全部权重可商用，见 docs/sop/license_matrix.md",
    "node_titles": { "3": "采样器", "6": "正向提示词" },
    "switches": [ /* 见 §6 */ ]
  },
  "3": { "class_type": "KSampler", "inputs": { ... } },
  "5": { "class_type": "EmptyLatentImage", "inputs": { ... } }
}
```

### 2.1 硬性规则

| # | 规则 | 为什么 |
|---|---|---|
| 1 | **顶层只有两类 key**：`_meta` 与节点（节点 key 是**字符串** ID） | 与 ComfyUI API 格式一致；下划线前缀是既有的「元数据隔离」约定（`26_validate_workflow.py` 与 `07_bench_workflow.py` 都按 `startswith("_")` 过滤） |
| 2 | **`_meta` 不得含任何 ComfyUI 不认识的注入** | `_meta` 在渲染时会被**整段剥离**，它只是仓库侧的注释载体 |
| 3 | 节点 key 必须是**短数字字符串**（`"3"`、`"17"`），不得使用 UI 格式的 `"id"` 字段形式 | 契约 §3.3 规定 `node_id` 是字符串；扁平 ID 便于人工核对与人工排障 |
| 4 | 文件必须是**扁平图**，不保留 UI 的 subgraph 嵌套 | 官方模板含 subgraph（如 `"13:5"` 这类 ID）；契约 §3.3 允许子图 ID，但**扁平化后 ID 更稳定、人工可读**，且我们已按此处理 klein 模板（见 §9） |
| 5 | 每个节点**不写** `"mode"` 字段 | V1 的 bypass 走**构建期裁剪**（§6），不依赖运行时 `mode`，避免"依赖未验证行为"（见 §6.4） |
| 6 | 连线只能用 `["<节点ID>", <输出序号>]` | 与 API 格式一致；校验脚本会检查目标节点存在与序号越界 |

### 2.2 `_meta` 字段表

| 字段 | 必填 | 说明 |
|---|---|---|
| `id` | ✅ | 稳定标识，`snake_case`，与 registry 及文件名一致 |
| `version` | ✅ | 整数。**任何改变出图结果的改动都要 +1**（换模型、改默认值、改节点接线） |
| `title` | ✅ | 中文标题 |
| `engine` | ✅ | 编写/验证时的 ComfyUI 版本（信息性，便于排障时定位版本差异） |
| `models` | ✅ | 该图用到的**权重文件名清单**，与节点入参里的文件名一致。P3-11 会把它写进产物元数据（FR-5.4 要求「元数据可看模型」） |
| `license_note` | ➖ | 许可提示；全部可商用时可省略 |
| `node_titles` | ➖ | `节点ID → 中文名`。**不写进 API 格式**（ComfyUI API 格式无 title 字段），仅供人工与文档引用 |
| `switches` | ➖ | bypass 开关声明，见 §6 |
| `source` | ➖ | 若结构取自官方模板，写明模板文件名（#16 的纪律：来源可追溯） |
| `todo` | ➖ | 遗留事项 |

---

## 3. 节点 ID 约定

### 3.1 已发布工作流的 ID **冻结**

`t2i_v1.json`、`flux2_klein_t2i_v1.json` 的节点 ID 一旦进入注册表，就**不得重排或复用**。

> **为什么**：`param_schema` 的 `targets` 用**节点 ID 字符串**指向节点
> （`contracts.md` §3.3）。改 ID 等于把后端校验、前端表单、注册表全部改一遍，
> 而且**改错不会报错，只会静默写错位置** —— 这是最难排查的一类故障。
> 把 ID 当成对外契约的一部分，代价最低。

### 3.2 新建工作流的分段预留

新增工作流时按「功能区段」分配 ID，让**看 ID 就能猜出节点职责**：

| 段位 | 用途 | 典型节点 |
|---|---|---|
| `1`–`9` | 加载器 | `CheckpointLoaderSimple` · `UNETLoader` · `VAELoader` · `CLIPLoader` |
| `10`–`19` | 输入与条件 | `CLIPTextEncode` · `EmptyLatentImage` · `LoadImage` |
| `20`–`29` | 采样 | `KSampler` · `RandomNoise` · `Flux2Scheduler` · `CFGGuider` · `KSamplerSelect` · `SamplerCustomAdvanced` |
| `30`–`39` | 后处理 | `VAEDecode` · 放大 · 局部重绘 · 高清修复 |
| `40`–`49` | 输出 | `SaveImage` · `PreviewImage` |
| `50`+ | 可选分支 / 控制 | `ControlNet*` · `IPAdapter*` · `BiRefNet*` |

> ⚠️ 该分段**不追溯适用于**已发布的两条工作流（它们的 ID 来自探针脚本产出，已冻结）。
> 新增工作流请遵守；重写老工作流时若确实需要重排，等同于**升大版本**（version +1）并在变更日志写明。

### 3.3 子图 ID

若某条工作流确实无法扁平化（例如直接引用了官方 subgraph 模板），子图内节点 ID 形如 `"13:5"`
（契约 §3.3 已允许）。此时 `targets.node_id` 必须写**带冒号的完整 ID**，且 §3.2 的分段约定失效。

---

## 4. 命名与分组约定

### 4.1 节点命名（`_meta.node_titles`）

API 格式没有节点名字段，所以命名落在 `_meta.node_titles` 上。约定：

| 角色 | 命名前缀 | 示例 |
|---|---|---|
| 唯一入口加载器 | `加载·` | `加载·SDXL底模` |
| 模型加载器 | `加载·` | `加载·VAE`、`加载·文本编码器` |
| 输入 | `输入·` | `输入·正向提示词`、`输入·画布` |
| 采样 | `采样·` | `采样·主采样器` |
| 后处理 | `处理·` | `处理·解码`、`处理·高清修复` |
| **输出** | `输出·` | `输出·保存图片` |
| 可选分支 | `可选·` | `可选·ControlNet深度` |

> **为什么强调「输出·」**：产物收集逻辑（A 流 `_collect_images`）是按**输出节点里有没有
> `images` 字段**来收图的，与节点 ID 无关。命名统一是为了**排障时能一眼确认「这张图是从哪条支路出来的」**——
> 多条支路（原图 / 高清修复 / 局部重绘）的图混在一个任务里是最难排查的情况。

### 4.2 输入 / 输出节点约定

| 项 | 约定 | 理由 |
|---|---|---|
| **输入** | 所有可调参数必须**能通过 `targets` 定位到具体入参**；不允许「藏在某个节点的常量里、schema 无法触达」的可调项 | 否则该参数对用户不可见、无法复现（FR-5.4/FR-5.5） |
| **输出** | 每条工作流**恰好一个** `SaveImage` 作为最终产物节点 | 多个 `SaveImage` 会让产物归属含糊（哪张是成品、哪张是中间预览） |
| **中间预览** | 需要调试看图时用 `PreviewImage`，但在**提交生产版本前移除** | `PreviewImage` 也会在 `/history` 的 outputs 里带 `images`，会被收图逻辑误收 |
| `SaveImage.filename_prefix` | 固定为 `<工作流id>/<变体>`，如 `t2i_v1/base` | 便于人工在生产机 output 目录里定位；**不要把 task_id 放进前缀**（会炸出海量目录，产物归属靠 DB 记录而非路径） |
| `filename_prefix` 覆写 | A 流可在渲染时通过 `output_prefix` 选项覆写（`engine.render`） | 需要按任务隔离时用得上，但默认不启用 |

---

## 5. 参数注入（`targets`）

**这是 B 流与 A 流的硬边界**，格式以 `contracts.md` §3.3 为准。本节只补「怎么用对」。

### 5.1 一条参数可以有多个 target

FLUX.2 klein 是典型例子：`width`/`height` 必须**同时**写给潜空间与调度器，
只写一个会导致调度器的 sigma 曲线按错误分辨率生成 —— 图看着有，但质量下降且难以归因。

```json
{ "key": "width", "type": "int", "default": 1024, "min": 512, "max": 2048, "step": 64,
  "targets": [
    { "node_id": "6", "input": "width" },   // EmptyFlux2LatentImage
    { "node_id": "7", "input": "width" }    // Flux2Scheduler  ← 容易漏
  ] }
```

> **排查提示**：如果某个参数改了「看起来没生效」，第一件事是检查它是否**漏了某个 target**，
> 而不是怀疑参数没传进去。`engine/validate.py` 会对「同名入参出现在多个节点、
> 但 schema 只声明了其中一个」发出警告。

### 5.2 `transform` 三种取值

契约 §3.3 定义了三种。使用场合与注意事项：

| 取值 | 用在哪 | 备注 |
|---|---|---|
| （不填） | 绝大多数（int/float/str/bool/enum） | 原样注入 |
| `ref_to_filename` | `image` / `image_list` 类型参数的 `LoadImage.image` | 把 `asset_id` 解析成 ComfyUI 可见的文件名 |
| `join_lines` | 数组 → 换行拼接字符串（如多行提示词、批量标签） | 目标入参必须是 `str` |
| `json_str` | 对象 → JSON 字符串 | 目标入参必须是 `str` |

⚠️ **`ref_to_filename` 的解析责任在 A 流**：B 流只声明「这个参数要转成文件名」，
**具体怎么把 `asset_id` 变成文件名由 A 流提供 resolver**（因为素材落盘路径是 A 流的领域）。
`engine/render.py` 通过 `asset_resolver` 回调接收，未提供时**报错而不是猜**。

### 5.3 空 `targets` 是合法的

`sku`、`upload_asset_ids`、`idempotency_key` 这类**只参与业务逻辑、不进图**的参数，
`targets` 写 `[]`（契约 §3.3 明确合法）。不要为了"看起来完整"而给它编一个 target。

### 5.4 占位符 `{{key}}`（补充机制）

`targets` 是**结构化注入**，适合「整个入参就是一个参数值」的场景。
但提示词常常需要**拼在固定文案中间**，例如：

```json
{ "class_type": "CLIPTextEncode",
  "inputs": { "text": "professional product photo of {{product}}, {{scene}}, soft studio lighting" } }
```

此时用 `{{key}}` 占位符。规则：

| # | 规则 |
|---|---|
| 1 | 占位符对**所有字符串型入参值**生效，语法严格为 `{{key}}`（key 必须匹配 `[a-z0-9_]+`） |
| 2 | 替换顺序：**先占位符，后 `targets`**。故 `targets` 优先级更高 |
| 3 | 同一参数**不要既用占位符又声明 `targets`** —— 行为虽已定义（targets 覆盖），但会让人误读。`registry.yaml` 加载时会对这种情况告警 |
| 4 | 未提供的 key 保留原样 `{{key}}`（**不静默清空**），并在渲染日志里列出 |
| 5 | 占位符**不做转义**。提示词里需要字面量 `{{` 的场景 V1 不存在，不支持 |

> **为什么保留未提供的占位符而不是清空**：静默清空会让"少传了一个参数"变成
> "出图正常但内容不对"，这类故障极难定位。保留原样 + 告警，故障会立刻暴露在提示词里。

### 5.5 不变量：「默认渲染」必须等价于「模板原样」

**规则**：`targets` 指向的入参若在模板里已有字面值，Schema 里该字段的 `default`
**必须与之一致**。

**为什么这条是有价值的**：`deploy/autodl/07_bench_workflow.py`、`26_validate_workflow.py`
等工具是**直接裸提交模板 JSON** 的（它们不知道注册表）。而生产路径是"渲染后再提交"。
若两者不一致，就会出现：

| 后果 | 说明 |
|---|---|
| **性能基准测的不是生产的那张图** | 折腾半天标出来的 4.59s，可能与生产实际跑的不是同一张图 |
| **排错时无法二分** | 图上出了问题，无法用"裸提交模板"来隔离"是参数问题还是渲染问题" |
| **看模板的人被误导** | 模板里写着 `steps: 25`，实际生产跑的是 `30` |

**校验方式**：`engine/validate.py` 会在 L1 对 `str`/`text` 类型字段做一致性比对并告警
（见 `_validate_schema_against_graph` 的「default 与模板字面值的一致性」段）。

> 数值型字段（如 `steps`）不做自动比对 —— 数值的差异通常是**有意的**（例如把模板里的
> 调试值改成生产值）。但字符串型（提示词、文件名前缀）差异几乎总是**漂移**，所以只查它。

---

## 6. Bypass 开关

### 6.1 要解决的问题

工作流里常有**可选分支**：高清修复、参考图分支、ControlNet 分支。
如果为每种组合各存一份工作流 JSON，组合数会爆炸且维护不同步
（改了主采样器参数要改 4 个文件 —— 一定会漏）。

**Bypass 的目标**：一份 JSON 覆盖全部组合，靠参数值决定分支是否启用。

### 6.2 机制：构建期裁剪 + 显式重连

Bypass **由渲染器在构建期完成**（不是靠 ComfyUI 运行时），声明在 `_meta.switches`：

```jsonc
"_meta": {
  "switches": [
    {
      "key": "hires_fix",                       // 与 param_schema 里的 field.key 同名
      "enabled_when": [true],                   // 取这些值时「分支启用」；其余值触发 disabled
      "disabled": {
        "prune": ["31", "32", "33"],            // 要裁掉的节点
        "rewire": { "41": { "images": ["30", 0] } }   // 裁掉后，谁改从哪里取输入
      }
    }
  ]
}
```

| 字段 | 必填 | 说明 |
|---|---|---|
| `key` | ✅ | 必须是 `param_schema` 里已声明的 `field.key`；且该 field 的 `targets` 通常为 `[]`（它不进图，只控制图） |
| `enabled_when` | ✅ | 值列表。「当前参数值 ∈ 此列表」= 分支启用，**不做任何裁剪** |
| `disabled.prune` | ✅ | 未启用时删除的节点 ID 列表 |
| `disabled.rewire` | ✅ | `节点ID → { 入参名: ["来源节点ID", 输出序号] }`。**必须显式写出**，不自动推断 |

### 6.3 为什么 `rewire` 必须显式写

**不做"自动推断 pass-through"**。理由：

自动推断需要判断「被裁节点的哪个输入对应哪个输出」，而不同节点语义完全不同
（`VAEDecode` 的 `samples` → `IMAGE`、`ImageScale` 的 `image` → `IMAGE`、
`LoraLoader` 的 `model`+`clip` → `MODEL`+`CLIP`）。猜错的后果是
**图能提交、能出图，但连错了线** —— 属于最难发现的一类故障。

显式声明虽然多写几行，但**它在离线校验里是可验证的**（§8），
而自动推断只能靠出图对比才能发现问题。用几行冗余换可验证性，值得。

### 6.4 不使用 ComfyUI 的 `mode: 4`（bypass 标记）

ComfyUI 节点在 UI 里有 bypass（`mode: 4`）与 mute（`mode: 2`）两种标记，
API 格式里也带 `mode` 字段。但 V1 **不使用**该机制，原因：

1. **行为未经本机验证**：`mode` 在 `/prompt` 提交路径下的处理逻辑（是否在服务端做重连、
   重连规则是什么）我们没有在本机 v0.36.0 上实测过。按 #14/#16 的教训，
   **不把未验证的运行时行为写进生产路径**。
2. **构建期裁剪完全可离线验证**：出图前的 `engine/validate.py` 就能确认裁剪与重连结果合法，
   不需要 GPU 机时。
3. 一旦依赖运行时 `mode`，**同一份 JSON 在不同引擎版本下可能行为不同** ——
   而 #17 已经证明「引擎默认值变更」是真实存在的风险面。

> ⚠️ **待 GPU 验证项**：`mode: 4` 在 v0.36.0 下的实际语义与重连规则。
> 验证方法：构造一个三节点最小图（A → B → C），给 B 加 `mode: 4` 提交，
> 观察 C 是否拿到 A 的输出。**在验证通过前，V1 一律使用构建期裁剪。**

---

## 7. Seed 与元数据注入

### 7.1 Seed

| 项 | 约定 |
|---|---|
| 声明方式 | `param_schema` 里 `type: "seed"`，`default: -1` |
| `-1` 语义 | **随机**（契约 §3.2） |
| 取值域 | `[0, 2^32-1]`（保守选择，保证所有节点与下游工具都接受该范围的整数） |
| 解析时机 | **由 `engine/render.py` 在渲染时解析成具体整数**，而不是交给 ComfyUI |
| 批次递增 | 同批次 `seed = base + idx`（契约 §2.4）。由 A 流调用 `engine.render.derive_seed()` 生成 |

> **为什么 `-1` 必须由渲染器解析**：
> ① 产物元数据要记录**真实 seed**（FR-5.4「元数据可看 seed」、FR-5.5「用同参数再生成」）——
> 若把 `-1` 直接提交，元数据里就只有一个 `-1`，**用户再也复现不出这张图**，可复现性直接失效。
> ② 解析后的 seed 与工作流 JSON 里的值一致，出现问题时可以**把渲染结果整份拷出来单独提交**，
> 便于隔离"是参数问题还是管线问题"。
>
> 代价：同一份 `params` 渲染两次会得到不同 seed（因为随机）。这是**预期行为**；
> 需要确定性时显式传 `seed`（≥0）。

### 7.2 元数据字段（P3-11）

产物 PNG 写入以下字段，**口径见 `docs/prd/tracking_plan.md`**：

| 字段 | 内容 | 对应需求 |
|---|---|---|
| `workflow_id` / `workflow_version` | 工作流标识与版本 | FR-6.3 可追溯 |
| `seed` | **解析后的**真实 seed | FR-5.4 · FR-5.5 |
| `models` | `_meta.models` 全量清单 | FR-5.4 · FR-9.1 许可可查 |
| `params` | 本次生效的**完整参数**（JSON） | FR-5.4 · FR-5.5「用同参数再生成」 |
| `task_id` / `batch_id` / `idx` | 归属关系 | FR-4.2 逐张定位 |
| `schema_version` / `created_at` | 元数据格式版本与时间 | 向前兼容 |

⚠️ **提示词的处理是刻意的双向设计**（需 team-lead 确认，见 §10）：

| 场景 | 是否含提示词全文 | 理由 |
|---|---|---|
| **产物 PNG 元数据**（用户自己的文件） | ✅ 含 | FR-5.5「用同参数再生成」依赖它；文件本身归用户所有 |
| **服务端埋点事件**（E01~E12） | ❌ 只记**长度与 hash** | `tracking_plan.md` §1.2 明确「用户输入的提示词全文」不埋 |

> 也就是说：**同一个提示词「写给用户」但不「写给埋点」**。
> 实现上由 `engine.metadata.build_metadata(include_prompt=...)` 控制，
> 若未来合规要求更严，只需把该开关置 `False`，不影响埋点侧。

#### 7.2.1 ⚠️ 两种口径是**正交**的（team-lead 2026-09-16 确认）

| 口径 | 服务的目标 | 因此要存什么 |
|---|---|---|
| 产物元数据 | **可复现性**（FR-5.4 / FR-5.5） | 必须含全文 —— 没有提示词就无法"用同参数再生成"，而这是用户自己的资产 |
| 埋点事件 | **指标聚合**（看板只需分布与去重） | 只记**长度 + hash** —— 存全文会让事件表体积爆炸，且平白引入隐私面 |

#### 7.2.2 ⚠️ 提示词 hash **必须加盐**（否则短提示词可被还原）

只记 hash 不等于安全：提示词的候选空间对短文本很小
（`"白色杯子"`、`"白底主图"` 这类），**不加盐的裸 sha256 会被彩虹表 / 暴力枚举还原**，
而"记录 hash 以规避隐私"的初衷就落空了。

| 规则 | 说明 |
|---|---|
| 摘要 = **`HMAC-SHA256(salt, prompt)`** | 盐从**配置**读（如 `settings.prompt_hash_salt`），**不写死在代码里**。<br>⚠️ **不用 `sha256(salt ‖ prompt)`** —— 那个构造对**长度扩展攻击**脆弱：已知 `sha256(salt‖P)` 的人能算出 `sha256(salt‖P‖padding‖X)`，即**为另一个提示词伪造出合法摘要**。威胁不高，但 HMAC 是零成本的标准解（2026-09-16 team-lead 批准切换；当时无生产数据，成本为零） |
| 摘要自带 `salted` 与 `alg` 标志 | `{"sha256": ..., "length": ..., "salted": true, "alg": "hmac-sha256"}` —— 否则「加盐/未加盐」、「新旧算法」的摘要混在一起比对，会得出**「提示词不同」的错误结论** |
| 跨系统比对须同盐 | 产物元数据的摘要要与服务端事件摘要对上，两侧必须同一个盐 |
| 换盐 = 有意识的迁移 | 换盐会作废全部历史摘要，必须当作一次数据迁移来计划，不能顺手改 |

> 实现见 `engine.metadata._prompt_digest()`，`length` 不受盐影响（它是统计口径）。

#### 7.2.3 提示词**内容层面的语法约束**是「按工作流不同」的

`prompt` 这个字段名在两条工作流上一样，但**能写什么语法不一样** ——
这是一条容易被漏掉的"同名不同义"：

| 工作流 | `(word:1.2)` 权重语法 | 原因 |
|---|---|---|
| `t2i_v1`（SDXL） | ✅ 正常解析 | 走 SD1 tokenizer，未禁用权重 |
| `flux2_klein_t2i_v1` | ❌ **严禁使用** | `KleinTokenizer` 显式传 `disable_weights=True`，括号/冒号/数字会**原样进入分词**，属污染提示词 |

> 完整依据（含源码位置与待验证项）见 `docs/sop/debug_log.md` **G7**。
> 处置：**前端提示文案与提示词库（`prompt_guide.md`）必须按工作流分别说明**，
> 不能给一条通用的"权重语法可用"提示 —— 那会让 klein 用户在毫不知情的情况下污染提示词。

⚠️ **`salted: false` 不等于安全 —— 它只是"可区分"。**
`engine.metadata` 在未传 `hash_salt` 时**仍会输出摘要并标注 `salted: false`**，
此时它就是一条**可被彩虹表还原的裸 sha256**。

| 环境 | 要求 |
|---|---|
| **生产** | **必须**配置 `prompt_hash_salt`；`salted: false` 的摘要**不得**当作隐私保护，也**不得**用来说明"我们没存提示词" |
| 本地 / 测试 | 允许 `salted: false`（便于对账与调试） |

> 为什么不做成"没盐就不输出摘要"：那样会让"忘了配盐"表现为**静默缺字段**，
> 而各处的统计与去重会静默失效（与"保留未解析占位符"同一类取舍 —— 让异常可见）。
> 现在的设计是**输出但自曝**：谁都能看出这条摘要没有盐。

---

## 8. 校验流程（出图前必须全绿）

| 级别 | 校验内容 | 工具 | 需要 GPU |
|---|---|---|---|
| **L1 结构自洽** | 节点 key 与 `_meta` 分离 · `switches`/`rewire` 指向的节点存在 · `targets` 的 `node_id` 存在 · `targets.input` 在模板里已存在或是可新增入参 · 占位符 key 都有对应 field · Schema 与 `_meta.switches.key` 对得上 | `engine/validate.py` | ❌ 不需要 |
| **L2 参数渲染** | 用 `default` 渲染一次 + 用一组「边界值」渲染一次，确认结果仍是合法 API 格式 | `engine/validate.py --render-smoke` | ❌ 不需要 |
| **L3 节点合法性** | `class_type` 存在 · 必填入参齐备 · 入参名被接受 · 连线序号不越界 · 枚举取值合法 | `engine/validate.py`（对**渲染后**的图）<br>`deploy/autodl/26_validate_workflow.py --object-info FILE`（对**模板原文件**） | ❌ 不需要 |
| **L4 真实出图** | 提交 → 出图 → 检查产物与元数据 | `deploy/autodl/07_bench_workflow.py`（模板）<br>生产路径 / `engine/render`（渲染） | ✅ **需要** |

### 8.1 ✅ L3 已可执行，且**两条工作流已实测通过**

`object_info` 缓存已入库：**`deploy/schemas/object_info.v0.36.0.json`**（v0.36.0，2530 个节点类，与升级后审计数字一致）。
`engine.validate` 会自动读取它，**无需额外参数**。

**当前结果（2026-09-16）**：

| 对象 | L1 | L2 | L3 |
|---|---|---|---|
| `workflows/t2i_v1.json` | ✅ | ✅ | ✅ **PASS** |
| `workflows/flux2_klein_t2i_v1.json` | ✅ | ✅ | ✅ **PASS** |

（L3 对"模板原文件"与"渲染后的图"两种对象都跑过，均 PASS。L4 仍未做 —— 见 §8.3.1。）

⭐ **L3 有两种用法，两种都要做**（它们抓的不是同一类错）：

| 方式 | 校验对象 | 能抓到 |
|---|---|---|
| `python -m engine` | **渲染后**的图 | 全部，**外加 `targets` 注入的错误**（节点 ID 漂移、注入的入参名/枚举值非法） |
| `python deploy/autodl/26_validate_workflow.py <file> --object-info deploy/schemas/object_info.v0.36.0.json` | **模板原文件** | 模板自身的问题 |

> 只做第二种是不够的：`targets` 里的 `node_id` 是**手写的字符串**，
> 一旦工作流的节点 ID 变动就会漂移，而漂移**不会报错**、只会静默注入到错位置或失败。
> 只有"对渲染后的图跑 L3"才覆盖得到。这是 L3 在本项目里的主要价值。

### 8.2 怎么跑

**安装（一次即可）**：`engine/` 是可安装包（flat layout，配置在**仓库根** `pyproject.toml`）。
从仓库根执行：

```bash
backend/.venv/bin/pip install -e . -e ./backend
```

装完后**在任何工作目录**都能 `import engine`，**不要**再靠 `sys.path` / `PYTHONPATH` 操纵。

```bash
# L1 + L2 + L3（object_info 已入库，自动加载）
python -m engine

# 输出会如实列出执行的级别，例如：已执行: L1/L2/L3 · 未执行: L4
# 若 L3 被跳过，会显式打印「节点类型/入参名/枚举取值未经验证」

# 连「Schema 边界 ⊆ 全局红线」一起查（contracts.md §3.4）
#   生成边界表（避免手工抄 settings 抄错；在 backend/ 目录下执行）：
PYTHONPATH=..:. .venv/bin/python -c "
from app.core.config import settings
from engine.validate import bounds_from_settings
import json; print(json.dumps(bounds_from_settings(settings), indent=2, ensure_ascii=False))
" > ../workflows/settings_bounds.yaml
python -m engine     # 会自动读该文件

# 单测
python -m pytest engine/tests -q
```

> 命令用 **`python -m engine`** 而不是 `python -m engine.validate`：
> 后者会触发 runpy 的 `RuntimeWarning`（`__init__.py` 已导入 `engine.validate`，
> 随后又要把它当 `__main__` 跑一次）。那条警告不是 bug，但**看起来像 bug**，
> 会让人怀疑工具本身。旧的写法仍可用，只是会打印该警告。
>
> `workflows/settings_bounds.yaml` 是**派生快照**，真相在 `backend/app/core/config.py`。
> 因此它没有入库 —— 每次要跑该检查时现生成，避免出现第三份"边界真相"。
> 若未提供，`python -m engine` 会**显式提示已跳过该检查**，不会静默放过。

### 8.3 提交纪律

**L1 + L2 + L3 全绿之前，不允许把工作流标记为「已就绪」**；
**L4 未通过之前，不允许写入注册表的 `status: enabled`。**
在无卡模式期间，所有 L4 结论必须显式标注为**「待 GPU 验证」**，
不得因为 L1~L3 通过就写成"已跑通"（这是本项目最看重的一条：过程可证明）。

#### 8.3.2 契约 §3.6 的不变量（`engine.validate` 会机械执行其中可检查的两条）

`contracts.md` §3.6 列了 4 条**契约级**不变量。其中两条能离线机械检查，**已实现为 `error`（不是警告）**：

| # | 不变量 | 检查方式 |
|---|---|---|
| **1** | 一条工作流的单次执行只产出 1 张图 → B 流**不得暴露** `batch_size` 这类参数 | 按**入参名**匹配黑名单（`batch_size` / `batch_count` / `num_images` …）。⚠️ 按入参名而非参数名 —— 叫 `n` 或 `count` 的参数照样可能写进 `batch_size` |
| **2** | 参数默认值必须与工作流 JSON 里的实际值一致 | 逐字段比对 `default` 与模板字面值。`seed` **豁免**（`-1`=随机，本就不该等于模板里的历史字面值）；模板用 `{{key}}` 占位符时**豁免**（字面值本来就是占位符） |

另两条（**3** 不得暴露不起作用的参数 · **4** `targets` 必须指向真实存在的节点与入参）
分别依赖语义判断与 `object_info`，由 L1/L3 覆盖，无法完全机械判定。

> 定级为 `error` 而非 `warn` 的理由：这两条一旦违反都是**静默失真** ——
> 不变量 1 会让成本核算与断点续跑同时错位，不变量 2 会让"UI 里看到的初始状态"
> 与"真实执行的"不是同一件事。警告会被忽略，错误不会。

#### 8.3.1 ⚠️ L4 要区分「模板裸提交」与「渲染路径」

这两件事**都叫 L4，但不是同一件事**，混为一谈会得出错误的放行结论：

| | 提交的是什么 | 谁在做 | 证明力 |
|---|---|---|---|
| **L4-模板** | 工作流 JSON **原样** | `deploy/autodl/07_bench_workflow.py` | 证明「**节点图**能出图」 |
| **L4-渲染** | `engine/render` 渲染后的 JSON | 生产路径（A 流 worker） | 证明「**我们的渲染器接进去也能出图**」 |

`verification: gpu_verified` 记的是 **L4-模板**；而 `status: enabled` 要求的是 **L4-渲染**。
因此注册表另设 `render_path_l4` 字段，并由 `engine/registry.py` **强制校验**：
`status: enabled` 必须同时满足 `render_path_l4: true` 且 `verification: gpu_verified`。

> **为什么盯着这个区分**：`engine/render` 会改写节点入参（`targets` 注入）、可能裁剪节点
> （bypass）、可能覆写输出前缀。任何一处写错都只在真实引擎上才暴露。
> 「模板出过图」推不出「渲染器接进去也能出图」—— 而这两者之间的落差
> 正是 P3-02 引入的全部风险面。
>
> 当前两条工作流都是 `disabled`：**模板**出过图，**渲染路径**尚未与真实引擎联调。

---

## 9. 工作流来源：优先官方模板

⭐ **有官方模板时必须先取官方模板，不要自己拼**（`项目进展.md` #16 验证 3 的教训）。

| 情形 | 做法 |
|---|---|
| 官方有模板（`comfyui-workflow-templates` 包内） | ① 找到对应 `image_*.json` ② **展开 subgraph** ③ 扁平化并重排 ID（遵守 §3） ④ 在 `_meta.source` 写明模板文件名 |
| 官方无模板 | 先用 `deploy/autodl/08_probe_nodes.py` **探测节点入参**，再照着拼 |
| 两种情形都要 | 在 `docs/sop/debug_log.md` 记录：**试过什么、错在哪、最终依据什么** |

> **为什么要显式记 `_meta.source`**：半年后回看「这个节点组合为什么是这样」时，
> 唯一的权威答案就是"它照抄了官方模板的某一行"。没有这个字段，就只能靠猜。

---

## 10. 待 team-lead 决策 / 已知偏差

| # | 事项 | 现状 | 建议 |
|---|---|---|---|
| 1 | **`contracts.md` §3.5 示例的节点 ID 与真实工作流不符** | 契约示例写 `prompt→"6"` / 画布`→"5"` / 采样`→"3"`；而真实 klein 工作流是 `prompt→"4"` / 画布`→"6"` / 采样分散在 `"7"`(steps)、`"9"`(cfg)、`"8"`(seed) | 契约示例属**示意性**，但 A/D 流可能照抄。建议在 §3.5 加一句「节点 ID 为示例，实际以 `workflows/registry.yaml` 为准」，**不修改格式本身** |
| 2 | **per-workflow 边界 vs 全局边界** | 契约 §3.4 说「A 后端边界值统一取 `settings`」；但 klein 蒸馏版 `cfg` 必须为 1、`steps` 应为 4，PRD §6.2 的 `cfg 1.0–20.0` 对它是错的 | 建议明确：**Schema 边界 ⊆ settings 边界，取更严者**；schema 可更窄（如 klein cfg 1.0–2.0），A 流校验时以 schema 为准 |
| 3 | **旧绑定机制 `param_bindings`（A 流已删除）** | ✅ **已结案（此前某轮完成，本次 2026-09-16 核实）**：该字段**已不存在** —— `grep -rn param_bindings` 全仓库只剩 `backend/app/models/workflow.py:74` 的删除说明注释与 `backend/tests/test_migration.py` 的反向门禁 `test_dropped_binding_mechanism_stays_dropped`。它是契约 §3.3 `targets` 之前的旧设计，曾与 `param_schema` 表达同一件事 | 无需再处理：**唯一绑定机制是契约 §3.3 的 `targets`**（注入位置就写在 `param_schema` 里），两套并存的隐患已消除 |
| 4 | **`visible` vs `advanced`** | ✅ **已结案（此前某轮完成，本次 2026-09-16 核实）**：`backend/app/models/workflow.py` 的 docstring **已不再出现** `"visible"` / `"basic"`（两条 grep 均无匹配，exit 1）；现用 `advanced`（默认 false，true 折叠）与 `visible_when`，与契约 §3.1 一致 | 无需再处理：docstring 已按契约 §3.1 同步 |
| 5 | **`object_info` 缓存缺失** | 见 §8.1，L3 校验当前无法离线执行 | 需要一位能在服务器上跑 `--cpu` 的同流产出缓存文件并回传仓库 |
| 6 | **提示词写入产物元数据** | 见 §7.2 的双向设计 | ✅ **2026-09-16 team-lead 已确认成立**，并追加要求：埋点侧 hash 须加盐（已写入 §7.2.2 并实现） |
| 7 | **`status: enabled` 的放行判据** | team-lead 裁定「L4 未跑前一律不得标启用」。施行时发现需再细分一层：既有 `gpu_verified` 说的是「**模板裸提交**出过图」，而生产走的是「**渲染路径**」—— 两者不是同一件事 | 已加 `render_path_l4` 字段并**用校验强制**：`status: enabled` 必须 `render_path_l4: true` + `verification: gpu_verified`。当前两条工作流均 `disabled`（见 §8.3） |

---

## 11. 变更记录

| 日期 | 版本 | 变更 | 影响 |
|---|---|---|---|
| 2026-09-16 | v1.0 | 初稿：文件结构 / 节点 ID 分段 / 命名约定 / `targets` 用法 / `{{}}` 占位符 / Bypass 构建期裁剪 / Seed 解析 / 元数据字段 / 四级校验流程 | B 流全部工作流 |
| 2026-09-16 | v1.1 | **L3 解锁并实测通过**（`object_info.v0.36.0.json` 入库；§8.1 改写，并区分"对模板"与"对渲染后的图"两种 L3）。新增 **§5.5**「默认渲染 ≡ 模板原样」不变量（后升格为契约 §3.6 不变量 2）。新增 **§8.3.2** 契约 §3.6 不变量的机械检查（不变量 1/2 已实现为 `error`）。§7.2.2 提示词摘要改为 **HMAC-SHA256**（原 `sha256(salt‖prompt)` 对长度扩展攻击脆弱）并补 `alg` 标志；补 `salted: false` **不等于安全**的说明 | B 流全部工作流 · A 流（埋点摘要须同盐同算法）· C 流（提示词库） |
