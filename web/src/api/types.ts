/**
 * REST API 的类型（契约 §2）。
 *
 * 这些类型是**从后端 Pydantic Schema 逐一抄来的**，字段名必须逐字一致：
 * 后端用 `snake_case`，前端**不做驼峰转换**（转换层是两套命名体系的开始，
 * 而本项目已经因为"同一个东西两套名字"吃过亏）。
 *
 * 三处容易踩的不对称，已在对应位置标注：
 * 1. 列表响应**大多是裸数组**，只有 `/assets` 是 `{total, items}`
 * 2. 工作流的 `param_schema` **嵌套**在详情响应里，不在列表响应里
 * 3. 提交任务后 `status` 是 `queued` 而**不是** `pending`（契约 §1.4）
 */

import type { AssetKind, BatchStatus, ErrorType, ModelKind, TaskStatus, UserRole } from './enums'
import type { ParamSchema } from '../features/workflow-form/types'

// ---------------------------------------------------------------- 用户与鉴权

export interface UserOut {
  id: number
  email: string
  role: UserRole
  quota_total: number
  quota_used: number
  /** 后端派生字段（`quota_total - quota_used`） */
  quota_remaining: number
  created_at: string
}

export interface TokenOut {
  access_token: string
  token_type: string
  /** 秒。默认 7 天。⚠️ 没有 refresh 端点，过期只能重新登录。 */
  expires_in: number
}

export interface UserRegisterIn {
  email: string
  /** 后端约束：8–128 位 */
  password: string
}

export interface UserLoginIn {
  email: string
  password: string
}

// ---------------------------------------------------------------- 工作流 / 模板 / 模型

export interface WorkflowOut {
  id: number
  name: string
  version: number
  display_name: string
  description: string | null
  is_active: boolean
  eval_score: number | null
  good_rate: number | null
}

/**
 * `GET /workflows/{name}/schema` 的响应。
 *
 * ⚠️ `param_schema` 是**嵌套**的，不是裸的 schema。取错层会得到 `undefined.fields`。
 * 另外后端**有意不返回 `definition`**（节点图），所以前端拿不到也不该拿。
 */
export interface WorkflowDetailOut extends WorkflowOut {
  param_schema: ParamSchema
}

export interface TemplateOut {
  id: number
  name: string
  category: string
  description: string | null
  workflow_id: number
  /** 预设参数包。**由后端合并**（优先级高于 workflow default、低于请求 params） */
  preset_params: Record<string, unknown>
  example_asset_id: number | null
  usage_count: number
  rating: number | null
  sort_order: number
}

export interface ModelRegistryOut {
  id: number
  name: string
  kind: ModelKind
  version: string
  license: string | null
  is_commercial_ok: boolean
  license_note: string | null
  eval_score: number | null
  good_rate: number | null
  is_active: boolean
  is_default: boolean
}

// ---------------------------------------------------------------- 任务

export interface TaskSubmitIn {
  workflow_name: string
  template_id?: number | null
  /** 提示词的顶层通道，优先级高于 `params.prompt`（契约 §3.4） */
  prompt?: string | null
  negative_prompt?: string | null
  /**
   * 参数。
   *
   * ⚠️ **只放用户改动过的字段**。后端合并优先级是
   * `workflow default < template.preset_params < params`，
   * 若把默认值全量塞进来，会**静默覆盖模板预设**。
   *
   * ⚠️ 参考图也走这里（`params.reference_image = asset_id`），
   * **不要**用顶层 `upload_asset_ids` —— 单任务路径从不读它（见 §"已知后端缺陷"）。
   */
  params?: Record<string, unknown>
  /** ⚠️ 单任务路径**忽略**该字段。仅为兼容批量语义而保留。 */
  upload_asset_ids?: number[]
  steps?: number | null
  cfg?: number | null
  /** `-1` 表示随机；**由服务端**解析成真实整数并回写。 */
  seed?: number | null
  idempotency_key?: string | null
}

export interface TaskOut {
  id: number
  batch_id: number | null
  idx: number
  /** ⚠️ 提交后是 `queued`，不是 `pending`（契约 §1.4） */
  status: TaskStatus
  workflow_id: number
  template_id: number | null
  params: Record<string, unknown>
  /** 服务端解析后的真实 seed（`-1` 已被替换） */
  seed: number | null
  retry_count: number
  error_type: ErrorType | null
  error_message: string | null
  started_at: string | null
  finished_at: string | null
  wait_seconds: number | null
  duration_ms: number | null
  gpu_seconds: number | null
  cost_yuan: number | null
  created_at: string
  /**
   * 是否可手动重试。
   *
   * ⚠️ **必须用这个字段**，不要自己按状态猜：
   * 判据是 `status ∈ {failed, canceled}`，而"可自动重试"是另一套
   * （`ErrorType.retryable()`）—— 两者混用会导致按钮该显不显。
   */
  can_retry: boolean
}

export interface TaskEstimateOut {
  total_images: number
  estimated_seconds: number
  estimated_cost_yuan: number | null
  gpu_concurrency: number
  queue_depth: number | null
  estimated_wait_seconds: number | null
}

export interface CancelOut {
  task_id: number
  status: string
  message: string
}

// ---------------------------------------------------------------- 批量

export interface BatchSubmitIn {
  workflow_name: string
  template_id?: number | null
  name?: string | null
  /** `SKU → 素材 ID 列表` */
  sku_assets: Record<string, number[]>
  common_params?: Record<string, unknown>
  /** 每个 SKU 出几张，后端约束 1–20 */
  images_per_sku?: number
  idempotency_key?: string | null
}

export interface BatchOut {
  id: number
  name: string | null
  status: BatchStatus
  workflow_id: number
  template_id: number | null
  total_count: number
  succeeded_count: number
  failed_count: number
  canceled_count: number
  /** 0–1 的比例 */
  progress: number
  started_at: string | null
  finished_at: string | null
  created_at: string
}

// ---------------------------------------------------------------- 素材

export interface AssetOut {
  id: number
  kind: AssetKind
  /** 产物所属任务；上传素材为 `null`。画廊「来源」筛选与跳转任务详情用它。 */
  task_id: number | null
  mime_type: string
  size_bytes: number
  width: number | null
  height: number | null
  original_name: string | null
  is_adopted: boolean
  is_favorite: boolean
  /**
   * 可复现性元数据（FR-5.4 / C1）。**只在产物上非空**，上传素材是 `{}`。
   *
   * 已知键（由 `backend/app/worker/tasks.py` 写入，与后端逐字一致）：
   * `seed` / `workflow`（形如 `t2i_v1@v3`）/ `engine_prompt_id` / `params`
   * （本次**实际生效**的完整参数）/ `models` / `filename` / `node_id` / `sha256`。
   *
   * `models` 是「这张图**实际加载**了哪些权重」的清单（C1 / FR-5.4），
   * 由 `backend/app/services/workflow_models.py` 的 `extract_models` 从
   * **提交给引擎的那张图**（已按 `_meta.switches` 裁掉 bypass 分支）提取，
   * 每项形如 `{ node, class_type, input, name }`（列表而非字典：
   * 多 LoRA 时字典的 `lora_name` 只能留下最后一个，会丢信息）。
   * 顺序 = 节点 id 数值序，同一输入跑两次结果一致。
   *
   * ⚠️ **后端已提供，但前端尚未展示**：`MetadataDrawer.tsx` 目前只渲染
   * `seed` / `workflow` / `params` / 标量键，还没消费 `models`（那是另一项工作）。
   * 在那之前**不要**在界面上编一个模型名 —— 要么不显示，要么如实显示这个字段。
   */
  meta: Record<string, unknown>
  created_at: string
  // ⚠️ 没有 url / object_key（后端有意不返回，见 schemas/asset.py 的 docstring）。
  // 取图只能走 `GET /assets/{id}/content`，且**必须带 Authorization 头**
  // —— 所以不能写 `<img src>`，要用 `api.assets.content()` 取 Blob（见 http.ts 的说明）。
}

/** ⚠️ 唯一不是裸数组的列表响应（契约 §2.3）。 */
export interface AssetListOut {
  total: number
  items: AssetOut[]
}

/**
 * 标记采纳 / 收藏（后端 `AssetUpdateIn`）。
 *
 * 两个字段都可选，但**至少给一个** —— 后端校验器会拒掉空请求体
 * （空请求体几乎总是调用方拼错了字段名，静默 200 会让前端以为标记成功）。
 */
export interface AssetUpdateIn {
  is_adopted?: boolean
  is_favorite?: boolean
}

/**
 * 批量打包的选取方式（后端 `AssetPackIn`）。
 *
 * ⚠️ `asset_ids` / `task_id` / `batch_id` **三选一**，给两个或零个都是 422。
 * 前端当前只用 `asset_ids`（勾选打包）；另外两种留给 P7-06 批量向导。
 */
export interface AssetPackIn {
  asset_ids?: number[]
  task_id?: number
  batch_id?: number
}

// ---------------------------------------------------------------- 查询参数

/**
 * 查询参数用 **type 别名**而不是 `interface`。
 *
 * 原因很具体：`RequestOptions.query` 收的是 `Record<string, unknown>`，
 * 而 TS 只给**类型别名**对象隐式索引签名，`interface` 不给 ——
 * 写成 interface 会直接编译不过（"Index signature is missing"）。
 */
export type TaskListQuery = {
  status?: TaskStatus
  batch_id?: number
  limit?: number
  offset?: number
}

export type TemplateListQuery = {
  category?: string
  limit?: number
  offset?: number
}

export type ModelListQuery = {
  commercial_only?: boolean
  kind?: ModelKind
}

export type AssetListQuery = {
  /** 取值来自 `ASSET_KIND`（契约 §6：前端不得硬编码枚举取值）。 */
  kind?: AssetKind
  /** 只看已采纳。⚠️ 后端**没有**「未采纳」参数 —— 见 `GalleryPage` 的说明。 */
  adopted_only?: boolean
  favorite_only?: boolean
  task_id?: number
  batch_id?: number
  /**
   * 创建时间边界。⚠️ 必须传**带时区**的 ISO 串（如 `...Z`）：
   * 库里存 UTC，而后端把 naive 串按 UTC 解释 —— 传本地裸时间会静默错 8 小时。
   */
  created_from?: string
  created_to?: string
  /** 1–200，后端默认 50。 */
  limit?: number
  offset?: number
}
