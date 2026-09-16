import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { __resetAssetContentGate } from '../../api/concurrency'
import { ASSET_KIND } from '../../api/enums'
import type { AssetOut } from '../../api/types'
import { useAuthStore } from '../../stores/authStore'
import { GalleryPage } from '../GalleryPage'

/**
 * 画廊页（P7-05）的契约测试。
 *
 * 这里钉住的都是**容易在重构中被顺手改回错误写法**的地方：
 *
 * - 上传素材不会被发采纳请求（后端会 409，前端必须在入口就挡住）
 * - 打包的请求体形状（`{asset_ids: [...]}`，且**不能**是 task_id/batch_id）
 * - object URL 的释放（漏掉不报错，只会慢慢吃掉内存 —— 这类回归必须靠测试守）
 * - 500 时渲染错误提示而不是白屏
 *
 * ⚠️ jsdom 没有 `IntersectionObserver`，`AssetImage` 在这种情况下**直接放行加载**
 * （见该组件的注释）。所以这里的断言覆盖的是"取图 → objectURL → 释放"这条链路，
 * **不代表**浏览器里的懒加载行为被验证过。
 */

// ---------------------------------------------------------------- 环境垫片

/**
 * jsdom **没有实现** `URL.createObjectURL`。
 *
 * 这正好让测试可以完整观察 object URL 的生命周期：创建了几个、内容是什么、
 * 卸载时有没有被释放。若这里没有垫片，组件会在 `createObjectURL` 处直接抛错。
 */
const createObjectURL = vi.fn((_blob: Blob) => 'blob:mock-object-url')
const revokeObjectURL = vi.fn((_url: string) => undefined)

const fetchMock = vi.fn()

beforeEach(() => {
  URL.createObjectURL = createObjectURL
  URL.revokeObjectURL = revokeObjectURL
  createObjectURL.mockClear()
  revokeObjectURL.mockClear()

  vi.stubGlobal('fetch', fetchMock)
  fetchMock.mockReset()
  // 模块级并发闸门在用例之间是共享的：不复位会串味（见 api/concurrency.ts）
  __resetAssetContentGate()

  // 未登录时 http 不发 Authorization，但也不会因此失败；给个 token 让请求形状接近真实
  useAuthStore.setState({ token: 'test-token', user: null })
})

afterEach(() => {
  vi.unstubAllGlobals()
})

// ---------------------------------------------------------------- 夹具与 mock

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function blobResponse(): Response {
  return new Response(new Blob(['fake-image-bytes'], { type: 'image/png' }), {
    status: 200,
    headers: { 'Content-Type': 'image/png' },
  })
}

function makeAsset(overrides: Partial<AssetOut> & { id: number }): AssetOut {
  return {
    kind: ASSET_KIND.OUTPUT,
    task_id: 1,
    mime_type: 'image/png',
    size_bytes: 1024,
    width: 1024,
    height: 1024,
    original_name: null,
    is_adopted: false,
    is_favorite: false,
    meta: {},
    created_at: '2026-09-01T00:00:00Z',
    ...overrides,
  }
}

/**
 * 按 URL 分发的 fetch mock。
 *
 * ⚠️ 每个分支都**返回新的 Response 实例**：`Response` 的 body 只能被读一次，
 * 复用同一个实例会让第二次读取抛 "Body has already been consumed"。
 */
function installFetch(options: {
  items?: AssetOut[]
  total?: number
  listStatus?: number
  listError?: string
}): void {
  const { items = [], total = items.length, listStatus = 200, listError = '服务端错误' } = options

  fetchMock.mockImplementation((input: unknown, init?: RequestInit) => {
    const url = String(input)
    const method = (init?.method ?? 'GET').toUpperCase()

    // ⚠️ 顺序要紧：/assets/pack 与 /assets/{id}/content 都包含 /assets
    if (url.includes('/api/v1/assets/pack')) return Promise.resolve(blobResponse())
    if (/\/api\/v1\/assets\/\d+\/content/.test(url)) return Promise.resolve(blobResponse())
    if (/\/api\/v1\/assets\/\d+$/.test(url)) {
      const body = items.find((a) => url.endsWith(`/assets/${a.id}`)) ?? items[0]
      return Promise.resolve(jsonResponse(200, body ?? { detail: undefined }))
    }
    if (url.includes('/api/v1/assets')) {
      return Promise.resolve(
        listStatus === 200
          ? jsonResponse(200, { total, items })
          : jsonResponse(listStatus, { detail: listError }),
      )
    }
    if (url.includes('/api/v1/tasks')) return Promise.resolve(jsonResponse(200, []))
    return Promise.resolve(jsonResponse(404, { detail: `未 mock 的请求：${method} ${url}` }))
  })
}

function renderGallery() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={['/gallery']}>
        <Routes>
          <Route path="/gallery" element={<GalleryPage />} />
          <Route path="/" element={<div>工作台占位</div>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

/** 取出所有非 GET 的请求，便于断言"有没有发出某个写操作"。 */
function writeCalls(method: string): Array<[unknown, RequestInit | undefined]> {
  return fetchMock.mock.calls.filter(
    (call) => ((call[1] as RequestInit | undefined)?.method ?? 'GET').toUpperCase() === method,
  ) as Array<[unknown, RequestInit | undefined]>
}

// ---------------------------------------------------------------- 用例

/**
 * 慢用例的显式超时。
 *
 * Vitest 默认 5s 不够用：本机 4 核，而并行跑的其它代理会长时间占满 CPU
 * （实测 load average ~4.9），antd + jsdom 下一次带交互的渲染实测 4–7.5s。
 * 放宽的是**等待上限**而不是断言 —— 打包用例已在 debug 里确认请求确实发出
 * （`POST /api/v1/assets/pack`），失败原因是纯延迟。
 */
const SLOW_TIMEOUT = 20_000

describe('空态 / 加载态 / 错误态', () => {
  it(
    '列表返回 0 条时渲染空态文案与「去工作台」入口',
    async () => {
      installFetch({ items: [], total: 0 })
      renderGallery()

      expect(await screen.findByText(/还没有产物/)).toBeInTheDocument()
      expect(screen.getByRole('button', { name: '去工作台' })).toBeInTheDocument()
      // 空态下不该出现任何卡片
      expect(screen.queryByTestId('asset-card')).toBeNull()
    },
    SLOW_TIMEOUT,
  )

  it('接口 500 时渲染错误提示而不是白屏', async () => {
    installFetch({ items: [], total: 0, listStatus: 500, listError: '产物服务暂时不可用' })
    renderGallery()

    expect(await screen.findByText('产物列表加载失败')).toBeInTheDocument()
    expect(screen.getByText(/产物服务暂时不可用/)).toBeInTheDocument()
  })
})

describe('列表渲染', () => {
  it('给定 3 条数据渲染 3 个卡片，采纳过的带采纳标记', async () => {
    installFetch({
      items: [
        makeAsset({ id: 1, is_adopted: true, meta: { workflow: 't2i_v1@v3', seed: 1234567 } }),
        makeAsset({ id: 2 }),
        makeAsset({ id: 3, is_adopted: true }),
      ],
      total: 3,
    })
    renderGallery()

    await waitFor(() => expect(screen.getAllByTestId('asset-card')).toHaveLength(3))
    // 只有 2 张被采纳
    expect(screen.getAllByLabelText('已采纳')).toHaveLength(2)

    // 卡片文案来自 meta（取不到才退到 id/时间），这里应显示 workflow 与 seed
    const first = screen.getByLabelText('素材卡片 1')
    expect(within(first).getByText('t2i_v1@v3')).toBeInTheDocument()
    expect(within(first).getByText('seed 1234567')).toBeInTheDocument()
  })
})

describe('只有产物能采纳（后端对上传素材返回 409）', () => {
  it('上传素材的采纳入口不可用，且点击不会发出任何 PATCH', async () => {
    installFetch({ items: [makeAsset({ id: 9, kind: ASSET_KIND.UPLOAD })], total: 1 })
    renderGallery()

    const card = await screen.findByLabelText('素材卡片 9')
    const adoptButton = within(card).getByLabelText('标记采纳')

    // 入口就该是禁用的：让用户点一个必然 409 的操作是纯粹的浪费
    expect(adoptButton).toBeDisabled()

    fireEvent.click(adoptButton)
    await waitFor(() => expect(screen.getByText('已选 0 张')).toBeInTheDocument())
    expect(writeCalls('PATCH')).toHaveLength(0)
  })

  it('产物（对照）的采纳入口可用，点击会发出 PATCH {is_adopted:true}', async () => {
    installFetch({ items: [makeAsset({ id: 10, is_adopted: false })], total: 1 })
    renderGallery()

    const card = await screen.findByLabelText('素材卡片 10')
    const adoptButton = within(card).getByLabelText('标记采纳')
    expect(adoptButton).not.toBeDisabled()

    fireEvent.click(adoptButton)

    await waitFor(() => expect(writeCalls('PATCH')).toHaveLength(1))
    const [url, init] = writeCalls('PATCH')[0]
    expect(String(url)).toContain('/api/v1/assets/10')
    expect(JSON.parse(String(init?.body))).toEqual({ is_adopted: true })
  })
})

describe('打包 zip（FR-5.2）', () => {
  it(
    '选中 2 张后点击「打包 zip」，请求体是 {asset_ids:[1,2]}',
    async () => {
      installFetch({ items: [makeAsset({ id: 1 }), makeAsset({ id: 2 }), makeAsset({ id: 3 })], total: 3 })
      renderGallery()

      await waitFor(() => expect(screen.getAllByTestId('asset-card')).toHaveLength(3))

      // 故意**逆序**勾选，验证提交的是排序后的 id 列表（顺序不稳定会让断言变成掷骰子）
      fireEvent.click(screen.getByLabelText('选择素材 2'))
      fireEvent.click(screen.getByLabelText('选择素材 1'))
      expect(screen.getByText('已选 2 张')).toBeInTheDocument()

      fireEvent.click(screen.getByRole('button', { name: '打包 zip' }))

      await waitFor(() => {
        const call = fetchMock.mock.calls.find((c) => String(c[0]).includes('/api/v1/assets/pack'))
        expect(call).toBeTruthy()
      })

      const call = fetchMock.mock.calls.find((c) => String(c[0]).includes('/api/v1/assets/pack'))!
      const init = call[1] as RequestInit
      expect(init.method).toBe('POST')
      // ⚠️ 必须是 asset_ids；写成 task_id/batch_id 会让后端按"整个任务"打包，
      // 用户明明只勾了 2 张却拿到全部 —— 这种错不会报错，只会静默多给。
      expect(JSON.parse(String(init.body))).toEqual({ asset_ids: [1, 2] })
    },
    SLOW_TIMEOUT,
  )

  it(
    '未选中任何卡片时「打包 zip」与「删除」都不可点',
    async () => {
      installFetch({ items: [makeAsset({ id: 1 })], total: 1 })
      renderGallery()

      await waitFor(() => expect(screen.getAllByTestId('asset-card')).toHaveLength(1))
      expect(screen.getByRole('button', { name: '打包 zip' })).toBeDisabled()
      expect(screen.getByRole('button', { name: '删除' })).toBeDisabled()
    },
    SLOW_TIMEOUT,
  )
})

describe('object URL 的释放（防内存泄漏回归）', () => {
  it('取图后创建 object URL，组件卸载时 revokeObjectURL 被调用', async () => {
    installFetch({ items: [makeAsset({ id: 1 })], total: 1 })
    const { unmount } = renderGallery()

    // 图片真的渲染出来了（说明走的是 Blob → objectURL → <img> 这条链路）
    const img = await screen.findByAltText('素材 1')
    expect(img).toHaveAttribute('src', 'blob:mock-object-url')
    expect(createObjectURL).toHaveBeenCalled()

    // 卸载之前**不能**释放：提前释放会让图片裂掉
    expect(revokeObjectURL).not.toHaveBeenCalled()

    const createdUrl = createObjectURL.mock.results[0].value as string
    unmount()

    expect(revokeObjectURL).toHaveBeenCalledWith(createdUrl)
  })
})
