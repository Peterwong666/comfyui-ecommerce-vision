/**
 * React Query 的 key 工厂。
 *
 * 集中定义的理由：key 散落在各组件里时，失效（invalidate）几乎必然漏掉一处，
 * 表现是"提交完列表不刷新"这类难查的问题。
 *
 * 约定：**同一资源的列表与详情共享第一段**，便于用前缀批量失效。
 */

import type { AssetListQuery, ModelListQuery, TaskListQuery, TemplateListQuery } from './types'

export const queryKeys = {
  // 鉴权
  me: () => ['auth', 'me'] as const,

  // 工作流
  workflows: () => ['workflows'] as const,
  workflowSchema: (name: string) => ['workflows', name, 'schema'] as const,

  // 模板 / 模型
  templates: (query?: TemplateListQuery) => ['templates', query ?? null] as const,
  templateCategories: () => ['templates', 'categories'] as const,
  models: (query?: ModelListQuery) => ['models', query ?? null] as const,

  // 任务
  tasks: (query?: TaskListQuery) => ['tasks', query ?? null] as const,
  task: (taskId: number) => ['tasks', 'detail', taskId] as const,

  // 批量
  batches: () => ['batches'] as const,
  batch: (batchId: number) => ['batches', 'detail', batchId] as const,

  // 素材
  assets: (query?: AssetListQuery) => ['assets', query ?? null] as const,
  asset: (assetId: number) => ['assets', 'detail', assetId] as const,
} as const
