import { useQuery } from '@tanstack/react-query'

import { api } from '../../api/client'
import { queryKeys } from '../../api/keys'
import type { ParamSchema } from './types'

/**
 * 取某个工作流的参数 Schema。
 *
 * 这个 hook 的唯一职责是**把嵌套层解开**：后端返回的是
 * `WorkflowDetailOut`（含 id/name/version/... 以及 `param_schema`），
 * 而表单只关心 `param_schema` 本身。
 *
 * 解层必须只有这一处 —— 散开就会有人写 `data.fields` 然后拿到 `undefined`
 * （契约 §2.4 特意标注了这个嵌套）。
 */
export function useWorkflowSchema(workflowName: string | null | undefined) {
  const name = workflowName ?? ''

  return useQuery({
    queryKey: queryKeys.workflowSchema(name),
    enabled: name !== '',
    queryFn: async (): Promise<ParamSchema> => {
      const detail = await api.workflows.schema(name)
      return detail.param_schema
    },
    // schema 变更需要重启后端（注册表要重新灌库），5 分钟足够新鲜
    staleTime: 5 * 60 * 1000,
  })
}
