import { Navigate, createBrowserRouter, useLocation } from 'react-router-dom'
import type { ReactNode } from 'react'

import { AppShell } from './layout/AppShell'
import { GalleryPage } from './pages/GalleryPage'
import { LoginPage } from './pages/LoginPage'
import { PlaceholderPage } from './pages/PlaceholderPage'
import { WorkbenchPage } from './pages/WorkbenchPage'
import { useAuthStore } from './stores/authStore'

/**
 * 路由。
 *
 * 未登录访问受保护页面时跳登录，并**记住原地址**（`state.from`）——
 * 用户登录后应当回到他本来要去的地方，而不是被丢到首页。
 */
function RequireAuth({ children }: { children: ReactNode }) {
  const token = useAuthStore((s) => s.token)
  const location = useLocation()

  if (!token) {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />
  }
  return <>{children}</>
}

/**
 * ⚠️ 必须显式标注类型：`createBrowserRouter` 的推断类型指向 pnpm 深层路径下的
 * `@remix-run/router`（`.pnpm/@remix-run+router@…`），TS 认为它"不可移植"而报 TS2742。
 * 用 `ReturnType<...>` 而不是去 import 那个传递依赖 —— 直接 import 会把
 * 一个非直接依赖钉进源码。
 */
export const router: ReturnType<typeof createBrowserRouter> = createBrowserRouter([
  { path: '/login', element: <LoginPage /> },
  {
    path: '/',
    element: (
      <RequireAuth>
        <AppShell />
      </RequireAuth>
    ),
    children: [
      { index: true, element: <WorkbenchPage /> },
      {
        path: 'batch',
        element: (
          <PlaceholderPage
            title="批量生成"
            taskId="P7-06"
            note="批量向导：CSV 导入、参数矩阵预览、提交前预估时长与成本。"
          />
        ),
      },
      {
        path: 'tasks',
        element: (
          <PlaceholderPage
            title="任务中心"
            taskId="P7-04"
            note="队列与实时进度、失败原因、一键重试、断点续跑。"
          />
        ),
      },
      {
        path: 'gallery',
        element: <GalleryPage />,
      },
      {
        path: 'templates',
        element: (
          <PlaceholderPage
            title="模板库"
            taskId="P7-08"
            note="模板浏览、搜索、一键套用。模板数据由 P3-10 提供。"
          />
        ),
      },
    ],
  },
])
