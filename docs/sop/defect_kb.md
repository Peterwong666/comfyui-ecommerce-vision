# 缺陷知识库（P8-06）

> 覆盖任务：**P8-06**（FR-7.3 / AC-P4：≥30 条，结构「瑕疵类型 → 成因 → 解法 → 关联参数」）
> 分类口径来源：`todolist.md:52`（覆盖 **手部 / 文字 / 畸变 / 过曝 / 材质失真 / 结构漂移** 六类）
> 上游素材：`assets/prompt_lib/negative.yaml`（现象 + 反向词）·
> `docs/sop/debug_log.md`（G1~G9、§2.1~§2.7）· `docs/sop/model_guide.md` §5 ·
> `docs/prd/PRD_v1.md` §6.1（EX-1~EX-14）· `docs/sop/prompt_guide.md`
> 机械校验器：`engine/tools/check_defect_kb.py`

---

## 0. ⚠️ 本文件**不**证明什么（必须先读，否则会误读全表）

| # | 本文件**不**声称 | 说明 |
|---|---|---|
| 1 | **不证明任何解法「有效」** | 表里写的是「按机制**应当**怎么做」，**不是**「这么做已被验证能修掉该瑕疵」。全库**没有一条**做过真实出图验证（`verified_by=gpu` 为 **0** 条） |
| 2 | **不提供任何画质数字** | 没有通过率、良品率、得分、对比图。一条都没有过评测流程 |
| 3 | **不证明反向词「够用」** | 多条解法的「反向词」一栏来自 `negative.yaml` 的**设计值**。该文件自己就写明「反向词不足以保证」（如 `neg-025` 去水印） |
| 4 | **不覆盖 V2 能力** | 表里涉及 LoRA / ControlNet / IP-Adapter / 专用 inpaint 模型 / 放大器模型的解法**一律未纳入** —— 这些权重在本项目**服务器上不存在**（`debug_log.md` §2.3 / §2.7、`model_guide.md` §5.3）。写了也执行不了 |
| 5 | **不替代人工质检** | 「成因 → 解法」是**排查索引**，不是判废标准。判废标准在 `negative.yaml` 的 `severity: hit` 上，由 `docs/sop/quality_sampling_sop.md` 负责 |
| 6 | **不区分「已验证」与「文档抄录」** | 见 §1 的 `verified_by` 定义。绝大多数条目只是 `doc` |

> 一句话：**本文件的价值在于「把已知的失败模式索引起来」，不在于「证明这些解法有效」。**
> 机械校验器能证明的**唯一**一件事是「`recommended_params` 里的参数名与取值在该工作流的 Schema 上合法」——
> 它**看不见画质**，也**看不见因果**。

---

## 1. 机器可读格式（与 `engine/tools/check_defect_kb.py` 的解析器一一对应）

每个分类一个二级标题，形如 `## 2. text · 文字`；每条一行表格，**恰好 9 列**，
**单元格内不得出现 `|`**：

```
| id | 瑕疵（现象） | 成因 | 解法 | 链路 | recommended_params | negative_refs | verified_by | 依据 |
```

| 列 | 约定 |
|---|---|
| `id` | `D-NN`，全库唯一 |
| `链路` | `sdxl` = 仅 CFG>1 链路成立 · `klein` = CFG=1 蒸馏链路 · `all` = 与 CFG 无关 · `post` = 后处理/流程侧，与链路无关 |
| `recommended_params` | `—` 表示**没有可机械校验的关联参数**（该条被校验器**跳过、不计入通过**）；否则为 JSON 对象，**必须含 `workflow` 键**指明所属工作流 |
| `negative_refs` | `neg-001,neg-004` 形式，或 `—` |
| `verified_by` | 见下表。**`gpu` 一条都没有** |

### 1.1 `verified_by` 的四个等级（**别把 `l3` 读成「已验证有效」**）

| 值 | 含义（**严格限定**） | 本库条数（`check_defect_kb.py` 实测） |
|---|---|---|
| `none` | **推演**。由 Schema / 机制推导出来的条目，**未在任何既有文档里找到对应记录** | **1**（D-41） |
| `doc` | **文档抄录**。现象/成因/解法取自 `negative.yaml` / `debug_log.md` / `model_guide.md` / `PRD` 的既有记载 | **10** |
| `l3` | `doc` **且** 该条 `recommended_params` **已通过 `check_defect_kb.py` 的机械合法性校验**（参数名存在于该工作流 Schema、类型/边界/enum/seed 域全部合法） | **33** |
| `gpu` | **真实出图验证过**。⚠️ 本库**当前为 0 条**，且在本项目跑通 golden set（`tests/golden_set/`）之前**不允许出现** | **0** |

> ⚠️ `l3` **只说明参数合法**。它**不**说明「把 `cfg` 从 6.5 降到 5.0 就真能修掉过曝」。
> 参数**有效性**只能靠真实出图 + 人工/自动质检（P8-03/P8-04），本项目**至今一次都没跑过**。

---

## 2. 🔴 反向词必须按链路分栏（**不区分链路就是假知识**）

`assets/prompt_lib/negative.yaml:5-13` 的口径，直接决定本表怎么写：

| 链路 | 反向词 | 依据 |
|---|---|---|
| **CGF>1 · SDXL**（`t2i_v1` / `i2i_v1` / `inpaint_v1` / `upscale_v1`） | ✅ **有效**，是主力手段 | `negative.yaml:7` |
| **CFG=1 · klein 蒸馏**（`flux2_klein_t2i_v1`） | ❌ **无效**。数学上 CFG=1 时去噪结果 = 纯条件分支，**反向分支权重为零**；官方 Distilled 子图对应的是 `ConditioningZeroOut`（负向**整体置零**） | `negative.yaml:8-9`、`debug_log.md` K1-3/K1-6 |
| **CFG=1 · klein 的更进一步** | 🚫 **不是「填了不生效」，是「无处可填」**：该工作流的 Schema 里**根本没有暴露 `negative_prompt` 字段** | `negative.yaml:10-11`、`registry.yaml` 的 `flux2_klein_t2i_v1.param_schema` |
| **与 CFG 无关**（水印 / 尺寸 / 合规类） | ⚠️ 反向词**不足以保证**，必须后处理兜底 | `negative.yaml:16-17`、`neg-025`/`neg-026` 的 `note` |

**因此本库的硬规矩：**

1. `链路 = klein` 的条目，`negative_refs` **必须是 `—`**。给 klein 开反向词 = 假知识。
2. `链路 = sdxl` 的条目**必须**给出 `negative_refs`（反向词是该链路的主力手段；若确实不靠反向词，请改标 `all`）。
3. 反过来也成立：`recommended_params` 指向的工作流**到底有没有** `negative_prompt` 字段，必须与 `链路` 列自洽。
   校验器（判据 R2/R3）用的是**推导**而不是硬编码工作流 id —— 换工作流时不需要改脚本。

> `negative.yaml:12` 另有一条纪律：**不要给 klein 的 Schema 补 `negative_prompt` 字段** ——
> 补了会让用户误以为能生效。本库同样不提供这个字段。

---

## 3. hand · 手部

| id | 瑕疵（现象） | 成因 | 解法 | 链路 | recommended_params | negative_refs | verified_by | 依据 |
|---|---|---|---|---|---|---|---|---|
| D-01 | 多手多指，手指数量错误 | 手部在电商构图中占比小、训练分布里手部姿态稀疏，SDXL 的手部先验弱；1024² 下模特手部可能只占几十像素，模型没有足够像素去「数清」手指 | ① 提高分辨率，让手部拿到足够像素 ② 反向词压制 ③ 手部越出画幅的构图（V1 取此路线最稳） | sdxl | {"workflow":"t2i_v1","width":1280,"height":1280} | neg-028 | l3 | `negative.yaml` neg-028；`debug_log.md` §2.5 I1-3 同类「分辨率决定细节上限」 |
| D-02 | 多余肢体，关节反折 | 与 D-01 同源；构图裁切使模型的「人体完整性」先验被破坏 —— 看不到的部分由模型自由补全，于是长出第三只手 | ① 构图改为全身完整入画，避免在关节处裁切 ② 反向词 ③ 模特图在 V1 属「谨慎」场景（`scenes.yaml` rc-015） | sdxl | {"workflow":"t2i_v1","width":1024,"height":1280} | neg-028 | l3 | `negative.yaml` neg-028；`scenes.yaml` rc-015 |
| D-03 | 脸部畸变，五官错位 | 脸部像素占比不足 + CFG 过高放大条件分支的局部误差 | ① 降 `cfg` ② 反向词 ③ 面部特写构图给足像素 | sdxl | {"workflow":"t2i_v1","cfg":5.5} | neg-029 | l3 | `negative.yaml` neg-029 |
| D-04 | 人物意外入镜（白底主图/材质特写不需要人时） | 提示词里出现「手持/佩戴/使用」类语义词，模型据此补全了人；负向未限制 | 反向词压制；或改写正向提示词，用「置于台面」替代「手持」 | all | — | neg-039 | doc | `negative.yaml` neg-039（该场景 `scenes: [白底主图, 材质特写]`） |
| D-05 | 手部细节糊成一团（有手指轮廓但分不清） | 远构图下模特手部像素过少；CFG 高时该区域被当作噪声细节「糊平」 | ① 提高分辨率 ② 改特写构图。⚠️ **不要靠加 steps 解决** —— 像素不足是采样步数解决不了的 | all | {"workflow":"t2i_v1","width":1536,"height":1536} | — | l3 | 机制推演（**`none`/`doc` 之外的加项，无既有文档直接记载**）；`model_guide.md` §6 显存上限 15.8G/24G 是它的约束 |
| D-06 | img2img 重绘时手部结构被改（原图手是好的，重绘后坏了） | `denoise` 过高 → 潜空间被注入了足以重建结构的噪声，参考图的手部约束被冲掉 | 降 `denoise`，让参考图结构主导；这是 i2i 保真的核心旋钮 | sdxl | {"workflow":"i2i_v1","denoise":0.35} | neg-028 | l3 | `debug_log.md` §2.5 I1-3（「参数取舍要写下取舍理由」）；`registry.yaml` i2i_v1 `denoise` default 0.6 |

---

## 4. text · 文字

| id | 瑕疵（现象） | 成因 | 解法 | 链路 | recommended_params | negative_refs | verified_by | 依据 |
|---|---|---|---|---|---|---|---|---|
| D-07 | 画面里出现乱码文字（包装上的假字、招牌、标签） | 训练数据里「商品图」与「文字」强相关，模型倾向补全；中文/小字渲染错误率极高 | ① 反向词压制 ② 正向提示词里显式排除文字 ③ **文案一律后期叠加，不进模型** | sdxl | {"workflow":"t2i_v1","cfg":5.5} | neg-026 | l3 | `negative.yaml` neg-026（`note`：中文渲染错误率极高）；`negative.yaml` neg-026 `works_on: all` |
| D-08 | 水印 / logo 叠加 | 训练数据带水印残留；版权图的浅层记忆 | ⚠️ **反向词不足以保证**（`neg-025` 自带此声明）。必须由**后处理 / 人工核查**兜底；合规侧另见 `license_matrix.md` | all | — | neg-025 | doc | `negative.yaml` neg-025 及其 `note` |
| D-09 | 伪造品牌标识（生成了不存在的商标） | 提示词/品类词触发了品牌联想；模型对「品牌视觉符号」有强先验 | 反向词 + **交付前人工核查**。这是**法律红线**，不可只靠提示词 | all | — | neg-027 | doc | `negative.yaml` neg-027（`note` 指向 `license_matrix.md` §6） |
| D-10 | ⚠️ **klein 链路上权重语法 `(word:1.2)` 不但无效，还会污染提示词** | `KleinTokenizer.tokenize_with_weights()` **内部硬编码 `disable_weights=True`** → 走 `parsed_weights = [(text, 1.0)]` 分支，**原样的 `(word:1.2)` 字符串进入分词**，括号/冒号/数字全变成 token 喂给模型。**这不是「空转」，是「主动破坏」** | ① klein 链路上**严禁**使用权重语法（口径是「严禁用」，不是「用了也没关系」）② 需要强调就用**词序 / 同义强化**替代括号语法 ③ 若确需权重，见 `debug_log.md` G7 待验证 ③（**机制上并不保证有效**） | klein | {"workflow":"flux2_klein_t2i_v1","steps":4,"cfg":1.0} | — | doc | `debug_log.md` **G7**（源码级定论：`comfy/text_encoders/flux.py:153,169`）；`prompt_guide.md` §1.1.1 |
| D-11 | 中文文案渲染错误（缺笔画、错字、鬼画符） | 中文在扩散模型里的字符级先验远弱于英文；且 CFG=1 的 klein 链路反向词无效，连兜底都没有 | **文案一律后期叠加，不进模型**。正向提示词里不要要求模型写具体中文 | post | — | — | doc | `negative.yaml` neg-026 `note`（原文：文案一律后期叠加，不进模型） |
| D-12 | 高清修复阶段重新「生成」出文字/水印 | 放大环节仍带低 `denoise` 重绘，画质增益全部来自这次重绘 → 也带来了重新生成伪影的机会 | 降 `denoise`；配合反向词压制 | sdxl | {"workflow":"upscale_v1","denoise":0.3} | neg-025,neg-026 | l3 | `debug_log.md` §2.7 U1-1（「增益全部来自低 denoise 重绘」） |

---

## 5. distortion · 畸变

| id | 瑕疵（现象） | 成因 | 解法 | 链路 | recommended_params | negative_refs | verified_by | 依据 |
|---|---|---|---|---|---|---|---|---|
| D-13 | 结构变形，轮廓畸变 | CFG 过高 → 有条件分支被过度放大，局部误差被强化成结构错误 | 降 `cfg` + 反向词。**商品保真专项（FR-8.1）的必配词** | sdxl | {"workflow":"t2i_v1","cfg":5.5} | neg-011 | l3 | `negative.yaml` neg-011 及其 `note`（「商品保真专项的必配词」） |
| D-14 | 比例失调（该长的短、该短的長） | 提示词未给尺度锚点；模型缺少绝对尺寸先验 | 在提示词里给**相对尺度锚点**（与常见参照物同框）+ 反向词 | sdxl | {"workflow":"t2i_v1","cfg":6.0} | neg-012 | l3 | `negative.yaml` neg-012 |
| D-15 | 多出零件，重复结构 | 模型的「部件计数」能力弱；3C 与五金件形状相似度高，容易被复制 | ① 正向提示词显式声明部件数 ② 反向词 ③ 提高分辨率让部件间有区分像素 | sdxl | {"workflow":"t2i_v1","steps":30} | neg-013 | l3 | `negative.yaml` neg-013 及其 `note`（「3C 与五金件最高发」） |
| D-16 | 缺少部件，残缺 | 与 D-15 反向同源；构图裁切也会造成「看起来残缺」 | ① 正向提示词声明部件数 ② 反向词 ③ 检查构图是否裁掉了部件 | sdxl | {"workflow":"t2i_v1","steps":30} | neg-014 | l3 | `negative.yaml` neg-014 |
| D-17 | 透视错误，轴对齐混乱（同一物体多个灭点） | 模型未收到明确的相机/视角约束；多视角元素混入 | ① 正向提示词锁定视角（正视/俯视等）② 反向词 | sdxl | {"workflow":"t2i_v1","cfg":6.0} | neg-016 | l3 | `negative.yaml` neg-016；`composition.yaml` 的「视角」标签族 |
| D-18 | 对称失衡（该对称的物体歪了） | 未声明对称约束；模型把对称当作可自由发挥的属性 | 正向提示词显式声明对称；反向词 | sdxl | — | neg-017 | doc | `negative.yaml` neg-017 |
| D-19 | ⚠️ **klein 改了 `width`/`height` 后画质下降，但完全没有任何报错** | klein 的 `width`/`height` **同时**挂在两个节点上：`EmptyFlux2LatentImage`（画布）与 `Flux2Scheduler`（sigma 曲线）。**只改前者 → 曲线按错误分辨率生成** | Schema 已为 `width`/`height` 各声明**两个 target**（节点 `"6"` 与 `"7"`）。排查时先确认**不是漏了 target**，而不是怀疑参数没传进去 | klein | {"workflow":"flux2_klein_t2i_v1","width":1024,"height":1024} | — | doc | `debug_log.md` **K1-4**（原文：改参数「看起来没生效」时，先查是不是漏了 target）；`workflow_spec.md` §5.1 |
| D-20 | 边缘融化，粘连（产品与背景/相邻部件边界糊在一起） | CFG 高时边缘高频细节被过度放大后又抹平；低分辨率下边缘像素不足 | ① 降 `denoise`（重绘强度）② 反向词 ③ 提高分辨率 | sdxl | {"workflow":"t2i_v1","denoise":0.9} | neg-015 | l3 | `negative.yaml` neg-015 |

---

## 6. exposure · 过曝

| id | 瑕疵（现象） | 成因 | 解法 | 链路 | recommended_params | negative_refs | verified_by | 依据 |
|---|---|---|---|---|---|---|---|---|
| D-21 | 暗部死黑、高光溢出（同时出现） | 光比过大且提示词未给动态范围约束；CFG 高会同时放大高光与阴影的条件响应 | ① 降 `cfg` ② 反向词 ③ 正向提示词改「柔和光比」 | sdxl | {"workflow":"t2i_v1","cfg":5.5} | neg-007 | l3 | `negative.yaml` neg-007；`light.yaml` 的「光比」标签族 |
| D-22 | ⭐ **过曝白底，产品边缘被背景吞掉**（白底主图特有失败模式） | 白底场景下产品浅色边缘与背景亮度接近，高光溢出后边界消失；CFG 高会加剧 | ① 降 `cfg` ② 反向词（`neg-040` 专为该模式立）③ 正向提示词改「浅灰渐变底」给边缘留对比（见 `scenes.yaml` rc-002） | sdxl | {"workflow":"t2i_v1","cfg":5.0} | neg-040 | l3 | `negative.yaml` **neg-040** 及其 `note`（「白底主图特有失败模式」）；`scenes.yaml` rc-002 |
| D-23 | 全局光溢出，画面灰蒙蒙 | 泛光/雾化被当作「高级感」生成；缺少通透度约束 | ① 反向词 ② 降 `cfg` ③ 正向提示词加「通透 / 干净背景」 | sdxl | {"workflow":"t2i_v1","cfg":6.0} | neg-024 | l3 | `negative.yaml` neg-024 |
| D-24 | 色调偏黄，白平衡错误 | 提示词与光影词里**色温信号冲突**（同时出现暖光与冷色调描述），模型按权重折中成黄；缺少白平衡锚定 | ① 正向提示词里**只保留一套色温词** ② 反向词 ③ 降 `cfg` 减少冲突放大 | sdxl | {"workflow":"t2i_v1","cfg":6.0} | neg-008 | l3 | `negative.yaml` neg-008；`light.yaml` 的「色温」标签族（暖/冷/双色温三档互斥） |
| D-25 | 过饱和，色彩失真 | CFG 高 + 采样器/调度器组合把色彩推过头 | ① 降 `cfg` ② 换稳定的 `sampler_name` + `scheduler` 组合 ③ 反向词 | sdxl | {"workflow":"t2i_v1","cfg":6.0,"sampler_name":"dpmpp_2m","scheduler":"karras"} | neg-009 | l3 | `negative.yaml` neg-009 |
| D-26 | 色带断层，渐变生硬（背景出现阶梯状色带） | 步数不足 + 低比特量化让平滑渐变被切成台阶 | 提高 `steps`；必要时改用带噪采样器（`dpmpp_2m_sde` 等）缓解 | sdxl | {"workflow":"t2i_v1","steps":35} | neg-010 | l3 | `negative.yaml` neg-010（`severity: soft`）；`registry.yaml` t2i_v1 `steps` 边界 1-100 |
| D-27 | 高清修复后白底过曝、边缘更糊 | 放大环节的低 `denoise` 重绘在此处「提亮」了白底，把原本尚可的边缘对比抹掉 | 降 `denoise`；必要时降低放大倍率保留更多原图信息 | sdxl | {"workflow":"upscale_v1","denoise":0.25} | neg-040 | l3 | `debug_log.md` §2.7 U1-1/U1-2；`negative.yaml` neg-040 |

---

## 7. material · 材质失真

| id | 瑕疵（现象） | 成因 | 解法 | 链路 | recommended_params | negative_refs | verified_by | 依据 |
|---|---|---|---|---|---|---|---|---|
| D-28 | **材质错误：金属变塑料**（高光与反射质感丢失） | 提示词未给出**反射方向 / 高光形态**；模型倾向生成「安全」的漫反射表面 | ① 正向提示词精确描述材质与反射（如「拉丝不锈钢，细腻横向纹理」）② 反向词 ③ 降 `cfg` 避免高光被推成塑料感 | sdxl | {"workflow":"t2i_v1","cfg":6.0} | neg-019 | l3 | `negative.yaml` neg-019；`material.yaml` mat-001 及其 `note`（「金属反射方向必须写清，否则高光位置随机」） |
| D-29 | 纹理重复，明显平铺 | 模型把「纹理」理解为可平铺的图案；缺乏尺度变化的描述 | ① 提示词给纹理的**尺度与随机性**（如「细密不规则的…」）② 反向词 | sdxl | {"workflow":"t2i_v1","cfg":6.0} | neg-020 | l3 | `negative.yaml` neg-020 |
| D-30 | 反射不真实，镜像错误 | 镜面材质对背景极敏感，未限定环境时模型自由发挥反射内容 | ① 提示词同时限定**材质 + 环境** ② 反向词 ③ 白底场景优先选非镜面材质描述 | sdxl | {"workflow":"t2i_v1","cfg":6.0} | neg-021 | l3 | `negative.yaml` neg-021；`material.yaml` mat-002 `note`（「镜面金属对背景极敏感」） |
| D-31 | ⭐ **过度磨皮，塑料感**（皮具/纺织品类最高发的失败模式） | 模型的「美化」先验把材质微表面细节抹平；`neg-006` 的 `note` 明确点出该模式 | ① 正向提示词显式要求**微表面细节**（毛孔、织纹、绒面）② 反向词 ③ 降 `denoise` 保留参考图纹理 | sdxl | {"workflow":"t2i_v1","cfg":6.0} | neg-006 | l3 | `negative.yaml` **neg-006** 及其 `note`（「皮具/纺织品类最常见失败模式」） |
| D-32 | 投影方向不一致（同一画面里多个投影朝向不同） | 光照描述里存在多套光位；模型按局部语义各自生成阴影 | ① 提示词只保留**一套光位** ② 反向词 ③ 用 `light.yaml` 里成组的布光词而非拼接 | sdxl | {"workflow":"t2i_v1","cfg":6.0} | neg-022 | l3 | `negative.yaml` neg-022；`light.yaml`「布光」标签族 |
| D-33 | 多重光源冲突（高光落在互相矛盾的位置） | 光影词拼接了互斥的光位（顶光 + 底光 + 逆光同时出现） | ① 用 `light.yaml` 的**成组布光词**（如 lig-001 三点布光）② 反向词 | sdxl | {"workflow":"t2i_v1","cfg":6.0} | neg-023 | l3 | `negative.yaml` neg-023；`light.yaml` lig-001 |
| D-34 | 悬浮无支撑，漂浮感（缺少接触阴影） | 白底场景下模型省略了支撑面与接触阴影 | 正向提示词加「接触阴影」类词（与 `neg-018` 配对使用）+ 反向词 | sdxl | {"workflow":"t2i_v1","cfg":6.0} | neg-018 | l3 | `negative.yaml` neg-018 及其 `note`（「与接触阴影正向词配对使用」）；`light.yaml`「阴影·接触阴影」 |
| D-35 | ⚠️ **klein 画面退化、细节糊**（用户按 Base 版习惯给了 20+ 步 / cfg 7~8） | `flux-2-klein-4b.safetensors` 是**蒸馏版**（文件名**不含** `base`），官方模板对应 **4 步 / cfg=1**。用 Base 版参数跑蒸馏版会得到「能出图但质量可疑」的结果；且 `steps`/`cfg` 的 Schema 边界已被收窄 | 按蒸馏版参数跑：`steps≤8`、`cfg≤2.0`。**先确认自己是哪个变体再调参** | klein | {"workflow":"flux2_klein_t2i_v1","steps":4,"cfg":1.0} | — | doc | `model_guide.md` **§5.1**；`debug_log.md` **K1-3**（「先确认自己是哪个变体再调参」） |
| D-36 | 材质词写得太强，覆盖了产品的真实材质 | 材质词包的强度高于商品本身特征时，模型会按词包重塑表面 | 只在**确认品类后**使用材质词；必要时降权/删词。⚠️ 注意 D-10：klein 链路上**不能**用 `(word:1.2)` 降权 | post | — | — | doc | `material.yaml` 文件头（原文：「材质词写得太强会覆盖产品真实材质 —— 只在确认品类后使用」） |

---

## 8. drift · 结构漂移

| id | 瑕疵（现象） | 成因 | 解法 | 链路 | recommended_params | negative_refs | verified_by | 依据 |
|---|---|---|---|---|---|---|---|---|
| D-37 | 同一 SKU 的多张图**光影/风格漂移**（单看都好，放一起不像一套） | 同批次内换了光影词或换了 seed。`light.yaml` 文件头明确：「同一批次/同一 SKU 的多张图必须复用同一套光影词，否则风格会漂」 | ① 锁定 seed ② **复用同一套光影词**（把光影词固化进模板而不是每次手拼） | all | {"workflow":"t2i_v1","seed":20260916} | — | l3 | `light.yaml` 文件头纪律；`negative.yaml`「按需白名单」同源思路 |
| D-38 | img2img 把**不该动的**商品细节改了（logo 位置、缝线、按钮） | `denoise` 过高，参考图约束被噪声冲掉 | 降 `denoise`；电商硬要求是**不该动的地方一个像素都不能动** | sdxl | {"workflow":"i2i_v1","denoise":0.3} | neg-011 | l3 | `debug_log.md` §2.6 **IP-2**（原文：不该动的地方一个像素都不能动）；`registry.yaml` i2i_v1 `denoise` default 0.6 |
| D-39 | 高清修复后结构细节漂移（放大出来的不是原来的东西） | 潜空间插值**本身不引入任何新信息**，全部画质增益（与全部漂移风险）都来自那次低 `denoise` 重绘 | 降 `denoise`；降低放大倍率；⚠️ **本条的画质增益是否成立尚未实测**（`debug_log.md` §2.7 待验证 ②） | sdxl | {"workflow":"upscale_v1","denoise":0.3,"scale_by":1.5} | neg-011 | l3 | `debug_log.md` §2.7 U1-1 与「待验证 ②」（原文：本项不声称画质达标） |
| D-40 | 局部重绘后**非选区**被轻微劣化（不该变的地方变了） | VAE 编解码往返会劣化整张图，即使 `SetLatentNoiseMask` 已保证非选区不被采样修改 | 工作流已用 `ImageCompositeMasked` 把**原图非选区贴回**（`inpaint_v1` 链路）。⚠️ 该步骤的必要性来自 `debug_log.md` 记载，**未实测** | post | — | — | doc | `debug_log.md` §2.6 **IP-2**（「看起来多余但保证语义」） |
| D-41 | ⚠️ 局部重绘**重绘错了区域**（把选区外改了、选区没动） | `LoadImage` 的 MASK 输出**极性未实测**（是否 `1 - alpha`）。极性相反会去重绘**非**选区。当前按常见约定假设「透明区 = 待重绘区」 | **必须先实测极性再定稿**：① 读 `/root/ComfyUI/nodes.py` 的 `LoadImage` 实现 ② 用「透明区 = 待重绘区」的图跑一次看 `MaskToImage` 预览 ③ 若相反，在 `GrowMask` 前插 `InvertMask`（**不预先插** —— 假设正确时反而会做错）。同时用 `mask_expand` 微调选区外扩量 | post | {"workflow":"inpaint_v1","mask_expand":8} | — | none | `debug_log.md` §2.6「待验证 ⚠️」（**文档明确标注为未验证**，故为 `none` 而非 `doc`）；`registry.yaml` inpaint_v1 `mask_expand` 边界 -32~128 |
| D-42 | 多色同款逐张生成时**颜色串味**（生成了别的品类/别的颜色元素） | 逐张生成时提示词未隔离，相邻品类的词渗入；或 seed 变化导致语义漂移 | ① 固定 seed ② 逐张复用同一模板只改颜色词 ③ 反向词压制「非目标品类元素」 | sdxl | {"workflow":"t2i_v1","seed":20260916} | neg-038 | l3 | `negative.yaml` neg-038；`scenes.yaml` **rc-014**（多色同款 · 逐张生成（推荐）） |
| D-43 | 宽高比变更导致**主体占比与主图合规漂移**（主体变小、留白超标） | 构图词与画幅不匹配；平台主图对主体占比有硬要求（`composition.yaml` cmp-001 `note`：建议 65%-80%） | ① 构图词与 `width`/`height` **成对调整** ② 主体占比词与画幅一起进模板 | all | {"workflow":"t2i_v1","width":1024,"height":1024} | — | l3 | `composition.yaml` **cmp-001** 及其 `note`；`composition.yaml`「画幅」标签族（方/横/竖） |
| D-44 | 不同 seed 导致**同批次构图不统一** | seed 随机（`-1`）时每张图重新掷骰；「同一 SKU 一套图」需要构图稳定 | 评测/量产批次固定 `seed`；探索期再用 `-1`。⚠️ 固定 seed 会牺牲多样性，是**取舍**不是纯收益 | all | {"workflow":"t2i_v1","seed":20260916} | — | l3 | `registry.yaml` t2i_v1 `seed` default -1（`-1 = 随机`）；`verify_render_path.py` 的确定性验证要求显式 seed |

---

## 9. ⚠️ 与其它文档的口径冲突（本文件以哪一处为准）

| # | 冲突 | 本文件取的口径 | 依据 |
|---|---|---|---|
| 1 | **良品率的分母**：`PRD_v1.md:548` 写作「采纳数 / **生成数**」；`docs/prd/tracking_plan.md:154,169` 判定分母应用 **succeeded 图片数**，并把「生成数做分母」列为**错误做法** | **以 `tracking_plan.md` 为准**：`良品率 = image_adopted 数 / succeeded 图片数` | `tracking_plan.md` §5 指标定义表 + §5.1「口径陷阱」。`PRD_v1.md:548` 那处是**笔误** |
| 2 | `engine/validate.py:14,17` 曾写「L3 不在本模块」「L3 当前无法执行：仓库里没有任何 object_info 缓存文件」 | **以代码与 `debug_log.md` §0.1 为准**：`check_object_info` 就在 `engine/validate.py`，`deploy/schemas/object_info.v0.36.0.json` **已入库**，L3 需**显式传** `--object-info` | `engine/validate.py:306-323`（`_load_object_info` / `check_object_info`）；`debug_log.md:35`。该过期描述已在本轮修正 |
| 3 | `PRD_v1.md:583`（AC-F3）要求「**每条**工作流有 golden set ≥30 例」 | V1 **做不到**：仓库只有 **5** 条工作流有 JSON；`style_transfer_v1` 缺权重、无 JSON，**无法覆盖**。详见 `tests/golden_set/README.md` | `workflows/registry.yaml`（`planned: [style_transfer_v1]`）；`debug_log.md` §2.3 |
| 4 | 「缺陷知识库 ≥30 条」的**条数口径** | 本文件 **44 条**（六类全有）。但其中**带可机械校验参数的**才计入校验器的「通过」，`recommended_params` 为 `—` 的被**跳过、不计入通过** | `engine/tools/check_defect_kb.py` 的 R6 与 `render_report` 的跳过口径 |

---

## 10. 怎样校验本文件（**可执行**）

```bash
# 参数机械合法性（纯离线，不需要 GPU）
timeout 120 backend/.venv/bin/python -m engine.tools.check_defect_kb
```

退出码：`0` 全部通过 · `1` 有失败（会**具体到条目 id 与参数名**）· `2` 用法/文件错。

⚠️ **该命令的结论只有一句：参数合法。**
它**不**证明任何解法有效 —— 参数「有效」只能靠真实出图 + 人工/自动质检（P8-03/P8-04），
而**本仓库至今未对任何一条缺陷条目做过真实出图验证**（`verified_by=gpu` 应为 **0** 条）。

> 判废标准（哪条瑕疵出现即淘汰）**不在本文件**，在 `assets/prompt_lib/negative.yaml` 的
> `severity: hit` 上，由 **`docs/sop/quality_sampling_sop.md`（P8-04）** 负责执行。
> 本文件只回答「为什么会出现、该动哪个参数」。
