import react from '@vitejs/plugin-react'
// 从 `vitest/config` 引入即可获得 `test` 字段的类型，
// 不需要 `/// <reference types="vitest/config" />`（且 ESLint 会报三重斜杠引用）
import { defineConfig } from 'vitest/config'

export default defineConfig({
  plugins: [react()],
  server: {
    // 5173 必须与 backend/app/main.py:53 的 CORS 白名单一致。
    // strictPort：**故意不自动换端口** —— 若 5173 被占，Vite 默认会退到 5174，
    // 而 5174 不在白名单里，前端会以「CORS 报错」的形式失败，极难定位。
    // 宁可直接起不来（错误信息明确），也不要换成一个连不上的端口。
    port: 5173,
    strictPort: true,
  },
  build: {
    outDir: 'dist',
    sourcemap: true,
  },
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    include: ['src/**/*.test.{ts,tsx}'],
    // 本机 4 核常被并行负载占满，重交互用例（如 WorkflowForm 折叠/展开）
    // 净耗时已到 8~14s，默认 5s 会随机变红。放宽到 15s 是折中：
    // 既容忍 CPU 争用抖动，又不会让真正卡死的用例等太久。
    testTimeout: 15_000,
  },
})
