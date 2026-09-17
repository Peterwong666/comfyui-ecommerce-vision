# 测试用例索引（P8-10 · 索引侧）

> **本文件是用例索引，不证明任何功能可用。**
> 它回答的是「哪个 AC 有哪条用例」与「**哪个 AC 至今没有用例**」，
> **不回答**「功能是否达标」。要判可达标，得去跑用例、看产物、上 GPU —— 那是别处的事。
>
> 需求原文：`todolist.md:417` ——「P8-10 测试用例集：功能 / 边界 / 性能 / 兼容 / 安全（对应 PRD 验收）」。
> 配套：`docs/review/test_plan.md`（测试计划，同为本批 P8-10 产出）。

---

## 0. ⚠️ 本文件**不**声称什么（先读这一段）

| # | 本文件**不**声称 | 说明 |
|---|---|---|
| 1 | **不给任何覆盖率数字** | 本项目**没有接入任何覆盖率工具**（`SUMMARY.md:150` 原文：「未接入覆盖率统计」「**不编造覆盖率数字**」）。本文件只有「有 / 无 / 部分」三档判读，没有百分比 |
| 2 | **不声称「CI 绿」** | `.github/workflows/ci.yml` 已存在，但仓库**没有远端**（`git remote -v` 为空），流水线**一次都没有运行过**（`todolist.md:275`、`SUMMARY.md:346`）。本文件的任何内容都不是 CI 产物 |
| 3 | **不把「有用例」读成「已验证通过」** | 索引只登记**判据存在于哪**。用例是不是真能检出回归，取决于它有没有做过变异验证 —— 只有少数文件明确记了（见 §5） |
| 4 | **不搬运、不新增测试** | 本目录是**索引与数据**，不是测试实现。P8-10 的产出路径写 `tests/`，但**实现不在这里**（理由见 §1） |
| 5 | **不覆盖 `tests/golden_set/README.md`** | 那是 P8-01 的产出，**已由另一路交付**。本文件只在 §6 说明与它的关系，不做任何增删改 |
| 6 | **「无用例」是结论，不是待办占位符** | §2.4 列出的 11 条 AC **确实一条对应用例都没有**。不要因为"看起来该有"就当成漏登记 |

> 一句话：**这是「判据在哪」的地图，不是「判据都过了」的证书。**

---

## 1. 为什么 P8-10 的产出路径是 `tests/`，而测试实现在别处

`todolist.md:417` 把 P8-10 的交付物定为 `tests/`。**但把测试从 `backend/tests/` 与
`engine/tests/` 搬进根目录的 `tests/` 是错的**，理由是机械的、不是偏好：

| 事实 | 含义 |
|---|---|
| `backend/tests/` 依赖 `backend/.venv` 与 `backend/pyproject.toml`（pytest 配置、`conftest.py` 的 fixture、`app.*` 的 import root） | 搬到 `tests/` 后 import root 变了，**整批测试会先在 collection 阶段就崩掉** |
| `engine/tests/` 的纪律是「**零运行时依赖**，只用标准库」（`engine/` 的设计前提） | 它与后端测试的依赖面完全不同，**不能混进同一个目录** |
| `web/src/**/__tests__/` 由 Vitest 运行，需要 Node | 更不可能与 Python 测试同目录 |
| 仓库根已有一份 `pyproject.toml`（给 `engine` 用）与一份 `backend/pyproject.toml` | 根目录再加测试会让「`ruff check .` 在哪跑」这个**已经踩过一次的坑**（见 `.github/workflows/ci.yml:33-38`）再出现一次 |

所以正确的做法是 **`tests/` 当索引与数据，测试留在各自的可运行位置**：

```
tests/
├── README.md          ← 本文件：用例索引 / AC 映射 / 边界值覆盖表
├── golden_set/        ← P8-01 产出：评测输入集（数据，不是测试代码）
├── unit/              ← 占位（.gitkeep）。**不在这里写测试**，理由见上表
└── integration/       ← 占位（.gitkeep）。同上
```

### 1.1 三层测试入口（真值位置）

| 层 | 位置 | 运行命令 | 规模（实测，2026-09-17） |
|---|---|---|---|
| 后端 | `backend/tests/` | `cd backend && .venv/bin/python -m pytest -q` | **547 passed** |
| 工作流内核 | `engine/tests/` | `backend/.venv/bin/python -m pytest engine/tests -q` | **169 passed / 1 skipped** |
| 前端 | `web/src/**/__tests__/` | `cd web && corepack pnpm test --run` | 7 个文件（本轮未复跑，见 §7） |

> ⚠️ 「547」这个数字**绑定 revision**（2026-09-17，末次实测）：其中含
> `backend/tests/test_boundary_values.py` 的 **29 条**。去掉它，提交态基线是 **518**
> （`547 − 29 = 518`）。数字脱离 revision 没有意义 —— 这是本项目已经吃过一次亏的地方
> （`SUMMARY.md:175`）。⚠️ 本文件此前记的「469」（及其后的 23 条 / 446）**是更早 revision 的数**，
> 不是口径冲突，已按实测更新。

---

## 2. AC → 用例映射表（PRD §5.6 / §6.3 / §8）

**AC 全集来自 `docs/prd/PRD_v1.md`**：§5.6（`AC-5.1`~`5.5`）· §6.3（`AC-6.1`~`6.6`）·
§8.1（`AC-F1`~`F8`）· §8.2（`AC-N1`~`N7`）· §8.3（`AC-P1`~`P5`），共 **31 条**。

状态三档的含义（**不要混用**）：
- ✅ **覆盖** —— 有对应判据，且判据与 AC 的措辞对得上；
- ⚠️ **部分** —— 有判据，但只盖住 AC 的一部分（差额在「差额」列写明）；
- 🔴 **无用例** —— **一条对应用例都没有**。

### 2.1 功能验收（AC-F1 ~ AC-F8）

| AC | 标准（PRD 原文） | 状态 | 判据 / 用例 | 差额 |
|---|---|---|---|---|
| AC-F1 | **V1：5 条**标准工作流全部上线且可跑通（**1 条顺延 V2** —— PRD §8.1 已按 ADR-008 裁定 1 修正原「6 条」口径） | 🔴 **无用例** | — | 仓库只有 **5** 条有可执行 JSON，`style_transfer_v1` 注册了但无权重（V1 已裁定顺延 V2）；「可跑通」需 GPU。**这两个词仍都不成立** |
| AC-F2 | 非技术用户照 SOP 10 分钟内完成 100 张批量出图 | 🔴 **无用例** | — | 人工实测类，需 GPU + 真人 |
| AC-F3 | 每条工作流有 golden set ≥30 例，一次通过率 ≥80% | 🔴 **无用例** | `engine/tools/check_golden_set.py` 只验**结构合法性** | 用例最多 18 条（< 30）；**通过率从未评测过**（`expected` 恒为 `null`），见 `tests/golden_set/README.md` §3 |
| AC-F4 | 批量失败可断点续跑，不重跑已成功项 | ⚠️ **部分** | `test_quota.py::test_retry_failed_charges_len_failed_and_is_all_or_nothing`（402 时不放跑任务） | **「不重跑已成功项」无断言** —— 现有用例验的是**配额口径**，不是续跑语义 |
| AC-F5 | 产物元数据完整（seed/模型 hash/参数），**可复现** | ⚠️ **部分** | 元数据侧覆盖：`test_worker.py::test_success_saves_asset_with_reproducible_meta` · `::test_success_records_used_models_in_asset_meta` · `::test_pruned_branch_loader_is_not_recorded_as_used_model` · `test_workflow_models.py`（13 条） | **「可复现」只有 2/5 工作流经实测**（另 3 条 L4 **从未执行**），见 `docs/review/reproducibility_report.md`。AC 的后半句未被满足 |
| AC-F6 | 模板库可用且按品类分组 | ✅ **覆盖** | `backend/tests/test_catalog_templates.py`（**2026-09-17 新增**）：列表只回启用模板 + 排序口径（字面量）· 品类筛选生效 · `/templates/categories` 去重有序非空且只统计启用模板 · `POST /templates` 的管理员 201 / 非管理员 403 / 工作流不存在 404 | — |
| AC-F7 | 动态表单只暴露 3–5 个关键参数，高级折叠 | ✅ 覆盖（前端） | `web/src/features/workflow-form/__tests__/defaults.test.ts` · `WorkflowForm.test.tsx`；后端 `test_web_form_fixtures.py` 守夹具与 registry 一致 | 后端只守「夹具没漂移」，不守「露几个」 |
| AC-F8 | 任务状态机全部迁移路径有单测覆盖 | ✅ 覆盖 | `test_state_machine.py`（31 条）· `test_worker.py`（40 条） | — |

### 2.2 状态机验收（AC-5.1 ~ AC-5.5，PRD §5.6）

| AC | 标准（PRD 原文） | 状态 | 判据 / 用例 | 差额 |
|---|---|---|---|---|
| AC-5.1 | 任一任务不会永久停留非终态（超时保护） | ⚠️ **部分** | `test_state_machine.py::test_find_timed_out` / `::test_find_timed_out_excludes_fresh` · `test_worker.py::test_requeue_orphans_picks_up_stale_queued` · `test_celery_config.py::test_beat_tasks_are_registered` | PRD 的验证方式是「**压测** + 僵尸扫描」；**压测未做** |
| AC-5.2 | 取消在 ≤1 个采样步内生效 | 🔴 **无用例**（真实生效） | 只有状态迁移：`test_state_machine.py::test_cancel_from_running`（docstring 自述「这里只验证状态迁移」）；mock 层 `test_driver.py::test_cancel_running_interrupts` | 「≤1 个采样步」是 ComfyUI **内部时序**，无真实出图无法判 |
| AC-5.3 | 重试不产生重复产物 | ⚠️ **部分** | `test_state_machine.py::test_terminal_state_cannot_transition_again` / `::test_cannot_retry_from_succeeded` · `test_worker.py::test_duplicate_delivery_is_skipped` | **「产物去重」未在真实产物上验** —— 现有用例证的是「状态机不会被重放」，不是「没有多出第二张图」 |
| AC-5.4 | 父任务状态与子任务聚合一致 | ✅ **覆盖** | `test_state_machine.py::test_batch_all_succeeded` / `_partial` / `_all_failed` / `_running_while_children_pending` / `_all_canceled` · `::test_derive_status_is_pure` | — |
| AC-5.5 | Worker 崩溃后任务自动恢复（T14） | ⚠️ **部分** | `test_state_machine.py::test_recover_zombie_tasks` / `::test_recover_ignores_fresh_heartbeat` · `test_worker.py::test_recover_zombies_requeues_immediately` | 靠**心跳超时模拟**；PRD 要求的「**杀进程演练**」未做 |

### 2.3 异常处理验收（AC-6.1 ~ AC-6.6，PRD §6.3）

| AC | 标准（PRD 原文） | 状态 | 判据 / 用例 | 差额 |
|---|---|---|---|---|
| AC-6.1 | OOM 任务经降级后**能成功**（而非直接失败） | 🔴 **无用例** | 只有分类层：`test_state_machine.py::test_oom_is_retried` · `test_driver.py::test_submit_oom_is_retryable` | 这两条证的是「OOM **可重试**」，**不是**「降级后能成功」。PRD 的验证方式是「构造大分辨率任务压测」→ 需 GPU |
| AC-6.2 | 致命错误不进入重试（EX-3/EX-9 直接 `failed`） | ✅ **覆盖** | `test_worker.py::test_fatal_error_does_not_retry` · `test_driver.py::test_submit_invalid_param_is_fatal` · `test_state_machine.py::test_invalid_param_is_not_retried` / `::test_model_missing_is_not_retried` / `::test_error_type_retryable_classification` | — |
| AC-6.3 | 重复提交返回同一 task_id（幂等，EX-6） | ✅ **覆盖** | `test_state_machine.py::test_duplicate_submit_returns_same_task` / `::test_different_idempotency_keys_create_new_tasks` / `::test_no_idempotency_key_always_creates_new` | — |
| AC-6.4 | 杀 Worker 后任务自动恢复且不重复产出（EX-7） | ⚠️ **部分** | 同 AC-5.5 的两条 + `test_worker.py::test_recover_zombies_noop_when_none` | **真故障注入（真杀进程）未做** |
| AC-6.5 | 磁盘/配额达阈值时拒绝新任务且提示明确 | ⚠️ **部分** | 配额侧 ✅：`test_quota.py`（8 条，含 402 结构化错误体与原子性） | **磁盘侧完全无用例**：`disk_min_free_gb` / `disk_warn_free_gb`（`config.py:137-138`）在全仓库**没有读取点**（只有 `celery_app.py:113` 的一句注释提到），即阈值未被实现 |
| AC-6.6 | 全部边界值（§6.2）有对应测试用例 | ⚠️ **部分** | `test_boundary_values.py`（**29 条**）+ `test_assets.py` 上传侧 + `test_queue_guard.py`（队列深度，2026-09-17） | 见 §3：**10 项已全部有用例**（队列深度 2026-09-17 补行为用例；提示词长度的 `params` 通道 2026-09-17 补长度校验用例）；**仍余 1 项是「钉住缺口」而非「守住期望」**（生成分辨率的 `params` 通道，见 §4） |

### 2.4 非功能验收（AC-N1 ~ AC-N7，PRD §8.2）

| AC | 标准（PRD 原文） | 状态 | 判据 / 用例 | 差额 |
|---|---|---|---|---|
| AC-N1 | 单卡 4090 连续 8h 混合压测零崩溃 | 🔴 **无用例** | — | 需 GPU + 8 小时 |
| AC-N2 | 失败自动重试成功率 ≥95% | 🔴 **无用例** | — | 需真实批量 |
| AC-N3 | 1000 张批量任务无人值守完成 | 🔴 **无用例** | — | 需 GPU（`test_boundary_values.py` 只证**能提交** 1000 张，不证能跑完） |
| AC-N4 | 单张成本可核算（元/张） | ⚠️ **部分** | `gpu_seconds` 有断言：`test_state_machine.py:53-56` · `test_worker.py:329` | **「元/张」换算无断言**；且换算被 `if settings.gpu_cost_per_hour > 0` 短路（默认 `0.0`，`config.py:164` 自注「待核实」） |
| AC-N5 | ComfyUI 端口不对外暴露，接口全部鉴权 | ✅ **覆盖** | `backend/tests/test_auth_coverage.py`（**2026-09-17 新增**）：从 `app.openapi()` **枚举全部路由**、**默认要求 401**（不带凭证），白名单**显式列出并写明理由** + 两条元测试防"枚举为空 ⇒ 假绿"；端口侧用 `Settings(...)` 直接构造证 `0.0.0.0` 被拒，并断言当前 `comfyui_base_url` 确为回环 | ⚠️ 只证"**没有凭证**时进不去"；「越权访问他人资源一律 404」由 `test_api_tasks.py` / `test_quota.py` 覆盖。⚠️ **真实网络暴露面未在真机验证**（无 GPU / 无目标机） |
| AC-N6 | 环境可一键重建并验证通过 | 🔴 **无用例** | — | `deploy/**` 脚本从未在本机被测试覆盖；`36_l4_three_workflows.sh` 等**从未执行过** |
| AC-N7 | 全部边界值有测试用例 | ⚠️ **部分** | 同 AC-6.6（两项是同一个清单的两个说法） | 同 AC-6.6 |

### 2.5 产品验收（AC-P1 ~ AC-P5，PRD §8.3）

| AC | 标准（PRD 原文） | 状态 | 判据 / 用例 | 差额 |
|---|---|---|---|---|
| AC-P1 | ≥5 人内测，≥3 人表示愿意继续使用 | 🔴 **无用例** | — | 人工，未开始 |
| AC-P2 | 北极星指标（周有效交付图片数）可准确统计 | ⚠️ **部分** | `image_downloaded` / `image_adopted` 埋点有断言：`test_assets.py:582-601`、`:607-650`、`:799` | **「周」聚合与统计查询无断言** |
| AC-P3 | 良品率看板与成本看板可访问 | 🔴 **无用例** | — | 看板未实现（`todolist.md` P8-05 未做） |
| AC-P4 | 缺陷知识库 ≥30 条 | ✅ **覆盖（结构层）** | `engine/tools/check_defect_kb.py` + `engine/tests/test_quality_tools.py`（含对**真实** `docs/sop/defect_kb.md` 的「≥30 条、六类齐、参数全合法」断言） | **不证明条目有效** —— 校验器自己声明「只证明参数合法，不证明参数有效」，KB 里 `verified_by=gpu` 的条目数恒为 0 |
| AC-P5 | 埋点漏斗可还原完整用户路径 | ⚠️ **部分** | `events` 表有零散断言（同上埋点用例） | **无端到端漏斗用例**；`page_view` 等前端事件无后端判据 |

### 2.6 小结（**这是本文件最重要的一张表**）

| 档 | 条数 | AC 列表 |
|---|---|---|
| ✅ **覆盖** | **8** | `AC-5.4` · `AC-6.2` · `AC-6.3` · `AC-F6` · `AC-F7` · `AC-F8` · `AC-N5` · `AC-P4`（结构层） |
| ⚠️ **部分** | **12** | `AC-5.1` · `AC-5.3` · `AC-5.5` · `AC-6.4` · `AC-6.5` · `AC-6.6` · `AC-F4` · `AC-F5` · `AC-N4` · `AC-N7` · `AC-P2` · `AC-P5` |
| 🔴 **无用例** | **11** | `AC-5.2` · `AC-6.1` · `AC-F1` · `AC-F2` · `AC-F3` · `AC-N1` · `AC-N2` · `AC-N3` · `AC-N6` · `AC-P1` · `AC-P3` |

> **8 + 12 + 11 = 31**（与 PRD 的 AC 总数对上）。
>
> ⚠️ 分档本身有主观成分（例如 `AC-F5` 的**元数据**半边是 ✅、**可复现**半边是 2/5，
> `AC-P4` 的**条数**有判据、**条目有效性**无判据）—— 本文件一律取**更保守**的那一档，
> 免得把半句话的达标读成整条达标。**但「无用例」的 11 条是确定的、无争议的。**
>
> ⚠️ **本次分档变动（2026-09-17）**：`AC-F6`（模板库）与 `AC-N5`（鉴权 / 端口）由
> 🔴 **无用例** 移入 ✅ **覆盖** —— `backend/tests/test_catalog_templates.py` 与
> `backend/tests/test_auth_coverage.py` 已落地（两者都**不需要 GPU**）。
> 覆盖 6 → **8**、无用例 13 → **11**，总档数不变。
>
> 其中 `AC-F1` 是**纯离线也不该缺**的一条，属本索引暴露出的**真实覆盖缺口**，已登记在 §8。

#### 11 条「无用例」全列（**可直接当补测清单**）

| # | AC | 为什么现在没有 | 解除条件 |
|---|---|---|---|
| 1 | `AC-5.2` | 「≤1 采样步」是 ComfyUI 内部时序 | GPU + 真实出图 |
| 2 | `AC-6.1` | 「降级后**能成功**」要真跑 OOM 场景 | GPU |
| 3 | `AC-F1` | 只有 5 条工作流有 JSON；`style_transfer_v1` 缺权重（V1 已裁定顺延 V2） | 权重入库 + GPU + 真实出图 |
| 4 | `AC-F2` | 人工 10 分钟实测 | GPU + 真人 |
| 5 | `AC-F3` | 用例数 <30 且通过率从未评测 | 补例 + GPU 评测 |
| 6 | `AC-N1` | 8h 压测 | GPU + 8h |
| 7 | `AC-N2` | 需真实批量统计成功率 | GPU |
| 8 | `AC-N3` | 需真正跑完 1000 张 | GPU |
| 9 | `AC-N6` | `deploy/**` 从未执行 | 目标机 + 凭据 |
| 10 | `AC-P1` | 内测未开始 | 人工 |
| 11 | `AC-P3` | 看板未实现 | 先做 P8-05 |

---

## 3. 边界值 10 项覆盖表（PRD §6.2 / `AC-6.6` / `AC-N7`）

**「已有 vs 新增」逐项对照。** 「新增」= 本批 `backend/tests/test_boundary_values.py`
（另加其头部 `test_settings_match_prd_section_6_2_literals` 把 10 项的字面值**一次性钉死**）。

| # | §6.2 边界（PRD `:506-517`） | 已有用例 | 本批新增 | 状态 |
|---|---|---|---|---|
| 1 | 上传大小 **100KB – 20MB** | ✅ `test_assets.py::TestUploadAsset::test_rejects_too_small`（`:203`）· `::test_rejects_too_large`（`:209`） | — | **已有** |
| 2 | 上传分辨率 **64×64 – 8192×8192** | ✅ `test_assets.py::TestUploadAsset::test_rejects_out_of_range_dimensions`（`:232`，参数化） | — | **已有** |
| 3 | 批量单任务张数 **1 – 1000** | ⚠️ `test_api_tasks.py::test_total_images_is_guarded_by_max_batch_size`（`:133`，**把配置改小**来测判据）· `::test_batch_at_total_limit_succeeds`（`:243`） | ➕ 真实 **1 / 1000 / 1001**（`test_batch_single_image_is_accepted` · `test_batch_total_images_at_upper_bound_is_accepted` 断言**真落库 1000 行 + 真派发 1000 次** · `test_batch_total_images_over_upper_bound_is_rejected` 断言**一张都不建**） | **已有 + 新增** |
| 4 | 提示词长度 **1 – 2000** | ❌ 无 | ➕ 顶层 `1`/`2000`（闭区间）· `2001`（拒）· `0`（拒）；`params` 通道：`2000` 放行 · `2001` 拒（结构化 422，`code=TEXT_LENGTH_OUT_OF_RANGE`）· **键名无关**（`caption` 同样受约束）· 批量通道整批被拒 · **空串放行**（`negative_prompt: ""` 是合法用法；下界**有意不施加**，见 §4） | **新增** |
| 5 | steps **1 – 100** | ❌ 无 | ➕ 顶层 + `params` **双通道**，边界与越界各测 | **新增** |
| 6 | CFG **1.0 – 20.0** | ❌ 无 | ➕ 同上（双通道） | **新增** |
| 7 | 生成分辨率长边 **512 – 2048** | ❌ 无 | ➕ `512`/`2048` 边界通过；**越界一条是「钉住缺口」**（API 不拒，见 §4） | **新增（含缺口）** |
| 8 | 重试次数 **默认 3 / 上限 5** | ✅ 语义：`test_state_machine.py::test_retry_exhausted_becomes_failed`（`:104`）· `test_worker.py::test_retry_exhaustion_becomes_failed`（`:805`） | ➕ 配置值钉死 + 三元关系；并**标注「上限 5 无任何代码读取」**（见 §4） | **已有 + 新增** |
| 9 | 单张超时 **60 – 600，默认 300** | ✅ 语义：`test_state_machine.py::test_find_timed_out`（`:324`）· `::test_find_timed_out_excludes_fresh`（`:339`） | ➕ 配置值钉死 + `min ≤ default ≤ max` 有序性 | **已有 + 新增** |
| 10 | 队列深度：软阈值 **10000** / 硬阈值 **20000**（= 2×） | ✅ 行为：`test_queue_guard.py`（2026-09-17 新增，**现 14 条**） | ➕ 配置值钉死 + 软层「告警但**仍接单**」/ 硬层 **422 `queue_full`** / 被拒不扣额不建任务 / 恰好等于硬阈值放行 / 在途状态口径 / 失败与取消不退额 | ✅ **已有**（2026-09-17 实现后补齐） |

**结论（如实，2026-09-17 重判）**：
- **10 项已**全部**有用例** —— 没有任何一项是"连用例都没有"；
- **唯一一处「钉住缺口」的是第 7 项（生成分辨率的 `params` 通道）**：API 层不拒，见 §4；
- 第 4 项（提示词长度）此前也是缺口，**已于 2026-09-17 由 `_validate_text_lengths` 补上长度校验**；其中**下界是有意不施加**（不是缺口，见 §4）；
- 「队列深度」此前唯一无行为用例，2026-09-17 补齐（实现 + `backend/tests/test_queue_guard.py`）—— 那此前不是漏写，是**实现不存在**；
- 其余 9 项有真实判据。

---

## 4. ⚠️ 「钉住缺口」的用例（**不是期望，是现状**）

登记在本节的用例是**刻意断言「当前是错的」**，用来把缺口变成可执行判据。
**修好之后这些用例会变红 —— 那正是它们的作用**：修的人必须同时把断言改成期望值。

⚠️ **2026-09-17 清账**：原先的 4 条里，**两条"提示词长度 `params` 通道"的已随缺口修复而下线**，
本节表格不再列它们 —— 照旧名字去找会扑空：

| 旧名（已不存在） | 现在叫什么 | 断言 |
|---|---|---|
| `test_params_prompt_channel_ignores_max_length` | `test_params_prompt_over_max_length_is_rejected` | 202 → **422** |
| `test_params_prompt_channel_ignores_min_length` | `test_params_prompt_empty_is_accepted` | **仍 202**，但**含义变了**：不再是"钉住缺口"，而是"下界**有意不施加**"（见下方说明） |

| 用例 | 钉住的缺口 | 证据 | 修好后要改成 |
|---|---|---|---|
| `test_generation_side_out_of_bounds_is_not_rejected_by_the_api` | 生成分辨率 `width/height` 越界时 **API 不拒**：`min_gen_side`/`max_gen_side` 的唯一读取点是 `engine/validate.py` 的 `SETTINGS_BOUNDS_MAPPING`（当**工作流 Schema 的一致性地板**），**不拿用户传入值去比** | 实测 `511` / `2049` / `4096` 均返回 **202**；前端 `validate.ts` 头部注释亲口确认这个空洞 | 断言改 `422` |
| `test_retry_hard_limit_is_a_ceiling_over_the_default`（docstring 标注） | `task_max_retries_hard_limit` **没有任何代码读取** —— 除定义（`config.py:113`）与**测试自身**之外，全仓库只有 `contracts.md` §1.1 的一句声明（`grep -rn task_max_retries_hard_limit` 即证据，**不要**去数"共几处"，那个数会随测试文件增删而变）。`mark_retrying` 只看 `task_max_retries` | 同上 | 补消费者后去掉 docstring 的缺口标注 |

> ⚠️ **「文本字段的下界」不是缺口，是裁定**：`tasks._validate_text_lengths` **只查上界**。
> 理由是**事实来源**：契约 §3 的 `param_schema` **没有** `required` / `allow_empty` 之类的标记，
> 所以"哪个文本字段允许为空"在数据里**没有事实来源**；按字段名硬编码 `prompt` / `negative_prompt`
> 会在 Schema 之外造出**第二份真相**，而「清空反向词」本来就是前端 `validate.ts` 认定的合法用法。
> 故 `test_params_prompt_empty_is_accepted` / `test_params_negative_prompt_can_be_closed` 断言的是
> **期望**（202），**不是缺口** —— 它们守的是"别有人把下界加回来"。

> **为什么不在这里顺手修实现**：这几条缺口跨越**实现 + 契约**两层（`params` 通道的校验策略
> 牵涉契约 §3.4 与 `param_schema` 的格式定义），应由实现方与契约方一并裁定。
> 缺口的处置建议见 `docs/review/test_plan.md` 的 gate `G-05`
> （其中「提示词长度的 `params` 通道」已于 2026-09-17 裁定并实现，见该 gate 的更新）。

---

## 5. 「能检出回归」的证据（变异验证）

「有用例」≠「用例有效」。**只有做过变异验证的用例才敢说能检出回归**：

| 文件 | 变异验证状态 | 出处 |
|---|---|---|
| `backend/tests/test_boundary_values.py` | ✅ P8-10 交付时对 **2 条**做过；**2026-09-17 又对"文本长度上界"补做一次**：把 `_validate_text_lengths` 的上界比较改成永不成立 ⇒ **3 条变红**（`test_params_prompt_over_max_length_is_rejected` · `test_params_caption_over_max_length_is_rejected` · `test_params_prompt_length_is_checked_on_the_batch_channel`）⇒ 还原后 29 passed，文件 md5 与变异前一致 | P8-10 交付报告 · 2026-09-17 报告 |
| `backend/tests/test_quota.py` | ✅ 声明含变异验证 | `SUMMARY.md:13` |
| `backend/tests/test_content_safety.py` | ✅ 声明逐条做过 | 文件头 docstring |
| `backend/tests/test_repo_hygiene.py` | ✅ 声明逐条做过 | 文件头 docstring |
| `backend/tests/test_worker.py` | ⚠️ **仅 1 条**做过（`test_pruned_branch_loader_is_not_recorded_as_used_model`） | `SUMMARY.md:36` |
| 其余文件 | ❌ **未做过，不等于做过** | — |

> ⚠️ 全仓库**没有** `mutmut` / `cosmic-ray` 之类的变异测试工具，也没有接入计划 ——
> 上表的「做过」都是**人工一次性**的（去掉某个分支、看是否变红）。**不要把它读成持续门禁。**

---

## 6. 与既有资产的关系（不重复、不覆盖）

| 资产 | 关系 |
|---|---|
| `tests/golden_set/README.md`（P8-01，**另一路已交付**） | 那是**评测输入集**的说明（42 条用例 / 5 条工作流 / 参考图未入库）。本文件**不修改它**，只在 §2 的 `AC-F3` 行引用其结论。两者关系：golden set = 「拿什么测」，本文件 = 「哪个 AC 有判据」 |
| `docs/sop/quality_sampling_sop.md`（P8-04，本批） | 它的 §8 把「本 SOP 被执行过」登记为 gate `G-03`；本索引不为它提供用例（人工抽检是人工流程，不是 pytest 用例） |
| `docs/review/reproducibility_report.md`（P8-09，本批） | 它的 §6 缺口登记为 gate `G-01`；本索引的 §2.1 `AC-F5` 行与它对齐（元数据 ✅ / 复现性 2/5） |
| `docs/review/test_plan.md`（P8-10，本批） | **配套文件**：那里写「测什么、怎么分层、哪些要等 GPU」，这里写「已经有什么、缺什么」 |
| `docs/sop/defect_kb.md`（P8-06）· `docs/sop/quality_rubric.md`（P8-02） | 两者的机械校验器在 `engine/tests/test_quality_tools.py`，对应本索引的 `AC-P4` |

---

## 7. 未验证 / 待 GPU / 待中间件（**不得含糊**）

| # | 项 | 阻塞于 |
|---|---|---|
| 1 | 任何**真实出图**相关的判据（`AC-5.2` / `AC-6.1` / `AC-F1` / `AC-N1` / `AC-N3`） | **GPU + SSH 凭据**（凭据是当前唯一阻塞项，见 `SUMMARY.md` 两次 GPU 尝试） |
| 2 | **真实 PostgreSQL** —— 547 条后端测试**全部**跑在 SQLite 上 | 本机无 PG 二进制也无 docker daemon |
| 3 | **真实 Redis / Celery worker** —— 队列用 `no_broker` 夹具与 fake 依赖 | 同上 |
| 4 | **真实 MinIO / S3** —— 存储用 `InMemoryAssetStorage` | 同上 |
| 5 | **前端 vitest** —— 本轮**未复跑** | 本机 4 核且常被并行代理占满；且已知有 CPU 争用型 flaky（`SUMMARY.md:212-223`） |
| 6 | **CI** —— `.github/workflows/ci.yml` 从未运行 | 仓库**无远端** |
| 7 | **`tests/unit/` 与 `tests/integration/` 是空占位** | 两目录**只有 `.gitkeep`**，**没有任何测试**（按 §1 的设计，测试不写在这里） |

---

## 8. 缺口台账（本索引暴露出来的）

| # | 缺口 | 影响 | 解除条件 |
|---|---|---|---|
| 1 | **11 条 AC 无用例**（§2.6 全列） | 其中多数需要 GPU / 真人 / 目标机；**纯离线可补的两条（`AC-F6` / `AC-N5`）已于 2026-09-17 消除** | 见 §2.6 的「解除条件」列 |
| 2 | ~~**`AC-N5` 鉴权零用例**~~ ✅ **已解除（2026-09-17）** | `backend/tests/test_auth_coverage.py`：从 `app.openapi()` **枚举全部路由**、**默认要求 401**，白名单显式化 + 两条元测试防"枚举为空 ⇒ 假绿"；端口侧证 `0.0.0.0` 被拒、当前 `comfyui_base_url` 为回环 | 已补（⚠️ 真实网络暴露面仍未在真机验证） |
| 3 | ~~**`AC-F6` 模板库零用例**~~ ✅ **已解除（2026-09-17）** | `backend/tests/test_catalog_templates.py`：列表只回启用模板 + 排序字面量 · 品类筛选 · `/templates/categories` · `POST /templates` 的 201 / 403 / 404 | 已补 |
| 4 | **`AC-6.5` 磁盘侧阈值未实现**（`disk_min_free_gb` / `disk_warn_free_gb` 无读取点） | 磁盘满时不会拒绝新任务（EX-4 的另一半） | 先实现再补测 |
| 5 | ~~**`AC-6.6` 队列深度无行为用例**~~ ✅ **已解除（2026-09-17）** | `queue_max_depth` 现有两个读取点（软阈值告警 / 硬阈值熔断），`ErrorType.QUEUE_FULL` 从零产生者变为 422 的 `detail.code` | 已实现；用例见 `backend/tests/test_queue_guard.py`（含 3 次变异验证） |
| 6 | **多数测试文件未做变异验证**（§5） | 「有用例」的强度未知 | 逐文件补，或引入变异工具（**本任务不引入**） |
| 7 | **无覆盖率工具** | 无法回答"代码被跑到多少" | 属工具选型决策，**本任务明确不做**（不引依赖） |

---

## 9. 复核方法（**离线可跑**）

```bash
# ① 后端全量（含本索引涉及的绝大多数用例）
cd backend && timeout 900 .venv/bin/python -m pytest -q          # → 547 passed

# ② 边界值 10 项专项（本批新增落点）
cd backend && timeout 600 .venv/bin/python -m pytest -q tests/test_boundary_values.py   # → 29 passed

# ③ 工作流内核
backend/.venv/bin/python -m pytest engine/tests -q               # → 169 passed / 1 skipped

# ④ ruff（⚠️ 必须从**仓库根**跑，口径见 .github/workflows/ci.yml:33-38）
backend/.venv/bin/ruff check .

# ⑤ 缺陷库机械校验（AC-P4 的判据实现）
backend/.venv/bin/python -m engine.tools.check_defect_kb

# ⑥ 本索引里「无用例」的核查方式（不要凭印象）
grep -rn "test_catalog\|test_templates" backend/tests/     # AC-F6 → **有命中**（test_catalog_templates.py，2026-09-17 补齐）
grep -rn "401" backend/tests/                              # AC-N5 → **有命中**（test_auth_coverage.py，2026-09-17 补齐）
grep -rn "disk_min_free_gb\|disk_warn_free_gb" backend/    # AC-6.5 → 只有定义与注释
```

> ⚠️ 上面 ① 的 **547** 与 ② 的 **29** 绑定本次 revision。数字会随用例增减而变 ——
> 复核时**以命令输出为准**，不要拿本文件的数字当期望值去"对答案"。
