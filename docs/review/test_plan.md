# 测试计划（P8-10 · 计划侧）

> 需求原文：`todolist.md:417` ——「P8-10 测试用例集：功能 / 边界 / 性能 / 兼容 / 安全（对应 PRD 验收）」。
> 配套：`tests/README.md`（用例索引 / AC 映射 / 边界值覆盖表，同为本批 P8-10 产出）。
> 上游验收：`docs/prd/PRD_v1.md` 的 §5.6 · §6.3 · §8.1 · §8.2 · §8.3（共 31 条 AC）。

---

## 0. ⚠️ 执行状态声明（先读这一段）

| # | 本文件**不**声称 | 依据 |
|---|---|---|
| 1 | **不给任何覆盖率数字** | 本项目**没有接入任何覆盖率工具**（`SUMMARY.md:150` 原文：「未接入覆盖率统计」「**不编造覆盖率数字**」）。本文件**不引入**覆盖率工具 —— 引依赖属于工具选型决策，不在本任务范围内 |
| 2 | **不声称「CI 绿」** | `.github/workflows/ci.yml` **已存在**（P2-11），但仓库**没有远端**（`git remote -v` 为空），流水线**一次都没有运行过**（`todolist.md:275`、`SUMMARY.md:346`、`.github/workflows/ci.yml:43-45` 自述）。凡「CI 已通过」的表述都是错的 |
| 3 | **不声称「测试全绿＝产品可用」** | 后端 469 条测试**全部**跑在 SQLite + `InMemoryAssetStorage` + 无 Redis 之上；**一次真实出图都没有跑过**。见 §4 |
| 4 | **不把「定义了计划」写成「计划已执行」** | 本文件定义的是**要测什么、怎么分层、哪些测不了**。31 条 AC 里 **13 条一条用例都没有**（清单见 `tests/README.md` §2.6） |
| 5 | **不把分母藏起来** | 凡是引用通过数，**必须同时给 revision 与命令** —— 数字脱离 revision 没有意义（`SUMMARY.md:175` 记的就是这个教训） |

**本文件当前的里程碑口径**：P8-10 的**离线部分**（计划 + 索引 + 边界值用例）已定义；
**性能 / 兼容 / 真实出图**三类**全部未执行**，阻塞项见 §3 的 gates。

---

## 1. 范围

### 1.1 在范围内（V1 的测试对象）

| 对象 | 内容 |
|---|---|
| 后端 | FastAPI 路由、Schema、状态机、配额、内容安全、素材与产物、生命周期清理、Celery 配置、驱动适配层 |
| 工作流内核 | `engine/`（零运行时依赖）：注册表校验、Schema、渲染、渲染路径验证工具、质检工具 |
| 前端 | 动态表单（校验 / 默认值 / 控件注册）、状态徽章、画廊页、HTTP 客户端错误映射 |
| 数据 | `tests/golden_set/`（评测输入集，P8-01） |
| 部署脚本 | `deploy/autodl/**`（⚠️ **在范围内但测不了**，见 §3 gate `G-01`/`G-06`） |

### 1.2 明确不在范围内

- **`/object_info` 快照之外的 ComfyUI 内部行为**（如采样步时序）—— 只能实测，不能单测。
- **第三方商用许可合规** —— CI 只能证明「权重没被误提交」，**不能**证明许可合规（`.github/workflows/ci.yml:27`）。
- **画质** —— 属 `docs/sop/quality_rubric.md`（P8-02）与人工抽检（P8-04），不是测试计划的判据。

---

## 2. 测试层次与当前覆盖状态

按 `todolist.md:417` 的五个类别分层。**每一层都如实标注"现在能不能跑 / 跑没跑过"。**

### 2.1 功能（functional）

| 项 | 内容 |
|---|---|
| 覆盖对象 | 状态机全部迁移（T1–T14）、批量聚合、幂等、配额、内容安全、素材生命周期、驱动错误分类、注册表装载 |
| 真值位置 | `backend/tests/`（469 条）· `engine/tests/`（169 条 / 1 skipped）· `web/src/**/__tests__/`（7 文件） |
| 当前状态 | ✅ **本机可跑且已跑过**（数字见 §5）。但**全部跑在替身上**（§4） |
| AC 对应 | `AC-5.4` · `AC-6.2` · `AC-6.3` · `AC-F5`（元数据）· `AC-F8` 为 ✅；`AC-5.1/5.3/5.5` · `AC-6.4` · `AC-F4` · `AC-P2/5` 为 ⚠️ 部分 |

### 2.2 边界（boundary）

| 项 | 内容 |
|---|---|
| 覆盖对象 | PRD `§6.2` 的**10 项边界值** |
| 真值位置 | `backend/tests/test_boundary_values.py`（本批新增 23 条）+ `backend/tests/test_assets.py`（上传侧已有） |
| 当前状态 | ⚠️ **部分**。10 项里 **1 项仍无行为用例**（队列深度 —— 实现不存在）；**2 项是「钉住缺口」而非守住期望**（`params` 通道的提示词长度 / 生成分辨率） |
| 逐项对照 | 见 `tests/README.md` §3 |

### 2.3 性能（performance）—— 🔴 **全部未执行**

| 项 | 内容 |
|---|---|
| 覆盖对象 | `AC-N1`（4090 连续 8h 零崩溃）· `AC-N2`（重试成功率 ≥95%）· `AC-N3`（1000 张无人值守）· 稳态基准（`deploy/autodl/34_bench_recheck.sh`） |
| 当前状态 | 🔴 **一条都没跑过**。仓库里**没有**性能测试框架，也没有基准数据（`debug_log.md` 各工作流的"性能参考值"栏一律写**「无（未出图，不填）」**） |
| 阻塞 | **GPU + SSH 凭据**（见 gate `G-02`） |
| ⚠️ 纪律 | **不要**用单测耗时冒充性能数据；**不要**在没有基准的情况下写「预计耗时」 |

### 2.4 兼容（compatibility）—— 🔴 **全部未执行**

| 项 | 内容 |
|---|---|
| 覆盖对象 | 真实 PostgreSQL（`JSONB` / 窗口函数 / 真并发 / `ALTER TABLE`）· 真实 Redis + Celery worker · 真实 MinIO/S3 · 跨环境（GPU 型号 / 驱动 / cuDNN）· `AC-N6`（环境一键重建） |
| 当前状态 | ⚠️ **有替代证据，但都是代理量**：`test_sqlite_compat.py`（9 条）证明「PG 方言产物未被改弱」（离线 DDL 断言 `JSONB` / `BIGINT` / partial `WHERE` 还在），**但它自己声明不能替代真实 PG 集成测试** |
| 阻塞 | 本机**无 PG 二进制、无 docker daemon、无 MinIO、无 Redis 服务**（见 gate `G-04`） |
| ⚠️ 已知最大信心缺口 | 生命周期清理含**物理删除**，而它恰恰最依赖真实对象存储 ——「在替身上删成功」推不出「在真实 MinIO 上删成功」 |

### 2.5 安全（security）

| 项 | 内容 |
|---|---|
| 覆盖对象 | 密钥/凭证不入库（`test_repo_hygiene.py`，12 条，含变异验证）· 内容安全输入侧（`test_content_safety.py`，46 条，含隐私红线：审计不含原词）· 上传格式魔数校验（`test_assets.py::TestImageProbe`，防"改后缀混进来" EX-10）· 越权访问（他人素材一律 404 而非 403） |
| 当前状态 | ⚠️ **部分**。上述已覆盖；**但 `AC-N5`「接口全部鉴权」零用例**（无任何 401 判据），`comfyui_base_url` 的 `0.0.0.0` 守卫（`config.py:166-176`）也无用例 |
| 阻塞 | **不阻塞** —— `AC-N5` 与 `AC-F6` 属**纯离线可补** |

### 2.6 层次小结

| 层 | 能跑 | 跑过 | 备注 |
|---|---|---|---|
| 功能 | ✅ | ✅（本机、替身） | — |
| 边界 | ✅ | ⚠️ 部分 | 1 项实现不存在 |
| 性能 | ❌ | ❌ | 需 GPU |
| 兼容 | ❌ | ❌ | 需 PG / Redis / MinIO |
| 安全 | ✅ | ⚠️ 部分 | 鉴权层零用例 |

---

## 3. 门禁（gates）：什么时候才算「可以往下走」

⚠️ **这些 gate 目前**没有**任何自动化执行器** —— 它们是**人工判定的出口条件**。
本文件**不写执行器**（写了也无法在无 GPU 的环境里验证，等于交付一个假东西）。

| gate | 名称 | 内容 | 当前状态 | 阻塞项 | 出口判据（打勾条件） |
|---|---|---|---|---|---|
| **`G-01`** | 可复现性 | 5 条工作流的渲染路径 L4 同 seed 两次一致 | ⚠️ **2 / 5** | **GPU + SSH 凭据**；`inpaint_v1` 另需蒙版极性判读 | 跑 `deploy/autodl/36_l4_three_workflows.sh`（**从未执行过**），三条**逐条**改注册表；细节见 `docs/review/reproducibility_report.md` §4 |
| **`G-02`** | 性能与稳态 | `AC-N1` / `AC-N2` / `AC-N3` + 稳态基准复测 | 🔴 **未执行** | **GPU**（8h 连续压测） | 基准报告 + 单张成本标定（`gpu_cost_per_hour` 至今为 `0.0`，`config.py:164`） |
| **`G-03`** | 人工验收 | 人工抽检 SOP 执行 · golden set 真实评测 · `AC-P1` 内测 · `AC-F2` 10 分钟实测 | 🔴 **未执行** | **GPU + 真人**；golden set 另缺参考图素材 | 见 `docs/sop/quality_sampling_sop.md` §0 的四条打勾条件（**一条都没满足**） |
| **`G-04`** | 真实中间件 | PostgreSQL / Redis+worker / MinIO 集成 | 🔴 **未执行** | 本机无 PG / Redis / docker | 至少最小化的真实集成验证（优先级：PG 并发 + MinIO 物理删除） |
| **`G-05`** | 离线补测与缺口决策 | ① 补 `AC-N5` 鉴权用例 · ② 补 `AC-F6` catalog 用例 · ③ **`params` 通道缺口**（提示词长度 / 生成分辨率）的实现取舍 · ④ `task_max_retries_hard_limit` 是否补消费者 · ⑤ `queue_max_depth` / 磁盘阈值是否实现 | ⚠️ **未开始** | **无**（纯离线） | ① ② 有用例；③④⑤ 有**明确决策记录**（做或不做，都要写清理由） |
| **`G-06`** | 部署脚本可执行性 | `deploy/autodl/**` 的脚本**从未被执行过** | 🔴 **未执行** | 目标机 + 凭据 | 首次真实执行并归档证据。⚠️ **不要**因为"脚本看起来很完整"就假设它能跑通 |

> ⚠️ **`G-01` 与 `G-03` 的编号是既有文档已经在引用的**（`reproducibility_report.md` §6/§7 引 `G-01`；
> `quality_sampling_sop.md` §0/§8/§10 引 `G-03`）。本文件**沿用**这两个编号，不要重编号。

---

## 4. 测试环境与非目标（诚实边界）

### 4.1 实际跑在什么之上

| 依赖 | 生产 | 测试实际用的 | 差距 |
|---|---|---|---|
| 数据库 | PostgreSQL | **SQLite 内存库**（`conftest.py`） | 不行使 JSONB 路径查询、窗口函数、真并发 |
| 对象存储 | MinIO / S3 | **`InMemoryAssetStorage`** | 物理删除、慢客户端、断连均未实测 |
| 队列 | Redis + Celery worker | **`no_broker` 夹具 + fake 依赖** | 真实重投、broker 抖动未实测 |
| 推理引擎 | ComfyUI（GPU） | **mock HTTP / 假 WebSocket**（`test_driver.py`）或 **fake resolver** | **一次真实出图都没有** |

### 4.2 本机约束（会影响"慢"的判读）

- **4 核**，且常被并行代理占满 → 测试偏慢。
- 前端 Vitest 未设 `testTimeout`，走默认 **5000ms**，已实测出**竞态型 flaky**
  （`SUMMARY.md:212-223`：同一用例一次 82 passed、一次 1 failed `Test timed out in 5000ms`，
  单独重跑 11 passed 且耗时 12976ms ⇒ **不是卡死**）。
  ⚠️ **不要把"超时红"当成回归**，也不要靠重跑掩盖 —— 正确做法是给 `vite.config.ts` 显式放宽超时。

---

## 5. 运行方式（**可执行**的命令表）

| 项 | 命令（在**仓库根**执行，除标注外） | 当前实测结果 | revision |
|---|---|---|---|
| 后端全量 | `cd backend && timeout 900 .venv/bin/python -m pytest -q` | **469 passed** | 本批（含新增 23 条） |
| 后端基线（不含本批新文件） | `cd backend && timeout 900 .venv/bin/python -m pytest -q --ignore=tests/test_boundary_values.py` | **446 passed** | 同上去新增 |
| 边界值专项 | `cd backend && timeout 600 .venv/bin/python -m pytest -q tests/test_boundary_values.py` | **23 passed** | 本批 |
| 工作流内核 | `backend/.venv/bin/python -m pytest engine/tests -q` | **169 passed / 1 skipped** | 本批 |
| ruff | `backend/.venv/bin/ruff check .` | `All checks passed!` | 本批 |
| 离线工作流校验 | `backend/.venv/bin/python -m engine` | `VALIDATE=PASS`（已执行 **L1/L2/L3**，L4 需 GPU） | 本批 |
| 缺陷库机械校验 | `backend/.venv/bin/python -m engine.tools.check_defect_kb` | 通过（`AC-P4` 的判据） | 本批 |
| golden set 结构校验 | `backend/.venv/bin/python -m engine.tools.check_golden_set` | 见 `tests/golden_set/README.md` §5 | P8-01 |
| 前端单测 | `cd web && corepack pnpm test --run` | ⚠️ **本批未复跑**（基线 82 passed / 7 files） | — |
| 前端类型 / lint / 构建 | `cd web && corepack pnpm typecheck` / `lint` / `build` | ⚠️ **本批未复跑** | — |

> ⚠️ **`ruff` 必须从仓库根跑**：根与 `backend/` 各有一份 `[tool.ruff.lint]`，
> `ruff check .` 的结论**取决于在哪执行**（本项目已因这个坑栽过，见 `.github/workflows/ci.yml:33-38`）。
>
> ⚠️ 「**本批未复跑**」= 改动的文件**不涉及**该层，不能读成"该层是绿的"。

---

## 6. 缺口台账

| # | 缺口 | 影响 | 解除条件 |
|---|---|---|---|
| 1 | **31 条 AC 里 13 条无用例** | 其中 `AC-F6`（模板库）与 `AC-N5`（鉴权/端口暴露）**纯离线就能补**；`AC-F1` 还缺权重且需 GPU | 见 `tests/README.md` §2.6 |
| 2 | **性能 / 兼容两层整体未执行** | 这两层的所有 AC（`AC-N1`~`N6`）都没有判据 | gate `G-02` / `G-04` |
| 3 | **真实出图零次** | 一切"链路是否真的通"的结论都无实测支撑 | gate `G-01` |
| 4 | **多数测试文件未做变异验证** | 「有用例」的强度未知；注释里的承诺可能无人守 | 逐文件补（见 `tests/README.md` §5） |
| 5 | **无覆盖率工具** | 无法回答"代码被跑到多少"；本文件**不给任何覆盖率数字** | 工具选型决策，**本任务明确不做** |
| 6 | **CI 从未运行** | 本文件所有数字都是**本机**数字 | 配置远端 |
| 7 | **`deploy/**` 脚本从未执行** | `AC-N6` 无判据 | gate `G-06` |
| 8 | **前端测试本批未复跑** | 前端层状态停留在上一 revision | 复跑（注意 §4.2 的 4 核争用） |

---

## 7. 相关文件

| 文件 | 关系 |
|---|---|
| `tests/README.md` | **配套文件（本批同交付）**：那里是「已经有什么、缺什么」，这里是「测什么、怎么分层、哪些测不了」 |
| `docs/prd/PRD_v1.md` §5.6 / §6.2 / §6.3 / §8 | AC 与边界值的**唯一来源** |
| `docs/review/reproducibility_report.md` | gate `G-01` 的细节与证据归档（覆盖 2/5） |
| `docs/sop/quality_sampling_sop.md` | gate `G-03` 的人工抽检流程（**未在任何真实批次上执行过**） |
| `tests/golden_set/README.md` | P8-01 产出（评测输入集）；**本文件不修改它** |
| `docs/sop/debug_log.md` §2.4 | L4 实测证据出处（可复现性的原始记录） |
| `.github/workflows/ci.yml` | 已建、**从未运行**；其头部自带「不覆盖什么」的声明 |
| `SUMMARY.md` | 上一轮夜间的测试数字与 flaky 判读（**工作区文件，未入库**） |
| `todolist.md` P8-10 / P12-01 | 本文件的需求出处；P12-01（按用例集逐条过）**未开始** |
