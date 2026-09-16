import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useAuthStore } from '../../stores/authStore'
import { ERROR_TYPE } from '../enums'
import { ApiError } from '../errors'
import { API_BASE_URL, request } from '../http'

/**
 * HTTP 层的契约测试。
 *
 * 重点覆盖**三种错误信封**与两个容易搞错的行为：
 * - 402 的判据是 `detail.code`（对象形式），**同时**保留 402 状态码后备 ——
 *   2026-09-17 之前它是纯字符串，那类响应契约上仍然合法
 * - 401 只在**非匿名**请求上才清会话（登录失败的 401 含义不同）
 */

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

/**
 * ⚠️ 用 `mockImplementation` 而不是 `mockResolvedValue`。
 *
 * `Response` 的 body **只能被读一次**。`mockResolvedValue` 会让同一个 Response
 * 实例被多次 `fetch` 共用，第二次 `res.text()` 直接抛
 * "Body has already been consumed" —— 表现为莫名其妙的非 ApiError 失败。
 * 每次调用返回一个新实例才是对的。
 */
function mockJson(status: number, body: unknown): void {
  fetchMock.mockImplementation(() => Promise.resolve(jsonResponse(status, body)))
}

const fetchMock = vi.fn()

beforeEach(() => {
  vi.stubGlobal('fetch', fetchMock)
  fetchMock.mockReset()
  useAuthStore.setState({ token: null, user: null })
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('错误信封（契约 §2.2 的三种形状）', () => {
  it('形状 1：字符串 detail', async () => {
    mockJson(404, { detail: '任务不存在' })

    const err = await request('/api/v1/tasks/1').catch((e: unknown) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect((err as ApiError).status).toBe(404)
    expect((err as ApiError).message).toBe('任务不存在')
    expect((err as ApiError).isNotFound).toBe(true)
  })

  it('形状 2：对象 detail（带 code 与 fields，可直接并进表单）', async () => {
    mockJson(422, {
      detail: {
        code: 'INVALID_PARAM',
        message: '参数不合法',
        fields: [{ key: 'width', reason: '超过单任务上限' }],
      },
    })

    const err = (await request('/api/v1/tasks').catch((e: unknown) => e)) as ApiError
    expect(err.status).toBe(422)
    expect(err.isValidation).toBe(true)
    expect(err.code).toBe('INVALID_PARAM')
    expect(err.message).toBe('参数不合法')
    expect(err.fields).toEqual([{ key: 'width', reason: '超过单任务上限' }])
  })

  it('形状 3：503 多了 error_type / retryable 两个顶层键', async () => {
    mockJson(503, {
      detail: '引擎无响应',
      error_type: 'engine_unresponsive',
      retryable: true,
    })

    const err = (await request('/api/v1/tasks/1').catch((e: unknown) => e)) as ApiError
    expect(err.status).toBe(503)
    expect(err.isEngineUnavailable).toBe(true)
    expect(err.errorType).toBe('engine_unresponsive')
    expect(err.retryable).toBe(true)
  })

  it('非 JSON 响应（如 Nginx 错误页）不会让解析崩掉', async () => {
    fetchMock.mockResolvedValue(new Response('<html>502 Bad Gateway</html>', { status: 502 }))

    const err = (await request('/api/v1/tasks').catch((e: unknown) => e)) as ApiError
    expect(err.status).toBe(502)
    expect(err.message).toContain('502')
  })

  it('网络层失败 → status 0 且 isNetwork（与"服务端拒绝"区分开）', async () => {
    fetchMock.mockRejectedValue(new TypeError('Failed to fetch'))

    const err = (await request('/api/v1/tasks').catch((e: unknown) => e)) as ApiError
    expect(err).toBeInstanceOf(ApiError)
    expect(err.status).toBe(0)
    expect(err.isNetwork).toBe(true)
  })
})

describe('402 额度不足', () => {
  it('形状 2：按 detail.code 识别（2026-09-17 起后端就是这个形状）', async () => {
    // 契约 §2.2：需要前端分支处理时 detail 用对象形式，code 取后端枚举的取值。
    mockJson(402, {
      detail: {
        code: ERROR_TYPE.QUOTA_EXCEEDED,
        message: '额度不足：需要 3，剩余 1',
        fields: [{ key: 'count', reason: '需要 3，剩余 1' }],
      },
    })

    const err = (await request('/api/v1/tasks', { method: 'POST', body: {} }).catch(
      (e: unknown) => e,
    )) as ApiError

    expect(err.isQuotaExceeded).toBe(true)
    expect(err.code).toBe(ERROR_TYPE.QUOTA_EXCEEDED)
    // message 保持人类可读，页面直接拿它当提示文案
    expect(err.message).toBe('额度不足：需要 3，剩余 1')
    expect(err.fields).toEqual([{ key: 'count', reason: '需要 3，剩余 1' }])
  })

  it('后备：老形状（纯字符串 detail、没有 code）的 402 仍要认得出来', async () => {
    // 契约 §2.2 明确保留字符串形式以兼容既有实现 ——
    // 也就是说"没有 code 的 402"在契约上仍合法。只按 code 判会让它降级成普通错误提示。
    mockJson(402, { detail: '额度不足：需要 10，剩余 3' })

    const err = (await request('/api/v1/tasks', { method: 'POST', body: {} }).catch(
      (e: unknown) => e,
    )) as ApiError

    expect(err.isQuotaExceeded).toBe(true)
    expect(err.code).toBeUndefined()
    expect(err.message).toBe('额度不足：需要 10，剩余 3')
  })

  it('code 是判据本身：换个状态码带同款 code 也认', async () => {
    // 这条钉住"code 分支真的存在"。若只留状态码后备，
    // 上面第一条仍会通过（状态恰好是 402），只有这一条会红。
    mockJson(400, { detail: { code: ERROR_TYPE.QUOTA_EXCEEDED, message: '额度不足' } })

    const err = (await request('/api/v1/tasks').catch((e: unknown) => e)) as ApiError
    expect(err.isQuotaExceeded).toBe(true)
  })
})

describe('401 与登出', () => {
  it('非匿名请求的 401 会清会话（令牌过期无法续期，只能重登）', async () => {
    useAuthStore.setState({ token: 'stale-token' })
    mockJson(401, { detail: '令牌无效' })

    await request('/api/v1/tasks').catch(() => undefined)

    expect(useAuthStore.getState().token).toBeNull()
  })

  it('匿名请求（登录）的 401 不清会话 —— 那是"密码错误"，不是会话过期', async () => {
    useAuthStore.setState({ token: null })
    mockJson(401, { detail: '邮箱或密码错误' })

    const err = (await request('/api/v1/auth/login', {
      method: 'POST',
      body: { email: 'a@b.c', password: 'x' },
      anonymous: true,
    }).catch((e: unknown) => e)) as ApiError

    expect(err.isUnauthorized).toBe(true)
    expect(useAuthStore.getState().token).toBeNull()
  })
})

describe('请求构造', () => {
  it('有令牌时带 Authorization；匿名时不带', async () => {
    useAuthStore.setState({ token: 'abc123' })
    mockJson(200, {})

    await request('/api/v1/workflows')
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect((init.headers as Record<string, string>).Authorization).toBe('Bearer abc123')

    fetchMock.mockClear()
    await request('/api/v1/auth/me', { anonymous: true })
    const [, anonInit] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect((anonInit.headers as Record<string, string>).Authorization).toBeUndefined()
  })

  it('查询参数跳过 undefined / null / 空串（否则会传字面量 "null"）', async () => {
    mockJson(200, [])

    await request('/api/v1/tasks', {
      query: { limit: 50, status: undefined, batch_id: null, offset: 0 },
    })

    const [url] = fetchMock.mock.calls[0] as [string]
    expect(url).toContain('limit=50')
    expect(url).toContain('offset=0')
    expect(url).not.toContain('status')
    expect(url).not.toContain('batch_id')
  })

  it('URL 由 API_BASE_URL 拼出，默认与 runbook 一致', async () => {
    mockJson(200, [])

    await request('/api/v1/workflows')

    const [url] = fetchMock.mock.calls[0] as [string]
    expect(url).toBe(`${API_BASE_URL}/api/v1/workflows`)
  })

  it('FormData 不手动设 Content-Type（boundary 必须由浏览器生成）', async () => {
    mockJson(201, { id: 1 })
    const fd = new FormData()
    fd.append('file', new Blob(['x']), 'a.png')

    await request('/api/v1/assets', { method: 'POST', body: fd })

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect((init.headers as Record<string, string>)['Content-Type']).toBeUndefined()
  })
})

describe('列表响应的不对称', () => {
  it('/assets 返回 {total, items} 而不是裸数组', async () => {
    mockJson(200, { total: 1, items: [{ id: 7, kind: 'upload' }] })

    const data = await request<{ total: number; items: unknown[] }>('/api/v1/assets')
    expect(data.total).toBe(1)
    expect(data.items).toHaveLength(1)
  })

  it('/tasks 是裸数组', async () => {
    mockJson(200, [{ id: 1 }, { id: 2 }])

    const data = await request<unknown[]>('/api/v1/tasks')
    expect(Array.isArray(data)).toBe(true)
    expect(data).toHaveLength(2)
  })
})
