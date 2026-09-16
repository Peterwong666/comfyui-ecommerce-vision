import { App as AntApp, ConfigProvider } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { RouterProvider } from 'react-router-dom'

import { router } from './router'

/**
 * React Query 客户端。
 *
 * 两处刻意的默认值：
 * - `retry: 1`：默认的 3 次重试会让"后端没起来"这种确定性失败多等好几秒才报错。
 * - `refetchOnWindowFocus: false`：切回标签页就重拉全部查询，在出图场景下
 *   会把后端打出一串无意义请求。
 */
const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      refetchOnWindowFocus: false,
    },
  },
})

export default function App() {
  return (
    <ConfigProvider locale={zhCN}>
      <AntApp>
        <QueryClientProvider client={queryClient}>
          <RouterProvider router={router} />
        </QueryClientProvider>
      </AntApp>
    </ConfigProvider>
  )
}
