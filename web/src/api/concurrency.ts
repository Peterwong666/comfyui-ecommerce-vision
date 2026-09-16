/**
 * 取图请求的并发闸门（P7-05）。
 *
 * ## 为什么需要它
 *
 * 后端**没有缩略图接口**：`GET /assets/{id}/content` 返回的是**原图**
 * （1024² PNG 约 1–2MB）。画廊一页 24–50 张，若同时发起取图请求，
 * 峰值就是 24–100MB 同时在飞 —— 既撞 NFR-1 的性能要求，也直接是带宽成本。
 *
 * 缩略图是**后端**的事（P6-10 未做，已登记为遗留）。在前端这一侧能做的两件事：
 *
 * 1. **懒加载**：进入视口才请求（见 `features/gallery/AssetImage.tsx`）；
 * 2. **并发上限**：无论有多少张卡同时可见，同时在飞的取图请求不超过
 *    `ASSET_CONTENT_MAX_CONCURRENT`，其余排队。
 *
 * 上限取 6 的理由：HTTP/1.1 下单域名浏览器本来就只开 6 条连接，
 * 取更大值只会把请求堆在浏览器队列里（先到先得，反而打乱"优先加载可见图"的意图）；
 * 取更小值则会让可见区的图一张张慢慢刷出来。
 *
 * ⚠️ 这是一个**模块级**计数器而不是 React 状态：并发限制的对象是"在飞的网络请求"，
 * 与组件树无关（同一张图可能同时被卡片和预览弹窗引用）。
 */

/** 同时最多几个取图请求在飞。 */
export const ASSET_CONTENT_MAX_CONCURRENT = 6

let active = 0
const waiters: Array<() => void> = []

async function acquire(): Promise<void> {
  if (active < ASSET_CONTENT_MAX_CONCURRENT) {
    active += 1
    return
  }
  // 排队。释放方会把名额**直接移交**给队首（见 release），所以这里不需要再自增。
  await new Promise<void>((resolve) => waiters.push(resolve))
}

function release(): void {
  const next = waiters.shift()
  if (next) {
    // 名额移交：`active` 不变，等价于"走后门插队但不超额"
    next()
    return
  }
  active -= 1
}

/**
 * 在并发闸门内执行一次取图。
 *
 * 必须用 `try/finally`：请求抛错（404/500/网络失败）时若不释放名额，
 * 几次失败之后闸门就被永久卡死，表现为"图再也加载不出来"——
 * 这类死锁只会出现在错误路径上，正常路径测不出来。
 */
export async function withAssetContentSlot<T>(fn: () => Promise<T>): Promise<T> {
  await acquire()
  try {
    return await fn()
  } finally {
    release()
  }
}

/**
 * 仅供测试复位闸门状态。
 *
 * 模块级计数器在多个用例之间是共享的：上一个用例若因断言失败中途退出，
 * 未释放的名额会泄漏到下一个用例里，制造出"单跑能过、一起跑就挂"的假象。
 */
export function __resetAssetContentGate(): void {
  active = 0
  waiters.length = 0
}
