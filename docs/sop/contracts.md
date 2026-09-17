# 跨流接口契约（冻结）

> **本文件的定位**：开发进入多流并行阶段后，**跨流共用**的边界必须唯一且稳定。
> 一旦分头定义，就是并行开发最典型的事故来源（前端照会变的接口写、两套状态取值、两套错误格式）。
>
> **冻结日期**：2026-09-16 ｜ **冻结粒度**：只冻结跨流共用的硬边界（内部实现不限制）
> **权威性**：本文与代码冲突时，**以本文为准**，并把代码改过来。

---

## 0. 冻结范围

### 0.1 冻结了什么

| # | 契约 | 谁依赖 |
|---|---|---|
| 1 | **任务状态枚举与状态机** | A 后端（实现）· D 前端（徽章/看板）· 埋点口径 |
| 2 | **REST API 契约**（路径 / 请求响应体 / 错误格式 / 状态码） | A 后端（实现）· D 前端（调用） |
| 3 | **工作流参数 Schema 格式** | B 工作流内核（产出）· A 后端（校验与注入）· D 前端（P7-02 动态表单） |
| 4 | **埋点事件名** | A 后端（触发）· D 前端（触发）· P10 看板 |

### 0.2 没有冻结什么（各流自由）

- 各流内部的目录结构、类名、函数名、测试组织
- A 流的 ORM 细节、SQL 语句、Celery 任务划分
- B 流的工作流节点选型（只要参数经 Schema 暴露）
- D 流的组件拆分、状态管理选型、样式方案
- 日志文案、注释语言

### 0.3 变更流程（改契约必须走）

1. 在本文档里改，并更新 §7 变更记录（日期 / 改了什么 / 为什么 / 影响哪些流）
2. 同步改代码里的对应常量（`backend/app/models/enums.py` 是**唯一事实来源**）
3. 若影响其他流，**通知该流暂停相关部分**再改
4. 改完跑：`cd backend && .venv/bin/python -m pytest -q && .venv/bin/ruff check app tests`

> ⚠️ **不要在流内部私自增删枚举取值**。前端徽章、埋点、看板全部硬依赖这套取值。

---

## 1. 任务状态枚举与状态机

**唯一事实来源**：`backend/app/models/enums.py`
**业务依据**：`docs/prd/PRD_v1.md` §5

### 1.1 TaskStatus —— 单张图（子任务）

| 取值 | 语义 | 终态 | 可取消 | 可重试 |
|---|---|---|---|---|
| `pending` | 已创建，待调度入队 | ❌ | ✅ | — |
| `queued` | 已入队，等待 GPU | ❌ | ✅ | — |
| `running` | 正在执行 | ❌ | ✅（步间隙生效） | — |
| `retrying` | 瞬时错误，退避等待重试 | ❌ | ✅ | — |
| `succeeded` | 成功产出 | ✅ | — | — |
| `failed` | 最终失败（重试耗尽 / 致命错误） | ✅ | — | ✅ |
| `canceled` | 用户取消 | ✅ | — | ✅ |

```python
TaskStatus.terminal()     == {succeeded, failed, canceled}
TaskStatus.cancellable()  == {pending, queued, running, retrying}
```

⚠️ **`canceled` 可重试** —— PRD §5.4 的 T13 只写了「failed/partial 可重试」，
但代码与 API（`POST /tasks/{id}/retry`）都允许 `canceled` 重试。
**以本契约为准**：`failed` 与 `canceled` 均可重试。PRD T13 待补。

### 1.2 BatchStatus —— 批量父任务

| 取值 | 语义 | 终态 |
|---|---|---|
| `pending` | 已创建 | ❌ |
| `queued` | 已入队 | ❌ |
| `running` | 有子任务在跑（进度 = 完成数/总数） | ❌ |
| `succeeded` | 全部子任务成功 | ✅ |
| `failed` | 全部子任务失败 | ✅ |
| `canceled` | 全部子任务取消 | ✅ |
| `partial` | **部分成功部分失败** | ✅ |

> `partial` 是关键设计：500 张成功 3 张失败，标 `succeeded` 会掩盖问题，标 `failed` 会让用户以为全废。

**父状态聚合规则**（`Batch.derive_status()`，单测 `test_batch_*` 全覆盖）

| 子任务情况 | 父状态 |
|---|---|
| 存在 pending/queued/running/retrying | `running` |
| 全部 succeeded | `succeeded` |
| 全部 failed | `failed` |
| 全部 canceled | `canceled` |
| 部分 succeeded、部分 failed/canceled | `partial` |

### 1.3 状态迁移表（**以实现为准**，比 PRD §5.4 更精确）

| # | 当前 | 事件 | 目标 | 备注 |
|---|---|---|---|---|
| T1 | — | 用户提交 | `pending` | 落库 |
| T2 | `pending` | **入队（提交时同步发生）** | `queued` | ⚠️ 见 §1.4 |
| T3/T4 | `pending` / `queued` | 用户取消 | `canceled` | — |
| T5 | `queued` | Worker 取任务 | `running` | 记录开始时间 |
| T6 | `running` | 执行成功 | `succeeded` | 保存产物 + 元数据 |
| T7 | `running` | 瞬时错误 | `retrying` | `retry_count += 1` |
| T8 | `retrying` | 退避结束且 `retry_count < 3` | `queued` | 默认上限 3 |
| T9 | `retrying` | `retry_count >= 3` | `failed` | — |
| T10 | `running` | 用户取消 | `canceled` | 步间隙中断，不硬杀 |
| T11 | `running` | 致命错误 | `failed` | **不重试** |
| T12 | 子任务变更 | — | 重算父状态 | 见 §1.2 聚合规则 |
| T13 | `failed` / `canceled` | 用户重试 | `pending` | 仅重试失败子任务 |
| T14 | `running` | Worker 崩溃（心跳超时） | `queued` | 启动时扫僵尸任务 |

**非法迁移**：除上表外的任何迁移都必须被 `_apply()` 拒绝。
`SUCCEEDED` 是**绝对终态**（无出边），任何把它改走的操作都是 bug。

### 1.4 ⚠️ 必须知道的行为事实（PRD 未写清，前端易踩）

| 事实 | 说明 |
|---|---|
| **提交后返回的状态是 `queued`，不是 `pending`** | `submit_task` 在 commit 前就调用了 `sm.enqueue()`。所以 `POST /tasks` 的 202 响应里 `status == "queued"`。`pending` 只在「重试」路径短暂出现（T13）后立即转 `queued` |
| 没有独立的调度器进程 | PRD §5.4 T2 写的是「调度器取任务」，实现里是**提交时同步入队**。若要改为异步调度，属于 §0.3 的契约变更 |
| `retry_count` 上限来自配置 | `settings.task_max_retries = 3`，硬上限 5（`task_max_retries_hard_limit`） |

### 1.5 ErrorType 与可重试判定

`ErrorType` 取值与 PRD §6.1 的 EX 编号一一对应：

| 取值 | 对应 | 可重试 | 说明 |
|---|---|---|---|
| `oom` | EX-1 | ✅ | 降级后可能成功 |
| `timeout` | EX-2 | ✅ | — |
| `disk_full` | EX-4 | ✅ | 清理后 |
| `queue_full` | EX-5 | ✅ | — |
| `worker_crash` | EX-7 | ✅ | — |
| `engine_unresponsive` | EX-8 | ✅ | — |
| `save_failed` | EX-13 | ✅ | — |
| `invalid_param` | EX-3 | ❌ | 重试一万次还是错 |
| `model_missing` | EX-9 | ❌ | — |
| `quota_exceeded` | EX-12 | ❌ | — |
| `duplicate` | EX-6 | — | 不新建任务，返回已有 id |
| `invalid_upload` | EX-10 | — | 提交前就拒绝 |
| `unknown` | — | ❌ | 兜底，按致命处理 |

> **T7 / T11 的区分是设计要点**：混淆会导致「队列被必然失败的任务堵死」。
> 判定入口：`ErrorType.is_retryable` / `ErrorType.retryable()`。

---

## 2. REST API 契约

**实现**：`backend/app/api/v1/` ｜ **交互式文档**：`GET /docs`

### 2.1 通用约定

| 项 | 约定 |
|---|---|
| 前缀 | 业务接口统一 `/api/v1`；`/health` 与 `/health/ready` 在根路径 |
| 鉴权 | `Authorization: Bearer <JWT>`；**除 health 外全部需要**（NFR-4） |
| 令牌 | `POST /api/v1/auth/{register,login}` 返回 `access_token` + `expires_in`（秒，默认 7 天） |
| 时间 | ISO 8601 带时区；数据库存 UTC |
| 分页 | `limit`（1-200，默认 50）+ `offset`（≥0，默认 0）；响应为**裸数组**，总数暂不透出。<br>⚠️ **唯一例外：`GET /api/v1/assets` 返回 `{total, items}`**（不对称响应）。理由是素材列表要驱动「已上传 N 张」与分页器，而 `total` 必须与列表**共用同一组 conditions（含同一个 join）** —— 两边不一致时用户会看到「说有 2 条、实际 0 条」，且再发一次专用 count 请求等于两次往返（还有竞态）|
| 幂等 | 提交类接口支持 `idempotency_key`（≤128 字符），见 §2.5 |
| 数据隔离 | 所有查询按 `user_id` 过滤；越权一律返回 **404 而非 403**（不泄露资源存在性） |
| **回收站记录的响应码** | 判据：**属于你但已在回收站 → 409；不存在或不属于你 → 404**。<br>落法：**读接口一律 404**（`GET /assets` 列表 · `GET /assets/{id}` 详情 · `GET /assets/{id}/content` 取图）；**改状态接口一律 409**（`DELETE /assets/{id}` · `PATCH /assets/{id}`）。<br>理由：读接口要一致地表现为「不存在」（回收站里的图不该还能取到）；改状态接口给出「为什么不能改」比让调用方猜更有用，且与既有的「重复删除 → 409」自洽。⚠️ V1 没有回收站恢复接口 |

### 2.2 错误响应格式

统一使用 FastAPI 标准信封：

```json
{ "detail": "人类可读的原因" }
```

**需要前端分支处理**时，`detail` 用结构化对象：

```json
{
  "detail": {
    "code": "quota_exceeded",
    "message": "额度不足：需要 10，剩余 3",
    "fields": [ { "key": "images_per_sku", "reason": "超过单任务上限" } ]
  }
}
```

> 保留字符串形式是为了不破坏既有实现（现有 20+ 处简单的 `detail` 字符串）。
> **新代码若需要机器可读码，一律用对象形式。** `code` 的取值分两类，别混：
> - **是 `ErrorType` 的取值**（对应 PRD §6.1 的 EX 编号）：一律**小写**，直接取
>   `backend/app/models/enums.py` 的枚举值（`quota_exceeded` / `invalid_param` / …）。
>   **枚举是唯一事实来源**，前端镜像由 `backend/tests/test_web_enum_parity.py` 双向守着 ——
>   自己在服务端拼一个 `QUOTA_EXCEEDED` 会造出第二个真相来源。
> - **不是 `ErrorType` 的码**（不对应任何 EX）：采用**大写下划线**命名，
>   例如内容安全命中用的 `CONTENT_BLOCKED`。大写写法让这类码与枚举值一眼可区分。

**HTTP 状态码 ↔ 场景映射**

| 状态码 | 场景 | 备注 |
|---|---|---|
| 200 | 查询成功 | — |
| 201 | 注册成功 | — |
| 202 | **任务/批量已接受**（异步） | 不代表已完成 |
| 400 | 请求格式错误 | — |
| 401 | 缺少 / 无效 / 过期令牌 | 带 `WWW-Authenticate: Bearer` |
| 402 | **额度不足**（EX-12） | 用 402 而非 403，语义更准。**body 形状**：2026-09-17 起 `detail` 用**对象形式** —— `code` 取 §1.5 的 `quota_exceeded`（即 `ErrorType.QUOTA_EXCEEDED`，小写、与枚举一致）、`message` 保持人类可读（`额度不足：需要 N，剩余 M`）、`fields` 为 `[{"key": "count", "reason": "需要 N，剩余 M"}]`；**扣减失败时 402 与"额度一个都没扣"是同一件事**（条件 UPDATE 的 `rowcount=0`，不留半成品写入） |
| 403 | 权限不足（如非管理员访问管理接口） | — |
| 404 | 资源不存在**或不属于当前用户** | 越权也用 404 |
| 409 | 状态冲突（不可取消 / 不可重试 / 邮箱已注册 / 无失败项可重跑） | — |
| 422 | 参数校验失败（EX-3） | 含边界值越界。**内容安全命中**（P6-13）也走 422：`detail` 为对象形式，`code=CONTENT_BLOCKED`（非 `ErrorType`，故用大写下划线）、`message` 只说明是哪一类问题、`fields` 为 `[{"key": "prompt", "reason": "…"}]`。⚠️ 不发明 451/403-内容码 —— 多一个表外的码就多一条前端分支，与上传校验因本表没有 413 而统一用 422 是同一个取舍。⚠️ `message` 与 `fields[].reason` **不回显命中的原词**（回显等于把词表泄露给攻击者，也等于把用户输入写回响应体）<br>**队列深度熔断**（EX-5，2026-09-17 实现）也走 422：`detail` 为**对象形式**、`code=queue_full`（§1.5 的 `ErrorType.QUEUE_FULL` 枚举值，故**小写**）、`message` 为「当前排队 N 个，预计等待 X 分钟」、`fields` 为 `[{"key": "queue_depth", "reason": "…"}]`。触发条件 = **在途任务数**（`queued` / `running` / `retrying`）**> 2 × `queue_max_depth`**（硬阈值）；被拒请求**不建任务、也不扣额**（检查排在扣额之前）。⚠️ 在途 > 软阈值（`queue_max_depth`，默认 10000）时**只告警不拒绝**（仍返回 202）—— `queue_full` 属**可重试**类型，此时拒绝用户是错的 |
| 500 | 未预期错误 | 必须记日志并带 trace |

### 2.3 接口清单（26 条，当前实现）

| 方法 | 路径 | 用途 | 鉴权 |
|---|---|---|---|
| GET | `/health` | 存活探针 | 否 |
| GET | `/health/ready` | 就绪探针（依赖项检查） | 否 |
| POST | `/api/v1/auth/register` | 注册（送额度，不需信用卡） | 否 |
| POST | `/api/v1/auth/login` | 登录 | 否 |
| GET | `/api/v1/auth/me` | 当前用户 + 配额 | ✅ |
| POST | `/api/v1/tasks` | 提交单次生成（FR-2.5） | ✅ |
| GET | `/api/v1/tasks` | 任务列表（FR-4.1） | ✅ |
| GET | `/api/v1/tasks/{task_id}` | 任务详情（FR-4.2） | ✅ |
| POST | `/api/v1/tasks/{task_id}/cancel` | 取消（FR-4.3） | ✅ |
| POST | `/api/v1/tasks/{task_id}/retry` | 重试（FR-4.4） | ✅ |
| POST | `/api/v1/tasks/estimate` | 提交前预估张数/耗时/成本（FR-3.3） | ✅ |
| POST | `/api/v1/batches` | 提交批量（FR-3.4） | ✅ |
| GET | `/api/v1/batches` | 批量列表 | ✅ |
| GET | `/api/v1/batches/{batch_id}` | 批量详情 | ✅ |
| POST | `/api/v1/batches/{batch_id}/retry-failed` | 断点续跑，只重跑失败项（FR-3.5） | ✅ |
| GET | `/api/v1/workflows` | 工作流列表 | ✅ |
| GET | `/api/v1/workflows/{name}/schema` | **工作流参数 Schema**（驱动动态表单） | ✅ |
| GET | `/api/v1/templates` · `/categories` · `/api/v1/models` | 模板与模型清单 | ✅ |
| POST | `/api/v1/templates` | 新增模板（管理员，FR-6.5） | ✅ 管理员 |
| POST | `/api/v1/assets` | 素材上传（P6-09） | ✅ |
| GET | `/api/v1/assets` | 素材列表 + 筛选（P6-09 / P6-10 扩展） | ✅ |
| GET | `/api/v1/assets/{asset_id}` | 素材详情 | ✅ |
| GET | `/api/v1/assets/{asset_id}/content` | **受控取图**（P6-10，唯一取图入口） | ✅ |
| PATCH | `/api/v1/assets/{asset_id}` | 标记采纳 / 收藏（P6-10） | ✅ |
| DELETE | `/api/v1/assets/{asset_id}` | 软删除（送进回收站，非物理删除） | ✅ |
| POST | `/api/v1/assets/pack` | 打包下载 zip（P6-10 / FR-5.2 · FR-3.7） | ✅ |

> **计数口径**：上表 **26 行**。其中有**合并行**（`/templates` · `/categories` · `/models` 占一行），
> 所以行数 ≠ 路径数（实际路由 28 个）。以「行」为准是为了可读；要逐路径核对请直接看 `GET /docs`。
> 2026-09-16 本轮补入 assets 系列 7 行（P6-09 上传时漏登记 4 行）与 `POST /templates` 1 行（同样漏登记）。

**尚未实现**：WebSocket 进度推送（P6-04）、管理后台接口（P7-09）、API Key 鉴权（P6-07）。

> ✅ 本段原先列的前两项**已完成，已移出**：
> - **素材上传（P6-09）** —— 已实现 4 条路由（见上表），此前只是没登记进本表。
> - **产物下载（P6-10）** —— 已实现，但**有意偏离**原措辞「签名 URL 下载」：
>   取图改走**受控代理接口** `GET /api/v1/assets/{asset_id}/content`（见上表）。
>   三条理由：① **V1 经 SSH 隧道访问，浏览器根本到不了 MinIO** —— 预签名 URL 里的 host 是
>   `127.0.0.1:9000`，是个打不开的地址，这是**功能性阻塞**而非偏好；② **对象键不该进接口契约**
>   （`AssetOut` 刻意不返回 `object_key`，而预签名 URL 把 key 编码进了 URL）；
>   ③ **预签名 URL 一旦签发，TTL 内无法撤销**，而软删除 / 过期必须立即生效。
>   代价：产物字节要过一次 API 进程（V1 单机量级可忽略；V2 上 CDN 时是**新增**一条路径，不是改这条）。
>
> 同轮还补了 P6-10 的另两条接口：`PATCH /assets/{id}`（标记采纳 / 收藏）与
> `POST /assets/pack`（打包下载），以及 `GET /assets` 的筛选参数扩展。

### 2.4 关键请求 / 响应体

#### `POST /api/v1/tasks` → 202

请求（`TaskSubmitIn`）：

```json
{
  "workflow_name": "flux2_klein_t2i_v1",
  "template_id": null,
  "prompt": "一只白色陶瓷咖啡杯，白色背景，柔光",
  "negative_prompt": null,
  "params": { "width": 1024, "height": 1024 },
  "upload_asset_ids": [],
  "steps": 4,
  "cfg": 1.0,
  "seed": 20260916,
  "idempotency_key": "可选-客户端生成"
}
```

响应（`TaskOut`，关键字段）：

```json
{
  "id": 123, "batch_id": null, "idx": 0,
  "status": "queued",
  "workflow_id": 2, "template_id": null,
  "params": { "...合并后的最终参数..." },
  "seed": 20260916,
  "retry_count": 0, "error_type": null, "error_message": null,
  "started_at": null, "finished_at": null,
  "wait_seconds": null, "duration_ms": null,
  "gpu_seconds": null, "cost_yuan": null,
  "created_at": "2026-09-16T03:00:00Z"
}
```

> ⚠️ `status` 是 `queued`（不是 `pending`），理由见 §1.4。

#### `POST /api/v1/batches` → 202

请求（`BatchSubmitIn`）：`sku_assets` 为 `SKU → 素材 ID 列表`，`images_per_sku` 默认 1、上限 20。

```json
{
  "workflow_name": "flux2_klein_t2i_v1",
  "name": "2026春-陶瓷杯",
  "sku_assets": { "SKU-A": [11, 12], "SKU-B": [13] },
  "common_params": { "width": 1024, "height": 1024 },
  "images_per_sku": 2
}
```

**子任务粒度 = 一张图**（PRD §3.1 决策）—— 这样失败能精确隔离，断点续跑才能只重跑失败的那几张。
同批次内 `seed` 自动递增（风格一致但每张不同）；要严格一致须显式固定 `seed`。

#### `POST /api/v1/tasks/estimate` → 200

```json
{
  "total_images": 6,
  "estimated_seconds": 120.0,
  "estimated_cost_yuan": null,
  "gpu_concurrency": 1,
  "queue_depth": 3,
  "estimated_wait_seconds": 60.0
}
```

> 单张耗时优先取该工作流**历史成功任务的平均值**，无历史时用保守经验值 20s。
> GPU 并发恒为 1（NFR-2），所以总耗时 = 单张 × 张数（串行）。
> `estimated_cost_yuan` 为 `null` 直到 `.env` 里配上 `GPU_COST_PER_HOUR`。

#### `PATCH /api/v1/assets/{asset_id}` → 200

请求（`AssetUpdateIn`）：两个字段都可选，但**至少要给一个**（空 body → 422）。

```json
{ "is_adopted": true, "is_favorite": false }
```

> ⚠️ `is_adopted` **只允许标在 `kind=output` 的产物上**，否则 409；回收站里的素材也是 409（见 §2.1）。
> `image_adopted` 只在 **False→True 跃迁**时埋点（重复 PATCH `true` 不重复计数、取消采纳不埋点）。
> 响应体是 `AssetOut`（见下）。

#### `POST /api/v1/assets/pack` → 200（zip 流）

请求（`AssetPackIn`）：三个选择器**必须且只能给一个**。

```json
{ "asset_ids": [11, 12] }        // 或 { "task_id": 123 } / { "batch_id": 7 }
```

| 情况 | 状态码 | 说明 |
|---|---|---|
| 正常 | 200 | `Content-Type: application/zip`，zip 内按 **SKU 分目录**（`params.sku`，空则回退 `task_{id}`），文件名 `{asset_id}.{ext}` |
| `asset_ids` 里有找不到 / 不属于你的 | **404** | **绝不静默少给**（少一张图用户不会发现，但会毁掉交付的可信度）|
| 结果 0 张 | 422 | 返回空 zip 会让用户分不清"还没出图"与"下载坏了" |
| 超过 `MAX_PACK_ASSETS`（默认 200） | 422 | 内存/磁盘护栏，非业务规则 |

> 埋点：`image_downloaded`，`pack=True`、`count=实际张数`、`scope` 由选择器推导
> （`asset_ids` → `selected`；`task_id` / `batch_id` → `all`）。

#### `AssetOut`（素材 / 产物共用；`GET /assets` 列表项、详情、PATCH 响应）

```json
{
  "id": 11, "kind": "output", "task_id": 123,
  "mime_type": "image/png", "size_bytes": 1085233,
  "width": 1024, "height": 1024,
  "original_name": null,
  "is_adopted": true, "is_favorite": false,
  "meta": {
    "seed": 20260916,
    "workflow": "flux2_klein_t2i_v1@v1",
    "engine_prompt_id": "9f2c…",
    "params": { "...本次实际生效的完整参数..." },
    "filename": "flux2_klein_t2i_v1_00001_.png",
    "node_id": "11",
    "sha256": "…"
  },
  "created_at": "2026-09-16T03:00:00Z"
}
```

> `meta` 的键由产物落盘时写入（`worker/tasks.py`）：`seed` 是**解析后的真实值**（`-1` 已实体化，
> 否则事后复现不出这张图）、`params` 是本次**实际生效**的完整参数（含 Schema 默认值）。

> ⚠️ **刻意不返回 `object_key` / URL**：取图一律走 §2.3 的 `GET /assets/{id}/content`。
> ⚠️ `meta` 与 `task_id` **只在产物（`kind=output`）上有值** —— 上传素材没有可复现性元数据，
> `meta` 是 `{}`、`task_id` 为 `null`。前端读之前先判 `kind`（或判空），别假设它总有值。
> `meta` 是 FR-5.4「元数据查看（seed/模型/参数）」的**唯一数据源**，即可复现性（C1）的用户侧入口。

### 2.5 幂等（EX-6）

同用户 + 同 `idempotency_key` 重复提交 → **返回已有 task_id，不新建任务**。
未传 `idempotency_key` 时不做去重。

---

## 3. 工作流参数 Schema 格式

**用途**：一个格式同时驱动三件事 ——
① 后端参数校验与注入 ② 前端动态表单（P7-02）③ 工作流注册表（P3-09）

**存放位置**：`Workflow.param_schema`（JSONB）。经 `GET /api/v1/workflows/{name}/schema` 对外暴露。

### 3.1 格式定义

```json
{
  "schema_version": 1,
  "fields": [
    {
      "key": "steps",
      "label": "采样步数",
      "type": "int",
      "default": 30,
      "min": 1,
      "max": 100,
      "step": 1,
      "group": "采样",
      "help": "klein 蒸馏版固定 4 步，调高无收益",
      "advanced": false,
      "targets": [ { "node_id": "3", "input": "steps" } ]
    }
  ]
}
```

| 字段 | 必填 | 说明 |
|---|---|---|
| `schema_version` | ✅ | 当前 `1`。破坏性改动必须递增 |
| `fields` | ✅ | 参数数组。**后端 `_merge_params` 依赖 `fields[].key` 与 `fields[].default`** |
| `key` | ✅ | 唯一，即 `params` 里的键名。命名用 `snake_case` |
| `label` | ✅ | 表单显示名（中文） |
| `type` | ✅ | 见 §3.2 |
| `default` | ✅ | **必填** —— 参数合并的兜底值 |
| `min` / `max` / `step` | type 为数值时必填 | 与 PRD §6.2 边界值一致 |
| `options` | `type=enum` 时必填 | `[{"value": "...", "label": "..."}]` |
| `group` | ➖ | 表单分组名，同组折叠在一起 |
| `help` | ➖ | 字段下方提示文案 |
| `advanced` | ➖ | 默认 `false`；`true` 则折叠进「高级设置」 |
| `visible_when` | ➖ | 条件显隐：`{"key": "mode", "equals": "controlnet"}` |
| `targets` | ✅ | 注入到 ComfyUI 图的位置，见 §3.4 |

### 3.2 type 取值表

| type | 前端控件 | 后端校验 | 备注 |
|---|---|---|---|
| `int` | 滑块 / 数字输入 | `min ≤ v ≤ max`，整数 | — |
| `float` | 滑块 / 数字输入 | `min ≤ v ≤ max` | 需配 `step`（如 `0.05`） |
| `str` | 单行文本 | `max_length` | 配 `max_length` 字段 |
| `text` | 多行文本 | `max_length` | 提示词用 |
| `bool` | 开关 | 布尔 | — |
| `enum` | 下拉 / 单选 | 必须在 `options[].value` 内 | — |
| `seed` | 数字 + 🎲 随机按钮 | 整数；`-1` 表示随机 | — |
| `image` | 上传 / 素材选择 | 单张，受 §6.2 上传边界约束 | 值经 `asset_id` 传递 |
| `image_list` | 多图上传 | 张数上限 | 同上 |

### 3.3 `targets` 注入约定（B ↔ A 的硬边界）

`targets` 声明「这个参数最终写到工作流 JSON 的哪个节点的哪个入参」：

```json
"targets": [ { "node_id": "3", "input": "steps" } ]
```

| 字段 | 必填 | 说明 |
|---|---|---|
| `node_id` | ✅ | ComfyUI API 格式里的节点 ID（**字符串**）。子图用运行时自身约定（如 `"13:5"`） |
| `input` | ✅ | 该节点的入参名，必须与 `/object_info` 一致 |
| `transform` | ➖ | 值适配方式，见下 |

`transform` 取值（不填 = 原样注入）：

| 取值 | 含义 |
|---|---|
| `ref_to_filename` | `asset_id` → ComfyUI 可见的文件名（用于图片类） |
| `join_lines` | 数组 → 换行拼接的字符串 |
| `json_str` | 对象 → JSON 字符串 |

> **一个参数可映射到多个 target**（如 `width` 同时写给 `EmptyLatentImage.width` 和某个 `Resize.width`）。
> `targets` 为空数组是**合法**的 —— 表示该参数只参与业务逻辑（如 `sku`、`upload_asset_ids`）而不注入图。

### 3.4 参数优先级与校验责任

**合并优先级（低 → 高）**

```
工作流 fields[].default  <  模板 preset_params  <  请求 body.params  <  顶层 steps / cfg / seed
```

> 顶层 `steps` / `cfg` / `seed` 是**便捷别名**，优先级最高。
> 这是既有实现的行为（`_merge_params` + `submit_task` 的显式覆盖），已冻结。

**校验责任划分**

| 层 | 责任 | 依据 |
|---|---|---|
| D 前端 | 提交前校验（体验） | Schema 的 `type` / `min` / `max` / `options` |
| A 后端 | **必须二次校验**（安全） | 前端校验可被绕过；非法参数会让 ComfyUI 报难以理解的错 |
| B 工作流 | 保证 `targets` 指向真实存在的节点与入参 | 用 `08_probe_nodes.py` + `26_validate_workflow.py` 验证 |

**两套边界的关系（⚠️ 易混淆）**

存在两套边界，含义不同，**必须同时满足**：

| 边界 | 存放位置 | 含义 | 谁能改 |
|---|---|---|---|
| **全局红线** | `backend/app/core/config.py` 的 `settings`（对齐 PRD §6.2） | 防炸机制：拦住 `steps=100000` 这类会让 GPU 崩掉的输入 | 改 PRD §6.2 时同步改 |
| **工作流有效边界** | 各工作流 Schema 的 `min` / `max` | 该工作流的**技术有效范围**，可以更严 | B 流按模型特性定 |

**判定规则：以更严者为准。**

```
实际可接受范围 = [max(schema.min, settings.min), min(schema.max, settings.max)]
```

- Schema 边界**比 settings 更严是正常的、也是推荐的**。例：FLUX.2 klein 是蒸馏版，`cfg` 必须为 1 —— Schema 写 `min:1, max:2, default:1` 并配 `help` 说明，比放开到 1.0–20.0 更正确。
- Schema 边界**比 settings 更宽是配置错误**。加载注册表时应告警，并按 settings 收紧（settings 是安全兜底，不可被工作流突破）。
- **固定值参数**用 `min == max` 表达（不要用特殊字段），这样前端无需特判、后端无需特判。这类参数应同时标 `advanced: true` 并在 `help` 里写明**为什么必须固定**。

**关于本文档里的示例**

§3.5 的节点 ID 取自 `workflows/flux2_klein_t2i_v1.json` 的**真实值**（2026-09-16 核对）。
但**节点 ID 会随工作流演进变化**，任何实现都必须以 `workflows/registry.yaml` 的实时 Schema 为准，
**不要把本文档示例里的 ID 硬编码进代码或测试**。

### 3.5 完整示例（FLUX.2 klein 文生图 · 节点 ID 取自真实工作流）

> 下列节点 ID 与入参名**逐项核对于 `workflows/flux2_klein_t2i_v1.json`**（2026-09-16）。
> 注意两处容易写错的地方：`seed` 在 klein 图里的入参名是 **`noise_seed`**（不是 `seed`）；
> `width` / `height` 需要**同时写两个节点**（见 `_comment`）。

```json
{
  "schema_version": 1,
  "fields": [
    {
      "key": "prompt", "label": "正向提示词", "type": "text",
      "default": "professional product photo of a matte white ceramic coffee mug on a light grey seamless background, soft studio lighting, subtle shadow, sharp focus, centered composition, high detail, commercial e-commerce photography",
      "max_length": 2000, "group": "提示词",
      "_comment": "节点 4 = CLIPTextEncode；klein 必须走它（输出键 qwen3_4b），不能用 CLIPTextEncodeFlux（依赖 t5xxl 键，会 KeyError）",
      "targets": [ { "node_id": "4", "input": "text" } ]
    },
    {
      "key": "width", "label": "宽度", "type": "int",
      "default": 1024, "min": 512, "max": 2048, "step": 64,
      "group": "画布",
      "_comment": "⚠️ 多 target 示例：EmptyFlux2LatentImage 与 Flux2Scheduler 都要 width，只写一个会导致潜空间尺寸与 sigmas 不一致",
      "targets": [
        { "node_id": "6", "input": "width" },
        { "node_id": "7", "input": "width" }
      ]
    },
    {
      "key": "height", "label": "高度", "type": "int",
      "default": 1024, "min": 512, "max": 2048, "step": 64,
      "group": "画布",
      "targets": [
        { "node_id": "6", "input": "height" },
        { "node_id": "7", "input": "height" }
      ]
    },
    {
      "key": "steps", "label": "采样步数", "type": "int",
      "default": 4, "min": 1, "max": 8, "step": 1,
      "group": "采样", "advanced": true,
      "_comment": "⚠️ 固定值示例：本工作流跑的是**蒸馏版**（flux-2-klein-4b，无 -base- 前缀），官方标定 4 步；调高不提升质量只增加耗时。故边界收窄到 1-8 而非 settings 的 1-100",
      "help": "蒸馏版固定 4 步，调高无收益",
      "targets": [ { "node_id": "7", "input": "steps" } ]
    },
    {
      "key": "cfg", "label": "CFG", "type": "float",
      "default": 1.0, "min": 1.0, "max": 2.0, "step": 0.1,
      "group": "采样", "advanced": true,
      "help": "蒸馏版已把 guidance 蒸进权重，必须保持 1.0",
      "targets": [ { "node_id": "9", "input": "cfg" } ]
    },
    {
      "key": "seed", "label": "随机种子", "type": "seed",
      "default": -1, "group": "采样",
      "_comment": "⚠️ 入参名是 noise_seed（节点 8 = RandomNoise），不是 seed；-1 由渲染器解析成真实整数后才注入，否则元数据里只剩 -1，用户复现不出那张图",
      "targets": [ { "node_id": "8", "input": "noise_seed" } ]
    }
  ]
}
```

**节点对照表**（供核对，同样以 registry 为准）

| 节点 ID | class_type | 本 Schema 使用的入参 |
|---|---|---|
| 4 | `CLIPTextEncode` | `text` |
| 6 | `EmptyFlux2LatentImage` | `width` `height` |
| 7 | `Flux2Scheduler` | `steps` `width` `height` |
| 8 | `RandomNoise` | `noise_seed` |
| 9 | `CFGGuider` | `cfg` |
| — | 1 / 2 / 3 / 5 / 10 / 11 / 12 / 13 | 加载器与管线节点，不由用户参数驱动 |

> ⚠️ 节点 6 的 `batch_size` **有意不暴露**（也不在示例中），理由见 §3.6 不变量 1。

---

### 3.6 工作流必须遵守的不变量

以下不是建议，是**契约约束**。违反会让系统的其他部分静默出错。

| # | 不变量 | 为什么 | 谁来保证 |
|---|---|---|---|
| **1** | **一条工作流的单次执行只产出 1 张图** | 「子任务粒度 = 一张图」是 PRD §3.1 的基石决策，**断点续跑（只重跑失败项）、单张成本核算、批量进度**全部建立在它之上。若某工作流一次产出 N 张，则：① 一张失败要连坐重跑 N 张（浪费 GPU）② `gpu_seconds` 是 N 张共享的，单张成本算不出来 ③ 批量进度分母与实际不符 | B 流**不得暴露** `batch_size` / `batch_count` 这类参数；A 流在收图时**断言产物数 == 1**，否则按 `ErrorType.UNKNOWN` 失败并告警 |
| **2** | **参数默认值必须与工作流 JSON 里的实际值一致** | 模板、契约示例、registry 三处的默认提示词一度出现字面差异（`subtle shadow` 缺失）。默认值不一致 = 用户在 UI 里看到的初始状态与真实执行的不是同一件事 | B 流（注册表加载时校验） |
| **3** | **不得暴露「不起作用」的参数** | 暴露一个无效参数比不暴露更糟：用户以为能调，调了没反应，且没有任何报错。典型：klein 的负向提示词（被 `ConditioningZeroOut` 整段置零）、蒸馏版的采样器选择（无调优空间） | B 流；A 流不额外限制 |
| **4** | **`targets` 必须指向真实存在的节点与入参** | 写错 ID 或入参名会**静默**把值写到别处或不生效 | B 流用 L3 校验（`object_info` 快照）|

---

## 4. 埋点事件名（冻结）

**唯一事实来源**：`backend/app/models/event.py` 的 `EventName`
**完整字段与口径**：`docs/prd/tracking_plan.md`

| 取值 | 编号 | 说明 |
|---|---|---|
| `user_register` | E01 | 注册 |
| `user_login` | E02 | 登录 |
| `image_uploaded` | E03 | 上传完成 |
| `template_selected` | E04 | ★ 关键转化点 |
| `param_changed` | E05 | 参数改动 |
| `task_submitted` | E06 | 提交（含 single/batch） |
| `task_started` | E07 | 开始执行 |
| `task_retry` | E08 | 重试 |
| `task_finished` | E09 | ★ **最重要**（5 个指标依赖） |
| `image_adopted` | E10 | ★ 良品率分子 |
| `image_downloaded` | E11 | ★ 北极星分子 |
| `image_regenerated` | E12 | 重新生成 |

**关键约束**：`task_finished` **只在终态触发**（`TaskStatus.terminal()`）。
单张成本用 `gpu_seconds` 而非挂钟时间。

**触发位置（P6-10 起补记，只记本轮核实过的）**

| 事件 | 触发位置 | 口径要点 |
|---|---|---|
| `image_adopted`（E10 ★ 良品率分子） | `PATCH /api/v1/assets/{asset_id}` 的 **False→True 跃迁** | 重复 PATCH `true` **不重复埋点**、取消采纳不埋点（否则良品率被重复计数灌水）。只有 `kind=output` 可标，否则 409 |
| `image_downloaded`（E11 ★ 北极星分子） | ① `GET /api/v1/assets/{asset_id}/content?download=1`（`pack=False`、`count=1`、`scope=selected`）<br>② `POST /api/v1/assets/pack`（`pack=True`、`count=实际张数`、`scope`：`asset_ids`→`selected` / `task_id`·`batch_id`→`all`） | ⚠️ **inline 预览（`download=0`）不算有效交付、不埋点** —— 北极星是"交付给用户的图"，划过去看一眼不是交付 |

> ⚠️ **这两个事件在 P6-10 之前是全仓库零处触发** —— 不是"看板还没做"，而是**指标连数据源都不存在**
> （良品率恒为 0）。详见 `项目进展.md` #31。凡带埋点的路径都必须有测试钉死（本次已补）。

---

## 5. 既有实现里需要修的不一致

冻结时查出的问题。**已修的标 ✅，未修的登记为待办**（不影响三流启动）：

| # | 位置 | 问题 | 状态 |
|---|---|---|---|
| 1 | `backend/app/models/enums.py` 文档字符串 | 写「8 个状态」，实际 `TaskStatus` 是 7 个（第 8 个 `partial` 属于 `BatchStatus`） | ✅ 已修 |
| 2 | `docs/prd/PRD_v1.md` §5.4 T13 | 只写「failed/partial 可重试」，漏了 `canceled` | ✅ 已修（按本契约 §1.1 补齐） |
| 3 | `docs/prd/PRD_v1.md` §5.4 T2 | 写「调度器取任务」，实际是**提交时同步入队** | ✅ 已修（补注并指向 §1.4） |
| 4 | `backend/app/schemas/task.py` `TaskOut.can_retry` | 用 `@property` 且恒返回 `False`，**Pydantic v2 不序列化 property → 前端 JSON 里压根没这个键**；且 `canceled` 返回 False，前端会把实际可点的重试按钮藏起来 | ✅ 已修（改为显式字段；判据＝状态 ∈ {failed, canceled}，与 §1.1 及 `/retry` 接口一致。瞬时/致命判定仍归 `ErrorType.is_retryable`，只管**自动**重试） |
| 5 | `TaskSubmitIn.prompt` 与 `params["prompt"]` | 两条通道传同一件事，优先级未文档化 | ✅ 已修（顶层覆盖 params，与 §3.4 一致；`Task.prompt` 改为从 params 回读，保证「落库的」与「注入图的」同值） |
| 6 | `BatchSubmitIn` 的 `max_batch_size` | 校验器查 **SKU 数**、`submit_batch` 查 **总张数**，同名不同义 | ✅ 已修（拆为 `max_sku_count` 与 `max_batch_size`） |
| 7 | `Task.seed` 列 与 `params["seed"]` | 与 #5 同类：两条输入通道可能发散 | ✅ 已裁定：**保留列，但取消"第二条输入通道"** —— `params` 是唯一事实来源，`Task.seed` 是派生投影（与 #5 的 prompt 同一模式）。不删列，因为它是可查询的一等属性 |
| 8 | `backend/app/models/workflow.py:74`（「已被删除」注释）· `backend/tests/test_migration.py:173`（反向门禁） | **曾**是 `targets` 之前的**旧绑定机制**，**曾与 `param_schema` 并存**；`param_bindings={"steps": ["1","inputs","steps"]}` 与本契约 §3.3 的 `targets` 语义重叠。**两套绑定并存必出事**（前端读一套、后端读一套） | ✅ **已修**：该字段**已不存在** —— **此前某轮已完成，本次 2026-09-16 核实**（**不是本次修的**）。核实方式：`grep -rn param_bindings` 全仓库只剩 `workflow.py:74` 的删除说明注释与 `test_migration.py` 的反向门禁 `test_dropped_binding_mechanism_stays_dropped`（断言模型与迁移都不得再出现该列）。⚠️ 旧行号 `workflow.py:63` 已失效 |
| 9 | `backend/app/models/workflow.py:57-69` docstring（`advanced` @67 · `visible_when` @68） | 示例**曾**写 `"visible": true` + `"group": "basic"`，与契约 §3.1 的 `advanced`（默认 false，true 折叠）冲突 | ✅ **已修**：docstring 现用 `advanced` / `visible_when`，已与 §3.1 一致 —— **此前某轮已完成，本次 2026-09-16 核实**（**不是本次修的**）。核实方式：`grep -n '"visible"' backend/app/models/workflow.py` 与 `grep -n '"basic"' backend/app/models/workflow.py` **均无匹配（exit 1）**。⚠️ 旧行号 `workflow.py:58` 已失效 |
| 10 | `backend/app/schemas/asset.py` `AssetOut.meta` / `task_id` | 字段本身没问题，但**只在产物（`kind=output`）上非空**：上传素材是 `meta={}`、`task_id=null`。前端若一刀切地读 `meta.seed`，画廊卡片会**静默显示空值**，且看不出是"没数据"还是"读错了" | ✅ 已在本契约 §2.4 写明（前端先判 `kind` 或判空）。⚠️ 代码侧**不做**默认值填充 —— `{}` 就是「没有可复现性元数据」的诚实表达 |

---

## 6. 三流可动边界（防止互相踩）

| 流 | 可自由改 | **不可动（须走契约变更）** |
|---|---|---|
| **A 后端** | `backend/` 下除枚举外的全部 | `app/models/enums.py` 的取值；API 路径与请求/响应字段名；错误格式 |
| **B 工作流内核** | `workflows/`、`engine/`、`docs/sop/workflow_spec.md`、`docs/sop/debug_log.md` | 参数 Schema 的字段名与语义（§3） |
| **C 资产与资料** | `assets/`、`engine/model_registry.yaml`、`deploy/` 下的下载脚本 | 不要改 `backend/`、`workflows/` |
| **D 前端**（暂缓） | `web/` 全部 | 不得在前端硬编码状态取值、参数默认值、枚举选项 —— 一律从 Schema 与常量推导 |

**跨流改动的唯一通道**：改本文档 → 通知对方流 → 双方同步。

---

## 7. 变更记录

| 日期 | 改了什么 | 为什么 | 影响流 |
|---|---|---|---|
| 2026-09-16 | **首次冻结**：任务状态机 / REST API / 参数 Schema / 埋点事件名 | 开发进入多流并行，跨流边界必须先唯一化，否则必然返工 | A · B · C · D |
| 2026-09-16 | 明确 `canceled` 可重试（覆盖 PRD T13） | 代码与 API 已如此实现，且语义合理（用户取消后想再跑是常见需求） | A · D |
| 2026-09-16 | 明确「提交后状态为 `queued`」 | 实现是提交时同步入队，前端若按 `pending` 写判断会出错 | A · D |
| 2026-09-16 | **补齐 §2.3 接口清单**（18 → **26 条**）：登记 assets 系列 7 条（其中 4 条为 P6-09 漏登记）与 `POST /templates`；**取图由「MinIO 预签名 URL」改为受控代理接口** `GET /api/v1/assets/{asset_id}/content`；补 §2.4 的 `PATCH /assets/{id}` / `POST /assets/pack` / `AssetOut` 请求响应体 | P6-10 交付（受控取图 / 标记采纳 / 打包下载 / 生命周期清理）。取图改方案的三条理由（SSH 隧道下浏览器到不了 MinIO / 对象键不该进契约 / 预签名 URL 不可撤销）见 §2.3 下方说明 | A 后端（实现）· D 前端（调用）。⚠️ **参数 Schema（§3）与任务状态机（§1）未动** |
| 2026-09-16 | 新增 §2.1「**回收站记录的响应码**」规则（读接口 404 / 改状态接口 409）与 `GET /assets` 的 `{total, items}` **不对称响应例外**；§4 补 `image_adopted` / `image_downloaded` 的**触发位置** | ① 回收站语义要在读与写两侧各自一致（读=不存在，写=说明为什么不能改）② `total` 必须与列表共用同一组 conditions ③ 这两个事件此前全仓库零处触发（良品率恒为 0），触发点必须进契约，否则前端不知道"什么时候才算一次交付" | A · D（埋点口径同时约束 P10 看板） |
| 2026-09-17 | §2.2 的 **402 改为对象形式 detail**（`code` = `quota_exceeded` / `message` / `fields`），前端判据由「只看 402 状态码」改为「`detail.code` 优先、402 状态码后备」 | 此前 402 是纯字符串 detail，与 §2.2 自己的规定（"新代码若需要机器可读码，一律用对象形式"）相冲突，前端 §"只能按状态码判断"的注释是把偏离当成了既定事实。改后前端可机器判定额度不足，且不必依赖状态码分配。⚠️ 扣减本身同时改为**一条原子条件 UPDATE**（`rowcount=0` ⇒ 402），修掉并发提交下读-改-写导致的额度超发 —— 语义不变（额度耗尽仍 402、扣减口径不变），只修原子性与错误体。**重试是否二次扣额、失败是否退还额度不在本次范围** | A（实现）· D（判据同步）。⚠️ **§1 状态机、§3 参数 Schema、§4 埋点事件名、§2.3 接口清单（条数不变）均未动** |
| 2026-09-17 | ① §2.2 的 **422 行补队列深度熔断**（EX-5）：走 422 + `code=queue_full`，`message` 含「当前排队 N 个 / 预计等待 X 分钟」，`fields` 为 `[{"key":"queue_depth",...}]`；硬阈值 = **2 × `queue_max_depth`**，软阈值（`queue_max_depth`）只告警不拒绝。② 同页同步：**`queue_max_depth` 由死配置变为两个消费点**（软阈值告警 + 硬阈值熔断），`ErrorType.QUEUE_FULL` 由零产生者变为 422 的 `detail.code` | ① 此前 `queue_max_depth` 全仓库**无消费方**、`queue_full` **零产生者**，EX-5 只有配置声明没有可执行行为；`PRD_v1.md` EX-5 原文「拒绝提交 → `pending` 保留」**自相矛盾**、「队列深度 ≤ 10000」也无法判定。按**实现**（检查排在 `_charge_quota` 之前，被拒不扣额不建任务）统一到本表，符合本文档头部的「以本文为准」约定。② ⚠️ **不新增配置项、不新增接口、不新增枚举取值** —— 复用既有 `queue_max_depth` 与 `ErrorType.QUEUE_FULL` | A（实现）· D（前端可在 422 上按 `detail.code == "queue_full"` 分支，**无需改代码**：`errors.ts` 已能展示 `detail.message`）。⚠️ **§1 状态机、§3 参数 Schema、§4 埋点事件名、§2.3 接口清单（条数不变）均未动** |
| 2026-09-17 | ① **统一 `code` 命名口径**：§2.2 的示例由 `QUOTA_EXCEEDED` 改为 `quota_exceeded`，并写明两类取值的规则（是 `ErrorType` 的一律小写取枚举值；不是 `ErrorType` 的用大写下划线，如 `CONTENT_BLOCKED`）。② §2.2 状态码表的 **422 行补内容安全命中**的说明（走 422 + `code=CONTENT_BLOCKED`，`message`/`fields[].reason` 不回显命中的原词）。③ 本次不新增接口：`POST /tasks` / `POST /batches` 的**请求体与成功响应形状均未变**，只在原先放行的地方多了一条 422 | ① 原文自相矛盾：示例与正文写「大写下划线」，但 §1.5 的表与 `models/enums.py` 冻结的是**小写**（`quota_exceeded`），且 `test_web_enum_parity.py` 双向守着 —— 以**枚举为唯一事实来源**统一，避免同一个码出现两个真相来源。② P6-13 交付了输入侧内容安全（FR-9.4 的可交付部分），前端需要能机器识别「内容被拦」；同时**必须明确不许回显命中的原词**（否则词表可被逐条试探，且把用户输入写回响应体）。③ 让 D 流的改动面尽量为零 | A（实现 + 错误体）· D（仅需知道 422 的对象形状，**无需改代码**：既有 `errors.ts` 已能展示 `detail.message`）。⚠️ **§1 状态机、§3 参数 Schema、§4 埋点事件名、§2.3 接口清单（条数不变）均未动**；⚠️ 本行**不代表 FR-9.4 已完成** —— NSFW **视觉**检测 V1 未实现（见 `PRD_v1.md` §3.2 与 `system_flow.md` §7 合规表） |
