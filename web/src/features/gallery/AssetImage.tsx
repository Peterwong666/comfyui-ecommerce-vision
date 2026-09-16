import { Spin, Typography } from 'antd'
import { useEffect, useRef, useState } from 'react'

import { useAssetContent } from '../../api/hooks'
import type { AssetOut } from '../../api/types'

/**
 * 画廊里显示一张产物的**唯一**入口。
 *
 * ## 为什么不能写 `<img src={`/api/v1/assets/${id}/content`}>`
 *
 * 鉴权走 `Authorization: Bearer <JWT>` **请求头**（`api/http.ts`），
 * 而浏览器发起 `<img>` 请求时**不会带任何自定义头** —— 那个请求到后端就是 401。
 *
 * 这个坑最阴的地方是它**可能看起来能跑**：本地开发时若对象存储或后端恰好放行了
 * 匿名访问，图片会正常显示，于是没人会去查；上线后图按用户隔离（FR-1.3），
 * 整个画廊**全白且控制台不报业务错误**。
 *
 * 正确路径：
 *   1. 用 http 客户端取 `Blob`（带 Authorization 头）；
 *   2. `URL.createObjectURL(blob)` 得到 `blob:` 地址；
 *   3. 把它交给 `<img src>`；
 *   4. **组件卸载 / blob 变化时 `URL.revokeObjectURL`**。
 *
 * ## 第 4 步为什么是硬要求
 *
 * object URL 会把 blob **钉在内存里**直至显式释放。画廊一页几十张、滚动几轮，
 * 泄漏量就是几百 MB 级。所以这里把创建与释放放在**同一个 `useEffect`** 里
 * （返回的清理函数就是释放），而不是"在某个卸载回调里记得调一下"。
 *
 * ## 原图 vs 缩略图
 *
 * 后端**没有缩略图接口**，`/content` 返回的是原图（1–2MB 一张）。因此这里做了两件事，
 * 缺一不可（否则一页 50 张原图会同时起飞）：
 *
 * - **进入视口才加载**：`IntersectionObserver`（`rootMargin` 提前 200px 预热）；
 * - **并发上限 6**：由 `api/concurrency.ts` 的闸门统一约束。
 *
 * 这两条只是前端侧的缓解。真正的解法是后端出缩略图接口，已登记为遗留项。
 */

/**
 * 把 `Blob` 变成可给 `<img>` 用的 object URL，并在卸载/换图时释放。
 *
 * 单独抽成一个 hook 是为了让"释放"这件事**只有一个实现**：创建与释放写在同一个
 * `useEffect` 里，清理函数就是释放点。
 *
 * ⚠️ 不 `export`：本文件同时导出组件，再导出函数会让 react-refresh 失效
 * （ESLint `react-refresh/only-export-components`）。它目前只服务于 `AssetImage`，
 * 不需要跨文件复用；组件的行为由 `GalleryPage.test.tsx` 经 `URL.revokeObjectURL` 的
 * spy 端到端钉住。
 */
function useObjectUrl(blob: Blob | null | undefined): string | null {
  const [url, setUrl] = useState<string | null>(null)

  useEffect(() => {
    if (!blob) {
      setUrl(null)
      return
    }
    const objectUrl = URL.createObjectURL(blob)
    setUrl(objectUrl)
    // ⚠️ 不要删这个清理函数：它是 object URL 唯一的释放点。
    return () => URL.revokeObjectURL(objectUrl)
  }, [blob])

  return url
}

/**
 * 进入视口前不加载。
 *
 * ⚠️ 测试环境（jsdom）**没有 `IntersectionObserver`**，此时直接放行 ——
 * 否则组件永远不取图，`useObjectUrl` 的释放行为就没法被回归测试覆盖。
 * 真实浏览器都实现了它，懒加载照常生效；这个分支只为测试存在，
 * 不是为了兼容老浏览器。
 */
function useInViewport<T extends Element>() {
  const ref = useRef<T>(null)
  const [inView, setInView] = useState(false)

  useEffect(() => {
    const element = ref.current
    if (!element || typeof IntersectionObserver === 'undefined') {
      setInView(true)
      return
    }
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) {
          setInView(true)
          // 一旦可见就不再需要观察：图片不会因为我们滚走而"卸载"
          observer.disconnect()
        }
      },
      { rootMargin: '200px' },
    )
    observer.observe(element)
    return () => observer.disconnect()
  }, [])

  return [ref, inView] as const
}

export function AssetImage({
  asset,
  height = 160,
  objectFit = 'cover',
}: {
  asset: AssetOut
  height?: number
  objectFit?: 'cover' | 'contain'
}) {
  const [ref, inView] = useInViewport<HTMLDivElement>()
  const content = useAssetContent(asset.id, inView)
  const objectUrl = useObjectUrl(content.data)

  return (
    <div
      ref={ref}
      style={{
        height,
        background: '#fafafa',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        overflow: 'hidden',
      }}
    >
      {objectUrl ? (
        <img
          src={objectUrl}
          alt={`素材 ${asset.id}`}
          style={{ width: '100%', height: '100%', objectFit }}
        />
      ) : content.isError ? (
        // 单张图取不到不该让整页报错：其余几十张还能看。这里就地降级。
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          取图失败
        </Typography.Text>
      ) : (
        <Spin size="small" />
      )}
    </div>
  )
}
