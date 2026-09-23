# Nightly Run Summary

- **日期**：**2026-09-17**
- **耗时**：**未精确计时**（不编数字）。可界定的边界：本轮（自 `7a81aa8` 起）共 **17 个提交**；其中**本次文档同步**为 **2 个提交**（`1b0aae7` / `3e3dd8c`）。会话自身没有留下可复核的起止时间戳，故不给任何工时估算。
- **收工 revision**：**`3e3dd8c`**（`git rev-parse HEAD` 实测）
- **收工时的工作区**：**干净**（`git status --short` 无输出）—— 本文件 `SUMMARY.md` 在收工时**已作为未提交改动留在工作区**（见「文件变更」）。**未 push**（仓库无远端，用户已决定不配）。

> 🔒 **本文件的措辞纪律（也是本文件里唯一允许出现下列词的位置）**：不得写「已验证 / 已通过 / CI 已跑 / L4 已执行 / 已跑通 / 出图成功 / 通过率 / 良品率」—— 这些在本轮**没有一条成立**。

---

## 一、本轮完成的任务

### 1 · P6-08 配额与限流 —— **已收口**（`todolist.md` 已打勾）

| 子项 | 事实 |
|---|---|
| ① 配额**原子化** | 扣减由 Python 层「读-改-写」改为**一条原子条件 UPDATE**（`UPDATE users SET quota_used = quota_used + :n WHERE id=:uid AND quota_used + :n <= quota_total`）+ `rowcount` 判据；新增 `backend/app/services/quota.py::charge()`，4 个调用点（submit / batch / retry / retry-failed）统一，`_assert_quota` 已删除。**显式回读** `quota_used`（Core 语句绕过 ORM 且 `expire_on_commit=False`）。402 走**结构化错误体**（`code=quota_exceeded`） |
| ② **EX-5 队列深度闸门** | **软阈值**（在途 > `queue_max_depth`，默认 10000）→ `log.warning`，**仍接单 202**（依据 C12「排队比崩掉好」；`queue_full` 在契约 §1.5 是**可重试**类型）；**硬阈值**（2 ×，默认 20000）→ **422 + `code=queue_full`**，`message` 给出「当前排队 N 个 / 预计等待 X 分钟」。在途口径 = `queued` / `running` / `retrying`，**按全局计数**；闸门**排在扣额之前**（被拒**不扣额、不建任务**）；提交与重试四条路径全挂上 |
| ③ **每用户在途任务数上限** | 新配置 `max_in_flight_per_user = 1000`；`当前在途 + 本次张数 > 上限` → **422 + `code=TOO_MANY_IN_FLIGHT`**，**整批**判定、恰好等于上限放行；**只加在两条提交路径**（`POST /tasks` · `POST /batches`），`retry` / `retry-failed` 刻意不加 |
| ④ **「按接口限流」裁定不做** | 理由：需求里**没有任何 QPS / 频次 / 窗口条款**，且 GPU 已被**全局串行化**（单 Redis 键 + `worker_concurrency=1`）⇒ 该粒度无作用。真正有意义的**公平性**问题（单用户一次 1000 张独占队列）由 ③ 承担 |
| ⑤ **计额口径裁定** | `retry-failed` **保持**按「新一次生成」计额（FR-3.5 省的是「已成功那部分不重复计额」，不是「重试不计额」）；**提交时预扣、终态失败 / 取消不退还**（🔶 协调者裁定，**可被推翻**，已落表 `PRD_v1.md` FR-1.2，V2 补偿机制仅登记不实现） |

### 2 · 四个缺陷修复

| # | 缺陷 | 修法 |
|---|---|---|
| 1 | **`alembic/env.py` 的 `fileConfig` 污染 logger** —— 默认 `disable_existing_loggers=True` 会把 `app.api.v1.tasks` 等**永久**置 `disabled`，而 `caplog` 不感知 `logger.disabled` ⇒ 一旦 `test_migration` 先跑过，此后所有日志断言**静默拿到空列表**（假绿/假红） | 改 `disable_existing_loggers=False`；新增 `test_alembic_logging.py`（2 条：真走 `alembic upgrade` 的回归用例 + 用旧行为证明该断言非空的**元测试**） |
| 2 | **`params` 通道的文本长度无上界**（顶层 `prompt` 有 2000 限制，走 `params.prompt` 时不触发，2001 字符会真的落库、直到 worker 渲染才被拒） | `_validate_text_lengths` 按**工作流的 `param_schema`** 施加**上界**，返回 422 + `code=TEXT_LENGTH_OUT_OF_RANGE`；覆盖顶层与 `params` 两条通道，不硬编码字段名。**随后裁定只查上界**（下界在 Schema 无 `required` / `allow_empty` 标记，且「清空反向词」是既有合法用法） |
| 3 | **`retry` / `retry-failed` 没有全局队列闸门**（上一轮只加在提交路径 —— 重试会把任务重新送回队列，队列该爆还是会爆） | 复用同一个 `_guard_queue`，位置同样在扣额之前 |
| 4 | **`AC-F6`（模板库）与 `AC-N5`（鉴权 / 端口）零覆盖** | 新增 `test_catalog_templates.py`（**10 条**）与 `test_auth_coverage.py`（**36 条**，用**遍历路由表**实现）；顺带修好 `conftest.py` 的拆卸缺陷（外键环导致 `drop_all` 顺序不合法 —— 该缺陷**一直存在**，只因全仓库从没有用例创建过 `templates` 行而从未被触发） |

### 3 · ADR-008：V1 范围收缩三条裁定

`docs/adr/0008-v1-scope-reduction.md`：① `style_transfer_v1`（P3-07）**顺延 V2**，V1 **5 条上线**，`AC-F1` 口径随之修正 ② V1 **不做 NSFW 视觉检测**，只做**输入侧文本过滤 + 人工抽检**（V2 走「构建期下载的离线小分类器」，引入前**必须先核权重许可**）③ V1 **不引入 numpy / Pillow 等图像重依赖**（将来用 **optional extra**，不进必装依赖；`P8-03` 拆成 `03a` / `03b`）。⚠️ **在 V2 引入真检测器之前，禁止把 `NullNsfwDetector.detect()` 改成返回 `safe`**。

### 4 · 跨目录同源口径清理（任务 2）

| # | 位置 | 处理 |
|---|---|---|
| 1 | `engine/model_registry.yaml:90` 的 `extra.role` | **已改**：「V1 六条工作流的主力底模」→「V1 的 **5 条**工作流的主力底模（第 6 条「风格迁移」顺延 V2 …）」。⚠️ 该文件**全仓库无任何代码读取**（`model_registry` 只出现在 docstring 引用与 DB 表名里） |
| 2 | `workflows/registry.yaml:1282` 的注释 | **已改**：补上「该计数已于 2026-09-17 按 ADR-008 裁定 1 修正为「V1 5 条上线 + 1 条顺延 V2」」。⚠️ 该段是**注释**，不参与 `engine/registry.py` 的解析；未动任何字段名 / 值 / 缩进 |
| 3 | `docs/adr/0005-base-model-selection.md:29` | **按 ADR 体例处理**：正文「六条工作流」**原文保留不动**，在文末**追加「## 勘误（2026-09-17 追加，原文保留不动）」小节**，注明按 ADR-008 修正为 **5 条 + 1 条顺延** |
| 4 | `tests/golden_set/README.md:34` 的「（i2i_v1，**6 条**）」 | **核实后确认不改**：它指的是 **i2i_v1 的 golden 用例条数**（全目录 42 = 18 + 8 + 6 + 5 + 5），**与工作流数无关**。无脑替换会造出新的错误 |

### 5 · 三份进度文档同步

- `todolist.md`（计划）：P6-08 打勾并写口径；P6 阶段结果 **7/13 → 8/13**；P8-10 的 AC 分档订正为 **覆盖 8 / 部分 12 / 无用例 11**；变更记录加 v1.6。
- `项目进度.md`（度量）：§0 速览、§2（P6 **8/13 · 62%**）、§0.2 交接快照（revision `caf67be`、测试基线、台账 #42~#47、未验证清单 **15 → 19 项**、纪律 15~19）、§4 每日记录、§5 风险 R16、§6 ADR 表（新增 ADR-008 行）、§10 变更记录。
- `项目进展.md`（纪实）：§0 速览、§1 时间线新增本轮小节、§3 台账新增 **#42~#47**（六段格式）、§4 未验证清单补 ⑯⑰⑱⑲、§5 决策记录补 5 行、§6.2 / §6.3 更新已收口的待办。

**完成数：`61/153 → 62/153`**（P6 7→8）。总进度 **40% → 41%**（62 ÷ 153 = **40.52%**）。
算式（按 `项目进度.md` §2 各行求和）：**8+18+10+8+0+4+8+3+3+0+0+0+0 = 62**。⚠️ **分母仍为 153，未变**；**P8 仍 3/10**；P6-09 / P6-13 仍不计入（部分完成 / 部分交付）。

---

## 二、跳过 / 未做的任务（**这些都没有完成**）

| 项 | 原因 |
|---|---|
| **三条工作流的 L4 真实出图**（`deploy/autodl/36_l4_three_workflows.sh`） | **从未执行**。卡点 = **本机无可用 SSH 凭据**（`SSHPASS` 未设置、`~/.ssh` 公钥被拒） |
| **蒙版极性判读**（`37_probe_inpaint_mask_polarity.py`）· **稳态基准复测**（`34_*`） | 同上，均**从未执行** |
| **真实 PostgreSQL 上的并发验证** | 本机无 PG；测试夹具是单连接 `StaticPool`、**不具备并发能力**，所以**没有写「两个并发 POST」的测试**（写了就是假证据） |
| **真实 MinIO / 真实 Redis 的最小集成** | 无 docker daemon / 未起服务；全部存储路径由 `InMemoryAssetStorage` 替身覆盖 |
| **`maintenance` 队列是否被 worker 监听** | 仓库里**没有 Celery worker 的启动脚本 / compose / runbook** —— 属部署层 TODO（风险 R14） |
| **CI 运行** | **用户已决定不配远端** ⇒ CI 保持「**从未运行**」标注，不得改写成其它说法 |
| **浏览器人工走查**（画廊页） | 需要起前后端 + 真人操作；目前「图能显示出来」只有 jsdom 级证据 |
| **P8-03a / P8-03b（自动质检）** | 按 ADR-008 顺延：`03a` 阻塞于「待真实产物」、`03b` 阻塞于「V2 需模型 + 许可审查」 |
| **P1-08 用户访谈 / P1-17 高保真原型** | 需真人 / 需选工具（长期挂起） |

---

## 三、测试状态（**本机实测**，收工前在 `caf67be` + 本轮 YAML 注释改动后复跑）

| 项 | 命令 | 结果 |
|---|---|---|
| 后端 | `cd backend && timeout 600 .venv/bin/python -m pytest -q` | **547 passed**（44.98s，274 warnings） |
| 引擎 | 仓库根 `timeout 300 backend/.venv/bin/python -m pytest engine/tests -q` | **169 passed / 1 skipped**（12.73s）—— **仓库根与 `backend/` 两个 CWD 数字必须一致** |
| 引擎自校验 | 仓库根 `timeout 120 backend/.venv/bin/python -m engine` | **`VALIDATE=PASS`**（L1/L2/L3；输出同时提示三条工作流 `verification=pending_gpu`） |
| ruff | 仓库根 `timeout 120 backend/.venv/bin/ruff check .` | `All checks passed!` |
| 前端 | `cd web && corepack pnpm exec vitest run` | ⚠️ **本轮未复跑**。最近一次全量实测为 **83 passed（7 files）**；本轮只改过 `validate.ts` 的**注释**（上一批） |
| 前端类型 / lint / build | `typecheck` / `lint` / `build` | ⚠️ **本轮未复跑**（沿用上一轮 exit 0；lint 有 1 条既有 warning，`router.tsx:17`） |

> ⚠️ **以上只覆盖本机可跑的部分**：
> - **不代表 CI 跑过** —— `.github/workflows/ci.yml` 已建，但仓库**无远端**，**流水线从未执行**。
> - **不代表能出图** —— 引擎自校验与两个离线校验器的输出都自己声明了这一点；**L4（真实出图）三条工作流都没跑过**。
> - ⚠️ **前端 vitest 的已知抖动**：本机 **4 核**且常有并行负载，`vite.config.ts` **未设 `testTimeout`**（走默认 5000ms）⇒ 同一用例可能一次全绿、一次 `Test timed out in 5000ms`（实测到过：单文件重跑该用例通过、净耗时 8~14s）。**这不是回归**；**未做的加固**：给重交互用例显式放宽超时。

---

## 四、文件变更（**实测**）

```
$ git rev-list --count 7a81aa8..HEAD          → 17
$ git diff --stat 7a81aa8 HEAD | tail -1      → 36 files changed, 2944 insertions(+), 225 deletions(-)
$ git rev-list --count caf67be..HEAD          → 2      （本次文档同步）
$ git diff --stat caf67be HEAD | tail -1      → 6 files changed, 194 insertions(+), 39 deletions(-)
```

**本次文档同步改动的 6 个文件**：

```
 M todolist.md                          # P6-08 打勾 / P6 8-13 / P8-10 AC 分档 / 变更记录 v1.6
 M 项目进度.md                           # §0 §0.2 §2 §4 §5 §6 §10
 M 项目进展.md                           # §0 §1 §3(#42~#47) §4 §5 §6.2 §6.3 + #6 的验证行
 M engine/model_registry.yaml           # extra.role 字符串（口径）
 M workflows/registry.yaml              # 注释（口径）
 M docs/adr/0005-base-model-selection.md # 文末追加「勘误」小节（原文保留）
?? SUMMARY.md                           # 本文件（⚠️ 见下方说明）
```

⚠️ **一个需要你知道的事实**：`SUMMARY.md` **已被 `de3cf39`（另一次「开源前整理」提交）纳入了版本控制**，并非「未追踪」。本任务按约定**没有 stage 它**，所以它现在以「已修改未暂存」的形式留在工作区。是否要把它从仓库里移除，属你的决定，我没有擅自处理。

---

## 五、剩余 TODO（按优先级）

1. **【最高优先·唯一卡点】拿到 SSH 凭据后跑三条工作流的 L4** —— `deploy/autodl/36_l4_three_workflows.sh`（**从未执行**），并附同批的**蒙版极性判读**（`37_*`）与**稳态基准复测**（`34_*`）。
   ⚠️ **凭据要解决的不是「密码是多少」，而是「怎么让密码进到进程环境」**：`export SSHPASS` 必须在**将要执行任务的进程启动时**被继承（在别的终端 export 对已在运行的进程无效）。
   ⚠️ **顺序永远是：先验凭据 → 再确认有卡 → 才点开机**（2026-09-17 凌晨第 1 次做反了，白烧约 2 分 47 秒机时）。
2. **生成分辨率的 `params` 通道缺口** —— `width` / `height` 越界时 **API 不拒**（实测 `511` / `2049` / `4096` 均返回 **202**）；现有用例是**「钉住缺口」**而不是「守住期望」。二选一：① 在 API 层补校验 ② 明确裁定「分辨率以工作流 Schema 为准」并写进契约。
3. **`task_max_retries_hard_limit` 无任何消费者** —— 除定义（`config.py:113`）与测试自身外，全仓库只有 `contracts.md` §1.1 的一句声明；`mark_retrying` 只看 `task_max_retries`。要么补消费者，要么从配置里删掉（它与 `queue_max_depth` 曾经的状态**完全同型**，不建议留成第二例）。
4. **真实 PG / MinIO / Redis 的最小集成验证** —— 起 P2-09 的 compose，各跑一遍：PG 上 `alembic upgrade head` + 并发提交（验条件 UPDATE 与两道闸门的并发行为）、MinIO 上 put/get/delete + 取图/打包、Redis 上「提交 → 排队 → worker 取锁 → 完成」。
   ⚠️ 附带：确认 worker 的 `-Q` **包含 `maintenance`**（否则清理任务会静静躺在队列里永不执行且不报错，风险 R14）。
5. **配远端（用户已决定不配）** —— 因此 **CI 保持「从未运行」的标注**，文档里不得出现任何暗示它跑过的说法。若将来配了，再补一次观察结果。
6. **P8 剩余的离线可做项** —— P8-05 质量看板（`todolist.md` 里该项标题含一个本文件禁用的指标名，此处**不复述**；数据源已由 P6-10 打通）· P8-07 A/B 对比工具。
7. **前端**：画廊页浏览器人工走查 · 展示 `meta["models"]`（后端已就绪）· 给重交互用例显式放宽 `testTimeout`（不要靠重跑掩盖抖动）。
8. **P6-07（API Key 鉴权）** · **P6-11（多租户隔离）** · **P6-12（审计日志覆盖范围与对外查询能力验收）**。
9. **A 流的批量 `reference_image` 缺陷**（见 `项目进展.md` §4）· `/assets` 增加 `exclude_adopted`。
10. **被测基础设施的遗留缺口**：ETag 语义无测试钉死（变异里唯一 SURVIVED）· 打包流慢客户端 / 断连实测 · 产物元数据关联模型 sha256。

> ⚠️ **P8 的十项：一次真实评测都没跑过。** golden set 的 `expected` **全为 `null`**、`expected_status` 全为 `pending_gpu`；rubric 的锚点未经任何真实产物标定；缺陷库 `verified_by=gpu` **严格为 0**。⇒ **任何得分 / 达标比例 / 覆盖比例类数字都不存在，也不允许被造出来**（措辞纪律见本文件顶部）。
> ⚠️ **P8-09 的「覆盖 2/5」是归档**（此前一次 GPU 窗口的结果，证据在 `docs/sop/debug_log.md:376`），**不是本批跑出来的**。

---

## 六、需要用户决策的事项（**只留真正还需要拍板的**）

| # | 决策点 | 选项与影响 |
|---|---|---|
| 1 | **生成分辨率的 `params` 通道缺口是否修？** | ① 在 API 层补校验（与文本长度同一形态，改动小、语义清楚）② 明确裁定「分辨率由工作流 Schema 决定，用户越界值以 Schema 为准」并写进契约（零改动、但要把现有用例的定位从「守住期望」改成「钉住缺口」）。**未拍板前，该用例不得被读成"已守住"** |
| 2 | **是否启动 `P8-03a`（离线粗指标：尺寸 / 留白 / 过曝欠曝 / 模糊）？** | ADR-008 裁定 3 已定「将来做时用 **optional extra**、不进必装依赖」。真正待定的是**何时启动**：它**阻塞于「待真实产物」**（阈值标定必须用真实图），所以更合理的顺序是**先跑 L4 拿到真实产物**。若你希望现在就搭工具链骨架，代价是得到一个**阈值未标定**的空壳 |
| 3 | **V2 的 NSFW 检测器选型** | 需要**先核实权重许可**（铁律「未知即不用」），再决定用哪个离线小分类器并登记进 `engine/model_registry.yaml`。V1 已裁定不做（ADR-008 裁定 2），所以**不阻塞当前进度** |
| 4 | **`task_max_retries_hard_limit` 补消费者还是删掉？** | 补：让 `retry` 请求侧参数受硬上限约束；删：减少一个死配置。**二选一即可**，现状（定义了却没人读）是最差的一种 |
| 5 | **`SUMMARY.md` 已被纳入版本控制（`de3cf39`）** | 它是「交付给你看的」还是「要入库的」？若是前者，建议从仓库移除并加进 `.gitignore`；我**没有擅自处理** |

> ⚠️ **GPU 侧提醒**：实例当前**已关机**，你此刻**不需要做任何 GPU 操作**；需要跑 L4 时请**提前约 10 分钟**告知（切换 GPU 模式**必须先关机**），且**开机之前先确认凭据可用**。
