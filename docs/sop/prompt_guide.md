# 提示词库使用说明与贡献规范（P5-08）

> 本文件回答两个问题：**怎么用**（写工作流/模板/后端时如何引用）与**怎么加**（新词条必须满足什么）。
> 词条本体在 [`assets/prompt_lib/`](../../assets/prompt_lib/)。
> **最后核实**：2026-09-16 ｜ 对应引擎：ComfyUI v0.36.0 + torch 2.13.0+cu130（见 `deploy/versions.lock`）

---

## 0. 一句话结论

提示词库的价值不在于「词多」，而在于**把电商出图的不确定性收敛成一张可检索、可组合、可回归的表**。
因此本库的每一条词都带 `id`、标签与适用场景，**配方只允许引用 id，不允许内联自由文本** —— 否则统计与回归都做不了。

---

## 1. ⚠️ 四条硬约束（先看这个，否则会白干）

| # | 约束 | 依据（**均为可直接核验的事实，不是机制推断**） | 后果 |
|---|---|---|---|
| 1 | 🚨 **klein 链路上严禁写权重 `(word:1.2)`** | 源码：`KleinTokenizer` 显式传 `disable_weights=True`（`comfy/text_encoders/flux.py:153,169`）→ 原样字符串进分词 | **不是"无效"，是污染提示词**（括号/冒号/数字成为 token）。详见 §1.1 |
| 2 | **klein 链路上反向词无效，且无处可填** | ① 图上：官方 Distilled 子图用 `ConditioningZeroOut` 把负向置零 ② 接口上：Schema 未暴露 `negative_prompt` 字段 | 填不进去。**不要为 klein 补该字段** = 避免"看起来能用"的陷阱。详见下节 |
| 3 | **材质词是商品真实性声明** | 商用红线（`license_matrix.md` §6） | 写了「真丝」实际是涤纶 = 虚假宣传，法律风险 |
| 4 | **文字不进模型，一律后期叠加** | 中文渲染错误率高；伪造品牌标识属法律红线 | 生成图中的乱码文字 + 假 logo 会直接毁掉交付物 |

> 💡 约束 1、2 的共同教训：**「该能力被禁用」与「该能力无效」是两种不同的用户指引**。
> 前者要写「**严禁/别写**」，后者会被理解成「写了无害」。
> 凡是「看起来能用但其实不能用」的字段，都是在给用户挖坑（`debug_log.md` G7 沉淀）。

### 1.1 🚨 权重语法 `(word:1.2)` 在 klein 链路上**严禁使用**（源码级定论，**不是"效果待测"**）

> **口径纪律（最重要的一条）**：必须写「**klein 上严禁使用权重语法**」，**不能**写「klein 上权重无效」。
> 后者会被用户理解成「写了也没关系」，而实际情况是**提示词被污染** —— 这是两种完全不同的用户指引。
> 依据：`docs/sop/debug_log.md` **G7**（源码级定论）＋ `项目进展.md` 待解决清单。

| 项 | 内容 |
|---|---|
| **结论** | klein 链路上 **`(word:1.2)` 不被解析，且其字面字符会进入分词 → 主动污染提示词**（括号、冒号、数字都变成 token 喂给模型） |
| **源码证据** | `comfy/text_encoders/flux.py:153,169` —— `KleinTokenizer.tokenize_with_weights()` **显式传 `disable_weights=True`**；`comfy/sd1_clip.py:585-588` —— 该分支走 `parsed_weights = [(text, 1.0)]`，**根本不调用 `token_weights()`**，原样字符串直接进分词<br>⚠️ **证据归属**：该源码为 **team-lead 在 `/root/ComfyUI` 读取**（`debug_log.md` G7）；**C 流未独立复核此源码**，本文件为**转录引用**。"该包提供 4 个 `BNK_*`"一项 C 流已独立用 `object_info` 复核 ✅ |
| **为什么 klein 会这样** | `KleinTokenizer` 继承 `SD1Tokenizer`（**本来有解析能力**），是这个子类**显式把能力关掉了** —— 不是引擎不具备该能力 |
| **能力来源澄清** | `(word:1.2)` 是**核心 `CLIPTextEncode`** 自带能力（`python_module=nodes`），**与 `ComfyUI_ADV_CLIP_emb` 无关**。后者的职责是「权重**怎么解释**」（提供 4 个 `BNK_*` 节点 + `token_normalization` / `weight_interpretation` 5 种语义），两条图里**一个 `BNK_*` 都没接** |
| **两条链路的最终口径** | `t2i_v1`（SDXL）：✅ **可正常使用权重**（走 SD1 tokenizer 且未 disable）<br>`flux2_klein_t2i_v1`：🚫 **严禁使用**（写了 = 污染） |
| **现状（重要）** | 提示词库**已正则扫过：全部 YAML 中没有任何 `(word:1.2)` 写法**，故**当前没有正在受损的内容**。这是「先查清再上」的窗口期，不需要回退任何东西 |
| **前端提示文案（D 流）** | 将来做提示词输入 UI 时，**必须在 klein 工作流下显示「禁用权重语法」警示**，而不是静默接受输入。口径同上：「严禁」而非「无效」 |

**⚠️ 本节写错过两次，都保留在案以免重犯（这是"机制推断"类错误的连环案例）：**

| # | 曾经的错误结论 | 谁推翻的 | 正确的机制 |
|---|---|---|---|
| ① | 「权重语法**依赖**保留节点 `ComfyUI_ADV_CLIP_emb`」 | B 流（`object_info` 静态核查） | 权重是**核心 `CLIPTextEncode`** 的能力；该节点只改**解释方式** |
| ② | 「`cfg=1` → 正负向相减为 0 → 权重无效果」 | team-lead（读源码，G7 ③） | **与 CFG 无关**：权重由 tokenizer 产出 `(token, weight)` 对、经 `encode_token_weights` 作用于 **embedding**。所以只要解析开着，`cfg=1` 也照样有加权效果；klein 失效的原因是**解析被显式关掉**，不是 CFG |

> ⭐ **教训（已写入 G7 沉淀）**：**"这样改应该能行"和"我推断的机制是对的"是同一类错误。**
> 两次错误都是「听起来很合理的机制推断」，且第二次是在同一个条目里、刚被第一次教训之后又犯的。
> → 因此本文件从此**不写未经验证的机制**：只写「源码/数据直接支撑的事实」+「明确的开放问题」。

**待验证（全部需 GPU，并入 P4；均为开放问题，不夹带预期结论）**

| # | 内容 | 目的 |
|---|---|---|
| ① | klein 上把 `(mug:1.5)` **原样**送入，与干净提示词对比 | 量化"主动污染"的程度（是否显著劣化） |
| ② | SDXL 上「带权重 vs 去权重」对照 | 验证机制（对照组，预期有差异） |
| ③ | 把 klein 的文本编码节点从核心 `CLIPTextEncode` 换成 **`BNK_CLIPTextEncodeAdvanced`**，看权重是否恢复 | **开放问题**。⚠️ 且**存在反例机制**：`KleinTokenizer` 内部硬编码了 `disable_weights=True`，因此**任何**走 `clip.tokenize*()` 的节点（含 `BNK_*`）拿到的都可能是**已丢弃权重**的 token 序列 —— 除非该节点自己重新解析并对 embedding 作用权重。**故不能预期它有效**；测它的价值在于：**若无效，"klein 支持权重"这条路即被证伪，不必再投入** |

### 两条链路的反向词策略（务必分清）

| 链路 | 采样参数 | 反向词 | 该怎么做 |
|---|---|---|---|
| `t2i_v1`（SDXL 1.0） | 25-30 步 / cfg 7.0-8.0 | ✅ **有效** | 直接用 `negative_packs.yaml` 的词包 |
| `flux2_klein_t2i_v1`（FLUX.2 klein 4B 蒸馏） | 4 步 / cfg 1.0 | ❌ **无效（且无处可填）** | 把反向诉求改写成：① 正向增强词（如 `structure accurate`）② 局部重绘 ③ 人工筛选 |

> ⚠️ **klein 链路上反向词的失效有两条独立的落地证据**（不依赖机制推断）：
> ① **图上**：官方 Distilled 子图用 `ConditioningZeroOut` 把负向**整体置零**
> ② **接口上**：B 流确认 klein 工作流的参数 Schema **根本没有暴露 `negative_prompt` 字段** → **在当前 API 上无处可填**
> 所以不是"填了不生效"，而是**填不进去**。**不要为 klein 的 Schema 补 `negative_prompt` 字段** ——
> 补了会让用户误以为能生效，属"看起来能用"的陷阱（与 §1.1 的「严禁 vs 无效」同一类问题）。
>
> **`negative.yaml` / `negative_packs.yaml` 在 V1 的实际作用域**：① `t2i_v1`/SDXL 链路 ② **人工质检清单**
> （`severity: hit` 的条目即判废标准）。**配方里的 `negative` 槽位在 klein 链路上不注入、不生效** ——
> 该事实已用机器可读的方式写在 `assets/prompt_lib/scenes.yaml` 的 `negative_policy` 段。

---

## 2. 怎么用

### 2.1 按「配方」用（推荐，覆盖 90% 场景）

`assets/prompt_lib/scenes.yaml` 的 `recipes` 是**可直接投产的成品**：

```yaml
- id: rc-001
  name: 白底主图 · 标准
  workflow: flux2_klein_t2i_v1
  canvas: {width: 1024, height: 1024, ratio: "1:1"}
  params: {steps: 4, cfg: 1.0}
  slots:
    subject: [pos-001]
    attribute: [pos-048, pos-051]
    light: [lig-001, lig-003, lig-029]
    composition: [cmp-001, cmp-013]
    style: [sty-001]
    negative: npk-001
  template: "{subject}，{attribute}，{light}，{composition}，{style}，产品摄影级画质，商业可用的干净画面"
```

**用法**：换品只需替换 `subject`（+ 必要时 `material`），其余槽位保持不变 —— 这就是「同一风格批量换品」的最小改动集。

### 2.2 按「槽位」拼装（P5-09 模板引擎的输入契约）

`scenes.yaml` 的 `slots_contract` 定义了拼装规则：

| 规则 | 值 | 说明 |
|---|---|---|
| 拼装顺序 | `subject → material → style → light → composition → quality_tail` | 靠前权重更高，主体词必须在最前 |
| 固定尾缀 | `pos-055`（产品摄影级画质）+ `pos-061`（商业可用的干净画面） | 全场景必带，兜底质量 |
| 词条上限 | 12 条 | 超限后边际收益急剧下降，且会稀释主体词 |

> **为什么要有上限**：提示词不是越长越好。klein 的文本编码器有效上下文有限（`qwen_3_4b`），堆到 20+ 条后主体词的相对权重被摊薄，
> **表现为「产品变了」而不是「更精细了」**。
> ⚠️ 该解释里含一个**未验证的机制假设**（「词条在文本里的相对权重会影响结果」）—— 按 §1.1 的教训，本文件不写未经验证的机制。
> 已知的是：**权重语法在 klein 上被禁用**（§1.1），所以 klein 上根本不存在"词条权重被摊薄"这回事；
> 因此对 klein 更可能的解释是**纯上下文长度**（token 预算被摊薄）。
> **上限 12 条的实操建议不受影响** —— 「词条过多 → 主体不保真」是实测现象，与机制解释无关。
> 若将来要写死机制，需先做对照实验（去权重、只变长度）。

### 2.3 按「原子词条」检索

需要精确控制时，按标签检索：

```bash
# 找所有「皮具」材质词
grep -n "皮具" assets/prompt_lib/material.yaml

# 找所有高风险（形变）风格
grep -n "morph_risk: high" assets/prompt_lib/style.yaml
```

### 2.4 配方**落在哪里**：走 `Template.preset_params`，**不要写进 Schema 的 `default`**

> ⚠️ 本节曾建议「把配方拼装结果作为 `prompt` 字段的 `default`」—— **那是错的，已按 B 流的架构约束更正**（2026-09-16）。

**正确落位**：配方 → `templates` 表的 **`preset_params`**（FR-2.4 / FR-3.2），由 A 流灌入。

| 维度 | 为什么是 `preset_params` 而不是 Schema `default` |
|---|---|
| **契约语义** | 契约 §3.4 的优先级链是 `fields[].default < preset_params < 请求 params < 顶层别名` —— **配方的语义正是「按模板覆盖默认值」**，`preset_params` 就是为它设计的 |
| **不变量** | B 流在 `workflow_spec.md` §5.5 定了「**`default` 必须等于模板 JSON 里的字面值**」。这条不是形式主义：`07_bench_workflow.py` 等工具**裸提交模板**，而生产走渲染路径 —— 两者不一致会导致**性能基线测的不是生产那张图**，且排错时无法用「裸提交」二分定位是参数问题还是渲染问题 |
| **基数不匹配** | Schema `default` 是**每条工作流一个**（2 条），配方是**每个场景一个**（16 条）。一对多塞进 `default` → 只有 1 个配方能当默认，其余 15 个无处安放 |
| **副作用成本** | 改模板字面值会**改动节点图** → 使既有 GPU 验证失效（`workflow_spec.md` G6/§8.3）。为了「让配方当默认」付这个代价不成比例 |

**Schema 里的 `prompt` 字段应该长这样**（`default` 保持为空字符串，等于模板字面值）：

```json
{
  "key": "prompt", "label": "正向提示词", "type": "text",
  "default": "",
  "group": "提示词", "max_length": 2000,
  "targets": [ { "node_id": "6", "input": "text" } ]
}
```

**配方则以 `preset_params` 的形式随模板提供**，例如：

```json
{
  "name": "白底主图 · 标准",
  "workflow_name": "flux2_klein_t2i_v1",
  "preset_params": {
    "prompt": "<rc-001 槽位拼装结果>",
    "width": 1024, "height": 1024, "steps": 4, "cfg": 1.0
  }
}
```

`{sku_name}` 一类的变量插值由 P5-09 模板引擎处理（批量换品的 SKU 名注入点）。
若希望某条配方**成为某工作流的默认**，那属于「改模板字面值」—— 需走 `改模板 + version+1 + 重跑 L4` 并**先与 team-lead 确认**，不是本文件能自行决定的事。

> ⚠️ **配方的 `workflow` 字段取值纪律**：只能引用 `workflows/registry.yaml` 里**已定义**的 id
> （当前为 `t2i_v1` 与 `flux2_klein_t2i_v1`）。`registry` 的 `planned` 段（`i2i_v1` / `upscale_v1` /
> `inpaint_v1` / `style_transfer_v1` / `batch_v1`）尚无定义文件，**一律不可引用** —— 本文件不自行造名字。

---

## 3. 怎么加（贡献规范）

### 3.1 新增词条的门槛

| 检查项 | 要求 |
|---|---|
| **可验证** | 必须能说清「它解决什么失败模式」，并在 ≥1 条 golden set 上验证过（P5-02 之后强制执行） |
| **不重复** | 语义重复词条合并，不新增。查重：`grep -n "<关键词>" assets/prompt_lib/*.yaml` |
| **带标签** | `tags` 第一级必须取自 `README.md` 的固定表，不允许自创一级标签 |
| **带场景** | `scenes` 取值只能来自六种固定场景（或反向/通用词的 `全部`） |
| **id 不复用** | 新增用当前最大序号 +1；**删除词条时不回收编号**（历史产物元数据靠 id 追溯） |
| **标风险** | 形变/合规有风险的一律加 ⚠️ 与 `note` 说明，风格词标 `morph_risk` |
| 🚫 **禁止写权重语法** | 词条的 `text` / `text_en` / 配方 `template` 中**一律不得出现 `(word:1.2)`**。理由：klein 链路上它是**污染源**（§1.1），而配方默认落在 klein。**若确有需要，必须在 `note` 里写明「仅 `t2i_v1` 适用」** |

### 3.2 新增配方（`scenes.yaml`）

新增配方必须同时给出：`workflow`、`canvas`、`params`、完整 `slots`、`template`、`negative`、`notes`。
**`notes` 必填** —— 配方不带踩坑提示等于把坑留给下一个人。

### 3.3 修改已有词条

| 改动类型 | 处理 |
|---|---|
| 改 `text`（语义变化） | **视为删除 + 新增**：旧 id 标记 `deprecated: true`，新 id 重新编号。理由：历史产物的元数据指向旧 id，改内容 = 篡改历史 |
| 改 `text`（仅措辞，语义不变） | 允许原地改，但需在提交信息里写明 |
| 改 `tags` / `scenes` | 允许原地改 |
| 改 `note` | 允许 |

### 3.4 提交前自检

```bash
# 1. YAML 语法与条目数
cd assets/prompt_lib && python3 -c "
import yaml
for f in ['positive','style','material','light','composition','negative','negative_packs','scenes']:
    d = yaml.safe_load(open(f + '.yaml'))
    k = 'entries' if 'entries' in d else ('packs' if 'packs' in d else 'recipes')
    print(f, len(d[k]))"

# 2. 引用完整性（配方里引用的 id 必须存在）
python3 - <<'PY'
import yaml, glob
ids = set()
for f in glob.glob('*.yaml'):
    d = yaml.safe_load(open(f))
    for k in ('entries', 'packs'):
        for e in (d.get(k) or []):
            ids.add(e['id'])
bad = []
for r in (yaml.safe_load(open('scenes.yaml')).get('recipes') or []):
    for slot, refs in (r.get('slots') or {}).items():
        if isinstance(refs, list):
            for x in refs:
                if x not in ids: bad.append((r['id'], slot, x))
print('BROKEN_REFS:', bad or 'NONE')
PY

# 3. 🚫 权重语法扫描（klein 禁用，见 §1.1 —— 这条必须为 NONE）
python3 - <<'PY'
import yaml, glob, re
pat = re.compile(r'\(\s*[^()]{1,60}:\s*\d+(\.\d+)?\s*\)')
bad = []
for f in glob.glob('*.yaml'):
    d = yaml.safe_load(open(f))
    for e in (d.get('entries') or []):
        for k in ('text', 'text_en'):
            if e.get(k) and pat.search(e[k]): bad.append((e['id'], k, e[k]))
    for key in ('packs', 'recipes'):
        for e in (d.get(key) or []):
            if pat.search(e.get('template') or ''): bad.append((e['id'], 'template', e['template']))
print('WEIGHT_SYNTAX_IN_CONTENT:', bad or 'NONE')
PY

# 4. 配方 workflow 名必须已在 workflows/registry.yaml 注册
python3 - <<'PY'
import yaml, re
ids = set(re.findall(r'^\s*-?\s*id:\s*([A-Za-z0-9_]+)',
                     open('../../workflows/registry.yaml').read(), re.M))
used = {r['workflow'] for r in yaml.safe_load(open('scenes.yaml'))['recipes']}
print('UNREGISTERED_WORKFLOW:', used - ids or 'NONE')
PY
```

> 第 3、4 项是**必须为 NONE** 的硬门禁：命中权重语法 = 会污染 klein 提示词；引用未注册 workflow = 运行期才炸。

---

## 4. 与项目其它部分的接口

| 接口 | 契约 |
|---|---|
| **工作流内核（B 流）** | 配方里的 `workflow` 名必须是 `workflows/registry.yaml` 里**已定义**的 id（当前 `t2i_v1` / `flux2_klein_t2i_v1`）；`params` 里的键必须存在于该工作流的参数 Schema。**配方本体走 `Template.preset_params`，不进 Schema 的 `default`**（见 §2.4） |
| **后端（A 流）** | ① 词条导入 DB 表 `prompt_library`（`backend/app/models/asset.py:67`）：`title` ← 配方 `name`；`category` ← 第一级标签/品类；`positive`/`negative` ← 拼装结果；`tags` ← 原样写入。② 配方 → `templates` 表 `preset_params`（FR-2.4/FR-3.2） |
| **模板库（P3-10）** | `assets/prompt_lib/templates/` 由 `scenes.yaml` 落地为工作流预设 |
| **产物元数据（P3-11）** | 每张产出图必须记录 `recipe_id` + 实际使用的词条 `id` 列表，否则无法复现（FR-5.4/FR-5.5） |
| **评测（FR-7.1）** | 每条配方在 golden set 上的良品率回填到配方 `notes` 上方新增的 `eval` 字段（P5-02 后启用） |

---

## 5. 待补齐项

| # | 事项 | 归属 | 说明 |
|---|---|---|---|
| 0 | ⚠️ **klein 链路禁用权重语法的前端提示** | **D 流（前端）+ C 流（文案）** | 见 §1.1。口径必须是「**严禁使用**」而非「无效」—— 后者会让用户以为"写了没关系"，实际是污染提示词（`debug_log.md` G7） |
| 0b | 🔬 **G7 待验证 ③：klein 的文本编码节点换成 `BNK_CLIPTextEncodeAdvanced` 能否恢复权重** | B 流（L4，需 GPU） | **开放问题，不夹带预期结论**。⚠️ 存在反例机制：`KleinTokenizer` 内部硬编码 `disable_weights=True`，故**不能预期它有效**；测它的价值在于「若无效则此路被证伪，不必再投入」 |
| 0c | 🔬 **G7 待验证 ①②：klein 上量化"污染"程度 + SDXL 对照** | B 流（L4，需 GPU） | ①`(mug:1.5)` 原样送入 vs 干净提示词 ②SDXL 带权重 vs 去权重 |
| 1 | `eval` 字段（配方良品率） | P5-02 之后 | 需 golden set 跑完才有数 |
| 2 | 品类维度的配方扩充 | 随模板库 | 当前 16 条配方覆盖六大场景，品类细分（母婴/宠物/运动户外）待补 |
| 3 | 变量插值语法冻结 | P5-09 | `{sku_name}` 等占位符的完整语法需在模板引擎里定义并回写本文件 |
| 4 | 英文提示词实测 | P5-02 | `text_en` 目前是人工对照，未做「中英良品率对比」实验 |
| 5 | 提示词 → 产物元数据的落库验证 | A 流 | 需 DB 侧确认 `prompt_library` 字段能容纳配方结构 |
| 6 | ~~配方 `workflow` 名与 `workflows/registry.yaml` 对账~~ | ✅ **已完成** | 2026-09-16 B 流答复：合法 id 为 `t2i_v1` + `flux2_klein_t2i_v1`；`rc-015` 的 `sdxl` 已改为 `t2i_v1`；`planned` 段 5 个 id 不可引用（已在 `scenes.yaml` 头部写明） |
| 7 | 配方落地到 `templates` 表的 `preset_params` | A 流（P3-10） | 落位方式已按 B 流约束更正（§2.4）；需 A 流在 `templates` 表提供入口 |
| 8 | ~~若 L4 证明 klein 上权重效果不足，是否接入 `BNK_*` 节点~~ | ✅ **已收敛** | G7 源码定论已把问题从"效果量级"变为"**klein 严禁写权重**"；是否接 `BNK_*` 并入上表 0b（开放问题）。`ComfyUI_ADV_CLIP_emb` 在白名单里已是 `review`（未接线） |
