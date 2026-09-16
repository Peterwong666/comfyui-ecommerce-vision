/**
 * 错误模型（契约 §2.2）。
 *
 * 后端的错误信封有**三种**形状，不是两种，必须都处理：
 *
 * 1. `{"detail": "人类可读的原因"}` —— 大多数情况
 * 2. `{"detail": {"code": "...", "message": "...", "fields": [...]}}` —— 需要前端分支时
 * 3. `{"detail": "...", "error_type": "...", "retryable": bool}` —— **503**，
 *    由 `backend/app/main.py` 的 `ComfyUIError` 处理器返回，**多两个顶层键**。
 *
 * ⚠️ 402（额度不足）的形状在 2026-09-17 变了：此前 `detail` 是**纯字符串**，
 * 判据只有状态码；现在是**形状 2**，带机器可读的 `code`（契约 §2.2）。
 * 两种都要兜住 —— 理由见 `isQuotaExceeded`。
 */

import { ERROR_TYPE } from './enums'

/** 需要前端逐字段提示时，后端用这个结构（`detail.fields[]`）。 */
export interface ApiFieldError {
  key: string
  reason: string
}

export class ApiError extends Error {
  /** HTTP 状态码；`0` 表示请求根本没发出去（网络层失败） */
  readonly status: number
  /** 机器可读码。**只有对象形式的 detail 才有** */
  readonly code?: string
  /** 逐字段错误，可直接并进表单的校验结果 */
  readonly fields?: ApiFieldError[]
  /** 引擎异常才有（503） */
  readonly errorType?: string
  /** 是否**会由系统自动重试**（不等于「用户可以点重试」，后者看 `TaskOut.can_retry`） */
  readonly retryable?: boolean

  constructor(
    status: number,
    message: string,
    opts: { code?: string; fields?: ApiFieldError[]; errorType?: string; retryable?: boolean } = {},
  ) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = opts.code
    this.fields = opts.fields
    this.errorType = opts.errorType
    this.retryable = opts.retryable
  }

  /** 网络层失败（DNS/连接被拒/CORS 预检失败），不是业务错误。 */
  get isNetwork(): boolean {
    return this.status === 0
  }

  /** 令牌缺失/无效/过期。⚠️ 没有 refresh 端点，只能重新登录。 */
  get isUnauthorized(): boolean {
    return this.status === 401
  }

  /**
   * 额度不足（EX-12）。
   *
   * **优先按 `detail.code` 判断**（契约 §2.2：需要前端分支处理的错误用对象形式，
   * `code` 取后端 `ErrorType` 的取值），因为那才是机器可读的语义标识 ——
   * 状态码只是传输层的约定，`code` 才是契约。
   *
   * ⚠️ **同时保留按 402 状态码的后备**：2026-09-17 之前后端返回的是纯字符串
   * detail（此时 `code` 为 `undefined`），而契约 §2.2 明确保留字符串形式以兼容
   * 既有实现 —— 也就是说"没有 code 的 402"在契约上仍然合法。去掉状态码后备
   * 会让这类响应被当成普通错误，用户看不到"额度不足"的专门提示。
   *
   * 两个判据取或，不会互相遮蔽：新响应两个都成立，老响应只成立后者。
   * `ERROR_TYPE.QUOTA_EXCEEDED` 是后端枚举的前端镜像（`enums.ts`），
   * 不在这里写字面量 —— 取值漂移由 `test_web_enum_parity.py` 拦。
   */
  get isQuotaExceeded(): boolean {
    return this.code === ERROR_TYPE.QUOTA_EXCEEDED || this.status === 402
  }

  /** 资源不存在**或不属于当前用户**。后端对越权也用 404（不泄露资源存在性）。 */
  get isNotFound(): boolean {
    return this.status === 404
  }

  /** 状态冲突：不可取消 / 不可重试 / 邮箱已注册 / 无失败项可重跑。 */
  get isConflict(): boolean {
    return this.status === 409
  }

  /** 参数校验失败（EX-3）。`fields` 可直接并进表单。 */
  get isValidation(): boolean {
    return this.status === 422
  }

  /** 引擎不可用（ComfyUI 挂了 / 连不上）。 */
  get isEngineUnavailable(): boolean {
    return this.status === 503
  }
}

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null && !Array.isArray(v)
}

/** 把任意响应体翻译成 `ApiError`。**三种信封都在这一个地方处理。** */
export function toApiError(status: number, body: unknown): ApiError {
  const envelope = isRecord(body) ? body : {}
  const detail = envelope.detail

  // 形状 2：对象 detail
  if (isRecord(detail)) {
    const rawFields = detail.fields
    const fields = Array.isArray(rawFields)
      ? rawFields
          .filter(isRecord)
          .map((f) => ({ key: String(f.key ?? ''), reason: String(f.reason ?? '') }))
      : undefined
    return new ApiError(status, String(detail.message ?? '请求失败'), {
      code: detail.code === undefined ? undefined : String(detail.code),
      fields,
    })
  }

  // 形状 1 与 3：字符串 detail（503 会额外带 error_type / retryable）
  const message =
    typeof detail === 'string' && detail
      ? detail
      : typeof envelope.message === 'string'
        ? envelope.message
        : `请求失败（HTTP ${status}）`

  return new ApiError(status, message, {
    errorType: typeof envelope.error_type === 'string' ? envelope.error_type : undefined,
    retryable: typeof envelope.retryable === 'boolean' ? envelope.retryable : undefined,
  })
}

/** 给用户看的兜底文案。区分「网络不通」与「服务端拒绝」很重要 —— 处置方式完全不同。 */
export function describeError(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.isNetwork) {
      return '无法连接后端服务。请确认后端已启动、端口正确，且地址在后端 CORS 白名单内。'
    }
    return err.message
  }
  if (err instanceof Error) return err.message
  return String(err)
}
