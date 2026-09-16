import { useQuery } from '@tanstack/react-query'

import { api } from './client'
import { TASK_STATUS_TERMINAL } from './enums'
import { queryKeys } from './keys'
import type { TaskListQuery } from './types'

/**
 * 与服务端状态相关的查询 hook。
 *
 * 全部经 React Query：它负责缓存、去重、重试与轮询，
 * 手写 `useEffect + fetch` 会把这些各写一遍且每处都不一样。
 */

/** 当前用户（含配额）。`enabled` 由调用方按"是否已登录"控制。 */
export function useMe(options: { enabled?: boolean } = {}) {
  return useQuery({
    queryKey: queryKeys.me(),
    queryFn: () => api.auth.me(),
    enabled: options.enabled ?? true,
    staleTime: 30 * 1000,
  })
}

/** 可用的工作流列表（后端只返回 `is_active=true` 的）。 */
export function useWorkflows() {
  return useQuery({
    queryKey: queryKeys.workflows(),
    queryFn: () => api.workflows.list(),
    staleTime: 5 * 60 * 1000,
  })
}

/** 任务列表。 */
export function useTasks(query?: TaskListQuery) {
  return useQuery({
    queryKey: queryKeys.tasks(query),
    queryFn: () => api.tasks.list(query),
  })
}

/**
 * 单个任务详情，**非终态时自动轮询**。
 *
 * ⚠️ 后端**没有** WebSocket 进度推送（P6-04 未做），所以当前只能靠轮询。
 * 轮询间隔 2s 是折中：单张出图约 2–5s，2s 能给出"快好了"的感觉，
 * 又不会把后端打满。等 P6-04 落地后应换成推送。
 */
export function useTask(taskId: number | null, options: { pollMs?: number } = {}) {
  const pollMs = options.pollMs ?? 2000

  return useQuery({
    queryKey: queryKeys.task(taskId ?? 0),
    queryFn: () => api.tasks.get(taskId as number),
    enabled: taskId !== null,
    refetchInterval: (query) => {
      const status = query.state.data?.status
      if (status === undefined) return false
      // 终态就停：继续轮询只是白烧请求。
      // ⚠️ 判据取自 `enums.ts`（由 parity 测试守），**不要**在这里写死
      // `['succeeded','failed','canceled']` —— 契约 §6 明令前端不得硬编码状态取值。
      return (TASK_STATUS_TERMINAL as readonly string[]).includes(status) ? false : pollMs
    },
  })
}
