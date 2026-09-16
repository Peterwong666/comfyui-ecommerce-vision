# 提示词库使用说明与贡献规范（P5-08）

> 本文件回答两个问题：**怎么用**（写工作流/模板/后端时如何引用）与**怎么加**（新词条必须满足什么）。
> 词条本体在 [`assets/prompt_lib/`](../../assets/prompt_lib/)。
> **最后核实**：2026-09-16 ｜ 对应引擎：ComfyUI v0.36.0 + torch 2.13.0+cu130（见 `deploy/versions.lock`）

---

## 0. 一句话结论

提示词库的价值不在于「词多」，而在于**把电商出图的不确定性收敛成一张可检索、可组合、可回归的表**。
因此本库的每一条词都带 `id`、标签与适用场景，**配方只允许引用 id，不允许内联自由文本** —— 否则统计与回归都做不了。

---

## 1. ⚠️ 三条硬约束（先看这个，否则会白干）

| # | 约束 | 依据 | 后果 |
|---|---|---|---|
| 1 | **反向提示词只在 CFG > 1 时生效** | CFG=1 时去噪结果 = 纯条件分支，反向分支权重为零；官方 klein 模板对应 `ConditioningZeroOut`（`项目进展.md` #16 验证 3） | ① `t2i_v1`/SDXL（cfg 7-8）可用 ② klein 链路**连 Schema 里的 `negative_prompt` 字段都没有**，无处可填 → 不要补，补了是"看起来能用"的陷阱。详见下方 §1.1 之后的说明 |
| 2 | **材质词是商品真实性声明** | 商用红线（`license_matrix.md` §6） | 写了「真丝」实际是涤纶 = 虚假宣传，法律风险 |
| 3 | **文字不进模型，一律后期叠加** | 中文渲染错误率高；伪造品牌标识属法律红线 | 生成图中的乱码文字 + 假 logo 会直接毁掉交付物 |

### 1.1 🔶 待验证项：权重语法在 **cfg=1 的 klein 链路**上的**效果量级**（不是"能不能写"）

> ⚠️ **本节曾有过一个错误前提，已由 B 流证伪，保留记录以免重犯**：
> 原先写「权重语法 `(word:1.2)` **依赖保留节点** `ComfyUI_ADV_CLIP_emb`」→ **不成立**。
> B 流用 `object_info.v0.36.0.json` 证明该包只提供 4 个 `BNK_*` 节点，**我们两条图里一个都没有**；
> 条件是**核心** `CLIPTextEncode` 自带的。详见 `docs/sop/debug_log.md` G7。

| 项 | 内容 |
|---|---|
| **已澄清（能力来源）** | ✅ 写权重是**核心 `CLIPTextEncode` 自带**能力（`python_module=nodes`），两条链路都能写 —— **与 `ADV_CLIP_emb` 无关** |
| **`ComfyUI_ADV_CLIP_emb` 实际管什么** | 它多出的是两个参数：`token_normalization`（4 种）与 **`weight_interpretation`（5 种：`comfy` / `A1111` / `compel` / `comfy++` / `down_weight`）** —— 即它管「**权重怎么解释**」，不管「能不能写权重」。当前两条图未接 `BNK_*` → 我们用的是 **ComfyUI 默认语义** |
| **结论状态** | 🔶 **待验证（L4，需 GPU）** —— 能否写已确定；**未确定的是 `cfg=1` 蒸馏链路上权重变更的「效果量级」是否实用**。蒸馏模型没有 guidance 放大，权重作用可能被削弱 |
| **影响面** | P5-09 模板引擎的权重插值、以及所有依赖「调权重压过某个词」的配方调优手法。（提示词库本身**没有** `weight_hint` 之类字段，也没有任何 `(word:1.2)` 写法 —— 已正则扫过，故**此刻没有正在受损的内容**） |
| **验证方式（B 流已写入 `debug_log.md` G7）** | 同一 seed、同一构图跑 3 组：① 无权重基线 ② `(mug:1.5)` 加权 ③ `(mug:0.6)` 降权。**klein（cfg=1）与 `t2i_v1`/SDXL（cfg≈6.5）各做一遍做对照**。<br>· klein 三组近乎一致 + SDXL 三组有明显差异 → 权重在 klein 上「结构上生效但效果量级不足」<br>· 两组都有差异 → 权重可正常用于两条链路 |
| **归属** | B 流（工作流定型时验证，L4） |
| **在此之前的写法纪律** | ① 配方 `notes` 与 `prompt_guide` 中**不得**把权重当作确定有效的手段来建议，一律标注「待验证」 ② 配方调优**优先用「增删词条」而非「调权重」** ③ 若实测发现 klein 上效果不足，则本文件的 weight 小节改为「**SDXL 可用、klein 不建议**」；若需要更细的权重语义，再评估接入 `BNK_*` 节点（该节点当前在白名单里是 `review`，正因未接线） |

### 两条链路的反向词策略（务必分清）

| 链路 | 采样参数 | 反向词 | 该怎么做 |
|---|---|---|---|
| `t2i_v1`（SDXL 1.0） | 25-30 步 / cfg 7.0-8.0 | ✅ **有效** | 直接用 `negative_packs.yaml` 的词包 |
| `flux2_klein_t2i_v1`（FLUX.2 klein 4B 蒸馏） | 4 步 / cfg 1.0 | ❌ **无效** | 把反向诉求改写成：① 正向增强词（如 `structure accurate`）② 局部重绘 ③ 人工筛选 |

> ⚠️ **klein 链路更进一步：连 `negative_prompt` 字段都没有暴露**（B 流已确认，契约 §3 的 Schema 里无此字段）。
> 原因是官方 Distilled 子图用 `ConditioningZeroOut` 把负向**整体置零** —— 所以不是"填了不起作用"，
> 而是**在当前 API 上根本无处可填**。`negative_packs.yaml` 对 klein **在 API 层面不可用**。
> **不要为 klein 的 Schema 补 `negative_prompt` 字段** —— 补了会让用户误以为能生效，属"看起来能用"的陷阱。
> `negative.yaml` / `negative_packs.yaml` 因此在 V1 的实际作用域是：① `t2i_v1`/SDXL 链路 ② **人工质检清单**（`severity: hit` 的条目就是判废标准）。

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
> ⚠️ 该解释依赖「词条在文本里的相对权重会影响结果」这一前提 —— **它同样受 §1.1 待验证项约束**
> （若 cfg=1 下权重机制不生效，「稀释」的成因描述需要修正为纯上下文长度问题）。
> 但**上限 12 条的实操建议不受影响**：无论机制如何，「词条过多 → 主体不保真」是实测现象。

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
```

---

## 4. 与项目其它部分的接口

| 接口 | 契约 |
|---|---|
| **工作流内核（B 流）** | 配方里的 `workflow` 名必须存在于 `workflows/registry.yaml`；`params` 里的键必须存在于该工作流的参数 Schema |
| **后端（A 流）** | 词条导入 DB 表 `prompt_library`（`backend/app/models/asset.py:67`）：`title` ← 配方 `name`；`category` ← 第一级标签/品类；`positive`/`negative` ← 拼装结果；`tags` ← 原样写入 |
| **模板库（P3-10）** | `assets/prompt_lib/templates/` 由 `scenes.yaml` 落地为工作流预设 |
| **产物元数据（P3-11）** | 每张产出图必须记录 `recipe_id` + 实际使用的词条 `id` 列表，否则无法复现（FR-5.4/FR-5.5） |
| **评测（FR-7.1）** | 每条配方在 golden set 上的良品率回填到配方 `notes` 上方新增的 `eval` 字段（P5-02 后启用） |

---

## 5. 待补齐项

| # | 事项 | 归属 | 说明 |
|---|---|---|---|
| 0 | 🔶 **提示词权重语法在 cfg=1 的 klein 链路上是否生效** | **B 流（最高优先）** | 见 §1.1。实测前本库一律不把权重当确定有效的手段；结果需回写 §1.1 与 `README.md` 的字段约定 |
| 1 | `eval` 字段（配方良品率） | P5-02 之后 | 需 golden set 跑完才有数 |
| 2 | 品类维度的配方扩充 | 随模板库 | 当前 16 条配方覆盖六大场景，品类细分（母婴/宠物/运动户外）待补 |
| 3 | 变量插值语法冻结 | P5-09 | `{sku_name}` 等占位符的完整语法需在模板引擎里定义并回写本文件 |
| 4 | 英文提示词实测 | P5-02 | `text_en` 目前是人工对照，未做「中英良品率对比」实验 |
| 5 | 提示词 → 产物元数据的落库验证 | A 流 | 需 DB 侧确认 `prompt_library` 字段能容纳配方结构 |
| 6 | 配方 `workflow` 名与 `workflows/registry.yaml` 对账 | B ↔ C | 已发出对账请求（C 流只改自己文件） |
