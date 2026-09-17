# golden set 评测集（P8-01）

> 覆盖任务：**P8-01**（`todolist.md:408`：电商 30-50 个 case，品类 × 材质 × 光影 × 构图，含输入与期望）
> 上游需求：**FR-7.1**（`PRD_v1.md:204`：golden set 评测集 ≥30 例/工作流 · Must）·
> **AC-F3**（`PRD_v1.md:583`：每条工作流有 golden set ≥30 例，一次通过率 ≥80%）
> 离线校验器：`engine/tools/check_golden_set.py`

---

## 0. ⚠️ 本目录**不**证明什么（必须先读）

| # | 本目录**不**声称 | 说明 |
|---|---|---|
| 1 | **没有任何通过率 / 得分 / 良品率数字** | 每个用例的 `expected` 字段**恒为 `null`**，`expected_status` **恒为 `"pending_gpu"`**。**一次真实评测都没跑过** |
| 2 | **不证明能出图** | 离线校验只做到「Schema 合法 → 渲染成功 → L1 结构自洽 → L3 节点合法性」。**L4（真实出图）需要 GPU**，未做 |
| 3 | **不证明期望输出正确** | 没有归档的参考图、没有评分。`expected: null` 是**刻意的硬约束**，不是占位符 |
| 4 | **不证明画质** | 画质判定属 `docs/sop/quality_rubric.md`（P8-02）与人工抽检（P8-04），本目录只提供**输入侧**的用例 |
| 5 | **不满足 AC-F3 的「每条工作流 ≥30 例」** | 见 §3。V1 现实下**做不到**，原因写在下节 |
| 6 | **参考图一张都没有** | `reference/` 目录当前**只有 `.gitkeep`**。带参考图的三条工作流在离线校验里用**桩 resolver**，不证明素材能被引擎读到 |

> 一句话：**本目录是「评测的输入集」，不是「评测结果」。**
> 任何从本目录读出的「通过率」都必然是编造的。

---

## 1. 目录结构

```
tests/golden_set/
├── README.md                  ← 本文件
├── cases/                     ← 用例（一文件一用例，便于 review 与增删）
│   ├── gs-t2i-001.json … gs-t2i-018.json          （t2i_v1，18 条）
│   ├── gs-klein-001.json … gs-klein-008.json      （flux2_klein_t2i_v1，8 条）
│   ├── gs-i2i-001.json … gs-i2i-006.json          （i2i_v1，6 条）
│   ├── gs-inpaint-001.json … gs-inpaint-005.json  （inpaint_v1，5 条）
│   └── gs-upscale-001.json … gs-upscale-005.json  （upscale_v1，5 条）
└── reference/                 ← 🔴 参考图**只许放这里**（见 §4）
    └── .gitkeep
```

**为什么用 JSON 而不是 YAML**：`engine/` 是「零运行时依赖」的包（只用标准库），
读 YAML 要引 `pyyaml`。用例是 `engine/tools/check_golden_set.py` 直接消费的数据，
所以必须能被 `json` 标准库直接读。

### 1.1 用例格式

```json
{
  "id": "gs-t2i-001",
  "workflow": "t2i_v1",
  "dimensions": {
    "category": "陶瓷咖啡杯",
    "material": "陶瓷釉面",
    "light": "三点布光",
    "composition": "主体70%居中"
  },
  "params": { "prompt": "…", "width": 1024, "seed": 20260916 },
  "expected": null,
  "expected_status": "pending_gpu",
  "notes": "这个用例想覆盖什么失败模式"
}
```

| 字段 | 约定 |
|---|---|
| `id` | `gs-<工作流简称>-NNN`，全局唯一 |
| `workflow` | `workflows/registry.yaml` 里的真实 id（**不能瞎写**，校验器会拦） |
| `dimensions` | **四维必填**：`category`（品类）/ `material`（材质）/ `light`（光影）/ `composition`（构图） |
| `params` | 该用例**要钉住**的参数。其余由渲染器用 Schema 的 `default` 兜底。**参数名必须属于该工作流的 Schema** |
| `expected` | 🔴 **恒为 `null`**。填任何非 null 值 = 编造评测结果，校验器会**判失败** |
| `expected_status` | 🔴 **恒为 `"pending_gpu"`** |
| `notes` | 写清这个用例想覆盖什么失败模式；便于将来评测后回填结论 |

### 1.2 为什么每条都钉住 `seed` 与采样参数

用例把 `seed`（统一 `20260916`）、`sampler_name`、`scheduler` 都写死，
是为了让**将来的评测可复现** —— 「同一用例两次跑出同一条结论」是评测可信的前提
（`docs/sop/debug_log.md` §2.4 的确定性检查同源）。
⚠️ 这不等于「固定 seed 是唯一正确做法」：量产时固定 seed 会牺牲多样性，
探索期仍应用 `-1`（见 `docs/sop/defect_kb.md` D-44）。

---

## 2. 四维覆盖矩阵（`todolist.md:408`）

### 2.1 按维度取值统计

| 维度 | 取值 |
|---|---|
| **品类**（8 类） | 陶瓷咖啡杯 · 不锈钢保温杯 · 真皮托特包 · 亚克力收纳盒 · 羊绒针织衫 · 胡桃木托盘 · 玻璃花瓶 · 碳纤维行李箱 |
| **材质**（8 种） | 陶瓷釉面 · 拉丝不锈钢 · 头层牛皮 · 亚克力透明 · 羊绒针织 · 胡桃木实木 · 玻璃透明 · 碳纤维纹理 |
| **光影**（6 种） | 三点布光 · 柔光箱大面积主光 · 顶光加底板反射 · 自然窗光漫射 · 逆光轮廓光 · 硬光侧光 |
| **构图**（6 种） | 主体70%居中 · 主体50%四周留白 · 特写占比 · 三分法 · 俯视 · 正视 |

**材质与品类一一绑定**（如「陶瓷咖啡杯」对应「陶瓷釉面」），
所以材质维度的覆盖数等于品类维度 —— 这是**刻意**的：材质词只有在确认品类后才有意义
（`assets/prompt_lib/material.yaml` 文件头：「材质词写得太强会覆盖产品真实材质」）。

### 2.2 轴数对照（真值来自 `cases/*.json` 实测，非人工统计）

见 §5 的校验输出 —— 校验器会打印实际分布。**本文件不手抄数字**，
因为手抄的数字会随用例增删而静默过期。

### 2.3 六类缺陷的覆盖（与 `defect_kb.md` 对应）

| 缺陷类 | 覆盖方式 |
|---|---|
| 手部 | ⚠️ **未覆盖** —— 需要模特图（`scenes.yaml` rc-015 标注 V1「谨慎」）。V1 用例全部是**无人商品图** |
| 文字 | 全部用例的 `negative_prompt` 含 `text in image` / `watermark` / `logo`（klein 除外，见下） |
| 畸变 | 结构类负向词 + 五金/缝线品类（真皮托特包 gs-t2i-003/011/018） |
| 过曝 | `gs-t2i-017` 用偏高 `cfg=7.0` 作观察点；白底构图占多数 |
| 材质失真 | 8 种材质**逐个覆盖**：金属（gs-t2i-002/010）、皮具（003）、透明（004/012）、纺织（005/013）、木质（006/014）、碳纤维（008） |
| 结构漂移 | 三条**同品类换光影/构图**的对照：gs-t2i-001/009/017（陶瓷杯）、gs-t2i-005/013（针织）、gs-t2i-003/011/018（皮包） |

> ⚠️ **klein 用例的 `negative_prompt` 为空是刻意的**，不是漏写：
> `flux2_klein_t2i_v1` 的 Schema **根本没有这个字段**，
> 写进去会让校验器判「参数不存在于该工作流 Schema」。
> 详见 `assets/prompt_lib/negative.yaml:5-13`。

---

## 3. ⚠️ AC-F3「每条工作流 ≥30 例」**V1 做不到**（如实说明）

`PRD_v1.md:583`（AC-F3）要求「**每条**工作流有 golden set ≥30 例」。
本目录 **42 条**，分布在 **5** 条工作流上，**平均每条 8.4 例**，**没有任何一条达到 30 例**。

原因是**机械的、不可绕过的**：

| 事实 | 依据 |
|---|---|
| 仓库只有 **5** 条工作流有可执行 JSON | `workflows/*.json` |
| `style_transfer_v1` **注册了但无 JSON**，且 `blocked_by: 缺权重`（`ipadapter/*`、`clip_vision/*`、LoRA 目录枚举**全为空**） | `debug_log.md` §2.3；`registry.yaml` 的 `planned` |
| 另外三条（`i2i_v1` / `inpaint_v1` / `upscale_v1`）`status: disabled`、`render_path_l4: false` —— **渲染路径 L4 未跑** | `registry.yaml`；`debug_log.md` §2.4 |
| 本项目**不做**「img2img + 风格提示词」的降级版冒充风格迁移 | `debug_log.md` §2.3（「功能名不副实」） |

**所以本目录的定位是「V1 的评测输入集」，而不是「满足 AC-F3 的交付物」。**
要在 V1 内真正满足 AC-F3，前置条件是：
① 至少 5 条工作流全部走通 L4（`render_path_l4: true`）；
② 每条补齐到 30 例（含**模特图/手部**场景，需要 `scenes.yaml` rc-015 解禁）；
③ 参考图素材入库（见 §4）。
**这三项都没做。** 本 README 不把「用例数够」说成「AC-F3 达成」。

---

## 4. 🔴 参考图放在哪：**只能放 `reference/`**

```
tests/golden_set/reference/     ← 参考图（商品原图 / 模特原图）**只许放这里**
```

`.gitignore:98-101` 里**只有** `reference/` 这一个路径：

```
tests/golden_set/reference/*
!tests/golden_set/reference/.gitkeep
```

⚠️ **不要**把参考图放进 `cases/`，也不要新建 `tests/golden_set/input/`、
`tests/golden_set/expected/` 这类**在 `reference/` 之外**的目录 ——
那些路径**不在忽略范围内**，大图会被 `git add` 提交进仓库，
把仓库体积和许可证风险一起带进来
（`docs/sop/license_matrix.md` 对图片素材有单独要求；`项目进展.md` #21 已踩过一次
「`.gitignore` 误挡/漏挡 → 工作区绿但仓库不完整」的坑）。

精确的实测覆盖范围见 §4.1。

### 4.1 实测结论（**用真实文件跑出来的，不是读规则推断的**）

`.gitignore:98-101` 的规则是：

```
tests/golden_set/reference/*
!tests/golden_set/reference/.gitkeep
```

本轮用真实文件 + `git status --ignored` **实测**（探针文件已删除）：

| 路径 | 结果 | 说明 |
|---|---|---|
| `tests/golden_set/reference/big.png` | `!!` **被忽略** | 规则生效 |
| `tests/golden_set/reference/input/big.png` | `!!` **被忽略** | `reference/` 的**子目录**也被 `*` 覆盖 |
| `tests/golden_set/input/big.png` | `??` **不被忽略** | 🔴 **会被提交进仓库** |
| `tests/golden_set/reference/.gitkeep` | `git add` 可追踪 | 例外生效 |

> ⚠️ **修正一处此前流传的说法**：曾有记载称「放进 `input/`、`expected/` 这类目录的大图
> **不会**被忽略」。实测表明：**在 `reference/` 之下的**子目录（含 `input/`）**是**被忽略的；
> 真正不被忽略的是 **`reference/` 之外**的路径（如上表的 `tests/golden_set/input/`）。
> 两种说法指向同一个操作纪律，但成因不同 —— 记准确，否则后人按错误原因去「修 `.gitignore`」会改错地方。

### 4.2 那条例外原先是**指向不存在的文件**

第 101 行的 `!tests/golden_set/reference/.gitkeep` 是一条**白名单例外**：
它的作用是让 `reference/` 目录本身能被 Git 追踪（空目录在 Git 里不存在），
从而让「参考图放哪」这件事**在仓库结构上可表达**。

在本次变更之前，`tests/golden_set/reference/` **整个目录都不存在** ——
这条例外**指向一个不存在的文件**，它要保护的对象不在，规则没有落到任何东西上；
同时 `reference/` 在 Git 里**完全没有表示**，「参考图该放哪」只能靠口头约定。
（证据：变更前 `ls -laR tests/golden_set/` 只有 `.gitkeep` 一个文件，`reference/` 不存在。）

本次建立 `tests/golden_set/reference/.gitkeep` **把例外落实**：目录真实存在、
例外有真实对象、`reference/` 在仓库里可追踪。

> **操作纪律**：参考图**只放 `reference/`**。放别处（包括新建
> `tests/golden_set/input/`、`tests/golden_set/expected/`）**不在规则覆盖范围内**，
> 会被提交。拿不准时用 `git status --ignored --short -- <路径>` 自查，不要凭印象。

> 参考图素材当前**未入库**。带参考图的三条工作流（`i2i_v1` / `inpaint_v1` / `upscale_v1`）
> 在离线校验里由 `verify_render_path._stub_resolver` 提供**假文件名**
> （`dryrun_asset_<id>.png`），**只验渲染器结构，不验素材可读**。
> 参考图的验收标准、命名约定与许可审查**尚未定义**，属待办（见 §6）。

---

## 5. 怎样校验（**可执行**）

```bash
# 完整跑：Schema → 渲染 → L1 → L3（不需要 GPU）
timeout 600 backend/.venv/bin/python -m engine.tools.check_golden_set

# 只看某一条工作流的用例
timeout 600 backend/.venv/bin/python -m engine.tools.check_golden_set --workflow t2i_v1

# CI 消费用
timeout 600 backend/.venv/bin/python -m engine.tools.check_golden_set --json
```

退出码：`0` 全绿 · `1` 有用例失败 · `2` 用法/路径错。

**校验器能抓出**：参数名跨工作流复制错（把 `mask_expand` 写进 `t2i_v1` 的用例）、
`seed` 越界、enum 拼错、`targets` 注入点漂移（节点 ID / 入参名对不上）。

⚠️ **它不能证明的**：期望输出是否正确、画质是否达标、**能不能出图**。
L4 需要 GPU，且本仓库**至今没跑过任何一次 golden set 评测**。

---

## 6. 待办（**未完成项，不许当成已做**）

| # | 待办 | 阻塞于 |
|---|---|---|
| 1 | 在 GPU 上真实跑一遍 golden set，归档产物 | 需开机 + 参考图素材（见 3） |
| 2 | 回填 `expected`（真实期望图路径）与评分 | 依赖 1 完成后另立字段，**不得**先把 `expected` 填成估计值 |
| 3 | 参考图素材入库 `reference/` + 许可审查 | 素材来源与许可未定；`license_matrix.md` 尚未覆盖图片素材 |
| 4 | 补齐到「每条工作流 ≥30 例」（AC-F3） | 需先解 `style_transfer_v1` 的权重缺口，且三条 L4 未跑 |
| 5 | 模特图 / 手部场景用例 | `scenes.yaml` rc-015 标注 V1 谨慎；需先有合规的模特素材 |
| 6 | 与 `docs/sop/quality_rubric.md` 打通（评分维度写回用例） | P8-02 已交付，但两者**尚未联调** |
