# 工作会话记录（Work Session Summary）

- **日期**: **2026-09-17（上午）**。本次会话做的是**三批离线工作的收口沉淀** —— 全程只改 `.md`，**未改任何代码或配置**。（唯一可锚定的时间点是前端 vitest 自报的 `Start at 09:48:05`。）
- **耗时**: **未精确计时**（**不编数字**）。可界定的边界：三批工作的 commit 从 `4f601e4` 到 `a618d0f`；本收工会话自身的起止**没有留下可复核的时间戳**，故不给任何工时估算。
- **收工时的 revision**: **`a618d0f`**（`git rev-parse HEAD` 实测）。上一版 SUMMARY 锚在 `fb6da15`，**已过期**。
- **收工时的工作区**: **只有未追踪的 `SUMMARY.md`**（`git status --short` 实测输出为单行 `?? SUMMARY.md`）。本会话**没有提交任何东西**，也**没有 push**（仓库无远端）。

## 本轮沉淀的三批工作（**均为已入库 commit**）

> 三批都**没有使用 GPU**：本机**无可用 SSH 凭据** ⇒ 一条工作流都没跑（见「GPU 侧」一节）。

### A · P6-08 配额与限流（`4f601e4` / `2c74d81` / `fb6da15`）

| 项 | 事实 |
|---|---|
| 🔴 **真 bug** | 配额扣减原为 Python 层**读-改-写**（`user.quota_used += N`，4 处），**全仓库零 `SELECT FOR UPDATE`、零条件 UPDATE** ⇒ 两个并发提交可同时读到同一 `quota_used` 再各写一次，**配额可被超发** |
| 处置 | 改为**一条原子条件 UPDATE**（`UPDATE users SET quota_used = quota_used + :n WHERE id=:uid AND quota_used + :n <= quota_total`）+ `rowcount` 判据；新增 `backend/app/services/quota.py` 的 `charge()`；`_assert_quota` **已删除**（避免两套判据）。**不用 `SELECT FOR UPDATE`**（SQLite 不支持，会让本地/测试与生产分叉） |
| 补测试 | 新建 `backend/tests/test_quota.py`（**8 条**，此前**零覆盖**）；关键一条 `test_stale_cached_remaining_cannot_overdraft` 用「DB 已扣满、ORM 缓存仍认为够」**确定性复现原 bug 形态** |
| 402 结构化 | `detail` 改为契约 §2.2 的**对象形式**（`code=quota_exceeded` / `message` / `fields`），前端改按 `detail.code` 优先、402 状态码后备；契约 §2.2 与 §7 已同步 |
| 记录中的变异验证 | 把实现**还原成原始的非原子读-改-写**后，**只有** `test_stale_cached_remaining_cannot_overdraft` 变红（`assert 202 == 402`）；另一次变异下超发被真实复现（`陈旧缓存下超发了额度（实际 101/100）`）。⚠️ **这两次变异是本批自己的记录，未由本次会话复跑**（本次只改 `.md`，不动代码） |
| ⚠️ **未验证** | **真实 PostgreSQL 上的并发行为** —— 本机无 PG；且测试夹具是单连接 `StaticPool`，**不具备并发能力**，所以**没有写「两个并发 POST」的测试**（写了就是假证据）。验证方法：起真 PG，把该用户置于 `quota_total - quota_used == 1`，两个真并发线程各 POST 一次，断言恰好一个 202 一个 402、最终 `quota_used == quota_total` |
| 🔴 **4 项已查实但未动的决策（待裁决）** | ① **重试二次扣额**：`retry-failed` 对失败项再扣一遍（批量 50 张、30 张 OOM 失败 → 用户被扣 80），而同文件注释写着续跑是为「省用户额度」；FR-1.2 只说配额是「**生成张数**上限」，按此重试不该另计 ② **失败 / 取消是否退额**：全仓库**零退还**，且**文档里完全没有规定** ③ **EX-5 队列深度上限**：`queue_max_depth=10000` 是**死配置**（全仓库无消费方），而 PRD `:493` 自己写「拒绝提交 → pending 保留」（两句不可能同时成立），且与 `user_flow.md:139`「保留在队列，不拒绝」**直接冲突** ④ **限流阈值 / 按用户并发上限**：后端**零限流**；需求里**没有任何 QPS / 频次 / 窗口条款**；且 GPU 已被全局串行化（单 Redis 键 + `worker_concurrency=1`）⇒「按用户 GPU 并发」实际无作用，真正有意义的是「**每用户在途任务数上限**」（现在单用户可一次把 1000 张塞进队列） |

### B · P6-13 内容安全（`461f587` / `6987ac9` / `6e0f6fe` / `b5643b4` / `b90a5b9`）—— **部分交付，不可标完成**

| 项 | 事实 |
|---|---|
| ✅ 已交付（**输入侧**） | 敏感词 / 合规词过滤 + 开关 + 留痕 + **422 结构化错误**（`code=CONTENT_BLOCKED`）；词表 `backend/app/data/content_blocklist.json`（**stdlib json**，不放 YAML 也不放 `assets/` —— 全仓库确认**没有任何运行时代码会加载 `assets/`**）；**5 条规则 / 38 条词条**（实测：`cs-child-01/02` = `hit`，`cs-brand-01`/`cs-sexual-01`/`cs-violence-01` = `warn`）；分级复用 `negative.yaml` 的 `hit`/`warn`，**硬拒只放极窄的法律红线** |
| 🔴 **NSFW 视觉检测 V1 未实现，且没有假实现** | `NullNsfwDetector`（`content_safety.py:407`）返回三态里的 **`unknown`，永不返回 `safe`**，并有测试钉死；commit footer 写的是 `Refs P6-13（部分交付，不关闭）` |
| 不做的依据 | ComfyUI 快照 2530 个节点类里**没有任何安全 / NSFW 分类节点**（⚠️ 两个陷阱：`easy prompt` 节点里那个叫 `nsfw` 的入参是**帮用户把 NSFW 词加进提示词**的下拉，语义相反；`WanVideoBlockList` 是 WanVideo 的**层号列表**）；模型注册表 6 个模型全是出图用的；后端无 numpy/PIL/torch 且项目有成文的「避免重依赖」纪律；许可铁律「未知即不用」 |
| ✅ 隐私红线 | 审计只记规则 id / `text_hash` / `text_length` / stage，**绝不记命中原词或 prompt 全文**（依据 `tracking_plan.md:30-32`）。⭐ 它的机械断言**抓到了 5 处自己泄露词表**（`reason` 文案里抄了 `neg-027/030/031` 的原文，而那几处恰好就是词条本身 —— 等于把黑名单印在错误提示里），已改写并加**两条长期盯防断言** |
| ⚠️ 一次「额度泄漏」是**夹具假象** | 夹具是单连接（内存 SQLite + `StaticPool`），路由线程里**未提交**的 UPDATE 对测试线程可见 → 读到 1。用 `rollback()` + 另开 session 验证为 **0**，**再用一次变异确认 `rollback()` 不会掩盖真泄漏** |
| ✅ 文档更正 | `PRD_v1.md` 把 Must 级的 FR-9.4 标成 ✅ 且宣称「无孤儿功能，无未落地需求」→ 已改如实；`system_flow.md` 的合规表把「数据隔离 / 审计」标「待实现」（两者早已实现）→ 已改。契约 `code` 命名口径统一（`ErrorType` 的码小写取枚举，非 `ErrorType` 的码用大写下划线） |
| 🔴 **仍未做** | NSFW 视觉检测 · 产物侧拦截（R-33 属 Could/V2，落 P8-03）· 上传授权声明勾选（R-43）· 词表运维后台（P7-09）。⚠️ 词表是「**窄而可解释**」而非完备：绕写（拆字 / 拼音 / 谐音 / 插引号）与**图片内容**都覆盖不到；`warn` 级仍有已知误报（`血腥玛丽`） |

### C · P8 质量体系（离线侧大量推进，但**一次真实评测都没跑过**）

`936c35a` / `7835d68` / `fc98162` / `150543f` / `1b04106` / `80ad5aa` / `6e5308a` / `56b8f47` / `08ccd5d` / `a618d0f`

| 子项 | 事实（**均为离线定义，零实测**） |
|---|---|
| **P8-02 评分标准** | `docs/sop/quality_rubric.md`，4 维度 × 1-5 档；⭐ 判废词汇**不另造**，直接映射 `negative.yaml` 的 `hit`/`warn`/`soft`；四个维度**刻意不做加权**（加权系数需真实数据标定）。⚠️ **`severity: hit` 实测 20 条**（`grep -c` 会得 21，多出来的是 `:17` 注释里那一条 —— 计数必须用 YAML 解析） |
| **P8-06 缺陷知识库** | `docs/sop/defect_kb.md` **44 条**（解析器实测），六类全覆盖（hand 6 / text 6 / distortion 8 / exposure 7 / material 9 / drift 8）；`verified_by` = **`l3 33 · doc 10 · none 1 · gpu 0`**（`gpu` 严格为 0，有测试专门防止它被写上）；⭐ **反向词类解法按链路分栏**（`sdxl=30` 必须给 `negative_refs`；`klein=3` 必须为 `—`，因为 CFG=1 反向词无效且该 Schema **根本没有 `negative_prompt` 字段**） |
| **P8-01 golden set** | `tests/golden_set/cases/` **42 个用例**，5 条工作流（`t2i_v1` 18 / `klein` 8 / `i2i_v1` 6 / `inpaint_v1` 5 / `upscale_v1` 5）。🔴 `expected` **全为 `null`**、`expected_status` **全为 `pending_gpu`**，并有测试断言钉死「不许编造期望值」。另建 `tests/golden_set/reference/.gitkeep`，把 `.gitignore` 那条**空转的例外规则落实** |
| **两个机械校验器** | `engine/tools/check_defect_kb.py`（校验 KB 的 `recommended_params` 对照真实 Schema：值越界 / 参数名不存在 / enum 拼错 / **跨工作流张冠李戴** / 给 klein 开反向词）与 `engine/tools/check_golden_set.py`（Schema → render → L1/L3，能抓 `targets` 注入点漂移）。各自的结尾显式打印「**本校验只证明参数合法，不证明参数有效**」「**本校验不证明能出图**」。本会话**真跑通两者**（缺陷库 37 条带参条目合法 / golden set 42 条全绿）。**4 次变异验证全部变红并还原**（本批记录，未由本次复跑） |
| **P8-04 / P8-09 / P8-10** | `docs/sop/quality_sampling_sop.md`（抽检 SOP）· `docs/review/reproducibility_report.md`（可复现性，**覆盖 2/5**）· `docs/review/test_plan.md` + `tests/README.md`（测试计划 + 用例索引 + 23 条边界用例） |
| **P8-09 的真实状态** | **覆盖 2/5**（`t2i_v1`、`flux2_klein_t2i_v1` 上「同 seed 两次出图，产物 IDAT 逐字节一致」，证据在 `debug_log.md:376`）。**这是归档此前 GPU 窗口的实测结果，不是本次跑出来的**。3 条未验证（`i2i`/`inpaint`/`upscale` 的 L4 **从未执行**；`inpaint_v1` 另卡蒙版极性） |
| **P8-10 的 AC 覆盖** | PRD 共 **31 条 AC** ⇒ **覆盖 6 / 部分 12 / 🔴 无用例 13**（`6+12+13=31`，与 PRD 的 AC 总数对上）。13 条无用例的清单已落表；其中 **`AC-F6`（模板库）与 `AC-N5`（接口全部鉴权）纯离线可补但本轮未补**（已用 grep 坐实：`backend/tests/` 里 `test_catalog|test_templates` 与 `401` **零命中**） |
| ⚠️ `params.prompt` 长度校验缺口 | **属实**（三条独立证据：前端 `validate.ts:26-33` 注释自证 + 代码路径 + **真落库实测**：2001 字符的 `params.prompt` 拿到 202、且 `Task.params["prompt"]` 长度确实是 2001）。**未擅自改实现**，也**未用 `xfail`**（理由：`xfail` 会在缺口修好时静默变绿），而是断言现状 + docstring 标注 + 写明「修好后必须把断言从 202 改成 422」 |
| ⚠️ **P8 十项一次真实评测都没跑过** | **P8 的绝大多数子项不能打勾** —— 依据 `todolist.md:28`「DoD：没有产出 = 没做完，不许打勾」与项目最看重的那条纪律（**不许把「静态校验通过」写成「已跑通」**）。本次的标注方式：`[x]` 仅 **P8-02 / P8-04 / P8-06**（均为「**定义**」类产出，且 P8-06 的条目数与参数经机械校验），其余用 `[~]` 并显式写「**定义完成 · 未实测**」；`P8-08` 拆成 **`P8-08a`（离线门禁，可做）/ `P8-08b`（真实 golden set 回归，待 GPU）**，拆分**不改变阶段任务数**（P8 仍计 10，**分母 153 不动**） |

## 跨批次的工程教训（已进 `项目进展.md` 台账 #35~#41）

| # | 一句话 |
|---|---|
| **#35** | 🔴 **空回执 worker 留下的产物是坏的**：`engine/tools/check_defect_kb.py` 的 `:514` 中文字符串里嵌了 ASCII 双引号导致字符串被提前截断（**与更早的 `backend/tests/test_repo_hygiene.py` 是同一类事故**），**模块根本不能导入**，`ruff` 也报错（`F401`）。**读代码完全看不出来**（docstring 判据表、`__all__` 都写得很齐）。**归因已查实**：502 发生在「AI Agent 已创建、执行中途」，不是「没执行」⇒ 本仓库连续三次（P6-10 接口 / P7-05 画廊页 / 本轮）出现「回执丢了但活干了」。⇒ **凡拿到「别人写的、从没跑过的」产物，第一步必须真跑一遍 + 真 lint 一遍** |
| **#36** | ⚠️ **「改了 A 没改 B」**：`1b04106` 修对了 `validate.py` **顶部 docstring**，却**漏了 argparse 的 `description`**，于是 `python -m engine --help` 仍在说谎（"不含 L3"）。已由 `6e5308a` 补上。⇒ **修一处陈述要顺手 grep 同一陈述的全部副本** |
| **#37** | ⚠️ **计数陷阱**：`negative.yaml` 的 `severity: hit` **YAML 解析是 20 条**，`grep -c` 得 **21**（多的是 `:17` 注释里那一条）。⇒ **数结构化数据的条目要用解析器** |
| **#38** | 🔴 **「跳过」被计入「通过」**：`check_golden_set.py` 曾把「跳过 L3」记进 `levels_run`（**谎称已执行 L3**），且"跳过"不算不通过 ⇒ `--no-l3` 会 **exit 0**，即 CI 会把「少做一层」当全绿。已修 + 有测试钉住。⇒ **「跳过」绝不能被计入「通过」，退出码要把"少做了一层"当失败** |
| **#39** | ⚠️ **`.gitignore` 空转规则的实际行为被纠正**：此前流传「放进 `input/`、`expected/` 的大图不会被忽略」是**不准确的**；本会话**实测** `git check-ignore -q --no-index tests/golden_set/reference/input/big.png` → **exit 0（被忽略**，`*` 覆盖子目录），而 `reference/.gitkeep` → **exit 1（不被忽略、已入库）**；真正不被忽略的是 `reference/` **之外**的路径。⇒ **`.gitignore` 的判断要用 `git check-ignore --no-index -v` 对真实文件做，不要靠推理** |
| **#40** | ⭐ **内容安全的机械断言抓到了「把黑名单印在报错里」**：隐私红线断言（审计不得含原词）顺带发现 `reason` 文案里抄了 **5 处 `neg-027/030/031` 的原文**，而那几处恰好就是**词条本身**。已改写 + 加两条长期盯防断言。⇒ **「不记原词」的断言要顺带覆盖「报错文案里也不能出现原词」** |
| **#41** | ⚠️ **又一次「夹具假象」**：内容安全测试里读到「额度泄漏了 1」，实为**单连接**（内存 SQLite + `StaticPool`）下**未提交的 UPDATE 对测试线程可见**。用 `rollback()` + 另开 session 验证为 **0**，**再用一次变异确认新读法不会掩盖真泄漏**。⇒ **单连接夹具下「看得到未提交改动」是假象，不是泄漏**；判定前必须先证伪夹具 |

## 本次会话对文档做的改动（**只改 `.md`**）

| 文件 | 改了什么 |
|---|---|
| `todolist.md` | ① **P6-08 记载订正为如实状态**（「按用户配额」已实现且现已原子化 + 有测试；「按接口限流」「并发上限」未实现）+ **落表 4 项待裁决** ② **P6-13 保持未完成**，写清「输入侧已交付 / NSFW 视觉检测 V1 未实现」 ③ **P8 十项逐条标注**（`[x]` 3 项 / `[~]` **定义完成 · 未实测** / `[ ]` 未开始），**P8-08 拆成 `08a`/`08b`**（子项拆分，阶段任务数不变） ④ 变更记录加一行 **v1.4** |
| `项目进度.md` | ① §0 一屏速览（当前阶段 / 总体进度 **40%** / 已完成 **61/153** / 未决项 / 最大风险 / 下一步 / 台账条数 **41**）② §1 里程碑 M7 标「🔵 进行中（离线定义 3/10 · 零实测）」 ③ **§2 阶段汇总：P8 0/10 → 3/10（30%）**，并**写出算式** `8+18+10+8+0+4+7+3+3+0+0+0+0 = 61`、`61 ÷ 153 = 39.87%` ④ **§0.2 交接快照全量更新**（revision `a618d0f` / 测试基线 / 台账 #35~#41 / 未验证清单 **11 → 14 项** / 纪律 11~14）⑤ §4 每日记录补本次一行 ⑥ §5 新增风险 **R16**（内容安全无视觉检测）⑦ §10 变更记录加一行 |
| `项目进展.md` | ① §0 速览（最新进展 / 已完成阶段 / 待解决 / 纪律 ⑬~⑯）② §1 时间线新增本轮小节（含本会话的复跑核实清单）③ **§3 台账新增 #35~#41（六段格式）** ④ §4 待解决清单补 **⑫⑬⑭⑮** 四项 + 更新「已解决」行 ⑤ §5 决策变更记录加 3 行（NSFW 不实现 / rubric 不加权 / P8-08 拆分）⑥ §6 新增 **6.1f（P6-13）/ 6.1g（P8）** 两节 + 更新 6.2 / 6.3 表 |
| `SUMMARY.md` | **本文件**（**不入库** —— 留工作区给你看） |

## 测试状态（**本会话在 revision `a618d0f` 上实测，全部复跑**）

| 项 | 命令 | 结果 |
|---|---|---|
| 后端 | `cd backend && timeout 600 .venv/bin/python -m pytest -q` | **469 passed**（43.75s）|
| 引擎 | 仓库根 `timeout 300 backend/.venv/bin/python -m pytest engine/tests -q` | **169 passed / 1 skipped**（19.59s）|
| ruff | 仓库根 `timeout 120 backend/.venv/bin/ruff check .` | `All checks passed!` |
| 引擎自校验 | 仓库根 `timeout 120 backend/.venv/bin/python -m engine` | **`VALIDATE=PASS`**（L1/L2/L3；输出同时提示三条工作流 `verification=pending_gpu`）|
| 缺陷库校验器 | `backend/.venv/bin/python -m engine.tools.check_defect_kb` | exit 0（37 条带参条目全部合法；结尾打印「只证明参数合法，不证明参数有效」）|
| golden set 校验器 | `backend/.venv/bin/python -m engine.tools.check_golden_set` | exit 0（42 条全绿；结尾打印「不证明能出图」）|
| 前端 | `cd web && timeout 900 corepack pnpm exec vitest run` | **83 passed（7 files）**（114.20s）|
| 前端类型 / lint / build | `corepack pnpm typecheck` / `lint` / `build` | ⚠️ **本次未复跑**（本会话未改前端代码；沿用上一轮实测 exit 0，lint 有 1 条既有 warning，`router.tsx:17`）|

> ⚠️ **以上「pass」只覆盖本机可跑的部分**：
> - **不代表 CI 跑过** —— `.github/workflows/ci.yml` 已建，但仓库**无远端**（`git remote -v` 为空），**流水线一次都没执行过**。
> - **不代表能出图** —— 引擎自校验与两个校验器的输出都自己声明了这一点；**L4 真实出图一条都没跑**。
> ⚠️ **前端 vitest 的已知 flaky**：本机 **4 核**且常有并行负载，`vite.config.ts` **未设 `testTimeout`**（走默认 5000ms）
> ⇒ 同一用例可一次全绿、一次 `Test timed out in 5000ms`（实测到过：重跑该文件 11/11 通过、该用例净耗时 8~14s）。
> **这不是回归**；本次全量跑得 83/83（单文件 94s）。**未做的加固**：给重交互用例显式放宽超时。

## GPU 侧（**结论先行：三条工作流的 L4 仍未执行**）

- 本会话**没有做任何 GPU 动作**，也**没有点开机**。实例**保持关机**。
- **卡点未变**：从「没卡」变成了「**没凭据**」（2026-09-17 凌晨实测控制台 `GPU空闲/总量 = 3/10` 有卡、开机成功到过 `运行中`，
  但**本机无任何可用 SSH 凭据** → 三次尝试后关机，**一条工作流都没跑**；第 2 次改为**先验凭据**，未通过即**未开机**）。
- `workflows/registry.yaml` 三条仍为 `render_path_l4: false` / `status: disabled` / `verification: pending_gpu` —— **保持原样，未造假**。
- `deploy/autodl/36_l4_three_workflows.sh` 与 `37_probe_inpaint_mask_polarity.py` **仍从未执行过**。

## 文件变更（实测）

```
# 本次会话只改 .md，且未提交：
 M todolist.md      # P6-08 订正 / P6-13 未完成 / P8 十项标注 / 变更记录 v1.4
 M 项目进度.md       # §0 §0.2 §1 §2 §4 §5 §10
 M 项目进展.md       # §0 §1 §3(#35~#41) §4 §5 §6
?? SUMMARY.md       # 本文件（刻意不入库）
```

```
# 三批工作在 git 里的规模（实测）：
$ git rev-list --count fb6da15..HEAD          → 17   （上一版 SUMMARY 锚在 fb6da15）
$ git diff --stat fb6da15 HEAD | tail -1      → 68 files changed, 6738 insertions(+), 42 deletions(-)
$ git rev-list --count 959994e..HEAD          → 16   （剔除上一轮的 #34 文档同步提交）
$ git diff --stat 959994e HEAD | tail -1      → 65 files changed, 6577 insertions(+), 17 deletions(-)
```
**新增的主要文件**（均为三批产出，非本会话）：`backend/app/services/content_safety.py`（434 行）·
`backend/app/data/content_blocklist.json` · `backend/tests/test_content_safety.py`（728 行）·
`backend/tests/test_boundary_values.py`（392 行）· `docs/sop/quality_rubric.md` · `docs/sop/defect_kb.md`（44 条）·
`docs/sop/quality_sampling_sop.md` · `docs/review/reproducibility_report.md` · `docs/review/test_plan.md` ·
`tests/README.md` · `tests/golden_set/`（42 例 + README + `reference/.gitkeep`）·
`engine/tools/check_defect_kb.py`（642 行）· `engine/tools/check_golden_set.py`（659 行）· `engine/tests/test_quality_tools.py`（584 行）

**未入库（有意）**：`SUMMARY.md`（交付给你看的，放工作区）。**未 push**：仓库无远端。

## 剩余 TODO

1. **【需你拍板·最高优先】P6-08 的 4 项产品口径** —— **纯决策，不需要 GPU，不拍板就无法继续**：
   ① 重试是否二次扣额（现为**再扣一遍**，与「续跑是为省额度」的注释矛盾）② 失败 / 取消是否退额（**全仓库零退还、文档无规定**）
   ③ `queue_max_depth` 是**死配置**，且 PRD `:493` 与 `user_flow.md:139` **自相矛盾** ④ 限流做到什么粒度
   （**真正有意义的是「每用户在途任务数上限」**，因为 GPU 已全局串行化；现在单用户可一次把 1000 张塞进队列）
2. **【需凭据】拿到 SSH 凭据后跑三条工作流的 L4** —— `deploy/autodl/36_l4_three_workflows.sh`（**从未执行过**），
   附同批的**蒙版极性判读**（`37_probe_inpaint_mask_polarity.py`）与**稳态基准复测**（`34_bench_recheck.sh`）。
   ⚠️ **凭据要点的不是「密码是多少」，而是「怎么让密码进到进程环境里」**：`export SSHPASS` 必须在**将要执行任务的进程启动时**被继承
   （在别的终端 export 对已在运行的进程**无效**）。⚠️ **顺序永远是：先验凭据 → 再确认有卡 → 才点开机**
3. **在真实 PostgreSQL 上验一次条件 UPDATE 的并发行为**（#34 唯一的原子性缺口，本会话已复核确实是**分析结论**而非实测结论）
4. **补两个纯离线就能补的 AC 用例**：`AC-F6`（模板库 catalog）与 `AC-N5`（接口全部鉴权 / 端口暴露）—— 已 grep 坐实零命中
5. **P6-13 的 NSFW 视觉检测** —— 需先决策是否引入图像分类依赖（当前纪律是「避免重依赖」）；**在决策前不得声称「NSFW 过滤已上线」**
6. **P8 剩余离线项**：P8-05 良品率看板（数据源已由 P6-10 打通）· P8-07 A/B 对比工具 · P8-03 自动质检（同上，需先立依赖决策）
7. **真实 MinIO / 真实 PG / 真实 Redis 的系统性缺口** —— **469 条**后端测试全跑在替身上，
   而生命周期清理（含**物理删除**）恰是最依赖真实外部系统的逻辑
8. **`maintenance` 队列是否被 worker 监听**（R14，部署层 TODO）：仓库里**没有 Celery worker 启动脚本 / compose**，
   若 `-Q` 排除了该队列，清理任务会**静静躺在队列里永不执行且不报错**
9. **配置远端并跑一次 CI**（`.github/workflows/ci.yml` 已建，**从未运行**）
10. **前端展示 `meta["models"]`**，并更正 `MetadataDrawer.tsx` 顶部同样已过期的注释
11. **画廊页的浏览器人工走查**（「图能显示出来」目前只有 jsdom 级证据）
12. **补 `IntersectionObserver` 懒加载的真实覆盖** · **打包流慢客户端 / 断连实测** · **ETag 语义测试**（变异里唯一 SURVIVED）
13. **产物元数据关联模型 sha256**（`engine/model_registry.yaml` 登记了 6 个模型的 sha256，**无任何消费方**）
14. **A 流的批量 `reference_image` 缺陷**（见 `项目进展.md` §4）· 后端 `/assets` 增加 `exclude_adopted`
15. **修前端 `vite.config.ts` 的超时配置**（不要靠重跑掩盖 flaky）

## 需要用户决策的事项

### 1. GPU 实例与凭据：**实例现在「已关机」，你此刻不需要做任何 GPU 操作；需要你拍板的是「凭据怎么给」**

| 方案 | 做法 | 适用 |
|---|---|---|
| **A（推荐）** | 从**导出了 `SSHPASS` 的那个 shell** **重新启动 CodeBuddy**，再重跑本任务。⚠️ 关键：光在**别的窗口** `export` **没用** —— 必须让**将要执行任务的这个进程在启动时就继承到**（已用 `/proc` 全进程 environ 扫描证明在运行的进程环境里**没有** `SSHPASS`）| 希望我接着自动跑完上传 + 执行 + 回传证据 |
| **B** | 你**自己在终端代跑** `deploy/autodl/36_l4_three_workflows.sh`（须把 `engine/`、`36_`、`37_`、`03_start_comfyui.sh`、参考图一并上传到 `$RD/`），再把 `$RD/l4_evidence/` 与 `$RD/l4_out/` 取回，落到 `deploy/comfyui/evidence/` 与 `render_l4_out/`，我负责入库 | 不想把密码交给会话环境 |

⚠️ 两条硬约束：① **开机之前先确认凭据可用**（第 1 次「先开机、后排查凭据」白烧了约 2 分 47 秒机时）② 切 GPU 模式**必须先关机**，且需**提前约 10 分钟预警**。
⚠️ 另：autoDL 控制台的实例密码是**掩码**显示，且浏览器自动化技能**明令禁止**从页面提取凭据 ⇒「让AI Agent 自己去控制台读密码」这条路**堵死**，不必再试。

### 2. P6-08 的 4 项口径（详见「剩余 TODO」第 1 条）—— 这是**唯一纯决策、零成本、且阻塞后续实现**的一项

### 3. 其他

**(a) 是否要配远端并跑一次 CI？** `.github/workflows/ci.yml` 已建且本机命令全绿，但**流水线一次都没执行过**（`git remote -v` 为空）。
若近期不配远端，建议在文档里保持「**从未运行**」的标注，不要出现「CI 已通过」这类表述。

**(b) 是否接受「无真实 MinIO / PG / Redis」继续跑下去？** 469 条后端测试**全部**运行在 `InMemoryAssetStorage` + SQLite + 无 Redis 之上。

**(c) 是否引入图像分类依赖以做 NSFW 视觉检测？** 当前项目有「避免重依赖」的成文纪律，且许可铁律是「未知即不用」——
两条都可能与「实现 NSFW 视觉检测」冲突，需要你决定优先级。

**(d) 本次未 push**：`git remote -v` 为空（仓库无远端）→ 全部 commit 只在本地。若有丢失风险，需先配远端。
