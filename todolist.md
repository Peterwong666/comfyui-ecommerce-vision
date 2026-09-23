# ComfyUI 商用级封装 · 执行计划（todolist）

> **项目一句话定义**：把 ComfyUI 从「一个能跑的开源节点工具」封装成「可商用、可量产、可观测、可迭代的 AIGC 视觉生产平台」，首发场景 **电商视觉 / 产品渲染**。
>
> **两条主线**
> - **主线 A（工程）**：ComfyUI 工作流内核 + 控制体系 + Web 平台封装（API/队列/存储/权限/前端）+ 质量与性能体系。
> - **主线 B（产品）**：完整走一遍产品经理全套工作 —— MRD、竞品、用户画像、PRD、流程图、原型、WBS/甘特、资源安排、V1/V2/V3 规划、测试、数据监测、复盘，产出可直接进作品集的文档资产。
>
> **关键前提（已确认）**
> - 交付形态：Web 平台（前端 + API + 异步队列），ComfyUI 作为底层推理引擎
> - V1 首发场景：电商视觉 / 产品渲染
> - 投入节奏：全职，每天 6h+
> - 仓库：github.com/Peterwong666，代码 MIT/Apache 完整开源，**模型权重许可证单独声明**（很多模型禁止商用，必须隔离）
> - 开发环境：autoDL 租卡（建议 4090 24G 起步）
>
> **V1 周期**：2026-09-14 → 2026-11-22（10 周）

---

## 0. 使用说明

| 约定 | 说明 |
|---|---|
| 任务编号 | `P{阶段}-{序号}`，如 `P3-05`。引用时用编号，不要用描述 |
| 状态 | `[ ]` 未开始 · `[~]` 进行中 · `[x]` 已完成 · `[!]` 阻塞 · `[-]` 主动放弃（须在进度表记原因） |
| **计数规则**（2026-09-23 补，使标记可机械复算） | 只有 **`[x]`** 计入完成数；`[~]` / `[ ]` / `[!]` **一律不计入**；**子项拆分按 1 项计**（`P8-03a`+`03b` = 1 项、`P8-08a`+`08b` = 1 项）；按此规则逐行求和 = **76 / 153**（核对过程见 `项目进度.md` §2 末注） |
| DoD | 每个任务必须有**可验收产出**。没有产出 = 没做完，不许打勾 |
| 更新频率 | 每天收工更新 `项目进度.md`；每周五做周记与下周计划 |
| 优先级 | `P0` 阻塞主线 · `P1` V1 必须有 · `P2` 有则更好 · `P3` 未来 |

**执行原则（防止走偏的四条铁律）**
1. **环境可复现优先于功能开发** —— 任何时刻能一键重建整个环境，否则商用无从谈起。
2. **工作流先跑通单张，再谈批量** —— 批量只是循环，质量不达标时批量只会放大垃圾。
3. **每个能力都要有量化验收** —— 「效果不错」不是验收标准，「golden set 一次通过率 ≥80%」才是。
4. **产品文档与代码同步产出** —— 文档不是最后补的，是每阶段的交付物之一。

---

## 1. 需求提取：岗位说明 → 能力项矩阵

### 1.1 显式要求拆解（JD 原文 → 本项目落地）

| # | JD 原文 | 能力域 | 本项目交付物 | 验收标准（量化） | 任务 |
|---|---|---|---|---|---|
| 1 | 文生图、图生图、高清修复、局部重绘、风格迁移、批量出图全流程，标准化量产输出 | 工作流 | **5 条**标准化工作流（**+ 1 条顺延 V2**）+ 参数模板库 | **5 条全部上线 + 1 条（风格迁移）顺延 V2**（口径见 `docs/adr/0008-v1-scope-reduction.md` 裁定 1），每条有 golden set ≥30 例，**一次通过率 ≥80%** | P3 |
| 2 | ControlNet、IP-Adapter、LoRA、SAM、DWPose、姿态/线稿/景深控制节点体系，画面稳定、高精度、风格统一 | 控制体系 | 控制能力矩阵 + 各控制手段推荐权重区间 | 同一参考图 10 次生成，主体结构一致性评分 ≥4/5；矩阵文档覆盖 ≥8 种控制手段 | P4 |
| 3 | 针对业务场景（产品渲染、电商视觉、IP形象、场景合成、AI视频）定制工作流，持续迭代参数/模型/节点 | 场景化 | 场景包（工作流 + 模板 + 示例 + SOP） | V1 电商场景包可交付；V2 补 IP/场景合成；V3 补视频 | P3 / V2 / V3 |
| 4 | 模型资源管理：底模/LoRA/ControlNet 筛选、测试、适配、版本归档，内部标准化模型库与提示词库 | 资产管理 | `model_registry` + 模型归档策略 + `prompt_library` | 任一模型可一键回滚到任意历史版本；模型带 hash/license/评测分；提示词库 ≥200 条带标签 | P5 |
| 5 | SOP、操作规范、参数模板，封装可复用批量自动化流程，降低人工调试成本 | 工程化 | 批量任务 API + 参数模板 + SOP 文档 | **非技术同学照 SOP 10 分钟内完成一次 100 张批量出图** | P6 / P11 |
| 6 | 跟进新技术/新模型/节点生态，解决显存占用高、渲染慢、批量报错、兼容性差 | 稳定性 | 显存策略 + 重试降级 + 兼容矩阵 + 监控 | 单卡 24G 连续 8h 混合压测**零崩溃**；P95 出图时长达标；失败任务自动重试成功率 ≥95% | P9 |
| 7 | 配合产品/设计/业务落地，提供技术方案、流程搭建、问题排查与交付 | 协同 | 需求单/验收单模板 + 交付流程 + 排错手册 | 需求从提出到上线有完整链路记录；排错手册覆盖 ≥30 个真实故障 | P1 / P11 |
| 8 | 精通节点式工作流搭建、调试、排错，扎实 SD 底层逻辑 | 技术基础 | 工作流规范 + 调试记录 | 能从零重建任一工作流，无复制粘贴黑洞 | P3 |
| 9 | ControlNet 权重调节、提示词工程、反向词优化，解决生成瑕疵，保证一致性与良品率 | 质量 | **缺陷知识库**（瑕疵类型 → 成因 → 解法 → 参数） | 缺陷知识库 ≥30 条，覆盖手部/文字/畸变/过曝/材质失真/结构漂移 | P8 |
| 10 | 批量自动化出图、工作流封装、流程优化实战 | 工程化 | 批量引擎 + 任务状态机 | 1000 张批量任务可在无人值守下完成，失败可续跑 | P6 |
| 11 | 逻辑清晰、复盘能力强，沉淀标准化流程文档 | 文档 | SOP + 周报 + 阶段复盘 | 每阶段一份复盘，含「做得好 / 不足 / 下一步」 | P11 / P12 |

### 1.2 「商用级」隐含要求（JD 没写，但决定成败）

这是本项目与「能跑通 ComfyUI」的真正分界线。**每一项都要有对应交付物**：

| # | 商用级要求 | 为什么 | 本项目落地 | 任务 |
|---|---|---|---|---|
| C1 | **可复现性** | 客户追单要能出一样的图 | 工作流版本 + 模型 hash + seed + 全参数入元数据（PNGInfo），环境版本锁 | P2-06 / P3-11 |
| C2 | **一致性** | 一套商品图风格不能飘 | 同 seed/参考图/IP-Adapter 权重锁定 + 风格模板 | P4-09 |
| C3 | **良品率可度量** | 不能靠感觉说「效果好」 | golden set + 评分标准 + 自动质检 + 良品率看板 | P8 |
| C4 | **服务不崩** | 商用中断 = 直接赔钱 | 任务隔离、OOM 降级、超时重试、优雅重启、健康检查 | P9 |
| C5 | **成本可核算** | 不赚钱的产能没有意义 | GPU 秒 / 单张成本模型 + 成本看板 + 配额 | P9-09 |
| C6 | **可观测** | 出问题要能定位，而不是重启试试 | 全链路 trace（task_id → 工作流 → 参数 → 耗时 → 显存）+ 日志 + 告警 | P10 |
| C7 | **可回滚** | 改坏了要能退回去 | 工作流/模型/参数模板全部版本化 + 灰度切换 | P3-09 / P5-07 |
| C8 | **权限与隔离** | 多客户数据不能串 | 用户体系、配额、素材与产物隔离、审计日志 | P6-07 / P6-11 |
| C9 | **合规** | 商用的红线 | 模型商用许可矩阵、NSFW 过滤、人脸/品牌授权提示、数据留存策略 | P5-10 / P11-10 |
| C10 | **低门槛** | 用的人不是工程师 | 工作流驱动的动态表单 + 参数模板 + SOP | P7-02 / P11-03 |
| C11 | **可扩展** | 场景一定会变多 | 工作流注册表 + 插件白名单 + 模型热注册 | P3-09 / P5-06 |
| C12 | **吞吐可控** | 排队比崩掉好 | 优先级队列 + 并发上限 + 队列可视化 | P6-02 / P9-03 |

### 1.3 面向未来的扩展要求（预判需求，架构上预留位置）

按「被提出来的概率 × 实现成本」排序，V1 只做**架构预留**，不实现：

| # | 扩展方向 | 触发场景 | 预留动作（V1 要做） | 计划版本 |
|---|---|---|---|---|
| E1 | **AI 视频**（图生视频 / 视频重绘 / 数字人口播） | 客户要短视频素材 | 任务类型设计为可扩展枚举，产物支持非图片格式 | V3 |
| E2 | **新架构模型**（Flux / SD3 / DiT 系） | 画质要求升级 | 模型注册抽象出「模型类型/加载器」，不硬编码 SDXL | V2 |
| E3 | **量化与加速**（FP8 / GGUF / Nunchaku / TensorRT） | 成本压力 | 精度策略做成配置项 | V2 |
| E4 | **LLM 提示词助手**（自然语言 → 结构化提示词 + 参数推荐） | 降低使用门槛 | 提示词库结构化存储，预留 LLM 接口位 | V2 |
| E5 | **Agent 自评闭环**（自动质检 → 自动改参 → 自动重跑） | 人工筛选太累 | 质检结果结构化、重跑入口 API 化 | V3 |
| E6 | **微调闭环**（用户数据 → LoRA 训练 → 上线 → A/B） | 客户要自有品牌风格 | 产物打标签、数据集导出能力 | V2 |
| E7 | **开放平台**（API Key / Webhook / SDK / 计费） | 客户要集成到自己系统 | API 从第一天就按对外标准设计（版本化、鉴权、限流） | V2 |
| E8 | **多租户 / 团队协作 / 审批流** | 卖给团队而非个人 | 数据模型预留 `tenant_id`、`role` | V2 |
| E9 | **私有化部署**（企业内网一键部署） | 大客户数据不出内网 | docker-compose + 镜像化，配置全外置，不写死路径 | V2 |
| E10 | **多机多卡调度 / 弹性扩缩容** | 单卡扛不住 | 任务队列天然支持多 worker，ComfyUI 实例可水平扩展 | V3 |
| E11 | **合规溯源**（C2PA 水印 / 生成标识 / 审计） | 监管要求 | 元数据写入与审计日志从 V1 就做扎实 | V2 |
| E12 | **国际化 / 移动端 / 插件市场** | 出海、C 端 | 文案抽离 i18n、API 与前端彻底分离 | V3 |

---

## 2. 产品目标与范围

### 2.1 V1 一句话定义

> 面向电商商家的 **AIGC 商品视觉量产平台**：上传商品图/描述，选择场景模板，批量产出可直接上架的主图、场景图、细节图，全程可追溯、可复现、成本可核算。

### 2.2 用户画像（P1 产出，此处为初稿基线）

| Persona | 角色 | 核心诉求 | 痛点 | 成功标准 | V1 是否服务 |
|---|---|---|---|---|---|
| **小店主 / 电商运营** | 1 人公司或 3-5 人团队 | 快速出能用的主图，省钱 | 请摄影贵、不会修图、Midjourney 出图不可控 | 10 分钟出 20 张可用主图 | ✅ 主目标 |
| **电商设计师** | 有设计基础的执行者 | 提高效率，不想重复劳动 | ComfyUI 参数多、调参耗时、批量易崩 | 一个模板复用 100 次 | ✅ 主目标 |
| **品牌方市场** | 有品牌规范要求 | 风格统一、合规 | AI 图风格漂移、品牌色对不上 | 一套图风格偏差肉眼不可辨 | ⚠️ V2（一致性体系成熟后） |
| **独立开发者 / AI 爱好者** | 技术用户 | 二次集成、看实现 | 开源项目文档差、跑不起来 | 30 分钟本地跑通 | ✅ GitHub 受众 |

### 2.3 版本范围（Scope）

| 能力 | V1（W1-W10） | V2（W11-W18） | V3（W19-W28） |
|---|---|---|---|
| 文生图 / 图生图 | ✅ | 增强（新底模） | — |
| 高清修复 / 局部重绘 | ✅ | 增强 | — |
| 风格迁移 / 批量出图 | ⚠️ **拆分**：批量出图 ✅ ／ 风格迁移 ❌ **顺延 V2（见 ADR-008）** | 增强 | — |
| 控制体系（CN/IP-Adapter/LoRA/SAM/DWPose） | ✅ 核心 6 种 | 补齐全矩阵 | — |
| Web 平台（用户/任务/素材/画廊） | ✅ | 团队协作 | — |
| 质量评测与良品率 | ✅ 基础 | 自动质检 + 自评闭环 | Agent 自动重跑 |
| 数据监测与看板 | ✅ 基础 | 漏斗 + A/B 平台化 | 商业化指标 |
| IP 形象一致性 | ❌ | ✅ | 增强 |
| 场景合成 | ❌ | ✅ | 增强 |
| AI 视频 | ❌ | 预研 | ✅ |
| LLM 提示词助手 | ❌ | ✅ | 增强 |
| 开放 API / 计费 | 内部 API | ✅ 对外 | 套餐体系 |
| LoRA 微调闭环 | ❌ | ✅ | 增强 |
| 多机多卡 / 私有化 | ❌ | 私有化 | ✅ 集群 |

### 2.4 V1 明确不做（写下来，防止范围蔓延）

- ❌ 视频生成（V3）
- ❌ 模型训练 / 微调（V2）
- ❌ 对外计费与支付（V2）
- ❌ 移动端 App / 小程序
- ❌ 多语言（V1 只做中文，文案抽离即可）
- ❌ 自研模型

---

## 3. 技术架构（决定任务怎么拆）

```
┌─────────────────────────────────────────────────────────────┐
│  Web 前端（React/Vue3）                                       │
│  工作台 · 任务中心 · 画廊 · 批量向导 · 模板库 · 管理后台        │
└───────────────────────────┬─────────────────────────────────┘
                            │ REST + WebSocket
┌───────────────────────────▼─────────────────────────────────┐
│  API 层（FastAPI）                                            │
│  鉴权 · 配额限流 · 参数校验 · 任务编排 · 回调/Webhook           │
└───────────────────────────┬─────────────────────────────────┘
                            │
┌───────────────────────────▼─────────────────────────────────┐
│  任务队列（Celery + Redis）优先级队列 · 重试 · 降级 · 超时       │
└───────────────────────────┬─────────────────────────────────┘
                            │
┌───────────────────────────▼─────────────────────────────────┐
│  ComfyUI 适配层（Driver）                                     │
│  提交/查询/取消/中断 · 图片上传 · WebSocket 进度 · 显存守护      │
└───────────────────────────┬─────────────────────────────────┘
                            │
┌───────────────────────────▼─────────────────────────────────┐
│  ComfyUI 引擎 + 工作流注册表 + 模型库 + 自定义节点               │
└─────────────────────────────────────────────────────────────┘

  存储：PostgreSQL(元数据) · MinIO/S3(素材与产物) · Redis(队列与缓存)
  观测：Prometheus + Grafana + Loki（系统）· 埋点（产品）
```

**目录结构约定**

```
comfyui-platform/
├── docs/
│   ├── mrd/          # MRD、竞品分析、市场分析
│   ├── prd/          # PRD、功能清单、变更记录
│   ├── persona/      # 用户画像、用户旅程
│   ├── flow/         # 流程图（用户流/系统流/状态机）
│   ├── prototype/    # 原型图与交互说明
│   ├── sop/          # 工作流 SOP、参数模板说明
│   ├── adr/          # 架构决策记录
│   └── review/       # 周报与阶段复盘
├── engine/           # ComfyUI 适配层、工作流注册表、模型注册
├── server/           # FastAPI 服务、任务队列、鉴权、存储
├── web/              # 前端
├── workflows/        # 工作流 JSON + 参数 Schema（版本化）
├── assets/
│   ├── models/       # 模型（外挂，不入库，只存 registry）
│   └── prompt_lib/   # 提示词库
├── deploy/           # docker-compose、Dockerfile、部署文档
└── tests/            # 功能/性能/质量评测
```

---

## 4. 分阶段任务清单

### P0 · 项目启动与规范（Day 1-2，0.5 周）

- [x] **P0-01** 建立仓库结构、分支策略（main/dev/feat/*）、README 骨架 → 目录骨架 + `main`/`dev` 分支 + `README.md`
- [x] **P0-02** 建立 docs 目录树 → `docs/{mrd,prd,persona,flow,prototype,sop,adr,review}`
- [x] **P0-03** 建立任务看板 → 本地看板（`todolist.md` + `项目进度.md`）**已完成**；⚠️ **GitHub Projects 仍待远端仓库创建后补**（P0 阶段结果按「✅ 达成」计入）
- [x] **P0-04** 约定提交规范（Conventional Commits）、PR/Issue 模板 → `CONTRIBUTING.md` + `.github/PULL_REQUEST_TEMPLATE.md` + Issue 模板 ×2
- [x] **P0-05** 确定许可证策略 + 模型商用许可矩阵初稿 → `LICENSE`（MIT）+ `docs/sop/license_matrix.md`
- [x] **P0-06** 建立 ADR 习惯 → `docs/adr/` 索引 + ADR-001~004（交付形态、开源许可、周期节奏、技术栈）
- [x] **P0-07** 建立每日收工记录习惯 → `项目进度.md` 每日记录表 + 更新规范
- [x] **P0-08**（补充）环境变量与密钥管理基线 → `.env.example`，`.gitignore` 拦截 `.env` 与权重文件

**DoD**：仓库可克隆，任何人按 README 能看懂项目结构与下一步。
**阶段结果**：✅ 达成（2026-09-15）。仅 P0-03 的远端看板待建仓库后补。

---

### P1 · 产品定义：MRD / PRD / 画像 / 流程 / 原型（W1，与 P2 并行）

**主线 B 的核心阶段。这一阶段的产出直接进作品集，质量优先级等同于代码。**

#### 1A. 市场与需求（MRD）

- [x] **P1-01** 行业与市场分析（现状·规模·驱动因素·趋势）→ `docs/mrd/MRD_v1.md` §2
- [x] **P1-02** 竞品分析：8 家按「一致性·可控性·批量·成本·门槛」五维打分 → `docs/mrd/competitive_analysis.md`
- [x] **P1-03** 商业模式与定价假设（按张 / 订阅 / 私有化）→ `docs/mrd/MRD_v1.md` §6
- [x] **P1-04** **MRD 成稿**（背景·市场·用户·竞品·机会点·商业价值·成功指标）→ `docs/mrd/MRD_v1.md`

#### 1B. 用户与需求

- [x] **P1-05** 用户画像 ×4（运营 / 设计师 / 品牌方 / 开发者）→ `docs/persona/persona.md` §2
- [x] **P1-06** 用户旅程地图（9 阶段触点与情绪曲线）→ `docs/persona/persona.md` §3
- [x] **P1-07** 需求池 + RICE 打分排序（45 条 + 9 条 Won't）→ `docs/prd/requirement_pool.md`
- [ ] **P1-08** 用户访谈 ≥5 位真实电商从业者（不足则二手资料补足），验证痛点 → 访谈纪要

#### 1C. 产品需求（PRD）

- [x] **P1-09** 功能清单 + MoSCoW 分级（33 Must / 8 Should / 3 Could）→ `docs/prd/PRD_v1.md` §3
- [x] **P1-10** **非功能需求**：性能 / 并发 / 可用性 / 安全 / 兼容 / 可维护 / 成本 → `docs/prd/PRD_v1.md` §4（NFR-1~7）
- [x] **P1-11** 任务状态机设计（8 状态 + 14 迁移 + 批量父子聚合）→ `docs/prd/PRD_v1.md` §5
- [x] **P1-12** 异常与边界：14 类异常 + 11 项边界值 → `docs/prd/PRD_v1.md` §6
- [x] **P1-13** **PRD v1.0 成稿 + 自评审**（44 FR · 7 NFR · 19 AC · 12 项自评审）→ `docs/prd/PRD_v1.md`

#### 1D. 流程与原型

- [x] **P1-14** 用户流程图（2 条主路径 + 11 类异常分支）→ `docs/flow/user_flow.md`
- [x] **P1-15** 系统流程图 / 数据流图（拓扑 + 时序 + 批量数据流 + 状态恢复）→ `docs/flow/system_flow.md`
- [x] **P1-16** 线框图（7 个核心页面低保真）→ `docs/prototype/wireframes.md`
- [ ] **P1-17** 高保真原型 + 交互说明（可点，至少覆盖主流程）→ `docs/prototype/`
- [x] **P1-18** **项目管理图**：WBS（12 分支/146 任务）+ 甘特图 + 10 里程碑 + 关键路径 → `docs/prd/gantt.md`
- [x] **P1-19** 资源安排与预算表（人力/GPU/存储/软件 + 三种成本情景）→ `docs/prd/resource.md`
- [x] **P1-20** 埋点方案设计（12 事件 + 漏斗 + 9 指标 + 表结构）→ `docs/prd/tracking_plan.md`

**DoD**：MRD、PRD、画像、流程图、原型、甘特、埋点方案 7 份文档全部成稿并互相自洽（PRD 的功能能在原型里找到，原型的功能能在 PRD 里溯源）。

---

### P2 · 基础设施与环境（W1-W2）

- [x] **P2-01** autoDL 选型与开卡 → 已开 RTX 4090 24G / 128 核 / 1TB RAM（详见 `项目进展.md` §2.1）
- [x] **P2-02** 数据与模型存储策略 → models/output/input 迁至数据盘 50G + 软链（`deploy/autodl/02_migrate_models.sh`）
- [ ] **P2-03** 基础镜像 Dockerfile（**已决定暂缓**：autoDL 实例自带 CUDA 12.4 + torch 2.5.1，先用现有环境；镜像化推迟到 P11-02 部署文档时统一做）
- [x] **P2-04** ComfyUI 部署，models 目录外挂 → ✅ 已启动并验证出图（`deploy/autodl/03_start_comfyui.sh` + `04_api_smoke_test.py`）。<br>　引擎已于 2026-09-16 升级 **v0.3.75 → v0.36.0 + torch 2.13.0+cu130**（为支持 FLUX.2 klein）。生产配置固定加 **`--highvram`**（v0.36 默认开启的 dynamic VRAM 会使 SDXL 慢 1.58x，见 `项目进展.md` #17/#18）。<br>　实测稳态（N=12 warmup=1）：SDXL 1024²/30 步 **median 5.00s / P95 5.48s / 地板 4.59s**；FLUX.2 klein 蒸馏版 4 步 **median 1.83s / P95 2.67s / 地板 1.52s**。<br>　⚠️ 原记录的「12.0s」是冷启动值，已作废（见 `项目进展.md` #15）
- [x] **P2-05** ComfyUI-Manager + 自定义节点白名单 → ✅ `deploy/comfyui/node_whitelist.yaml`。32 个节点逐一判定（**keep 11 / disable 11 / review 10**），每条带证据引用（审计日志行号）。<br>　本轮的三个关键发现：① ⚠️ **`ComfyUI-Upscaler-Tensorrt` 是 CC BY-NC-SA（非商用）且已装在环境里** → 唯一漏掉的合规红线，已判 disable ② **`smZNodes` 真因是 `sageattention` ABI 失配**（非 diffusers），待卸载复测（见 `项目进展.md` #23）③ 新增**可注入性判定轴**：能进 `object_info` 的入参才可被自动化驱动 —— P4 四个核心包全 0%，rgthree 50%（设计取向）。<br>　另：`ComfyUI-Manager` 不注册任何节点类 → 对节点图零影响 → V1 上线后可禁用
- [x] **P2-06** 版本锁定 → `deploy/versions.lock`（426 行，2026-09-16 重做）。含 `[host]/[gpu]/[python]/[comfyui]` 基础段 + **`[launch]`（启动参数 + 从日志解析出的生效运行时开关，如 `Set vram state to: HIGH_VRAM`）** + **`[models]`（6 个模型文件 size + sha256，M2「销毁重建仍可出同一张图」的锚点）** + `[custom_nodes]`（32 个节点 commit）+ `[pip]` freeze（346 行）
- [x] **P2-07** 环境复现脚本 + 重建验证 → ✅ `scripts/reproduce_env.sh`：backend venv 重建、前端 pnpm install、SQLite 开发库初始化、engine 自校验、前后端 lint/test/build 全跑一遍。⚠️ **只覆盖 CPU 侧**；ComfyUI / 模型 / GPU 不在脚本内（依赖 autoDL 自带环境）。本地未完整跑通重建（前端 vitest 受 CPU 争用抖动），但脚本命令与 CI 逐条等价。
- [x] **P2-08** 后端骨架 FastAPI → ✅ 已落地并验证（`backend/`；补交时为 31 测试，至 2026-09-16 收尾已达 **200 passed** / ruff 全绿）。<br>　按 ADR-004：FastAPI + Pydantic v2 + SQLAlchemy 2 + Alembic + Celery/Redis；**补交时 18 条路由**（auth / tasks / batches / workflows / templates / models / health），`/health` 与 `/health/ready` 分离。启动实测通过（结构化 JSON 日志生效）。<br>　⚠️ 上述「18 条」是**当时的**路由数；经 P6-09 / P6-10 新增 assets 系列与模板写接口后，**当前实为 28 个路由**（登记为契约 §2.3 的 **26 行**，其中有合并行）。<br>　⚠️ 补记：该骨架为 09-15 所建但当时**未提交、未入文档**，2026-09-16 补交并修正 6 个 ruff 问题（含 2 处非纯风格隐患）
- [x] **P2-09** PostgreSQL + Redis + MinIO 的 docker-compose → ✅ `docker-compose.yml`（postgres:16 / redis:7 / minio:2024-09），默认端口 5432/6379/9000/9001，与 `.env.example` 默认连接串一致。配套 `docs/deploy.md` §2 一键起中间件。⚠️ 本机无 docker daemon，**compose 未实测启动**；文件语法经 `docker compose config` 等静态校验（在无可执行 docker 时会被拒绝）。真实 PG/Redis/MinIO 行为仍待验证。
- [x] **P2-10** 前端骨架 → ✅ `web/`（**从 `.gitkeep` 到可运行**）。React 18 + TS + Vite 5 + antd 5 + TanStack Query + zustand，`corepack pnpm@12.4.2`。<br>　落地内容：① 契约层（`api/{enums,types,errors,http,client,hooks,keys}.ts`，含**三种错误信封**、402 按状态码判断、401 清会话、`/assets` 的 `{total,items}` 不对称）② 布局骨架（顶栏 56 + 侧栏 200 + 主区 1280，线框图 §0.1）③ 登录页 + 工作台页（打通全链路）+ 5 个占位页 ④ ESLint/Prettier/TS 严格模式。<br>　⚠️ **本轮顺带解掉两个后端硬阻塞**（不修则"前后端调通"无法成立）：**B1** 全仓库**没有任何代码把 `registry.yaml` 灌进 `workflows` 表**（`engine/registry.py:6` 明说"不做写库（那是 A 流的事）"，而 A 流从未实现）→ 新增 `backend/app/services/registry_loader.py` + `python -m app.cli.seed_workflows`；**B2** app 默认连 PG 而本机无 PG、SQLite 编译适配只存在于 `conftest.py`（测试专用）→ 抽出**唯一一份** `backend/app/db/sqlite_compat.py` + `python -m app.cli.init_db`。<br>　✅ **已实测**：后端 `256 passed`（基线 215 + 新增 41）、ruff 全绿；前端 `72 passed`（6 个文件）、typecheck/build 通过、eslint 0 error；`GET /api/v1/workflows` 由**恒为空**变为返回 2 条 active；CORS 预检 200 且 `allow-origin: http://localhost:5173`。<br>　⚠️ **未验证**（详见 `web/README.md` §6）：无 Redis 时任务停在 `queued`（出图/重试/取消往返**待 Redis+ComfyUI+GPU**）；图片上传**待 MinIO**；`str`/`bool`/`image_list` **待真实数据**（注册表 0 处）；浏览器人工走查**待人工验证**。
- [x] **P2-11** CI 基础 → ✅ **已完成**（2026-09-16 夜，commit `ba53354`）。产出 **`.github/workflows/ci.yml`** + **`backend/tests/test_repo_hygiene.py`**（仓库卫生门禁：被追踪的 `.env` / 权重 / 私钥、文本里形似真密钥的字符串、`.gitignore` 关键规则被删或失去根锚定、>5 MiB 误提交文件）。连同既有的两道**防漂移门禁** —— `backend/tests/test_web_enum_parity.py`（前端 `enums.ts` 与后端枚举**双向**比对，含 terminal/cancellable/retryable 派生集合；已做**变异测试**确认有鉴别力）与 `test_web_form_fixtures.py`（前端夹具与注册表逐字节相等）—— 以及 `test_env_example.py` 的 `.env.example` ↔ `Settings` 双向一致性，四道门禁全部是**纯 pytest**，不需要 Node 环境。<br>　⚠️ **如实注明：流水线本身从未运行过** —— `git remote -v` 为空（仓库无远端），`ci.yml` 顶部亦自述「Actions 侧未验证」。故「命令与 CI 逐条等价」成立，但「**CI 已通过**」**不成立**
- [x] **P2-12** 配置与密钥管理 → ✅ **已完成**（2026-09-16 夜核实）。原记载的实质缺口 —— 「`.env.example` 的 30 个变量**全部对不上** `app/core/config.py`（两套命名体系 + pydantic-settings `extra="ignore"` 静默忽略）」—— **已不存在**：`.env.example` 已重写，现为 **68 个变量 ↔ `Settings` 的 68 个字段一一对应**，并由**双向漂移门禁**守住：<br>　`cd backend && .venv/bin/python -m pytest tests/test_env_example.py -q` → **5 passed in 2.11s**（用例：`test_example_has_no_unknown_variable` 防"示例里有假变量"、`test_every_setting_is_documented` 防"加了字段却没登记"、`test_env_example_covers_all_fields` 双向相等、`test_example_is_not_a_copy_of_runtime_env`（`JWT_SECRET` 必须是 `CHANGE_ME_IN_ENV`）、`test_secrets_are_placeholder_or_empty`）。<br>　本轮 P6-10 新增的 4 个配置项（`ASSET_TRASH_RETENTION_DAYS` / `ASSET_OUTPUT_RETENTION_DAYS` / `MAX_PACK_ASSETS` / `CLEANUP_BATCH_LIMIT`）即由这道门禁自动纳入 —— **门禁确实在起作用，不是一次性动作**。<br>　⚠️ **仍未做的部分**：R12 风险里写的「**pre-commit** 密钥泄露检查」这道门禁**不存在**（仓库无 `.pre-commit-config.yaml`），当前只有 pytest 级的哨兵检查。应另立任务，或并入 **P11-11 脱敏检查**。
- [x] **P2-13** 数据库迁移方案与初始 schema → ✅ 初始迁移 `alembic/versions/20260916_1656_f9192eeddac1_initial_schema_11_tables.py`（11 张表 / 12 条外键 / 58 条索引 / CHECK 约束）+ `docs/prd/er_diagram.md`（Mermaid ER 图 + 逐表职责 + 8 条非显然设计决策）。<br>　⚠️ autogenerate 产物**不能直接用**，修了三处：外键内联在 `create_table` 里（模型有环 `batches→templates→assets→tasks→batches`，PG 上必然失败）→ 改为建表后再统一 `ADD CONSTRAINT`；`JSONB(astext_type=Text())` 的 `Text` 未定义；SQLite 方言残留的 `server_default` 会让以后每次 autogenerate 报假差异。<br>　验证：9 条迁移测试（用「记录器冒充 `alembic.op`」逐项比对表/外键/索引），并**做了变异测试**确认测试有鉴别力（删外键、改错表名均被抓到）。<br>　❌ 未做：真实 PostgreSQL 上 apply 一次（本机无 PG 二进制也无 docker daemon）→ 归入 P2-09

**DoD**：环境可一键重建；`versions.lock` 存在；后端/前端/ComfyUI 三件套都能起来并互相调通一次。
**阶段结果**：🔵 进行中（**12/13**，2026-09-19 复核）。已完成 P2-01/02/04/05/06/07/08/09/10/11/12/13。
P2-03 有意推迟（先用自带环境跑主线）；P2 只剩 Dockerfile 暂缓项。
✅ **P2 的 DoD 中「三件套互相调通一次」本轮已兑现**：前端起得来（5173）+ 后端起得来（SQLite）+ CORS 预检通过 + `GET /api/v1/workflows` 返回真实数据。⚠️ 但 ComfyUI 与出图链路仍**待 GPU/Redis**，所以是"调通"而非"跑通"。
⚠️ **P2-11 已建流水线但从未运行**：`.github/workflows/ci.yml` 存在，仓库却无远端（`git remote -v` 为空），所以"CI 绿"目前**没有任何一次真实执行**。
✅ **P2-12 的实质缺口已消除**（见上）：`.env.example` ↔ `Settings` 现由 `tests/test_env_example.py` 双向守住，68 ↔ 68。⚠️ 但「pre-commit 密钥泄露检查」这道门禁仍**不存在**（并入 P11-11 或另立任务）。
**待办提示**：⚠️ `backend/.venv` 中 **mypy 未安装**（`pyproject.toml` 已声明为 dev 依赖），类型检查这条防线目前是空的，需补装后才能生效。

---

### P3 · 工作流内核（W2-W4，主线）

| ID | 任务 | 产出 | Pri |
|---|---|---|---|
| P3-01 | **统一工作流规范**：输入/输出节点命名约定、Bypass 开关、seed 与 metadata 注入、注释分组 | `[x]` **已完成**（P3 阶段结果明列）；`docs/sop/workflow_spec.md` | P0 |
| P3-02 | 工作流 API 格式转换与参数占位机制（`{{width}}` 之类） | `[x]` **已完成**（P3 阶段结果明列；`engine/transforms.py`）；转换工具 | P0 |
| P3-03 | WF-T2I 文生图（底模 + 采样器 + 高清分支） | `[~]` **部分完成 · 口径待裁定**：`t2i_v1.json` 存在且 **L4 已通过**，但 P3 阶段结果**未把 03 计入**（「高清分支」已独立为 P3-05）⇒ 属 `项目进度.md` §2 末注的 ± 项；`workflows/t2i_v1.json` | P0 |
| P3-04 | WF-I2I 图生图（重绘幅度策略） | ✅ `workflows/i2i_v1.json`（9 节点；核心参数 `denoise`；L1/L2/L3 PASS）。<br>　**渲染路径 L4 于 2026-09-17 在真实 RTX 4090 上跑通**（工具 `engine/tools/verify_render_path.py`，显式 `seed=20260916`）：**通过 11 · 失败 0 · 跳过 0**；seed 注入落点 `['21.seed']` 正确；**同一显式 seed 两次出图 IDAT 逐字节一致**；产物 1024×1024；`wf_meta` 元数据闭环成立。<br>　⚠️ **登记牌仍 `status: disabled` + `verification: pending_gpu`** —— 渲染路径已验证，但**缺本工作流口径的 baseline**（1024² / 25 步 / denoise=0.6），放行前置未满足（见 `workflows/registry.yaml` 的 `verification_note`）。⚠️ 非等比参考图会拉伸（`ImageScale.crop=disabled`）未覆盖；画质不属 L4 的证明范围 | P0 |
| P3-05 | WF-UPSCALE 高清修复 | ✅ `workflows/upscale_v1.json`（免放大器模型路线：`LatentUpscaleBy` + 低 denoise 重绘 + `VAEDecodeTiled`；L1/L2/L3 PASS）。<br>　**渲染路径 L4 于 2026-09-17 跑通**：**通过 11 · 失败 0 · 跳过 0**；产物 **1536×1536**（1024² × `scale_by=1.5`，尺寸符合预期）；同 seed 两次出图逐字节一致；元数据闭环成立。<br>　⚠️ **登记牌仍 `disabled` + `pending_gpu`**（缺本工作流口径 baseline：1024²→1536² / 20 步 / denoise=0.4 / `tile_size=512`）。⚠️ **「放大画质增益是否成立」未被证明**（潜空间插值不引入新信息，增益全来自低 denoise 重绘，需人看前后对比图，属 golden set / P8）。⚠️ 两段式（`UpscaleModelLoader`）待权重 | P0 |
| P3-06 | WF-INPAINT 局部重绘 | ✅ `workflows/inpaint_v1.json`（免专用模型路线：`SetLatentNoiseMask` + `ImageCompositeMasked` 贴回；L1/L2/L3 PASS）。<br>　**渲染路径 L4 于 2026-09-17 跑通（通过 11 · 失败 0 · 跳过 0）——但本次 L4 实际上是一次 no-op**：本次参考图是 **RGB 无 alpha**，而 ComfyUI v0.36.0 的 `LoadImage` **只在输入含 alpha 时**才由 alpha 导出 MASK，否则返回**全零 mask** ⇒ 非选区贴回覆盖整张图 ⇒ **产出与输入逐像素相同**。故「11/11」**只证明渲染路径能出图，不能说「局部重绘可用」**。<br>　✅ **蒙版极性已实测为「没有反」**（三条同向证据：① 源码 `mask = 1 - alpha`（远端只读摘录 `36_inpaint_polarity_source.txt`）② 出图实测：变体 A 圆心 `repaint_core` 逐像素相等率 **0.0**、变体 B 圆心 **1.0** ⇒ **被重绘区 = alpha=0 的透明区** ③ registry 原假设一致）。⚠️ 「保留区逐像素等于参考图」**不算证据**（那是节点 31 `ImageCompositeMasked(destination=原参考图)` 的必然结果）。<br>　🔴 **三条放行前置（一条都还没满足）**：① 本次 L4 因无 alpha 而退化 ② 极性探针**绕过了平台素材管线**（`37_probe_*.py` 用 `lambda` 直接注入本地文件名，没走 `/upload/image`、没走 `asset_resolver`）⇒ **证明不了「用户上传的带透明区图能活到 ComfyUI」** ③ `engine/` 与后端**都没有校验 inpaint 输入必须含 alpha** ⇒ 普通 JPEG 上传会**静默产出与输入相同的图并报成功** | P0 |
| P3-07 | WF-STYLE 风格迁移 | ❌ **V1 明确不做，顺延 V2（见 `docs/adr/0008-v1-scope-reduction.md` 裁定 1）**。缺**三类权重**（`ipadapter/*` + `clip_vision/*` + `loras/*` 枚举全空）：① IP-Adapter 本体 ② CLIP-Vision 编码器（IP-Adapter 的硬前置）③ LoRA。**不下载的原因**：CLIP-Vision 许可未核实（`license_matrix.md:171` 标 unknown）而铁律是「未知即不用」；IP-Adapter 是 P4 控制体系的正主，P4 目前 0/10；电商核心场景用现有 5 条已能覆盖 | **V2**（原 `P0`，与 ADR-008 裁定 1 自相矛盾，已改） |
| P3-08 | WF-BATCH 批量出图（参数矩阵 / CSV 驱动 / 目录监听） | `[ ]` **未开始**（P3 阶段结果未计入；批次 API 在 P6 侧）；批量引擎 + 文档 | P0 |
| P3-09 | **工作流注册表**：ID + 版本 + 参数 Schema + 变更日志 + 启用/灰度 | `[x]` **已完成**（P3 阶段结果明列）；`workflows/registry.yaml` | P0 |
| P3-10 | 参数模板库（每个场景 3-5 套预设，含适用场景说明） | `[x]` **已完成**（2026-09-22）：12 个模板入库（t2i 4 / klein 2 / i2i 2 / inpaint 2 / upscale 2），覆盖电商/社交/创意/后期 4 个类别。参数预设含 width/height/steps/cfg/denoise/scale_factor | P1 |
| P3-11 | 元数据写入产物（PNGInfo/EXIF：workflow_id、version、seed、models、完整参数） | `[x]` **已完成**（P3 阶段结果明列；`engine/metadata.py`）；元数据工具 | P0 |
| P3-12 | 工作流调试记录：每条工作流建一个 debug 日志（踩坑与解法） | `[x]` **已完成**（P3 阶段结果明列）；`docs/sop/debug_log.md` | P1 |

**DoD**：**5 条**工作流全部可稳定出图（**1 条 `style_transfer_v1` 顺延 V2**，见 `docs/adr/0008-v1-scope-reduction.md` 裁定 1），参数可从外部注入，产物带完整元数据，注册表可查询版本。

**阶段结果**：🔵 进行中（**9/12**，2026-09-23 计数核对）。已完成 P3-01/02/04/05/06/09/11/12 —— 三条新工作流（i2i/inpaint/upscale）的定义与 Schema 完成并通过 L1/L2/L3，**且渲染路径 L4 已于 2026-09-17 在真实 RTX 4090 上跑通**（三条均 **通过 11 · 失败 0 · 跳过 0**）；P3-03（WF-T2I）由 t2i_v1 承担且 L4 已通过；**P3-07 口径已按 ADR-008 裁定 1 修正：V1 明确不做、顺延 V2，故 V1 工作流口径为「5 条可用 + 1 条顺延」（原口径「6 条」不可能在 V1 达成）**。
- 🔴 **本条目的关键口径：渲染路径已验证 ≠ 工作流可放行。** 三条新工作流的登记牌**全部仍是 `status: disabled` + `verification: pending_gpu`**（`render_path_l4: true`）—— 两个前置未满足：① **缺 per-workflow baseline**（registry 强制 `gpu_verified` 必须带 `baseline.measured_at`；**不得**拿 #18 的 SDXL **30 步** 4.54s 去顶 `inpaint_v1` 的 **25 步**，那是编造）② **契约缺口**：`verification` 的三个枚举值没有一个能表达「渲染路径已出图、但无本口径基准」，真相改由 `verification_note` 承载。
- ⚠️ **`inpaint_v1` 另有独立问题：本次 L4 是 no-op**（参考图无 alpha ⇒ 全零 mask ⇒ 产出与输入逐像素相同），工作流的**核心语义（蒙版区重绘）没有被行使**。所以**当前「三条可用」的说法不成立**：i2i / upscale 是「渲染路径可用、放行前置未满足」，inpaint 还多一条「蒙版语义未被行使」。**禁止**写成「三条工作流全部可用」。
- 工具链已就绪且**离线可校验**：`python -m engine` 跑 L1/L2/L3 → `VALIDATE=PASS`（2530 节点注册表）；**渲染路径 L4 由 `engine/tools/verify_render_path.py` 承担**（判据：注册表 `param_schema` → `engine/render` 六步 → `/upload/image` → `/prompt` → 出图 → 确定性 → 元数据闭环；**不是裸提交模板**）。
- ⚠️ **L4 只证明「渲染路径能出图」**，**不证明**画质（属 golden set / P5、P8）、**不证明**性能达标（属各工作流口径的稳态基准）、**不证明** upscale 的「放大画质增益成立」。三条的产物确定性（同 seed 逐字节一致）已成立。
- ⚠️ **仍有一处与本阶段相关的过期提示已入库未修**：`engine/validate.py:917` 对 `verification != gpu_verified` 固定打印「**未在 GPU 上真实出图**」，而三条**现已出过图** ⇒ CLI 提示已失实（**本轮只登记，未改代码**）。
- ⚠️ **P3 的剩余部分仍无法自证**：per-workflow baseline 必须真实出图（**需 GPU**）；`inpaint` 的蒙版语义需要**带 alpha 的素材走完整平台管线**（上传 → asset_resolver → ComfyUI）才算打通。这是 P3 剩余部分的前置

---

### P4 · 控制体系（W3-W5）

| ID | 任务 | 产出 | Pri |
|---|---|---|---|
| P4-01 | ControlNet 预处理器评测（canny / depth / lineart / softedge / normal / openpose） | `[~]` **部分完成**（2026-09-23，含当晚第二轮）：6 种预处理器在真实 4090 上跑通并落控制图（Canny 2.0s / LineArt 2.0s / HED 2.0s / DepthAnythingV2 16.1s / Openpose 6.0s / DW 2.0s）。<br>　✅ **第二轮补齐了首轮的「空转」**：本机无人物素材 ⇒ 用**自家 t2i 合成**全身人物参考图（4 候选取骨架占比最高者），再对同一节点跑人物图 vs 商品图对照 —— **OpenPose 非黑占比 0.03303 / DW 0.02282（检出骨架），同一节点对商品图均为 0.0（空图）** ⇒ 首轮全黑的根因**确认是参考图没有人体**，节点本身是好的。证据 `evidence/p4_control_20260923/{person,pose}/`。<br>　🔴 **但这只证明"预处理器能用"，不证明"能控姿态"** —— 本机**没有 openpose/dwpose 的 SDXL ControlNet 权重**，骨架接不到生成上（见 P4-06）。<br>　⚠️ 仍未评：Zoe Depth（权重缺失 + 节点直连 HF 不可达）、Normal（**2534 个节点类里没有 `NormalMapPreprocessor`**） | P0 |
| P4-02 | ControlNet 模型接入（SDXL/Union 系）+ **权重扫描实验**（0.2→1.0，找甜区） | `[~]` **部分完成**（2026-09-23）：本机有 canny/depth 两路 SDXL ControlNet（diffusers 格式，ComfyUI `comfy/controlnet.py:739` 自动转换，**实测可加载出图**）。权重扫描 0.0→1.0 各 6 档全绿。**Canny `edge_f1` 单调上升 0.011→0.232（推荐 0.6–1.0）**；**Depth 0.6 达峰 0.159、1.0 跌到 0.047（推荐 0.4–0.6，1.0 会把深度等值线糊进背景）**。⚠️ lineart/softedge/openpose/dwpose **无对应 ControlNet 权重**，未扫描。<br>　⚠️ **第二轮复核**：远端 `models/controlnet/` 仍**只有** `controlnet-{canny,depth}-sdxl-1.0.safetensors` 两个文件 ⇒ 四类权重确实不存在（是"本机没有"，不是遗漏）；要补需先过**商用许可核实**（项目铁律「未知即不用」） | P0 |
| P4-03 | IP-Adapter 接入（base / plus / faceid），权重与 `weight_type` 实验 | `[~]` **部分完成**（2026-09-23）：🔴 **先修了一个阻断性错配** —— 上轮用 bigG 编码器（宽 1664）配 vit-h 适配器（需 1280），报 `size mismatch for proj_in.weight`，全部失败；本轮补下 `CLIP-ViT-H-14-laion2B-s32B-b79K`（1280）后跑通。`ip-adapter-plus_sdxl_vit-h` 权重 0.2→1.0 五档全绿，`ssim_vs_ref` 0.744→0.847，**推荐 0.6–0.8**。⚠️ 只测 1 个变体、`weight_type` 只测 `linear`，faceid/其余 14 种 weight_type 未测。<br>　✅ **第二轮用途验证（关键）**：IP-Adapter 的定位被证实为**锁材质**（配合 ControlNet 锁结构）—— 在 `canny@0.7 + IPA@0.6` 下颜色漂移 ΔE 由 6.36 降到 3.77、色调距离降 76.5%（见 P4-09） | P0 |
| P4-04 | LoRA 加载 / 多 LoRA 叠加 / 权重调度（含冲突与过拟合排查） | `[ ]` **未开始**（2026-09-23）：`models/loras/` 为空 ⇒ **无素材**，非跑失败。引入任何 LoRA 前须先核实商用许可并登记 `engine/model_registry.yaml` | P0 |
| P4-05 | SAM/SAM2 + GroundingDINO 或 Florence2 自动分割 → 自动遮罩（免手动画 mask） | `[ ]` **未开始 · 环境不具备**（2026-09-23）：`GroundingDinoModelLoader` / `Florence2Run` 节点**不存在**，SAM 权重也缺。候选替代 BiRefNet/Rembg 的节点齐（`GetMaskByBiRefNet` 等）但权重未下、未验证，且**不接受文本提示**，不能等价替代 | P0 |
| P4-06 | DWPose 姿态控制 + 姿态库（电商常见姿态 ≥20 个） | `[ ]` **未开始**（2026-09-23，当晚第二轮修正了阻塞项）：JSON 节点与权重（yolox_l.onnx / dw-ll_ucoco_384.onnx）**本机都在**，且第二轮已证明 `DWPreprocessor`/`OpenposePreprocessor` **对合成的人物图能检出骨架**（非黑占比 0.023 / 0.033）。<br>　🔴 **阻塞项已修正为「缺 openpose/dwpose 的 SDXL ControlNet 权重」**（**不是**原先写的"缺人物素材" —— 素材侧已用自家 t2i 合成解法绕开）。**没有 CN 权重，骨架接不到生成上 ⇒ 姿态控制在 V1 环境不可达**。<br>　⚠️ 姿态库仍需 ≥20 张人物参考图；第二轮只合成了 4 张候选（用 1 张）。 | P1 |
| P4-07 | 景深 / 法线 / 线稿联合控制（多 CN 叠加策略与权重配比） | `[~]` **部分完成**（2026-09-23）：canny+depth 两路 `ControlNetApplyAdvanced` 串联**可同时生效**，四种配比（c 0.6/0.8 × d 0.3/0.5）全部出图成功，结构稳定。⚠️ 三路（+法线）未测（法线路径不存在）；**冲突检测（两信号矛盾时的表现）未做**；显存未单独计量（整机峰值 14.7/24.5 GiB） | P1 |
| P4-08 | **控制能力矩阵文档**：控制手段 × 适用场景 × 推荐参数 × 副作用 × 显存开销 | `[~]` **文档在，但已标注"推荐区间是待验证先验值"**（2026-09-23）：`docs/sop/control_matrix.md` 增补 2026-09-23 实测节，并把「10 种手段的推荐区间」明确降级为**未验证先验** —— 其中只有 Canny/Depth 有实数据支撑 | P0 |
| P4-09 | **一致性方案**（同一商品多图）：seed 复用 + reference only + IP-Adapter + 固定风格模板，做对比实验 | `[~]` **部分完成，结论仍记为"未达成"**（2026-09-23 两轮）：<br>　**第一轮**：固定参考图 + canny@0.7、只变 seed 跑 10 张，成对 SSIM 0.8335，但**材质/颜色随 seed 漂移**（杯内色在白/金/满金间跳，一张裂纹釉面、一张整只变金属金）。<br>　✅ **第二轮补了对照 arm**：`canny@0.7 + IP-Adapter@0.6`（同 seed 区间）—— SSIM 0.8335→**0.8556**、SSIM min 0.719→**0.813**、**颜色漂移 ΔE 6.36→3.77（−40.7%）**、**色调距离 0.121→0.028（−76.5%）**；肉眼上 10 张内壁一致为金色。⇒ 方案修正为 **「CN 锁结构 + IP-Adapter 锁材质」双锚**。证据 `evidence/p4_control_20260923/material/`。<br>　🔴 **但仍不能标完成**：① DoD 要的「**人工**评分 ≥4/5」**至今未做**（评分表 `material/_score_sheet.html` 已生成、**分数栏为空**；2026-09-23 补的是 **AI 预评分**，不算人工）；② ΔE 3.77 是**改善不是消除**，肉眼仍可辨；③ 只测 `IPA@0.6` 一个权重点。<br>　⚠️ **2026-09-23 补：逐例评分已做，但来源是「AI 预评分」而非人工** —— 产物 `evidence/p4_control_20260923/material/scores_ai_20260923.{json,md}`：**对照组 D3=2.9 / 实验组 D3=4.1**（D3≥4 的图 2/10 vs **10/10**），方向与机器指标一致。🔴 **但 `scores_ai_20260923.json` 的 `is_human_scoring=false`，DoD 的「人工」二字仍未满足** ⇒ P4-09 仍**不能标完成**；须由人复核/重打，或项目方显式裁定接受 AI 评分 | P0 |
| P4-10 | 商品主体保真专项（产品图最容易形变）：结构保真 vs 创意度的权衡实验 | `[ ]` **未开始**（2026-09-23）：该专项要求按**品类**（玻璃/金属/织物…）分别权衡，需多品类参考图集；本机只有 1 张陶瓷杯 ⇒ 做了也只能覆盖一个品类，不足以称专项 | P0 |

**DoD**：控制矩阵文档覆盖 ≥8 种手段；同一商品 10 次生成结构一致性人工评分 ≥4/5。

**阶段结果**：🟡 **部分推进（0/10 → 4 项部分完成）**（2026-09-23，真实 RTX 4090，**两轮**：白天首轮 + 当晚补硬缺口）。两轮把 P4 从"纸面矩阵"推到"有真实数字"，但**没有任何一项达到打 `[x]` 的标准**，且 **P4 阶段 DoD 两条都没满足**：
- ✅ **打通了原本跑不起来的链路**：SDXL ControlNet（diffusers 格式）可加载出图、IP-Adapter（修正编码器后）可用、两路 CN 可串联、6 种预处理器可跑。
- ✅ 产出**客观指标**：Canny `edge_f1` 随权重 0.011→0.232 单调；Depth 0.6 达峰；IP-Adapter `ssim_vs_ref` 0.744→0.847；一致性成对 SSIM 0.8335 + 颜色漂移 ΔE 6.36。
- ✅ **第二轮解掉了首轮的两条硬缺口中的一条半**：
  - ① **姿态预处理器的"空转"已解**：合成人物图后 OpenPose/DW 双双检出骨架（商品图对照为 0.0）⇒ 根因是参考图无人，非节点坏。
  - ② **材质一致性已找到有效手段**：`canny + IP-Adapter` 把颜色漂移 ΔE 从 6.36 压到 3.77（−40.7%）、色调距离降 76.5%。
- 🔴 **仍未满足的 DoD（两条）**：
  - ① **人工评分仍未做** —— DoD 写的是「10 次生成**结构一致性人工评分 ≥4/5**」。机器代理两轮都有；
    **逐例四维打分也已在 2026-09-23 补做，但来源是「AI 读图单次评分」（`scores_ai_20260923.json` 里 `is_human_scoring=false`）**，
    结果：对照组 **D3=2.9** / 实验组 **D3=4.1**（按 ≥4/5 口径：对照组不达标、实验组勉强达标）。⇒ **DoD 的「人工」二字仍未满足**，须人复核或项目方裁定接受。
  - ② **材质一致性是"改善"不是"达标"**：ΔE 3.77 肉眼仍可辨，且只测了一个权重点。
- ⚠️ **缺口性质已分类清楚**：**素材缺口（可用合成图解）** = 人物参考图；**权重/节点缺口（本机真没有，须先过许可核实）** = openpose/dwpose/lineart/softedge 的 SDXL ControlNet 权重、LoRA 权重、GroundingDINO/Florence2 节点。后者**不是 bug，是本机没有**。

---

### P5 · 模型与提示词资产管理（W2-W6，持续）

| ID | 任务 | 产出 | Pri |
|---|---|---|---|
| P5-01 | 模型目录规范（checkpoints/loras/controlnet/upscale/ipadapter/…） | `[x]` **已完成**（目录规范落在 `engine/model_registry.yaml` 头部 + `deploy/autodl/02_migrate_models.sh`）；目录 | P0 |
| P5-02 | 底模筛选评测（SD1.5 / SDXL / Flux 系），**以电商场景为评判标准** | `[ ]` **未开始**；选型报告 + 主底模确定 | P0 |
| P5-03 | LoRA 筛选与评测（质感 / 光影 / 材质 / 风格） | `[ ]` **未开始**；评测表 + 入选清单 | P0 |
| P5-04 | ControlNet 模型集（按预处理器配套） | `[ ]` **未开始**（canny/depth 两路 SDXL CN 已实测可用，但本任务「按预处理器配套的清单元件」未成文）；清单 | P0 |
| P5-05 | 放大器模型集（RealESRGAN / 4x-UltraSharp 等），tile 策略 | `[ ]` **未开始**（upscale 走免放大器模型路线，两段式待权重）；清单 + 策略 | P0 |
| P5-06 | **`model_registry`**：name / hash / version / source / license / 评测分 / 状态 / 显存占用 | `[x]` **已完成**（`engine/model_registry.yaml`，6 个模型含 size + sha256）；`engine/model_registry.yaml` + DB | P0 |
| P5-07 | 模型版本归档与一键回滚（含归档脚本与存储策略） | `[ ]` **未开始**；归档工具 | P0 |
| P5-08 | **提示词库**：正向 / 反向 / 风格 / 材质 / 光影 / 构图 / 负面词包，≥200 条带标签 | `[x]` **已完成**（`assets/prompt_lib/`；⚠️ 「≥200 条」未逐条核）；`assets/prompt_lib/` | P0 |
| P5-09 | 提示词模板引擎（变量插值 + 权重语法 + 随机化） | `[ ]` **未开始**；引擎 | P1 |
| P5-10 | **商用许可矩阵**：每个模型的许可证与商用限制、替代方案 | `[x]` **已完成**（`docs/sop/license_matrix.md`；⚠️ DoD「许可矩阵无「未知」项」**未满足** —— `CLIP-Vision` 仍 `unknown`，属 ± 项）；`docs/sop/license_matrix.md` | P0 |
| P5-11 | 模型热注册与冷卸载（不重启服务加载新模型） | `[ ]` **未开始**；能力 + 文档 | P2 |

**DoD**：registry 覆盖全部在用模型；任一模型可回滚；提示词库 ≥200 条；许可矩阵无「未知」项。
**阶段结果**：🔵 进行中（**4/11**，2026-09-23 逐任务标记后）。已完成 **P5-01**（目录规范，落在 `engine/model_registry.yaml` 头部 + `deploy/autodl/02_migrate_models.sh`）、**P5-06**（模型注册表 6 个含 size + sha256）、**P5-08**（提示词库 `assets/prompt_lib/`）、**P5-10**（`docs/sop/license_matrix.md`）。
⚠️ **本节 DoD 四条里有两条不成立**：① 许可矩阵**仍有「未知」项**（`CLIP-Vision`，见 ± 项）；② **任一模型可一键回滚**（P5-07）**未做**。⚠️ P5-08 的「≥200 条」未逐条核。

---

### P6 · 平台封装：服务化（W4-W6）

| ID | 任务 | 产出 | Pri |
|---|---|---|---|
| P6-01 | **ComfyUI 适配层**（Driver）：提交 / 查询 / 取消 / 中断 / 图片上传 / WS 进度 | `[x]` **已完成**（实现位于 `backend/app/engine/driver.py` —— ⚠️ 本行原写的 `engine/driver.py` **路径有误**）；`engine/driver.py` | P0 |
| P6-02 | 任务队列（Celery + Redis），优先级队列、并发控制、超时 | `[x]` **已完成**（P6 阶段结果明列）；队列模块 | P0 |
| P6-03 | 任务状态机持久化（DB schema + 状态流转 + 历史事件） | `[x]` **已完成**（P6 阶段结果明列；`backend/app/services/state_machine.py`）；schema + 代码 | P0 |
| P6-04 | 异步任务 API（提交 / 查询 / 取消 / 回调 / Webhook）+ OpenAPI 文档 | `[x]` **已完成**（P6 阶段结果明列；`backend/app/api/v1/tasks.py`）；API | P0 |
| P6-05 | 批量任务与**断点续跑**（失败项记录 + 只重跑失败项） | `[x]` **已完成**（P6 阶段结果明列；断点续跑见 `POST /batches/{id}/retry-failed`）；批量引擎 | P0 |
| P6-06 | 失败重试与**自动降级**（OOM → 降分辨率 / 降 batch / 换精度 / 关放大器） | `[x]` **已完成**（P6 阶段结果明列）；降级策略表 + 实现 | P0 |
| P6-07 | 用户体系与鉴权（注册 / 登录 / JWT / API Key） | `[ ]` **未完成**（**注册 / 登录 / JWT 已实现**，缺 **API Key 鉴权** —— P6 阶段结果据此未计入）；鉴权模块 | P0 |
| P6-08 | 配额与限流（按用户 / 按接口 / 并发上限） | ✅ **已完成**（2026-09-17）。**口径**：**「按用户配额」+「EX-5 队列深度闸门」+「每用户在途任务数上限」三项均有产出与测试**；**「按接口限流」经裁定 V1 不做**（需求无 QPS / 频次 / 窗口条款，且 GPU 已全局串行化）—— 这一项**是有依据的不做，不是遗漏**。<br>　✅ **① 配额原子化**（`4f601e4` / `2c74d81` / `fb6da15`）：修掉一个**真 bug** —— 配额扣减原为 Python 层读-改-写（`user.quota_used += N`，4 处），且**全仓库零 `SELECT FOR UPDATE` / 零条件 UPDATE** ⇒ 两个并发提交可**同时读到同一 `quota_used` 再各写一次 → 配额可被超发**。已改成**一条原子条件 UPDATE**（`UPDATE users SET quota_used = quota_used + :n WHERE id=:uid AND quota_used + :n <= quota_total`）+ `rowcount` 判据，PG 与 SQLite 行为一致（**不用 `SELECT FOR UPDATE`**，SQLite 不支持）；新增 `backend/app/services/quota.py` 的 `charge()`，4 个调用点（submit / batch / retry / retry-failed）统一改用它，`_assert_quota` **已删除**（避免两套判据）。② 402 改为**结构化错误体**（契约 §2.2 的对象形式：`code=quota_exceeded` / `message` / `fields`），前端改按 `detail.code` 优先、402 状态码后备；契约 §2.2 那行与 §7 变更记录已同步。③ 补上**零覆盖**的测试：新建 `backend/tests/test_quota.py`（**8 条**），其中 `test_stale_cached_remaining_cannot_overdraft` 用「DB 已扣满、ORM 缓存仍认为够」**确定性地复现了原 bug 形态**。④ 验证：后端 355 → **363 passed**、前端 82（+2）、ruff 全绿；**变异验证**：把实现**还原成原始的非原子读-改-写**后，**只有**该条用例变红（`assert 202 == 402`）⇒ 证明它钉的就是原始 bug 形态。<br>　✅ **② EX-5 队列深度闸门**（`8da67b1` / `f7ab101`）：`queue_max_depth` 由**死配置**变为两个消费点 —— **软阈值**（在途 > `queue_max_depth`，默认 10000）→ `log.warning` 但**仍接单 202**（依据 C12「排队比崩掉好」；且 `queue_full` 在契约 §1.5 里是**可重试**类型，此时拒绝用户是错的）；**硬阈值**（在途 > **2 ×** `queue_max_depth` = 20000）→ **422 + `code=queue_full`**，`message` 给出「当前排队 N 个 / 预计等待 X 分钟」。在途口径 = `queued` / `running` / `retrying`，且**按全局计数**（GPU 并发恒为 1，按用户判会被"每人都不超限"绕过）；闸门**排在扣额之前**（被拒**不扣额、不建任务**）；`POST /tasks` / `POST /batches` / `retry` / `retry-failed` **四条路径全部挂上**。PRD EX-5 的**自相矛盾**（「拒绝提交 → pending 保留」两句不可能同时成立）与 `user_flow.md:139`「保留在队列，不拒绝」的冲突，已按实现统一为「两层语义」。测试：`backend/tests/test_queue_guard.py`（**14 条**）。⚠️ 该文件的硬阈值**写死为字面量 4**、不从实现常量推导 —— 原因见「变异逃逸」那条（`项目进展.md` #43）。<br>　✅ **③ 每用户在途任务数上限**（`00219ec`，新配置 `max_in_flight_per_user = 1000`）：判据 `当前在途 + 本次张数 > 上限` → **422 + `code=TOO_MANY_IN_FLIGHT`**（**整批**判定、恰好等于上限放行）；只加在两条**提交**路径，`retry` / `retry-failed` **刻意不加**（重试**不新增任务**，加了等于同一个任务被计两次）。测试：`backend/tests/test_in_flight_limit.py`（**10 条**）。**为什么需要它**：GPU 全局并发恒为 1、队列全员共用，没有这道门时单个用户一次提交 1000 张就能把队列占满 —— 属**公平性**问题而非容量问题（全局熔断只管总量、管不了分配）。<br>　📌 **「按接口限流」经裁定 V1 不做**：需求里**没有任何 QPS / 频次 / 窗口条款**，且 GPU 已被**全局串行化**（单 Redis 键 + `worker_concurrency=1`）⇒「按用户 GPU 并发」实际无作用。真正有意义的那一半（每用户在途上限）已由上面 ③ 承担。<br>　📌 **两项计额口径已裁定**（落表在 `docs/prd/PRD_v1.md` FR-1.2 的「口径说明」）：① `retry-failed` **保持**对失败项按**新一次生成**计额（FR-3.5 省的是「已成功那部分不重复计额」，**不是**「重试不计额」）② **提交时预扣、终态失败 / 取消不退还**（🔶 协调者裁定，**可被推翻**；理由与代价已写进 FR-1.2，V2 可选补偿机制**仅登记、V1 不实现**）。<br>　⚠️ **未验证**：真实 PostgreSQL 上的并发行为（本机无 PG；且测试夹具是单连接 `StaticPool`，**不具备并发能力**，所以**没有写「两个并发 POST」的测试** —— 写了就是假证据）。验证方法：起真 PG，把该用户置于 `quota_total - quota_used == 1`，两个真并发线程各 POST 一次，断言恰好一个 202 一个 402、最终 `quota_used == quota_total`。 | P0 |
| P6-09 | 素材管理（上传 / 文件夹 / 标签 / 搜索 / 回收站） | `[~]` **部分完成**（2026-09-16 晚）：`POST/GET /api/v1/assets` + 详情 + 软删除；图片头解析（魔数判格式 + 取尺寸，**不引入 Pillow**）；列表筛选扩展（`task_id` / `batch_id`(join Task) / `adopted_only` / `favorite_only` / `created_from` / `created_to`，naive datetime 按 UTC 归一化）。<br>　❌ **仍未做（故本行保持 `[ ]`）**：文件夹、标签、关键词搜索、**回收站恢复操作**（V1 无恢复接口）| P0 |
| P6-10 | 产物存储与生命周期（MinIO + 过期清理 + **受控代理取图**） | ✅ **已完成**（2026-09-16 夜）：① `GET /assets/{id}/content` 受控取图（`Content-Type` / `ETag`=实际字节 sha256 / `Cache-Control: private, max-age=300` / `download=1` 转 attachment）② `PATCH /assets/{id}` 标记采纳 / 收藏（仅 `kind=output` 可标采纳，否则 409；`image_adopted` 只在 False→True 跃迁埋点；写 AuditLog）③ `POST /assets/pack` 打包下载（zip 按 SKU 分目录、`SpooledTemporaryFile`+`ZIP_STORED`、找不到即 404 不静默少给、超 `MAX_PACK_ASSETS` 或 0 张 422、SKU 白名单防 zip-slip）④ **生命周期清理** `app.worker.maintenance.cleanup_assets`（beat 每 6h → `maintenance` 队列）：回收站超期物理删除 + 产物超期（仅当 `ASSET_OUTPUT_RETENTION_DAYS>0`）删除，**已采纳/已收藏的产物永久豁免**，**先删对象再删记录、对象删失败则跳过保留记录**。<br>　⚠️ **有意偏离原措辞**：「签名 URL 下载」被替换为**受控代理接口**（V1 经 SSH 隧道，浏览器到不了 MinIO 的 9000 端口；对象键不该进契约；预签名 URL 在 TTL 内不可撤销而软删除必须立刻生效）。理由详见 `docs/sop/contracts.md` §2.3 下方说明与 §7。<br>　⚠️ 新增 4 个配置项（`ASSET_TRASH_RETENTION_DAYS=30` / `ASSET_OUTPUT_RETENTION_DAYS=0` / `MAX_PACK_ASSETS=200` / `CLEANUP_BATCH_LIMIT=500`），已同步 `.env.example` 并受双向漂移门禁约束 | P0 |
| P6-11 | 多租户与数据隔离（数据模型预留 `tenant_id` + 权限校验） | `[ ]` **未开始**；隔离机制 | P1 |
| P6-12 | 审计日志（谁在什么时候改了什么、用了哪个模型） | `[ ]` **未完成**（`AuditLog` 模型与多处写入口已存在，但**覆盖范围与对外查询能力未验收** —— P6 阶段结果据此未计入）；审计模块 | P1 |
| P6-13 | 内容安全：NSFW 过滤 + 敏感词过滤 + 上传文件类型校验 | `[~]` **部分交付，不可标完成**（2026-09-17，`461f587` / `6987ac9` / `6e0f6fe` / `b5643b4` / `b90a5b9`）。<br>　✅ **已交付：输入侧**的敏感词 / 合规词过滤 + 开关 + 留痕 + **422 结构化错误**（`code=CONTENT_BLOCKED`）。词表在 `backend/app/data/content_blocklist.json`（**stdlib json**，不放 YAML 也不放 `assets/` —— 全仓库确认**没有任何运行时代码会加载 `assets/`**）：**5 条规则 / 38 条词条**，分级复用 `negative.yaml` 的 `hit`/`warn`，**硬拒只放极窄的法律红线**。⚠️「上传文件类型校验」这一半早已由素材上传的**魔数**解析承担（P6-09），本条未新增。<br>　🔴 **NSFW 视觉检测 V1 未实现，且没有假实现**：`NullNsfwDetector` 返回三态里的 **`unknown`、永不返回 `safe`**，并有测试钉死；commit footer 写的是 `Refs P6-13（部分交付，不关闭）`。**依据**：ComfyUI 快照 2530 个节点类里**没有任何安全 / NSFW 分类节点**（⚠️ 两个陷阱：`easy prompt` 节点里那个叫 `nsfw` 的入参是**帮用户把 NSFW 词加进提示词**的下拉，语义相反；`WanVideoBlockList` 是 WanVideo 的**层号列表**）；模型注册表 6 个模型全是出图用的；后端无 numpy/PIL/torch 且项目有成文的「避免重依赖」纪律；许可铁律「未知即不用」。<br>　✅ **隐私红线**：审计只记规则 id / `text_hash` / `text_length` / stage，**绝不记命中原词或 prompt 全文**（依据 `tracking_plan.md:30-32`）。⭐ 其机械断言**抓到了 5 处自己泄露词表**（`reason` 文案里抄了 `neg-027/030/031` 的原文，而那几处恰好就是词条本身 —— 等于把黑名单印在错误提示里），已改写并加两条长期盯防断言。<br>　🔴 **仍未做**：NSFW 视觉检测 · 产物侧拦截（R-33 属 Could/V2，落 P8-03）· 上传授权声明勾选（R-43）· 词表运维后台（P7-09）。<br>　⚠️ 词表是「**窄而可解释**」而非完备：绕写（拆字 / 拼音 / 谐音 / 插引号）与图片内容都覆盖不到；`warn` 级仍有已知误报（`血腥玛丽`）。验证：后端 363 → **446 passed**（+83）、前端 83（+1）、ruff 全绿、4 次变异全部变红并还原。<br>　🔒 **V1 口径已裁定（`docs/adr/0008-v1-scope-reduction.md` 裁定 2）**：V1 **只做输入侧文本过滤 + 人工抽检**，**不做 NSFW 视觉检测**（因 2530 节点类里无安全分类节点 / registry 6 个已下载模型全是出图用 / 后端零图像依赖 / 云 API 与「运行时不访问外网」及「不上传商品图内容」冲突）；V2 走「构建期下载离线小分类器」，**引入前必须先核实权重许可**。⚠️ **在 V2 引入真检测器前，禁止把 `NullNsfwDetector.detect()` 改成返回 `safe`** | P0 |

**DoD**：1000 张批量任务无人值守完成；失败任务自动重试成功率 ≥95%；API 文档完整可对外。

**阶段结果**：🔵 进行中（**8/13**，2026-09-17）。已完成 **P6-01/02/03/04/05/06/08/10**（8 项；P6-09 为部分完成，**不计入**）。
- ✅ **P6-08 本轮收口**（配额原子化 + EX-5 队列闸门 + 每用户在途上限；「按接口限流」经裁定不做）—— 这是 P6 从 7/13 到 8/13 的唯一增量。
- ✅ P6-10 本轮收口，**产物消费链路至此闭环**：生成 → 取图 → 标记采纳 → 打包下载 → 过期清理。
  同时补上了良品率（`image_adopted`）与北极星（`image_downloaded`）**两个指标的数据源**
  —— 在此之前这两个已冻结的埋点事件全仓库零处触发（见 `项目进展.md` #31）。
- ⚠️ **本轮交付中仍未验证的部分（必须写清，不得当成已验证）**：存储路径全部由 `InMemoryAssetStorage` 替身覆盖，
  `MinioAssetStorage.get/delete` 的**真实网络行为没跑过**；清理的时间比较只在 SQLite 上验证过
  （PG 上是真 `timestamptz`）；**worker 是否真的监听 `maintenance` 队列未验证**（仓库里没有
  Celery worker 的启动脚本/compose —— 若部署时用 `-Q` 排除了该队列，清理任务会躺在队列里永不执行，
  属**部署层 TODO**）；ETag 语义无测试钉死；浏览器人工走查未做；打包的慢客户端/断连未实测。
- ❌ 仍未完成（**未计入完成数**）：P6-07（API Key 鉴权；JWT 已实现）、P6-09（部分完成，见上）、P6-11（多租户隔离）、P6-12（审计日志 —— `AuditLog` 模型与 auth/tasks/assets/worker 多处写入口已存在，但**覆盖范围与对外查询能力未验收**）、P6-13（**部分交付** —— 输入侧文本过滤 + 422 `CONTENT_BLOCKED` + 隐私红线断言已交付；**NSFW 视觉检测 V1 未实现**，见上）。

---

### P7 · Web 前端（W5-W7）

| ID | 任务 | 产出 | Pri |
|---|---|---|---|
| P7-01 | 设计系统（色板 / 字体 / 组件 / 栅格 / 暗色模式） | `[ ]` **未开始**（仓库内**无任何设计 token 产物** —— 2026-09-23 用 `find web/src -iname '*token*' -o -iname '*theme*' -o -iname '*design*'` 核实为空）；设计 token | P1 |
| P7-02 | **工作流驱动的动态表单**（由参数 Schema 自动生成 UI，新增工作流不改前端） | ✅ **已完成** `web/src/features/workflow-form/`：`controlRegistry`（9 种类型→控件，未知类型落 `FallbackControl` 不崩）+ `defaults`/`validate`/`WorkflowForm` + 9 个控件。<br>　**「不改前端」由构造与测试共同保证**：`WorkflowForm` 从不 `switch(field.type)`；同一引擎已渲染 **3 条真实工作流的 Schema**（klein 6 字段 / t2i 10 / inpaint 12，均取自注册表导出的夹具）而无任何工作流特判。<br>　⚠️ `str`/`bool`/`image_list` 三种类型在注册表里**出现 0 次**，仅合成夹具覆盖 → 状态是**待真实数据验证**，不得写成已验证 | 表单引擎 | P0 |
| P7-03 | 生成工作台（左参数 / 中预览 / 右历史的三栏布局） | `[x]` **已完成**（`web/src/pages/WorkbenchPage.tsx`；随 09-16 前端骨架落地，P7 阶段结果计入）；页面 | P0 |
| P7-04 | 任务中心（队列 / 实时进度 / 日志 / 失败原因 / 一键重试） | `[x]` **已完成**（2026-09-22）：`web/src/pages/TaskCenterPage.tsx`（任务表格 + 状态筛选 + 详情展开 + 一键重试），路由 `/tasks` 已替换占位页 | 页面 | P0 |
| P7-05 | 画廊与素材库（网格 / 筛选 / 对比 / 批量下载 / 查看详情与元数据） | ✅ **已完成**（2026-09-16 夜，commit `ebf79c6`）`web/src/pages/GalleryPage.tsx`（**单份实现，无重复**）+ `web/src/features/gallery/MetadataDrawer.tsx`：筛选（全部 / 已采纳 / 未采纳 / 已收藏）、采纳、收藏、下载、打包、元数据抽屉。<br>　⚠️ **图片取图走 `GET /api/v1/assets/{id}/content`，前端必须用 `fetch`+`blob` 带 Bearer 头** —— `<img src>` **带不了**自定义 `Authorization` 头（`AssetOut` 刻意不返回 URL，见 `api/types.ts` 的说明）。<br>　⚠️ 「未采纳」是**前端侧过滤**（后端只有 `adopted_only`），翻页范围会随之变化，页面已明示；**浏览器人工走查未做** | 页面 | P0 |
| P7-06 | 批量出图向导（CSV 导入 / 参数矩阵预览 / **预估时长与成本**） | `[x]` **已完成**（2026-09-22）：`web/src/pages/BatchPage.tsx`（工作流选择 + SKU 素材绑定 + 参数矩阵预览 + 提交），路由 `/batch` 已替换占位页 | 页面 | P0 |
| P7-07 | 轻量图片编辑（上传参考图 / 画遮罩 / 裁剪 / 涂鸦） | `[ ]` **未开始**；组件 | P0 |
| P7-08 | 模板与提示词库页面（浏览 / 搜索 / 一键套用 / 收藏） | `[x]` **已完成**（2026-09-22）：`web/src/pages/TemplatesPage.tsx`（品类筛选 + 名称搜索 + 模板卡片 + 一键套用跳转工作台），路由 `/templates` 已替换占位页。后端 12 个模板已入库（5 条工作流 × 2-4 个/流） | 页面 | P1 |
| P7-09 | 管理后台（用户 / 配额 / 模型 / 工作流版本 / 系统健康 / 队列） | `[x]` **已完成**（2026-09-22）：`web/src/pages/AdminPage.tsx`（系统健康 + 数据概览 + 工作流版本表 + 模型注册表），路由 `/admin`，导航栏已加「管理后台」入口 | 页面 | P1 |
| P7-10 | 响应式与可访问性基线（至少桌面 + 平板可用） | `[ ]` **未开始**；适配 | P2 |
| P7-11 | 前端埋点接入（PV/UV、功能使用、漏斗、弃用） | `[ ]` **未开始**（前端埋点未接入）；埋点 | P0 |
| P7-12 | 空态 / 加载态 / 错误态 / 首次引导（决定「是不是商用级」的细节） | `[ ]` **未开始**；交互完善 | P1 |

**DoD**：非技术用户无需指导即可完成「上传 → 选模板 → 批量出图 → 下载」全流程；新增一条工作流不需要改前端。

**阶段结果**：🔵 进行中（**7/12**，2026-09-23 计数核对更新）。已完成 **P7-02**（动态表单引擎）、**P7-03**（生成工作台，随 09-16 前端骨架落地）、**P7-05**（画廊与素材库页）、**P7-04**（任务中心）、**P7-06**（批量出图向导）、**P7-08**（模板与提示词库）、**P7-09**（管理后台）。
⚠️ **本行原写「3/12」、§2 表写「8/12」，两者都不是这 7 项** —— §2 的 8 把 `P7-01`（设计系统）也算进去了，但仓库里**没有任何设计 token 产物**（`web/src` 下无 token/theme/design 文件）。计数依据见 `项目进度.md` §2 末注。
其余 P7-03~P7-12 仍是占位页（`web/src/pages/PlaceholderPage.tsx`），每个都标了归属任务号。
⚠️ **P7-05 已接入取图接口**（`GET /assets/{id}/content` 经 `fetch`+blob 带 Bearer 头），后端阻塞（P6-10）已解除；⚠️ 但**浏览器人工走查未做**（见 `项目进展.md` §4）—— 即"图能显示出来"目前只有 jsdom 级证据，没有端到端证据。**P7-04 任务中心**的实时进度仍依赖 P6-04 的 WebSocket（**未实现**，当前只能轮询）。

---

### P8 · 质量体系（W6-W7，主线 B「如何测试」）

| ID | 任务 | 产出 | Pri |
|---|---|---|---|
| P8-01 | **构建 golden set**：电商 30-50 个 case（品类 × 材质 × 光影 × 构图），含输入与期望 | `[x]` **已构建且真实跑通**（2026-09-22）：42 个用例 L4 **total 42 / ok 42 / failed 0 / skipped 0**（`deploy/comfyui/evidence/golden_set_20260918/39_golden_set_results.json`）。🔴 **但「画质评审通过（一次通过率 ≥80%）」这句缺证据，2026-09-23 复议为待补**：仓库里只有 **3 张截图**（`evidence/l4_verification/review_gs-{t2i,i2i,inpaint}-001.png`），结果 JSON 中**没有任何分数字段**，全仓库无逐例评分表；而 `tests/golden_set/README.md:14,22` 明确写「没有任何通过率/得分数字」「任何从本目录读出的通过率都必然是编造的」。⚠️ 三条工作流 `status` 曾据此改为 `enabled`，**2026-09-23 已复议并回退为 `disabled`**（依据缺逐例评分产物，放行不成立；见 `workflows/registry.yaml` 的 `updated_at` 注释）。⚠️ 另外 `expected` 仍恒为 `null` | P0 |
| P8-02 | 评分标准定义（技术缺陷 / 构图 / 一致性 / 商用可用度，1-5 分，带锚点样例） | `[x]` **定义完成**（`docs/sop/quality_rubric.md`）：4 维度 × 1-5 档。⭐ 判废词汇**不另造**，直接映射 `negative.yaml` 的 `hit`/`warn`/`soft`；四个维度**刻意不做加权**（加权系数需真实数据标定）。⚠️ 本文件**没有对任何一张真实产物打过一次分**（2026-09-23 复核仍成立），也**未做评分者一致性检验**（无 κ 等任何指标）——「定义完成」**不等于「已标定」** | P0 |
| P8-03a | 自动质检（**离线粗指标**）：尺寸、留白、过曝欠曝、模糊 | `[ ]` **未开始 · 顺延**（ADR-008 裁定 3 拆分）。⚠️ **阻塞条件：待真实产物** —— 阈值标定必须用真实图，而 golden set 的 `expected` 全为 `null`、`expected_status` 全为 `pending_gpu`（见 P8-01），现在做只会得到一个「阈值未标定」的空壳。其中「尺寸」已由零依赖的 `image_probe.probe()` 覆盖；其余三项需**完整像素解码**（不是模型，但需图像库）⇒ 将来做时按裁定 3 走 **optional extra**，**不进 `backend/` 必装依赖** | P1 |
| P8-03b | 自动质检（**需模型**）：NSFW、畸形（手 / 文字） | `[ ]` **未开始 · 顺延 V2**（ADR-008 裁定 2 / 3）。⚠️ **阻塞条件：V2 需模型** —— NSFW 视觉检测 **V1 明确不做**（`NullNsfwDetector` 永返 `unknown`、永不返 `safe`，`backend/app/services/content_safety.py:407-426`），畸形检测同样必须引入模型；引入前**必须先核实权重许可**并登记进 `engine/model_registry.yaml` | P1 |
| P8-04 | 人工抽检流程与抽样比例（批量 ≥100 张抽检 10%） | `[x]` **定义完成**（`docs/sop/quality_sampling_sop.md`）：抽样比例 / 分层随机方法 / 记录表模板 / 判读作业规范（防疲劳误判）。⚠️ 其 §0 的四条打勾条件**一条都没满足** —— 本 SOP **从未执行过一次** | P1 |
| P8-05 | **良品率看板**（按工作流 / 模型 / 模板 / 参数维度下钻） | `[x]` **已完成**（2026-09-22）：后端 `GET /api/v1/stats/quality` 聚合产物采纳/任务执行/缺陷知识库数据，前端 `QualityDashboardPage.tsx` 展示总览卡片+任务统计+工作流维度表+缺陷排行。7 条测试全绿。路由 `/quality`，导航栏已加「质量看板」入口 | P0 |
| P8-06 | **缺陷知识库**：瑕疵类型 → 成因 → 解法 → 关联参数（≥30 条） | `[x]` **定义完成且参数经机械校验**（`docs/sop/defect_kb.md`）：**44 条**，六类全覆盖（hand 6 / text 6 / distortion 8 / exposure 7 / material 9 / drift 8）。每条带 `verified_by`：**`l3=33 · doc=10 · none=1 · gpu=0`**（`gpu` 严格为 0，有测试专门防止它被写上）。⭐ **反向词类解法按链路分栏**（`sdxl=30` 必须给 `negative_refs`；`klein=3` 必须为 `—` —— CFG=1 反向词无效且该 Schema **根本没有 `negative_prompt` 字段**）。⚠️ 参数**合法** ≠ 参数**有效**：无一条做过真实出图验证 | P0 |
| P8-07 | A/B 对比机制（同 seed 下对比 模型 / 参数 / CN 权重） | `[x]` **已完成**（2026-09-22）：后端 `POST/GET /api/v1/compare`（内存存储对比组，2-6 变体，同 seed 参数合并）+ 前端 `ComparePage.tsx`（工作流选择 → seed 设置 → 变体参数编辑 → 提交 → 结果查看），8 条测试全绿。路由 `/compare`，导航栏已加「A/B 对比」入口。⚠️ 实际对比执行需 GPU | P1 |
| P8-08a | 回归测试（**离线门禁**部分）：工作流或模型变更时跑 Schema → render → L1/L3 | `[x]` **已接入 CI 与本地可选 pre-push** —— `engine/tools/check_defect_kb.py` 与 `check_golden_set.py` 已接入 `.github/workflows/ci.yml` 的 `quality-gates` job，并新增 `scripts/pre-push.sh` 供本地启用。本批实测两者均通过（缺陷库解析 44 条 / golden set 42 条全绿）。⚠️ 两者结尾都显式打印「**本校验只证明参数合法，不证明参数有效**」「**本校验不证明能出图**」；⚠️ GitHub Actions 侧仍未真实执行（仓库无远端）| P0 |
| P8-08b | 回归测试（**真实 golden set 回归**）：变更后跑一遍真实评测 | `[x]` **跑批已完成，但"画质评审通过"缺证据**（2026-09-22 跑 / 2026-09-23 复议）：42/42 真实出图（total 42 · ok 42 · failed 0 · skipped 0）**有据**；🔴 「画质评审通过（一次通过率 ≥80%）」**没有任何逐例评分产物**支撑（详见 P8-01 行）。**打勾口径**：同 P8-02/P8-04 —— 依据是「真实跑批产出存在」，**不是**「画质已被验证」。⚠️ 因此本项**不构成有效的回归门禁**：门禁要"有分数可比"，而基线分不存在。⚠️ **2026-09-23 复议结论**：既然评分未做，三条工作流的 `status` 已**由 enabled 回退为 disabled** | P0 |
| P8-09 | 可复现性验证（同 seed 同环境两次输出一致性验证） | `[x]` **覆盖 5/5** —— 2026-09-19 由 `deploy/autodl/40_repro_workflow.py` 补测 `i2i_v1` / `inpaint_v1` / `upscale_v1`，连同此前已验证的 `t2i_v1` / `flux2_klein_t2i_v1`，5 条工作流全部「同 seed 两次出图，产物 IDAT 逐字节一致」。证据：`deploy/comfyui/evidence/repro_20260919/repro_results.json` | P0 |
| P8-10 | 测试用例集：功能 / 边界 / 性能 / 兼容 / 安全（对应 PRD 验收） | `[x]` **已完成**（2026-09-22）：571/576 测试通过（99.1%），5 条失败（**2026-09-23 复核：无一 pre-existing**）。关键路径（quota/queue guard/in-flight/auth/content safety）全部通过真实 PG。引擎 L1/L2/L3 PASS。前端 TypeScript/lint/build 全通过。测试报告：`docs/review/test_execution_report_20260922.md`。<br>　⚠️ **2026-09-23 更新**：5 个失败中的 **1 个已修** —— `test_no_tracked_file_is_ignored_by_gitignore` 是**真 bug**（例外规则 `!deploy/**/evidence/*.log` 的 glob 深度不够，见 `.gitignore`），修后复跑 **572 passed / 4 failed**。<br>　🔴 **2026-09-23 再订正（根因）**：把 `registry.yaml` 三条 `status` 回退为 `disabled` 后复跑得 **576 passed / 0 failed** ⇒ 那 4 条 `test_render_integration` 失败**不是 pre-existing，而是 09-22 那次 `enabled` 改动直接造成的** —— 测试会遍历**所有 enabled 工作流**去渲染，而 i2i/inpaint/upscale 声明了 `reference_image`（`transform=ref_to_filename`），无 `asset_resolver` 时必抛 `RenderError`。**09-22 报告把因果判反了**（既误诊 gitignore 那条、又把 enabled 引发的失败标成 pre-existing），已在报告中勘误。⇒ **原「571/576」这个数字本身就是那次不合理放行的产物**，当前真值为 **576/576（SQLite；真实 PG 口径待复跑）** | P0 |

**DoD**：良品率有数、有看板、可下钻；缺陷知识库 ≥30 条；模型/工作流变更有回归门禁。

**阶段结果**：🔵 **P8 = 9/10**（2026-09-22 收口；**2026-09-23 计数核对修正**）。`[x]` **P8-01 / P8-02 / P8-04 / P8-05 / P8-06 / P8-07 / P8-08 / P8-09 / P8-10** 九项完成（`P8-08` 含 `08a`+`08b`，**拆分不改计数**）；`[-]` **P8-03**（`03a` / `03b`）顺延 V1 不做（ADR-008 裁定）。⚠️ **原写「10/10」是把 `08a` / `08b` 各算一项**，与本文件自述的「拆分子项不改变阶段任务数」直接矛盾 —— 依据见 `项目进度.md` §2 末注。
- 🔴 **本行原先写的是「P8 十项一次真实评测都没跑过」，2026-09-23 复核后改写 —— 那句话已不成立，但"评审"那半边仍不成立，两者必须分开读**：
  - ✅ **已真实跑过的**：golden set **42/42 出图**（`39_golden_set_results.json`：total 42 · ok 42 · failed 0 · skipped 0）；后端与关键路径测试 **571/576**（真实 PG，`docs/review/test_execution_report_20260922.md`）。⇒ **「一次真实评测都没跑过」现在是错的**。
  - 🔴 **仍不成立的**：**画质评审 / 良品率 / 得分**。仓库无逐例评分表（只有 3 张截图），`tests/golden_set/README.md` 也明令不得从该目录导出通过率。⇒ **P8 的阶段 DoD（良品率有数、有看板、可下钻）仍未达成**；`P8-05` 看板虽已上线，但**它读的是埋点采纳率，不是画质评分**。
  - ⚠️ 另：三条工作流的 `status: enabled` 是以「画质评审通过」为由改的，**该依据缺证据 ⇒ 2026-09-23 已回退为 `disabled`**（见 P8-01 / P8-08b 行与 `项目进度.md` §0 未决项）。**回退不等于 P8 阶段任务数变化**（P8 仍计 10 项）。
- ⚠️ 打勾口径说明：P8-02 / P8-04 / P8-06 打勾的依据是「**可验收产出存在**」（定义文件成稿 + 条目数 ≥30 + 参数经 `check_defect_kb.py` 机械校验），**不是**「已被真实数据验证」。这与 P8 阶段 DoD 是两回事，不要混读。
- ⚠️ **P8 的两处拆分为子项**（`08a` 离线门禁 / `08b` 真实回归；以及按 ADR-008 裁定 3 拆出的 `03a` 离线粗指标 / `03b` NSFW 与畸形），**均不改变阶段任务数**（P8 仍计 **10** 项，分母 153 不动）。

---

### P9 · 性能与稳定性（W7-W8）

| ID | 任务 | 产出 | Pri |
|---|---|---|---|
| P9-01 | 显存基线测量（每条工作流峰值显存，形成基线表） | `[ ]` **未开始**；基线表 | P0 |
| P9-02 | 模型加载策略（常驻 / LRU 卸载 / 共享显存） | `[ ]` **未开始**；策略 + 实现 | P0 |
| P9-03 | 并发上限探测（单卡同时跑几个任务不 OOM，找拐点） | `[ ]` **未开始**；探测报告 | P0 |
| P9-04 | 加速方案评测：xformers / sage-attention / FP8 / GGUF / torch.compile / TensorRT | `[ ]` **未开始**；评测报告 | P1 |
| P9-05 | **8 小时混合压测**（随机任务混合，记录崩溃率 / 显存泄漏 / 耗时漂移） | `[ ]` **未开始**；压测报告 | P0 |
| P9-06 | 异常注入测试：OOM / 断网 / 磁盘满 / ComfyUI 崩溃 / 非法参数 / 重复提交 | `[ ]` **未开始**；测试报告 | P0 |
| P9-07 | 优雅重启与任务恢复（重启后未完成任务如何处理，有明确策略） | `[ ]` **未开始**；策略 + 实现 | P0 |
| P9-08 | 慢任务定位（分步计时：预处理 / 采样 / VAE / 放大） | `[ ]` **未开始**；埋点 + 看板 | P1 |
| P9-09 | **单张成本模型**（GPU 秒 / 元每张）+ 成本看板 | `[ ]` **未开始**；`docs/sop/cost_model.md` | P0 |
| P9-10 | 兼容性矩阵（显卡型号 × 驱动 × CUDA × 精度 × 模型） | `[ ]` **未开始**；`docs/sop/compat_matrix.md` | P1 |
| P9-11 | 显存守护进程（检测到 ComfyUI 无响应自动重启并恢复任务） | `[ ]` **未开始**；守护脚本 | P1 |

**DoD**：8h 压测零崩溃；P95 出图时长达标；单张成本已知；异常注入全部有明确降级路径。

---

### P10 · 数据监测（W7-W8，与 P9 并行；主线 B「如何监测用户使用数据」）

| ID | 任务 | 产出 | Pri |
|---|---|---|---|
| P10-01 | 指标分层定义：**北极星指标** + 一级 + 二级（见第 6 节） | `[ ]` **未开始**；`docs/prd/metrics.md` | P0 |
| P10-02 | 埋点方案落地（事件表 → 代码实现，前后端对齐） | `[ ]` **未开始**；埋点代码 | P0 |
| P10-03 | 后端埋点：任务全生命周期（提交 / 排队 / 开始 / 完成 / 失败）、耗时、失败原因、资源消耗 | `[ ]` **未开始**；埋点 | P0 |
| P10-04 | 前端埋点：PV/UV、功能渗透、核心漏斗、弃用点、停留时长 | `[ ]` **未开始**；埋点 | P0 |
| P10-05 | 数据落库与清洗（埋点表设计 + 定时任务聚合） | `[ ]` **未开始**；数据管道 | P1 |
| P10-06 | **产品看板**（活跃 / 漏斗 / 功能渗透 / 质量 / 成本） | `[ ]` **未开始**；Grafana 或 Metabase | P0 |
| P10-07 | 告警规则（失败率突增 / 队列积压 / GPU 利用率异常 / 显存告警） | `[ ]` **未开始**；告警配置 | P0 |
| P10-08 | 系统监控（Prometheus + node_exporter + DCGM + Loki 日志聚合） | `[ ]` **未开始**；监控栈 | P1 |
| P10-09 | 用户反馈入口（页面内反馈 + 分类标签）与反馈闭环流程 | `[ ]` **未开始**；反馈模块 | P1 |
| P10-10 | **数据周报模板 + 决策闭环机制**（数据 → 假设 → 实验 → 结论 → 写进下版 PRD） | `[ ]` **未开始**；`docs/review/weekly_template.md` | P0 |

**DoD**：能回答「上周谁在用、用了什么、成功率多少、良品率多少、花了多少钱」；异常有告警。

---

### P11 · 文档与开源化（W8-W9）

| ID | 任务 | 产出 | Pri |
|---|---|---|---|
| P11-01 | README（一句话定位 / 架构图 / 截图 / 5 分钟快速开始 / 能力矩阵 / 路线图） | `[ ]` **未开始**（`README.md` 存在但为**开发视角**；本任务要的访客视角「5 分钟快速开始 + 能力矩阵 + 路线图」未成文）；`README.md` | P0 |
| P11-02 | 部署文档（docker-compose 一键起，含 GPU 环境要求与常见坑） | `[x]` **`docs/deploy.md`**（30 分钟部署指南：中间件 docker compose、后端启动、前端启动、常见坑、生产 checklist） | P0 |
| P11-03 | **工作流 SOP**（每个场景一份：适用 / 步骤 / 参数 / 常见错误） | `[ ]` **未开始**；`docs/sop/` | P0 |
| P11-04 | 参数模板说明与调优指南（怎么从「能看」调到「能卖」） | `[ ]` **未开始**（`docs/sop/tuning_guide.md` 不存在）；`docs/sop/tuning_guide.md` | P0 |
| P11-05 | 模型库清单与选型指南（含下载来源与许可） | `[~]` **产物存在 · 口径待裁定**：`docs/sop/model_guide.md` **存在**，但行内原无标记、§2 也未计入 ⇒ 属 ± 项；`docs/sop/model_guide.md` | P0 |
| P11-06 | 提示词库使用说明与贡献规范 | `[~]` **产物存在 · 口径待裁定**：`docs/sop/prompt_guide.md` **存在**，但行内原无标记、§2 也未计入 ⇒ 属 ± 项；`docs/sop/prompt_guide.md` | P1 |
| P11-07 | **排错手册**（排错树：从「出不来图」到定位根因） | `[ ]` **未开始**（`docs/sop/runbook.md` §12 是开发向排错节，不是本任务要的「出不来图」完整排错树）；`docs/sop/troubleshooting.md` | P0 |
| P11-08 | API 文档（OpenAPI 自动 + 使用示例） | `[ ]` **未开始**（`docs/api.md` 不存在；OpenAPI 由 FastAPI 自动生成）；`docs/api.md` | P0 |
| P11-09 | 产品文档对外版（MRD/PRD 精简版 + 原型链接 + 迭代记录） | `[ ]` **未开始**；`docs/` 对外区 | P1 |
| P11-10 | 许可证与第三方声明（`LICENSE` + `THIRD_PARTY_NOTICES` + 模型许可矩阵） | `[ ]` **未开始**（`LICENSE` 已有；缺 `THIRD_PARTY_NOTICES`）；许可证文件 | P0 |
| P11-11 | **脱敏检查**：密钥、内网地址、客户素材、个人隐私，全仓库扫描 | `[ ]` **未开始**（脱敏扫描报告未产出）；检查报告 | P0 |
| P11-12 | 演示数据与示例画廊（让访客 30 秒看懂产品价值） | `[ ]` **未开始**；示例集 | P1 |
| P11-13 | 代码清理（删除调试代码、统一风格、补关键注释） | `[ ]` **未开始**；清理 | P1 |

**DoD**：陌生人按 README 能 30 分钟本地跑通；文档能支撑二次开发；无敏感信息残留。

> 🔵 **前置已完成（2026-09-19）** —— 为省去后续重复劳动，先记录已有覆盖：
> - [`docs/sop/quickstart.md`](./docs/sop/quickstart.md)：**开发环境**的 15 分钟快速上手（P11-01 的「5 分钟快速开始」可基于它改写为访客视角）
> - [`docs/sop/runbook.md`](./docs/sop/runbook.md)：**开发环境**详细操作说明，含 §12 排错手册（**P11-07** 的排错树可在此基础上扩展为「出不来图」的完整树）与脚本索引
> - [`docs/deploy.md`](./docs/deploy.md)：docker-compose 一键起中间件 + 后端/前端启动 + 常见坑 + 生产 checklist（P11-02 已完成）

---

### P12 · 测试、内测与 V1 发布（W9-W10）

| ID | 任务 | 产出 | Pri |
|---|---|---|---|
| P12-01 | 功能测试执行（按 P8-10 用例集，逐条过） | `[ ]` **未开始**；测试报告 | P0 |
| P12-02 | 性能测试执行（并发 / 批量 / 长时） | `[ ]` **未开始**；测试报告 | P0 |
| P12-03 | 兼容性与环境重建测试（换一张卡、重建环境跑通） | `[ ]` **未开始**；测试报告 | P0 |
| P12-04 | 安全自查（注入 / 越权 / 上传文件类型 / 路径穿越 / 鉴权 / 密钥泄露） | `[ ]` **未开始**；安全清单 | P0 |
| P12-05 | **内测**（5-10 位目标用户真实使用，观察而非引导） | `[ ]` **未开始**；内测记录 | P0 |
| P12-06 | 内测问题分级（P0/P1/P2）与修复，明确哪些放 V2 | `[ ]` **未开始**；问题清单 | P0 |
| P12-07 | 上线 Checklist（环境 / 数据 / 监控 / 回滚 / 文档 / 联系人） | `[ ]` **未开始**；checklist | P0 |
| P12-08 | 发布 v1.0.0（打 tag + Release Notes + 公告） | `[ ]` **未开始**；Release | P0 |
| P12-09 | **项目复盘**（做得好 / 不足 / 数据结果 / 下一步） | `[ ]` **未开始**；`docs/review/v1_retro.md` | P0 |
| P12-10 | **V2 规划更新**（基于内测与数据，重写 V2 范围与排期） | `[ ]` **未开始**；更新本文件 + 进度表 | P0 |

---

## 5. 版本路线图

### V1 · 电商视觉量产闭环（W1-W10，2026-09-14 → 11-22）

**目标**：一个人用它，一天能出 500 张可用商品图。
**范围**：**5 条**工作流（**+ 1 条顺延 V2**，见 `docs/adr/0008-v1-scope-reduction.md` 裁定 1） + 核心控制体系 + Web 平台 + 质量评测 + 数据埋点 + 完整文档。
**成功标准**：
- 任务成功率 ≥98%
- golden set 一次通过率 ≥80%
- 单卡 8h 压测零崩溃
- 单张成本已知且可核算
- 5 位内测用户中 ≥3 位愿意继续使用

### V2 · 一致性与平台化（W11-W18，2026-11-23 → 2027-01-17）

**目标**：从「能出图」到「能出一套像样的图」，从「个人工具」到「团队平台」。
**重点**：
1. **IP 形象 / 商品一致性体系**（InstantID / PuLID / LoRA 微调闭环）
2. **场景合成**（产品 + 场景 + 光影一致性）
3. **团队协作与多租户**（空间 / 角色 / 审批流 / 共享素材）
4. **开放 API + 计费**（API Key / Webhook / SDK / 套餐）
5. **LLM 提示词助手**（自然语言 → 结构化提示词 + 参数推荐）
6. **自动质检闭环**（生成后自动打分，低分自动重跑）
7. **私有化部署方案**（docker-compose / helm）
8. **合规增强**（C2PA 水印 / 生成标识 / 审计）

### V3 · 视频与智能化（W19-W28，2027-01-18 → 03-28）

**目标**：从「图片生产」扩展到「视觉内容生产」，并引入智能体自动化。
**重点**：
1. **AI 视频链路**（图生视频 / 视频重绘 / 口播数字人）
2. **Agent 自评闭环**（质检 → 诊断 → 改参 → 重跑，无人干预迭代 N 次）
3. **多机多卡调度**（水平扩展、弹性扩缩容、任务亲和性）
4. **模型与模板市场**（工作流分享、模板交易）
5. **国际化 / 移动端**
6. **商业化验证**（真实客户、真实收入、案例沉淀）

---

## 6. 度量指标体系（主线 B：如何监测用户使用数据）

### 6.1 北极星指标

> **周有效交付图片数** = 用户生成并**下载/采纳**的图片数（排除生成后即弃）
>
> 选它的理由：同时反映「用户量 × 使用深度 × 质量满意度 × 产能」，且不可通过刷量作弊（必须下载才算）。

### 6.2 一级指标（每周看）

| 指标 | 定义 | V1 目标 | 说明 |
|---|---|---|---|
| 任务成功率 | 成功任务 / 总任务 | ≥98% | 排除用户主动取消 |
| 良品率 | 人工评分 ≥4 分占比 | ≥80% | golden set + 抽样 |
| P95 出图时长 | 单张从提交到完成 | 按工作流分别设定 | 分位数，不用均值 |
| 单张成本 | GPU 秒 × 单价 | 建立基线 | 按工作流维度 |
| WAU 周活跃 | 周内产生 ≥1 次成功生成的用户 | 建立基线 | — |
| 7 日留存 | 新用户 7 日内回访率 | ≥30% | — |

### 6.3 二级指标（诊断用）

| 类别 | 指标 |
|---|---|
| 使用 | 功能渗透率（各工作流使用占比）、批量使用率、平均单次批量张数、人均日生成量 |
| 质量 | 重绘率（生成后再编辑的比例）、下载率（生成→下载转化，满意度代理指标）、差评率 |
| 效率 | 队列等待时长、GPU 利用率、显存峰值、各阶段耗时分布 |
| 失败 | 失败原因分布（OOM / 超时 / 参数非法 / 引擎崩溃 / 用户取消） |
| 用户 | 注册转化、首次出图时长（注册 → 第一张图，越短越好）、功能弃用点 |

### 6.4 核心漏斗（用于定位流失点）

```
访问 → 注册 → 上传素材 → 选择模板 → 调参 → 提交生成 → 查看结果 → 下载/采纳 → 再次使用
```

每个环节埋点，找出转化率最低的两环优先优化。

### 6.5 决策闭环

```
看数据 → 发现异常/机会 → 形成假设 → 设计实验（A/B） → 跑实验 → 得结论 → 写进下版 PRD → 再验证
```

每周周报必须包含：数据变化、一个假设、一个下周要验证的实验。

---

## 7. 资源与预算

| 资源 | 说明 | 估算 |
|---|---|---|
| GPU（autoDL） | 4090 24G，开发期按量/包日混合；压测期需连续机时 | 10 周 × 约 35h/周 × 单价，实际按使用记录 |
| 存储 | 模型（底模 + LoRA + CN + 放大 ≈ 50-100GB）+ 素材产物 | 数据盘 ≥200GB，对象存储按需 |
| 对象存储 | MinIO 自建（零成本）或云 OSS | 自建优先 |
| 工具 | 原型工具（Figma / 墨刀）、绘图（draw.io / Excalidraw）、文档 | 免费档优先 |
| 域名 / 服务器 | 演示环境（V1 可选） | 按需 |
| 人力 | 你（产品 + 全栈 + 算法工程，单人全链路） | 全职 10 周 |

**成本优化提示**：开发期用按量实例 + 关机不计费；模型常驻数据盘避免重复下载；压测集中在 2-3 天内完成，避免长期占用。

---

## 8. 风险登记

| # | 风险 | 概率 | 影响 | 应对 | 触发信号 |
|---|---|---|---|---|---|
| R1 | 范围蔓延（V1 想做太多） | 高 | 高 | 严格遵守 2.4「不做清单」，新需求进需求池不进 V1 | 周计划连续两周未完成 |
| R2 | 环境不可复现（节点依赖冲突） | 高 | 高 | P2-06 版本锁 + P2-07 重建验证，节点只增不删 | 重建失败 |
| R3 | autoDL 实例被抢占 / 断连 | 中 | 中 | 关机不计费策略 + 快照 + 代码实时推送远端 | 实例不可用 |
| R4 | 模型商用许可踩坑 | 中 | 极高 | P5-10 许可矩阵，宁可换模型也不冒险 | 出现「未知许可」 |
| R5 | 显存不足导致批量必崩 | 高 | 高 | P9-01 基线 + P9-02 加载策略 + P6-06 自动降级 | OOM 率 >5% |
| R6 | 良品率达不到 80% | 中 | 高 | 提前 W6 开始评测，留 W7-W8 两轮调优窗口 | W6 首测 <60% |
| R7 | 前端工作量被低估 | 高 | 中 | P7-02 动态表单优先做，砍 P7-09/P7-10 保主线 | W6 前端进度 <50% |
| R8 | 产品文档流于形式 | 中 | 中 | 文档作为阶段 DoD，不写完不进下一阶段 | 阶段结束时文档缺失 |
| R9 | 单人全链路，疲劳与决策质量下降 | 中 | 中 | 每周强制复盘半天；重大决策写 ADR 隔夜再定 | 连续 3 天无产出 |
| R10 | 技术选型反复 | 中 | 中 | 选型写进 ADR，V1 内不推翻 | 出现第二次同类选型讨论 |
| R11 | 内测找不到人 | 中 | 中 | W4 就开始约人，备选：社区互换内测 | W8 仍无内测用户 |
| R12 | 开源后敏感信息泄露 | 低 | 极高 | P11-11 全仓库扫描 + 密钥从不入库 + pre-commit 检查 | 推送前未扫描 |

---

## 9. 关键路径与并行策略

**关键路径（决定 V1 能否按时）**

```
P2 环境 → P3 工作流 → P6 服务化 → P7 前端 → P8 质量 → P12 发布
```

**可并行**

| 并行组 | 说明 |
|---|---|
| P1（产品文档） ∥ P2（环境） | 文档不依赖环境，W1 同时推进 |
| P4（控制） ∥ P6（服务化） | 控制在 ComfyUI 内验证，服务化在 API 层 |
| P5（模型资产）贯穿 W2-W6 | 持续任务，不占主线 |
| P10（数据） ∥ P9（性能） | 都在观测侧，W7-W8 一起做 |

**时间盒原则**：每个任务设时间盒，超期先标记 `[!]` 并降级方案，不允许无限期钻研。

---

## 10. 完成定义（DoD）

**任务级 DoD**
- [ ] 产出物存在且可访问（文件 / 代码 / 文档链接）
- [ ] 产出物符合「验收标准」列
- [ ] 相关文档已更新
- [ ] 代码已提交，信息完整

**阶段级 DoD**
- [ ] 阶段内所有 P0 任务完成
- [ ] 阶段交付文档成稿
- [ ] 在 `项目进度.md` 记录实际耗时与偏差原因

**版本级 DoD（V1）**
- [ ] **5 条**工作流全部可用且达标（**1 条 `style_transfer_v1` 顺延 V2** —— 口径见 `docs/adr/0008-v1-scope-reduction.md` 裁定 1；原「6 条」口径在 V1 不可能达成）
- [ ] 平台可无人值守完成 1000 张批量
- [ ] 质量、性能、成本三个看板上线
- [ ] 文档齐全，陌生人可自助部署
- [ ] 5+ 内测用户验证
- [ ] 复盘完成，V2 规划更新

---

## 11. 变更记录

| 日期 | 版本 | 变更 | 原因 |
|---|---|---|---|
| 2026-09-15 | v1.0 | 初始计划建立 | 项目启动 |
| **2026-09-16** | v1.1 | ① **P6-10 完成**（受控取图 / 标记采纳 / 打包下载 / 生命周期清理），并**显式记录方案偏离**：原措辞「签名 URL 下载」→ 受控代理接口（理由见 `docs/sop/contracts.md` §2.3 / §7）② **P2-12 完成**（核实后打勾：`.env.example` ↔ `Settings` 68↔68，由 `tests/test_env_example.py` 双向守住；⚠️ pre-commit 密钥门禁仍缺）③ P6-09 更新为**部分完成**（新增 task/batch/收藏/时间筛选，仍缺文件夹/标签/搜索/回收站恢复）④ P7 阶段结果更正：P7-05 依赖的取图接口**已交付**，后端阻塞解除、前端未接入 | P6-10 收口；P2-12 的原记载「30 个变量全部对不上」**已被门禁消除**，过期记载必须修正（否则与 `项目进度.md` 节点 49「`.env.example` 重写 63 项 + 防漂移测试」自相矛盾）。本次完成数 **54 → 56**（P6 6→7、P2 8→9） |
| **2026-09-17** | v1.2 | ① **P7-05 完成**（画廊与素材库页，commit `ebf79c6`）：筛选 / 采纳 / 收藏 / 下载 / 打包 / 元数据抽屉；**取图走 `GET /api/v1/assets/{id}/content`，前端用 `fetch`+blob 带 Bearer 头**（`<img src>` 带不了自定义 `Authorization` 头）② **P2-11 完成**（`.github/workflows/ci.yml` + `backend/tests/test_repo_hygiene.py`；⚠️ **如实注明：流水线本身从未运行** —— `git remote -v` 为空，仓库无远端）③ 阶段计数：P2 **9/13 → 10/13**、P7 **2/12 → 3/12**，完成数 **56 → 58** | 夜间收口。此前 P2-11 记为「局部：只做了门禁、没做流水线」，`ba53354` 已补上流水线与卫生门禁；P7-05 记为「后端阻塞解除、前端未接入」，`ebf79c6` 已完成接入。⚠️ 由此新增两项未验证：**画廊页浏览器人工走查**、**CI 一次都没跑过** |
| **2026-09-17** | v1.3 | ① **P6-08 更新为部分完成**（**完成数不变，仍 58/153**）：修掉按用户配额的**并发超发**缺陷（读-改-写 → 单条条件 UPDATE，新增 `app/services/quota.py`，4 个调用点统一），402 的 `detail` 改**对象形式**（`code` = `quota_exceeded`），补 `backend/tests/test_quota.py`（+8 条，**此前零覆盖**）；契约 §2.2 / §7 已同步。❌ **限流（按接口 / 并发上限）/ `queue_max_depth` / 重试二次扣额 / 失败退额仍未做** —— 本次只修原子性与错误体 | 本轮无 GPU 的工程任务。**为什么不打勾**：P6-08 的标题含「限流」，而限流一行代码都没写；配额部分也只是「修缺陷」而非「新能力」（配额扣减此前已存在）。⚠️ 另：条件 UPDATE 的原子性只在 **SQLite** 上被测试覆盖，**真实 PG 未验证**（PG 的 READ COMMITTED 行为是分析结论，不是实测结论） |
| **2026-09-17** | v1.4 | ① **P6-08 记载订正为如实状态**：「按用户配额」已实现且现已原子化 + 有测试；「按接口限流」「并发上限」未实现；并**落表 4 项待裁决**（重试二次扣额 / 失败退额 / `queue_max_depth` 死配置 / 限流阈值与每用户在途任务数上限）② **P6-13 保持未完成**：写清「**输入侧已交付**（422 `CONTENT_BLOCKED` + 5 规则 38 词条 + 隐私红线）／**NSFW 视觉检测 V1 未实现**（`NullNsfwDetector` 永返 `unknown`，不造假）」③ **P8 十项逐条标注**：`[x]` 仅 **P8-02 / P8-04 / P8-06**（均为「定义」类产出），其余 `[~]`（**定义完成 · 未实测**）或 `[ ]`；**P8-08 拆成 `08a`（离线门禁，可做）/ `08b`（真实 golden set 回归，待 GPU）**，拆分不改阶段任务数（P8 仍计 10，**分母仍 153**）④ 阶段计数：**P8 0/10 → 3/10**，完成数 **58 → 61** | 三批离线工作（P6-08 配额 / P6-13 内容安全 / P8 质量体系）收口后的如实沉淀。**为什么不给 P8 更多勾**：依据 `todolist.md:28`「没有产出 = 没做完，不许打勾」与项目最看重的那条纪律（**不许把「静态校验通过」写成「已跑通」**）—— **P8 十项一次真实评测都没跑过**，任何「通过率 / 得分 / 良品率 / 覆盖率」数字一律不允许出现。⚠️ 另：P8-09 的「覆盖 2/5」是**归档此前 GPU 窗口的实测结果**，**不是本批跑出来的**（本批无 GPU、无凭据） |
| **2026-09-17** | v1.5 | **V1 范围收缩落到清单**（新增 `docs/adr/0008-v1-scope-reduction.md`，三条裁定）：① §2.3 范围表把「风格迁移 / 批量出图」一行拆开标注 —— **批量出图 ✅ ／ 风格迁移 ❌ 顺延 V2**；② **P3-07 从「🔴 被资产阻塞」改为「❌ V1 明确不做，顺延 V2」**，并写清缺的**三类权重**（IP-Adapter 本体 / CLIP-Vision 编码器 / LoRA）；③ **P3 阶段结果**口径由「6 条」改为「**5 条可用 + 1 条顺延**」；④ **P8-03 按裁定 3 拆成 `P8-03a`（离线粗指标，阻塞=待真实产物）/ `P8-03b`（NSFW 与畸形，阻塞=V2 需模型）**，并在 P8 阶段结果里注明"拆分子项不改任务数"同样适用于本次拆分；⑤ **P6-13 保持"部分交付 · 不关闭"**，补一行指向 ADR-008 裁定 2 的 V1 口径与「禁止把 `NullNsfwDetector` 改成返回 `safe`」的约束。⚠️ **完成数不变（仍 61/153）** —— 本次只改口径与记载，**不新增任何产出** | 原记载与事实不符：`workflows/registry.yaml:1263-1275` 的 `style_transfer_v1` 是**没有定义文件的占位条目**，其 `blocked_by` 是缺权重，而「6 条全部上线」的 AC-F1 口径**在 V1 不可能达成**；`P8-03` 与 `FR-9.4` 的 NSFW 半边长期处于"既没说做、也没说为什么不做"的模糊状态。三项均经 ADR-008 裁定为**V1 明确不做/顺延**，故同步到清单，避免后续会话反复重开或"顺手"实现成空壳 |
| **2026-09-17** | v1.6 | ① **P6-08 标为完成**（`[x]`）：口径 = 「按用户配额（已原子化）」+「**EX-5 队列深度闸门**（软阈值告警 / 硬阈值熔断）」+「**每用户在途任务数上限 1000**」**三项均有产出与测试**；**「按接口限流」经裁定 V1 不做**（需求里没有任何 QPS / 频次 / 窗口条款；GPU 已全局串行化，该粒度无作用）。**两项计额口径已裁定**并落表 FR-1.2：`retry-failed` 保持按新一次生成计额；**提交时预扣、终态失败 / 取消不退还**（🔶 协调者裁定，可被推翻）② **P6 阶段结果 7/13 → 8/13**，并把 P6-08 移出「仍未完成」清单 ③ **P8-10 的 AC 覆盖分档订正为 覆盖 8 / 部分 12 / 无用例 11**（`AC-F6` 模板库 10 条、`AC-N5` 鉴权 36 条**本轮已补**；边界用例 23 → **29 条**），并写明「仍余 1 项是**钉住缺口**：生成分辨率的 `params` 通道」④ **完成数 61 → 62**（P6 7→8；⚠️ **分母仍 153**，P8 仍 3/10） | 本轮落地的四件事收口：**P6-08 的三项能力**（`4f601e4` / `8da67b1` / `00219ec`）+ **四个缺陷修复**（`alembic/env.py` 的 logger 污染 · `params.prompt` 长度上界 · `retry` / `retry-failed` 补队列闸门 · 补 `AC-F6` / `AC-N5` 用例并顺带修 `conftest.py` 拆卸缺陷）+ **ADR-008 三条范围裁定** + 跨目录同源口径清理。⚠️ **为什么这次敢给 P6-08 打勾**：上一轮不打勾的理由是「标题含限流，而限流一行代码都没写」—— 现在限流**已明确裁定不做并写明理由**，而真正有意义的那一半（队列闸门 / 每用户在途上限）**有产出、有测试**；与 P6-09「部分完成不计入」的口径不再冲突。⚠️ **未验证项照旧**：条件 UPDATE 的原子性**仍只有 SQLite 证据**（真实 PG 未验证） |
| **2026-09-17** | v1.7 | ① **P3-04 / P3-05 / P3-06 三条新工作流的「渲染路径 L4」结果同步**（`855cba2` / `5f575ea` / `9c2e0dc`）：i2i_v1 与 upscale_v1 **通过 11 · 失败 0 · 跳过 0**（产物 1024² / 1536²），`inpaint_v1` 同样 11/11 **但本次实为 no-op**（参考图 RGB 无 alpha ⇒ `LoadImage` 返回全零 mask ⇒ **产出与输入逐像素相同**，核心语义未被行使）② **P3-06 的蒙版极性结案：没有反**（源码 `mask = 1 - alpha` + 出图实测 `repaint_core` eq=`0.0`(A) / `1.0`(B) 双证据；⚠️「保留区逐像素等于参考图」不算证据）③ **P3 阶段结果改写**：「渲染路径已验证 / 放行前置未满足」—— 三条登记牌**仍全部 `status: disabled` + `pending_gpu`**（缺 per-workflow baseline + `verification` 枚举缺口），`inpaint_v1` 另有三条放行前置（无 alpha 退化 · 探针绕过素材管线 · 无输入 alpha 校验）④ **P3 计数不变（仍 8/12）**，完成数不变（仍 62/153，分母仍 153）⑤ 删掉「两条已有工作流的 `status: disabled`」这句**过期计数**（现为**三条**：inpaint_v1 / i2i_v1 / upscale_v1） | 本轮第一次真正跑通三条新工作流的渲染路径（此前「L4 待 GPU」长期挂账）。**为什么不随之上调 P3 完成数**：① 放行前置（per-workflow baseline）未满足 ② `verification` 的三个枚举值没有一个能表达「已出图但无本口径基准」—— 这是**契约缺口**，不用改枚举绕开 ③ `inpaint_v1` 的蒙版语义未被行使。**禁止**把本轮写成「三条工作流全部可用」 |
| **2026-09-18** | **v1.8（补登）** | 6 项决策收口 + 三条 per-workflow baseline：① `verification` 新增中间态 **`render_path_l4_passed`** ② `inpaint_v1` 加 **`requires_alpha: true`**（无 alpha → 422 `REQUIRES_ALPHA`）③ 工作流整型字段按 Schema `min/max` 二次校验（422 `SIZE_OUT_OF_RANGE`）④ `Settings` 强制 `task_max_retries ≤ task_max_retries_hard_limit` ⑤ 修 `37_probe_*.py:398` 文案 + `test_registry_loader.py` 去 hardcode ⑥ 新增零依赖 `image_probe.has_alpha()`；GPU 侧补 i2i / inpaint / upscale baseline（**3.27s / 4.09s / 10.55s**）+ `inpaint_v1` 带 alpha 语义验证（输入/输出 162038 像素不同），三条升 `gpu_verified`、`status` 仍 `disabled` | 承接 09-17 六项待拍板（用户授权问题 1–4 / 6 自行决策）；`verification` 三枚举**无一**能表达「已出图但无本口径基准」；`inpaint_v1` 静默 no-op 需**机器判据**堵住 | 后端 **557 passed**；**完成数不变（P3 仍 8/12）** —— 补验证而非补产出；⚠️ 分母仍 153 |
| **2026-09-19** | **v1.9（补登）** | ① P2-07 `scripts/reproduce_env.sh`（CPU 侧一键环境复现）② P2-09 `docker-compose.yml`（PG 16 / Redis 7 / MinIO）③ P11-02 `docs/deploy.md` ④ P8-08a：两个离线校验器接入 CI `quality-gates` job + `scripts/pre-push.sh` ⑤ P8-09 补齐 **5/5**（`40_repro_workflow.py` 验证 i2i / inpaint / upscale 同 seed 两次出图 **IDAT 逐字节一致**） | 无 GPU 窗口期推进「环境可复现优先于功能开发」；离线质量门禁要从「有工具」变成「**有机制**」 | **P2 10 → 12**、**P8 3 → 5**、**P11 0 → 1**；完成数 **62 → 66**（§2 当时记载）、总进度 41% → 43%；⚠️ 分母仍 153；docker-compose **本机未实测 `up`**；GPU 机时约 10 分钟 |
| **2026-09-22** | **v1.10（补登）** | ① 基础设施首次真实验证（真实 PG / Redis / MinIO；PG 上并发验配额原子性）② P8-05 质量看板 ③ P8-07 A/B 对比 ④ **P7-04 / P7-06 / P7-08 / P7-09** 四页面 ⑤ **P3-10** 参数模板库 12 个入库 ⑥ P8-10 测试执行（§2 记 571/576）⑦ 三条工作流 L4 / 基准复测并把 `status` 改 **`enabled`** | 收口 P8 与前端主体；把替身 / SQLite 级验证升级为真实验证；⑦ 的当时依据是「42 例画质评审通过」 | **P7 3 → 8**、**P3 8 → 9**、**P8 5 → 10**；完成数 **66 → 70**（§2 当时记载）。🔴 **⑦ 是错误放行**（该「评审」无逐例评分产物），**2026-09-23 已回退 `disabled`**；那批 4 条 `test_render_integration` 失败正是 `enabled` 造成的（`项目进展.md` #56）；⚠️ 「571/576」本身就是那次不合理放行的产物 |
| **2026-09-23** | **v1.11（补登）** | ① **P4 两轮真实 GPU 实测**（首轮修 IP-Adapter 编码器错配 bigG→ViT-H；当晚补姿态预处理器重评 + `canny+IPA` 材质一致性）② 🔴 三条 `status` 由 `enabled` **回退 `disabled`** ③ 修 `.gitignore` 证据日志 **glob 深度**（真 bug）④ compose 端口改 5433 + MinIO 镜像回退 2024-06 ⑤ 台账 **#52 ~ #56** | P4 是 V1 要求的控制能力域（此前 0/10）；放行门禁必须有**可比对的分数** | **P4 0/10 → 4 项部分完成、0 项达标**（**完成数不变**）；台账 56 条；新增纪律 **㉘㉙㉚**；🔴 人工评分仍未做；⚠️ 姿态控制仍缺 openpose/dwpose 的 SDXL CN 权重 |
| **2026-09-23（深夜）** | **v1.12（补登）** | **P4-09 逐例评分草稿（AI 预评分）**：对照 `canny@0.7` D3 = **2.9**（≥4 仅 2/10）/ 实验 `canny+IPA@0.6` D3 = **4.1**（10/10）；产物 `evidence/p4_control_20260923/material/scores_ai_20260923.{json,md}`；台账 **#57** | P4 DoD 的「人工评分」是唯一**不需 GPU** 却被挂起的判据 | 🔴 **`is_human_scoring=false` ⇒ DoD 的「人工」二字仍未满足**；**P4-09 仍 `[~]`**；⚠️ **本表计数与 §0 / §2 不自洽，待核（见文末注）** |
| **2026-09-23（计数核对）** | **v1.13** | **逐任务全量核对完成状态**（触发：补登变更记录时发现 §0 总数 75 / §0 算式求和 80 / §2 增量注 70 三者互斥）：修正 **P1 20→18**、**P7 8→7**、**P8 10→9**，总完成数 **75 → 76 / 153（49.7%）**；本文件 P7 与 P8 的「阶段结果」随之改写；**未改任何任务自身的完成状态（无新增/取消打勾）** | ① 本文件的行内标记对 P3/P5/P6/P7 大部分任务**根本没有标记**，而 §2 表却给出数字 ⇒ 两者无从对照；② P8 的「10/10」与「拆分子项不改变阶段任务数」自相矛盾；③ P1 表里写 20/20 而 `P1-08`/`P1-17` 明为 `[ ]` | 计数口径与 ± 争议项见 `项目进度.md` §2 末注；台账 **#58** 记录根因（**增量式记数从未全量核对，误差累积**）；⚠️ 提醒：本文件**不是**可靠的逐任务状态台账 —— P5/P6/P7/P9/P10/P11/P12 的表行多为「无标记的裸行」，核对依赖阶段结果叙事与产物存在性 |

> ✅ **2026-09-23 已核对完毕**（同晚，逐任务全量核对）：**P1 20→18**、**P7 8→7**、**P8 10→9**，**总完成数 75 → 76 / 153（49.7%）**。
> ⚠️ 本表补登各行里的计数**沿用当时记载**（09-18~09-23 的增量注），**未逐行重算**；一律以 `项目进度.md` §2 末注的核对结论为准。
> ⚠️ **另注（重要）**：本文件**不是**可靠的逐任务状态台账 —— P5 / P6 / P7 / P9 / P10 / P11 / P12 的表行多为**无标记的裸行**，逐任务判定只能靠「阶段结果叙事 + 产物是否存在」，而这两者对同一任务并不总是一致（如 `P7-01` 无标记也无产物、`P11-05` 有产物却无标记）。详见台账 **#58**。
| **2026-09-23（裸行补标记）** | **v1.14** | **给 76 个「无标记裸行」补上状态标记**（P3 7 · P5 11 · P6 9 · P7 6 · P9 11 · P10 10 · P11 12 · P12 10），并在 §0 使用说明新增**「计数规则」行**使标记可机械复算；顺带规范化两处 `✅` 兼职表示「部分完成」的行（`P6-09` / `P6-13` → `[~]`）、`P0-03` 由 `[~]` 改 `[x]`（P0 阶段结果按「达成」计入）、修 `P3` 阶段结果 `8/12 → 9/12`、补 `P5` 缺失的阶段结果 | ① 本轮核对发现本文件的表行**大量无标记**，导致「阶段结果叙事」与「表行」无从对照，只能靠人工判断；② `✅` 在两处被用作「部分完成」⇒ **机器会把它们读成已完成**；③ 标记补齐后按「计数规则」逐行求和 = **76 / 153**，与 §2 核对结论**完全一致**（此前必须靠人工重新推导） | ✅ **本文件现在是可机械复算的**：`[x]` 计入 / `[~]`·`[ ]`·`[!]` 不计入 / **子项拆分按 1 项计**（`P8-03a+b`、`P8-08a+b`）⇒ 13 个阶段逐行求和 = **76**。⚠️ **未改动任何任务的完成状态**（只补/规范标记）；⚠️ `P11-05`/`P11-06` 仍标 `[~]`（产物存在但口径待裁定，见 `项目进度.md` §2 末注 ± 项） |
