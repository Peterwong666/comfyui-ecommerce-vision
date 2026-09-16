/**
 * HTTP 客户端底座。
 *
 * 三个刻意的设计选择：
 *
 * 1. **不改用 Vite proxy**，直接打真实 CORS。
 *    `backend/app/main.py:53` 已经把 `localhost:5173` / `127.0.0.1:5173` 放进白名单，
 *    生产又是同源反代（Nginx）。本地走真 CORS 才是在验证那份配置，
 *    走 proxy 等于把它绕过去，等上线才发现白名单不对。
 *
 * 2. **401 不做"刷新令牌"**。后端**根本没有 refresh 端点**
 *    （`api/v1/auth.py` 只发 `access_token` + `expires_in`），
 *    所以"自动续期"是不可能实现的，只能清会话并回到登录页。
 *    重定向交给路由（观察 `isAuthenticated`），而不是在这里命令式跳转 ——
 *    命令式跳转会全页刷新，丢掉 SPA 状态。
 *
 * 3. **不做 snake_case ↔ camelCase 转换**。保持与后端逐字一致，
 *    转换层就是两套命名体系的开始。
 *
 * 4. **图片必须走 `getBlob`，不能写 `<img src="/api/v1/assets/1/content">`**。
 *    鉴权靠 `Authorization: Bearer <JWT>` **请求头**，而浏览器发 `<img>` 请求时
 *    **不会带任何自定义头** —— 这种写法在本地（若 URL 恰好可匿名访问）可能"看起来能跑"，
 *    上线后按用户隔离的图会**全部变成裂图**，而且不会报任何错。
 *    正确做法：`requestBlob` 取回 `Blob` → `URL.createObjectURL` → 交给 `<img src>`，
 *    并在组件卸载时 `URL.revokeObjectURL`（见 `features/gallery/AssetImage.tsx`）。
 */

import { useAuthStore } from '../stores/authStore'
import { ApiError, toApiError } from './errors'

/** 后端地址。默认与 `docs/sop/runbook.md` 一致；用 `.env.local` 覆盖。 */
export const API_BASE_URL: string =
  (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? 'http://127.0.0.1:8000'

export interface RequestOptions {
  method?: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE'
  /** 对象会被 JSON 序列化；`FormData` 原样发送（素材上传走这条） */
  body?: unknown
  query?: Record<string, unknown>
  signal?: AbortSignal
  /**
   * 匿名请求（注册 / 登录 / 健康检查）。
   *
   * 同时表示两件事：**不发 Authorization**，且 **401 不当作会话过期**
   * —— 登录接口的 401 含义是"邮箱或密码错"，若顺手清会话会误导用户。
   */
  anonymous?: boolean
}

function buildUrl(path: string, query?: Record<string, unknown>): string {
  const base = API_BASE_URL.replace(/\/+$/, '')
  const url = `${base}${path.startsWith('/') ? path : `/${path}`}`
  if (!query) return url

  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(query)) {
    // 跳过未设置的值：传 `null` 会被后端当成字面量 "null"
    if (value === undefined || value === null || value === '') continue
    params.append(key, typeof value === 'boolean' ? String(value) : String(value))
  }
  const qs = params.toString()
  return qs ? `${url}?${qs}` : url
}

function buildInit(options: RequestOptions): RequestInit {
  const headers: Record<string, string> = { Accept: 'application/json' }
  const init: RequestInit = { method: options.method ?? 'GET', headers }

  if (options.body instanceof FormData) {
    // 不要手动设 Content-Type：boundary 必须由浏览器生成
    init.body = options.body
  } else if (options.body !== undefined) {
    headers['Content-Type'] = 'application/json'
    init.body = JSON.stringify(options.body)
  }

  if (!options.anonymous) {
    const token = useAuthStore.getState().token
    if (token) headers.Authorization = `Bearer ${token}`
  }

  if (options.signal) init.signal = options.signal
  return init
}

async function readBody(res: Response): Promise<unknown> {
  const text = await res.text()
  if (!text) return undefined
  try {
    return JSON.parse(text)
  } catch {
    // 非 JSON（如 Nginx 的 HTML 错误页）——原样返回，便于排错
    return text
  }
}

/**
 * 发一次请求并完成**鉴权与错误信封**处理，返回原始 `Response`。
 *
 * 抽出来的唯一理由是取图/打包这两条路径拿的是**字节**而不是 JSON：
 * 它们必须在成功时用 `res.blob()`，而 `request` 会在成功时 `res.text()` ——
 * 响应体只能被读一次，先 text 再 blob 必然拿到空内容。
 *
 * ⚠️ 这里刻意**只在失败时**读响应体：成功路径由调用方决定怎么解析。
 */
async function send(path: string, options: RequestOptions = {}): Promise<Response> {
  const url = buildUrl(path, options.query)

  let res: Response
  try {
    res = await fetch(url, buildInit(options))
  } catch (err) {
    // 主动取消（组件卸载 / 切换查询）不是错误，原样抛出让 React Query 忽略
    if (err instanceof DOMException && err.name === 'AbortError') throw err
    throw new ApiError(0, 'NETWORK')
  }

  if (!res.ok) {
    // 错误信封永远是 JSON（契约 §2.2 的三种形状），与成功体的类型无关
    const body = await readBody(res)
    const error = toApiError(res.status, body)
    if (error.isUnauthorized && !options.anonymous) {
      // 令牌失效：清会话，由路由把用户送回登录页
      useAuthStore.getState().logout()
    }
    throw error
  }

  return res
}

/**
 * 发一次请求。成功返回解析后的响应体；失败**一律抛 `ApiError`**。
 *
 * 抛错而不是返回 `{ok, data}`：调用方（React Query）用异常区分
 * "请求成功但数据为空"与"请求失败"，返回联合类型会让人忘记检查。
 */
export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const res = await send(path, options)
  return (await readBody(res)) as T
}

/**
 * 发一次请求并把成功响应体当成**二进制**读回来（取图 / 打包 zip）。
 *
 * ⚠️ 不要退回 `request<T>` 再 `await res.blob()`：`request` 内部先 `text()`
 * 读走了 body，之后 `blob()` 只会得到空内容 —— 这是一类**不报错**的错误。
 */
export async function requestBlob(path: string, options: RequestOptions = {}): Promise<Blob> {
  const res = await send(path, options)
  return res.blob()
}

export const http = {
  get: <T>(path: string, options?: Omit<RequestOptions, 'method' | 'body'>) =>
    request<T>(path, { ...options, method: 'GET' }),
  post: <T>(path: string, body?: unknown, options?: Omit<RequestOptions, 'method' | 'body'>) =>
    request<T>(path, { ...options, method: 'POST', body }),
  patch: <T>(path: string, body?: unknown, options?: Omit<RequestOptions, 'method' | 'body'>) =>
    request<T>(path, { ...options, method: 'PATCH', body }),
  del: <T>(path: string, options?: Omit<RequestOptions, 'method' | 'body'>) =>
    request<T>(path, { ...options, method: 'DELETE' }),

  /** 取二进制（`res.blob()`），不是 JSON。 */
  getBlob: (path: string, options?: Omit<RequestOptions, 'method' | 'body'>) =>
    requestBlob(path, { ...options, method: 'GET' }),
  postBlob: (path: string, body?: unknown, options?: Omit<RequestOptions, 'method' | 'body'>) =>
    requestBlob(path, { ...options, method: 'POST', body }),
}
