/**
 * 类型化的接口封装。**唯一**允许拼接口路径的地方 ——
 * 页面与组件一律经由 `api.*` 调用，不要自己 `fetch`：那样会绕过错误信封处理。
 *
 * 路径与契约 §2.3 的清单一一对应（18 条，其中"尚未实现"的部分见文件末尾）。
 */

import { http } from './http'
import type {
  AssetListOut,
  AssetOut,
  AssetListQuery,
  AssetPackIn,
  AssetUpdateIn,
  BatchOut,
  BatchSubmitIn,
  CancelOut,
  CompareGroup,
  CompareSubmitIn,
  ModelListQuery,
  ModelRegistryOut,
  QualityDashboard,
  TaskEstimateOut,
  TaskListQuery,
  TaskOut,
  TaskSubmitIn,
  TemplateListQuery,
  TemplateOut,
  TokenOut,
  UserLoginIn,
  UserOut,
  UserRegisterIn,
  WorkflowDetailOut,
  WorkflowOut,
} from './types'

export const api = {
  auth: {
    /** 201。注册即送额度（无需信用卡）。 */
    register: (body: UserRegisterIn) =>
      http.post<TokenOut>('/api/v1/auth/register', body, { anonymous: true }),

    /** ⚠️ 密码错误时是 401，但**不代表会话过期** —— 故用 anonymous 跳过登出逻辑。 */
    login: (body: UserLoginIn) =>
      http.post<TokenOut>('/api/v1/auth/login', body, { anonymous: true }),

    me: () => http.get<UserOut>('/api/v1/auth/me'),
  },

  workflows: {
    /** 只返回 `is_active=true` 的工作流。**不含 `param_schema`**。 */
    list: () => http.get<WorkflowOut[]>('/api/v1/workflows'),

    /**
     * 工作流详情 + `param_schema`（驱动动态表单）。
     *
     * ⚠️ schema **嵌套在 `param_schema` 键下**，不是裸的；
     * 响应里**没有 `definition`**（后端有意不暴露节点图）。
     */
    schema: (name: string) =>
      http.get<WorkflowDetailOut>(`/api/v1/workflows/${encodeURIComponent(name)}/schema`),
  },

  templates: {
    list: (query?: TemplateListQuery) => http.get<TemplateOut[]>('/api/v1/templates', { query }),
    categories: () => http.get<string[]>('/api/v1/templates/categories'),
  },

  models: {
    list: (query?: ModelListQuery) => http.get<ModelRegistryOut[]>('/api/v1/models', { query }),
  },

  tasks: {
    /**
     * 提交单次生成。
     *
     * ⚠️ 返回 `202`，且响应里的 `status` 是 **`queued`** 而不是 `pending`
     * （契约 §1.4：提交时同步入队）。
     */
    submit: (body: TaskSubmitIn) => http.post<TaskOut>('/api/v1/tasks', body),

    list: (query?: TaskListQuery) => http.get<TaskOut[]>('/api/v1/tasks', { query }),

    get: (taskId: number) => http.get<TaskOut>(`/api/v1/tasks/${taskId}`),

    cancel: (taskId: number) => http.post<CancelOut>(`/api/v1/tasks/${taskId}/cancel`),

    /** ⚠️ 是否可重试请看 `TaskOut.can_retry`，不要按状态自己推断。 */
    retry: (taskId: number) => http.post<TaskOut>(`/api/v1/tasks/${taskId}/retry`),

    /**
     * 提交前预估（FR-3.3）。
     *
     * ⚠️ **当前实现无法用于单张图**：它收的是 `BatchSubmitIn`，
     * 空 `sku_assets` 会被校验器直接拒（`schemas/task.py` 的 `_check_sku_count`），
     * 而且内部会连 ComfyUI 取队列深度。
     * 所以工作台**不调用**它；留给 P7-06 批量向导。
     */
    estimate: (body: BatchSubmitIn) => http.post<TaskEstimateOut>('/api/v1/tasks/estimate', body),
  },

  batches: {
    submit: (body: BatchSubmitIn) => http.post<BatchOut>('/api/v1/batches', body),
    list: () => http.get<BatchOut[]>('/api/v1/batches'),
    get: (batchId: number) => http.get<BatchOut>(`/api/v1/batches/${batchId}`),
    /** 断点续跑：**只重跑失败项**（FR-3.5）。 */
    retryFailed: (batchId: number) =>
      http.post<BatchOut>(`/api/v1/batches/${batchId}/retry-failed`),
  },

  assets: {
    /**
     * 上传素材（multipart，字段名必须是 `file`）。
     *
     * 后端校验：100KB–20MB、**按魔数判格式**（JPEG/PNG/WebP，扩展名不作数）、
     * 边长 64–8192。不合格返回 422 并带具体原因。
     */
    upload: (file: File) => {
      const fd = new FormData()
      fd.append('file', file)
      return http.post<AssetOut>('/api/v1/assets', fd)
    },

    /** ⚠️ 返回 `{total, items}`，**不是裸数组**（唯一例外）。 */
    list: (query?: AssetListQuery) => http.get<AssetListOut>('/api/v1/assets', { query }),

    get: (assetId: number) => http.get<AssetOut>(`/api/v1/assets/${assetId}`),

    /**
     * 取图片字节（P6-10，**唯一的取图入口**）。
     *
     * ⚠️ 返回 `Blob` 而不是 URL：鉴权走 `Authorization` **请求头**，
     * 而 `<img src="...">` 不会带这个头 —— 直接写 src 在本机可能侥幸能显示，
     * 上线后按用户隔离的图会全部裂掉，且不报错。
     * 调用方拿到 Blob 后要 `URL.createObjectURL` 并负责 `revokeObjectURL`。
     *
     * `download: true` → 响应头是 `attachment`，**并计入 `image_downloaded` 埋点**；
     * 画廊里的内联预览刻意不传（划过去看一眼不是"交付"，否则北极星指标被灌水）。
     */
    content: (assetId: number, options: { download?: boolean } = {}) =>
      http.getBlob(`/api/v1/assets/${assetId}/content`, {
        // false 时**不传该参数**：后端默认 inline，传 `download=false` 只是噪音
        query: options.download ? { download: true } : undefined,
      }),

    /**
     * 标记采纳 / 收藏（PATCH）。
     *
     * ⚠️ **只有 `kind=output` 的产物能标记采纳**，对上传素材会 409；
     * 回收站里的素材同样 409。前端应在入口处就禁用，而不是靠 409 兜底。
     */
    update: (assetId: number, body: AssetUpdateIn) =>
      http.patch<AssetOut>(`/api/v1/assets/${assetId}`, body),

    /**
     * 打包 zip（FR-5.2 Must）。返回 `application/zip` 的 `Blob`。
     *
     * ⚠️ 失败**不是静默少给**：超过 `max_pack_assets` 是 422，
     * 选中的 id 里有不存在/不属于自己的是 404。
     */
    pack: (body: AssetPackIn) => http.postBlob('/api/v1/assets/pack', body),

    /** 软删除（标记而物理保留）。重复删除是 409。 */
    remove: (assetId: number) => http.del<AssetOut>(`/api/v1/assets/${assetId}`),
  },

  stats: {
    /** 质量看板（P8-05）。聚合产物采纳、任务执行、缺陷知识库数据。 */
    quality: () => http.get<QualityDashboard>('/api/v1/stats/quality'),
  },

  compare: {
    /** 提交 A/B 对比（P8-07）。同 seed 下多变体参数对比。 */
    submit: (body: CompareSubmitIn) => http.post<CompareGroup>('/api/v1/compare', body),
    /** 获取对比组详情。 */
    get: (groupId: string) => http.get<CompareGroup>(`/api/v1/compare/${encodeURIComponent(groupId)}`),
    /** 列出所有对比组。 */
    list: () => http.get<CompareGroup[]>('/api/v1/compare'),
  },
}

// ---------------------------------------------------------------- 已知缺口
//
// 以下端点在契约 §2.3 里被标注为"尚未实现"，前端**不要假设它们存在**：
//   · 缩略图接口 —— `/assets/{id}/content` 返回的是**原图**（1-2MB/张），
//     所以画廊必须做并发受限的懒加载，不能一页几十张一起拉（见 `api/concurrency.ts`）
//   · WebSocket 进度推送（P6-04）—— 目前只能靠轮询 `GET /tasks/{id}`
//   · 管理后台接口（P7-09）
//   · API Key 鉴权（P6-07）
//
// ✅ P6-10 已补齐（不再属于缺口）：取图 `/assets/{id}/content`、标记 PATCH、
//    打包 `POST /assets/pack`、软删除 DELETE。
