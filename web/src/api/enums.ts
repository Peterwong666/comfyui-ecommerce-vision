/**
 * 冻结枚举的前端镜像（契约 §1 状态机 / §1.5 错误类型 / §4 埋点事件名）。
 *
 * ⚠️ **唯一事实来源是后端**：`backend/app/models/enums.py` 与 `backend/app/models/event.py`。
 * 本文件不是事实来源。要改这里的取值，必须先走契约变更流程
 * （`docs/sop/contracts.md` §0.3：改文档 → 改后端常量 → 通知其它流）。
 *
 * `backend/tests/test_web_enum_parity.py` 会**双向**比对两边取值 ——
 * 少一个、多一个、拼错都会让后端测试变红。这是刻意的：
 * 前端徽章、埋点、看板全部硬依赖这套取值，靠"记得同步"是守不住的。
 *
 * ⚠️ 这里只放**取值集合**。中文名与颜色属于**展示**，放在 `components/StatusBadge.tsx`。
 * 两者混在一起会导致"加了状态但忘了加徽章"这类漏洞。
 */

// ---------------------------------------------------------------- 任务状态（单张图）

export const TASK_STATUS = {
  PENDING: 'pending',
  QUEUED: 'queued',
  RUNNING: 'running',
  RETRYING: 'retrying',
  SUCCEEDED: 'succeeded',
  FAILED: 'failed',
  CANCELED: 'canceled',
} as const
export type TaskStatus = (typeof TASK_STATUS)[keyof typeof TASK_STATUS]

/** `TaskStatus.terminal()` —— 埋点 `task_finished` 只在终态触发。 */
export const TASK_STATUS_TERMINAL: readonly TaskStatus[] = ['succeeded', 'failed', 'canceled']

/** `TaskStatus.cancellable()` —— 决定「取消」按钮是否可点。 */
export const TASK_STATUS_CANCELLABLE: readonly TaskStatus[] = [
  'pending',
  'queued',
  'running',
  'retrying',
]

// ---------------------------------------------------------------- 批量状态（父任务）

export const BATCH_STATUS = {
  PENDING: 'pending',
  QUEUED: 'queued',
  RUNNING: 'running',
  SUCCEEDED: 'succeeded',
  FAILED: 'failed',
  CANCELED: 'canceled',
  PARTIAL: 'partial',
} as const
export type BatchStatus = (typeof BATCH_STATUS)[keyof typeof BATCH_STATUS]

/** `BatchStatus.terminal()`。⚠️ `partial` 属于**父任务**，任务状态里没有它。 */
export const BATCH_STATUS_TERMINAL: readonly BatchStatus[] = [
  'succeeded',
  'failed',
  'canceled',
  'partial',
]

// ---------------------------------------------------------------- 错误类型

export const ERROR_TYPE = {
  OOM: 'oom',
  TIMEOUT: 'timeout',
  INVALID_PARAM: 'invalid_param',
  DISK_FULL: 'disk_full',
  QUEUE_FULL: 'queue_full',
  DUPLICATE: 'duplicate',
  WORKER_CRASH: 'worker_crash',
  ENGINE_UNRESPONSIVE: 'engine_unresponsive',
  MODEL_MISSING: 'model_missing',
  INVALID_UPLOAD: 'invalid_upload',
  SAVE_FAILED: 'save_failed',
  QUOTA_EXCEEDED: 'quota_exceeded',
  UNKNOWN: 'unknown',
} as const
export type ErrorType = (typeof ERROR_TYPE)[keyof typeof ERROR_TYPE]

/** `ErrorType.retryable()` —— 只用于**自动**重试（T7 vs T11）。 */
export const ERROR_TYPE_RETRYABLE: readonly ErrorType[] = [
  'oom',
  'timeout',
  'disk_full',
  'queue_full',
  'worker_crash',
  'engine_unresponsive',
  'save_failed',
]

// ---------------------------------------------------------------- 素材 / 用户 / 模型

export const ASSET_KIND = {
  UPLOAD: 'upload',
  OUTPUT: 'output',
} as const
export type AssetKind = (typeof ASSET_KIND)[keyof typeof ASSET_KIND]

export const USER_ROLE = {
  USER: 'user',
  ADMIN: 'admin',
} as const
export type UserRole = (typeof USER_ROLE)[keyof typeof USER_ROLE]

export const MODEL_KIND = {
  CHECKPOINT: 'checkpoint',
  UNET: 'unet',
  VAE: 'vae',
  TEXT_ENCODER: 'text_encoder',
  LORA: 'lora',
  CONTROLNET: 'controlnet',
  IPADAPTER: 'ipadapter',
  UPSCALE: 'upscale',
  OTHER: 'other',
} as const
export type ModelKind = (typeof MODEL_KIND)[keyof typeof MODEL_KIND]

// ---------------------------------------------------------------- 埋点事件名

export const EVENT_NAME = {
  USER_REGISTER: 'user_register',
  USER_LOGIN: 'user_login',
  IMAGE_UPLOADED: 'image_uploaded',
  TEMPLATE_SELECTED: 'template_selected',
  PARAM_CHANGED: 'param_changed',
  TASK_SUBMITTED: 'task_submitted',
  TASK_STARTED: 'task_started',
  TASK_RETRY: 'task_retry',
  TASK_FINISHED: 'task_finished',
  IMAGE_ADOPTED: 'image_adopted',
  IMAGE_DOWNLOADED: 'image_downloaded',
  IMAGE_REGENERATED: 'image_regenerated',
} as const
export type EventName = (typeof EVENT_NAME)[keyof typeof EVENT_NAME]

/** `EventName.all()` —— P7-11 埋点接入时用来做穷尽性检查。 */
export const EVENT_NAME_ALL: readonly EventName[] = [
  'user_register',
  'user_login',
  'image_uploaded',
  'template_selected',
  'param_changed',
  'task_submitted',
  'task_started',
  'task_retry',
  'task_finished',
  'image_adopted',
  'image_downloaded',
  'image_regenerated',
]
