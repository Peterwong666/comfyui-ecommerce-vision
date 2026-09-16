import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { api } from './client'
import { withAssetContentSlot } from './concurrency'
import { TASK_STATUS_TERMINAL } from './enums'
import { queryKeys } from './keys'
import type { AssetListQuery, AssetPackIn, AssetUpdateIn, TaskListQuery } from './types'

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

// ---------------------------------------------------------------- 素材 / 产物（P7-05）

/**
 * 素材列表（分页 + 筛选）。
 *
 * ⚠️ 响应是 `{total, items}` 而不是裸数组（唯一例外），分页器要用 `total`。
 *
 * `keepPreviousData` 是刻意的：翻页时列表不会先塌成空白再长出内容，
 * 否则每翻一页整个网格闪一下。代价是新页到达前仍显示旧页数据 ——
 * 所以调用方要用 `isPlaceholderData` 表示"正在换页"。
 */
export function useAssets(query?: AssetListQuery) {
  return useQuery({
    queryKey: queryKeys.assets(query),
    queryFn: () => api.assets.list(query),
    placeholderData: keepPreviousData,
  })
}

/**
 * 取一张图的字节。
 *
 * ⚠️ **不要**用它的结果直接拼 `<img src>`：这里拿回来的是 `Blob`，
 * 必须经 `URL.createObjectURL` 才能给 `<img>`，且由调用方负责 `revokeObjectURL`
 * （见 `features/gallery/AssetImage.tsx`）。
 *
 * ⚠️ 取的是**原图**（后端无缩略图接口）。`enabled` 由调用方按"是否进入视口"控制，
 * 并且请求本身受 `withAssetContentSlot` 的并发闸门约束。
 *
 * `staleTime: Infinity`：同一 id 的字节是不变的（后端 ETag 就是内容 sha256），
 * 重用缓存不会显示过期图；`gcTime` 只保留 5 分钟，避免几十张原图长期占住 JS 堆。
 */
export function useAssetContent(assetId: number, enabled = true) {
  return useQuery({
    queryKey: queryKeys.assetContent(assetId),
    queryFn: () => withAssetContentSlot(() => api.assets.content(assetId)),
    enabled,
    staleTime: Infinity,
    gcTime: 5 * 60 * 1000,
  })
}

/**
 * 标记采纳 / 收藏（FR-2.7 · FR-5.3）。
 *
 * 成功后失效**整个 `assets` 前缀**：采纳状态会同时影响列表某个筛选组合下的
 * 成员集合（例如「已采纳」筛选）与详情缓存，只失效当前 key 会漏。
 */
export function useUpdateAsset() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: ({ assetId, body }: { assetId: number; body: AssetUpdateIn }) =>
      api.assets.update(assetId, body),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.assetsRoot() })
    },
  })
}

/**
 * 批量软删除（FR-5.3）。
 *
 * ⚠️ `onSettled` 而不是 `onSuccess` 里失效：删 3 张时第 2 张若返回 409
 * （比如在另一个标签页里已删过），另外 2 张其实已经删成功了 ——
 * 只在成功时刷新会让界面停在一个**部分陈旧**的状态上。
 */
export function useDeleteAssets() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (assetIds: number[]) => Promise.all(assetIds.map((id) => api.assets.remove(id))),
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.assetsRoot() })
    },
  })
}

/**
 * 打包 zip（FR-5.2 Must）。返回 `application/zip` 的 `Blob`。
 *
 * ⚠️ 不做乐观更新也不做缓存：zip 可能几十 MB，塞进 React Query 缓存会让内存爆掉。
 * 调用方拿到 Blob 后应当立刻写盘并让它被回收。
 */
export function usePackAssets() {
  return useMutation({
    mutationFn: (body: AssetPackIn) => api.assets.pack(body),
  })
}
