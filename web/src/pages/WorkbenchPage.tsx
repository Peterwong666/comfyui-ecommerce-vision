import { Alert, Button, Card, Descriptions, Select, Space, Spin, Typography, message } from 'antd'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'

import { api } from '../api/client'
import { TASK_STATUS_CANCELLABLE } from '../api/enums'
import { describeError } from '../api/errors'
import { useTask, useWorkflows } from '../api/hooks'
import { queryKeys } from '../api/keys'
import { TaskStatusBadge } from '../components/StatusBadge'
import { WorkflowForm } from '../features/workflow-form/WorkflowForm'
import type { TaskSubmitIn } from '../api/types'
import { useWorkflowSchema } from '../features/workflow-form/useWorkflowSchema'

/**
 * 工作台 —— **P2 的 DoD「前端与后端互相调通一次」就落在这一页**。
 *
 * 它串起完整的一条链路：
 * 取工作流列表 → 取参数 Schema → 动态渲染表单 → 提交任务 → 轮询状态 → 展示徽章。
 *
 * ⚠️ 本地环境下的**能力边界**（页面里如实告知，不装作都能用）：
 * - **没有 Redis** → 任务不会被执行，状态**停在 `queued`**，永远到不了 `succeeded`。
 *   能验证的是"请求被接受且落库"，不是"出图成功"。
 * - **没有 MinIO** → 图片类字段的上传会失败（501/500）。
 * - **没有 ComfyUI / GPU** → 更不可能出图。
 */
export function WorkbenchPage() {
  const [workflowName, setWorkflowName] = useState<string | null>(null)
  const [taskId, setTaskId] = useState<number | null>(null)
  const queryClient = useQueryClient()

  const workflows = useWorkflows()
  const schema = useWorkflowSchema(workflowName)
  const task = useTask(taskId)

  const submit = useMutation({
    mutationFn: (params: Record<string, unknown>) => {
      const body: TaskSubmitIn = {
        workflow_name: workflowName as string,
        // 模板功能（P3-10）未接入，显式传 null 而不是省略 ——
        // 让"确实没有模板"与"忘了传"在日志里可区分
        template_id: null,
        params,
        // ⚠️ `seed` 走 `params.seed` 这一条通道，不用顶层别名：
        // 两条通道传同一件事迟早分叉（契约 §5 #5/#7 记录过同类事故）
      }
      return api.tasks.submit(body)
    },
    onSuccess: (created) => {
      setTaskId(created.id)
      void queryClient.invalidateQueries({ queryKey: queryKeys.tasks() })
      // 提交后的状态是 queued 而不是 pending（契约 §1.4）
      void message.success(`已提交，任务 #${created.id}（状态：${created.status}）`)
    },
    onError: (err) => {
      void message.error(describeError(err))
    },
  })

  const cancel = useMutation({
    mutationFn: (id: number) => api.tasks.cancel(id),
    onSuccess: (res) => {
      void message.info(res.message)
      void queryClient.invalidateQueries({ queryKey: queryKeys.task(res.task_id) })
    },
    onError: (err) => void message.error(describeError(err)),
  })

  const retry = useMutation({
    mutationFn: (id: number) => api.tasks.retry(id),
    onSuccess: (next) => {
      setTaskId(next.id)
      void message.info('已重新提交')
    },
    onError: (err) => void message.error(describeError(err)),
  })

  const quotaError = submit.error !== null && (submit.error as { status?: number }).status === 402

  return (
    <Space direction="vertical" size="middle" style={{ width: '100%' }}>
      <Card title="工作台 · 单次生成">
        <Space direction="vertical" size="middle" style={{ width: '100%' }}>
          <Space>
            <Typography.Text strong>工作流</Typography.Text>
            <Select
              style={{ width: 280 }}
              placeholder="选择一条工作流"
              loading={workflows.isLoading}
              value={workflowName ?? undefined}
              onChange={(value) => {
                setWorkflowName(value)
                setTaskId(null)
              }}
              options={(workflows.data ?? []).map((w) => ({
                value: w.name,
                // 用 display_name 而非 name：用户看的是「文生图（极速）」而不是 flux2_klein_t2i_v1
                label: `${w.display_name} · v${w.version}`,
              }))}
            />
          </Space>

          {workflows.isError && (
            <Alert type="error" showIcon message={describeError(workflows.error)} />
          )}

          {workflowName === null && (
            <Alert
              type="info"
              showIcon
              message="请选择工作流"
              description="表单会依据该工作流的参数 Schema 自动生成 —— 新增工作流不需要改前端代码（P7-02）。"
            />
          )}

          {quotaError && (
            <Alert
              type="warning"
              showIcon
              message="额度不足"
              // 后端对额度不足返回的是**纯字符串** detail，所以这里只按状态码判断（402）
              description={describeError(submit.error)}
            />
          )}

          {schema.isLoading && <Spin />}
          {schema.isError && <Alert type="error" showIcon message={describeError(schema.error)} />}

          {schema.data && workflowName && (
            <WorkflowForm
              key={workflowName}
              workflowName={workflowName}
              schema={schema.data}
              submitting={submit.isPending}
              onSubmit={(params) => submit.mutate(params)}
            />
          )}
        </Space>
      </Card>

      {taskId !== null && (
        <Card title={`任务 #${taskId}`}>
          {task.isLoading && <Spin />}
          {task.isError && <Alert type="error" showIcon message={describeError(task.error)} />}

          {task.data && (
            <Space direction="vertical" size="middle" style={{ width: '100%' }}>
              <Space>
                <TaskStatusBadge status={task.data.status} />
                <Typography.Text type="secondary">
                  轮询中（后端尚无 WebSocket 推送，P6-04 未做）
                </Typography.Text>
              </Space>

              {task.data.status === 'queued' && (
                <Alert
                  type="info"
                  showIcon
                  message="任务已入队"
                  description="本地开发环境没有 Redis 与 ComfyUI，因此任务不会被消费，状态会停在「排队中」。这验证的是「请求被接受并落库」，不等于「已出图」。"
                />
              )}

              {task.data.error_message && (
                <Alert
                  type="error"
                  showIcon
                  message={`失败原因：${task.data.error_type ?? '未知'}`}
                  description={task.data.error_message}
                />
              )}

              <Descriptions size="small" column={2} bordered>
                <Descriptions.Item label="重试次数">{task.data.retry_count}</Descriptions.Item>
                <Descriptions.Item label="种子">
                  {/* -1 表示随机；真实值由服务端确定后回写 */}
                  {task.data.seed ?? '—'}
                </Descriptions.Item>
                <Descriptions.Item label="耗时(ms)">
                  {task.data.duration_ms ?? '—'}
                </Descriptions.Item>
                <Descriptions.Item label="GPU 秒">{task.data.gpu_seconds ?? '—'}</Descriptions.Item>
              </Descriptions>

              <Space>
                {/* 可取消的判据取自 enums.ts（由 parity 测试守），不在这里硬编码状态 */}
                {(TASK_STATUS_CANCELLABLE as readonly string[]).includes(task.data.status) && (
                  <Button onClick={() => cancel.mutate(taskId)} loading={cancel.isPending}>
                    取消
                  </Button>
                )}
                {/* ⚠️ 重试按钮必须看 `can_retry` 字段，不要自己按状态猜：
                    它的判据是 status ∈ {failed, canceled}，与"可自动重试"是两套 */}
                {task.data.can_retry && (
                  <Button onClick={() => retry.mutate(taskId)} loading={retry.isPending}>
                    重试
                  </Button>
                )}
              </Space>
            </Space>
          )}
        </Card>
      )}
    </Space>
  )
}
