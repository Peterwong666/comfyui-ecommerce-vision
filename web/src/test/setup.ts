import '@testing-library/jest-dom/vitest'

/**
 * jsdom 缺的浏览器 API 垫片。
 *
 * 只垫 antd 真正依赖的两个：
 * - `matchMedia`：antd 的响应式栅格与 `Grid.useBreakpoint` 会调用它
 * - `ResizeObserver`：部分组件（如 Select 的下拉定位）会用到
 *
 * ⚠️ 垫片让**渲染**能跑，但不代表这些组件在浏览器里的行为被验证过。
 * 交互类断言仍以浏览器实测为准（见本轮验证记录的「待人工浏览器验证」）。
 */

if (!window.matchMedia) {
  Object.defineProperty(window, 'matchMedia', {
    writable: true,
    value: (query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    }),
  })
}

if (!globalThis.ResizeObserver) {
  globalThis.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver
}
