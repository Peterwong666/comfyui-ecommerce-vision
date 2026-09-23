/**
 * 任务中心（P7-04）。
 *
 * 队列与实时进度、失败原因、一键重试。
 * 数据源：`GET /api/v1/tasks` + `GET /api/v1/tasks/{id}`。
 */

import {
  ReloadOutlined,
} from '@ant-design/icons'
import {
  Button,
  Card,
  Descriptions,
  Empty,
  Pagination,
  Space,
  Table,
  Tag,
  Tooltip,
  Typography,
  message,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useState } from 'react'

import { useTasks, useTask } from '../api/hooks'
import type { TaskOut } from '../api/types'
import { TASK_STATUS } from '../api/enums'
import { api } from '../api/client'
import { useQueryClient } from '@tanstack/react-query'
import { queryKeys } from '../api/keys'

const { Text, Title } = Typography

/** 状态标签颜色。 */
function statusColor(s: string): string {
  if (s === 'succeeded') return 'green'
  if (s === 'failed') return 'red'
  if (s === 'canceled') return 'default'
  if (s === 'running') return 'blue'
  if (s === 'retrying') return 'orange'
  return 'processing'
}

/** 状态中文名。 */
function statusName(s: string): string {
  const map: Record<string, string> = {
    pending: '等待中',
    queued: '排队中',
    running: '生成中',
    retrying: '重试中',
    succeeded: '已完成',
    failed: '失败',
    canceled: '已取消',
  }
  return map[s] ?? s
}

/** 格式化耗时。 */
function fmtDuration(ms: number | null): string {
  if (ms === null) return '—'
  if (ms < 1000) return `${Math.round(ms)}ms`
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`
  return `${(ms / 60000).toFixed(1)}min`
}

export function TaskCenterPage() {
  const queryClient = useQueryClient()
  const [page, setPage] = useState(1)
  const [statusFilter, setStatusFilter] = useState<string | undefined>()
  const [selectedTask, setSelectedTask] = useState<number | null>(null)

  const pageSize = 20
  const tasks = useTasks({
    status: statusFilter as any,
    limit: pageSize,
    offset: (page - 1) * pageSize,
  })
  const taskDetail = useTask(selectedTask)

  const handleRetry = async (taskId: number) => {
    try {
      await api.tasks.retry(taskId)
      message.success('重试已提交')
      void queryClient.invalidateQueries({ queryKey: queryKeys.tasks() })
    } catch {
      message.error('重试失败')
    }
  }

  const columns: ColumnsType<TaskOut> = [
    {
      title: 'ID',
      dataIndex: 'id',
      key: 'id',
      width: 80,
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 100,
      render: (s: string) => <Tag color={statusColor(s)}>{statusName(s)}</Tag>,
    },
    {
      title: '工作流',
      dataIndex: 'workflow_id',
      key: 'workflow_id',
      width: 100,
    },
    {
      title: 'Seed',
      dataIndex: 'seed',
      key: 'seed',
      width: 100,
      render: (s: number | null) => s ?? '—',
    },
    {
      title: '耗时',
      dataIndex: 'duration_ms',
      key: 'duration_ms',
      width: 100,
      render: (ms: number | null) => fmtDuration(ms),
    },
    {
      title: 'GPU',
      dataIndex: 'gpu_seconds',
      key: 'gpu_seconds',
      width: 80,
      render: (s: number | null) => (s !== null ? `${s.toFixed(1)}s` : '—'),
    },
    {
      title: '重试',
      dataIndex: 'retry_count',
      key: 'retry_count',
      width: 60,
    },
    {
      title: '错误',
      dataIndex: 'error_type',
      key: 'error_type',
      width: 120,
      render: (e: string | null) =>
        e ? <Tooltip title={taskDetail.data?.error_message}><Tag color="red">{e}</Tag></Tooltip> : '—',
    },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 160,
      render: (t: string) => new Date(t).toLocaleString(),
    },
    {
      title: '操作',
      key: 'actions',
      width: 100,
      render: (_: unknown, record: TaskOut) => (
        <Space>
          {record.can_retry && (
            <Button
              size="small"
              type="link"
              icon={<ReloadOutlined />}
              onClick={() => handleRetry(record.id)}
            >
              重试
            </Button>
          )}
        </Space>
      ),
    },
  ]

  // 统计
  const data = tasks.data ?? []

  return (
    <Space direction="vertical" size="large" style={{ width: '100%' }}>
      <Title level={4} style={{ margin: 0 }}>任务中心</Title>

      {/* ── 状态筛选 ── */}
      <Space wrap>
        <Button
          type={statusFilter === undefined ? 'primary' : 'default'}
          onClick={() => { setStatusFilter(undefined); setPage(1) }}
        >
          全部
        </Button>
        {Object.values(TASK_STATUS).map((s) => (
          <Button
            key={s}
            type={statusFilter === s ? 'primary' : 'default'}
            onClick={() => { setStatusFilter(s); setPage(1) }}
          >
            {statusName(s)}
          </Button>
        ))}
      </Space>

      {/* ── 任务表格 ── */}
      <Card size="small">
        <Table
          dataSource={data}
          columns={columns}
          rowKey="id"
          loading={tasks.isLoading}
          pagination={false}
          size="small"
          onRow={(record) => ({
            onClick: () => setSelectedTask(record.id),
            style: { cursor: 'pointer' },
          })}
          locale={{ emptyText: <Empty description="暂无任务" /> }}
        />
        <div style={{ marginTop: 16, textAlign: 'right' }}>
          <Pagination
            current={page}
            pageSize={pageSize}
            total={tasks.data?.length === pageSize ? page * pageSize + 1 : (page - 1) * pageSize + data.length}
            onChange={setPage}
            showSizeChanger={false}
            size="small"
          />
        </div>
      </Card>

      {/* ── 任务详情 ── */}
      {selectedTask && taskDetail.data && (
        <Card
          title={`任务 #${selectedTask} 详情`}
          size="small"
          extra={
            <Button size="small" onClick={() => setSelectedTask(null)}>
              关闭
            </Button>
          }
        >
          <Descriptions column={3} size="small" bordered>
            <Descriptions.Item label="状态">
              <Tag color={statusColor(taskDetail.data.status)}>
                {statusName(taskDetail.data.status)}
              </Tag>
            </Descriptions.Item>
            <Descriptions.Item label="工作流">{taskDetail.data.workflow_id}</Descriptions.Item>
            <Descriptions.Item label="Seed">{taskDetail.data.seed ?? '—'}</Descriptions.Item>
            <Descriptions.Item label="创建时间">
              {new Date(taskDetail.data.created_at).toLocaleString()}
            </Descriptions.Item>
            <Descriptions.Item label="开始时间">
              {taskDetail.data.started_at ? new Date(taskDetail.data.started_at).toLocaleString() : '—'}
            </Descriptions.Item>
            <Descriptions.Item label="完成时间">
              {taskDetail.data.finished_at ? new Date(taskDetail.data.finished_at).toLocaleString() : '—'}
            </Descriptions.Item>
            <Descriptions.Item label="耗时">{fmtDuration(taskDetail.data.duration_ms)}</Descriptions.Item>
            <Descriptions.Item label="GPU 耗时">
              {taskDetail.data.gpu_seconds !== null ? `${taskDetail.data.gpu_seconds.toFixed(1)}s` : '—'}
            </Descriptions.Item>
            <Descriptions.Item label="排队等待">
              {taskDetail.data.wait_seconds !== null ? `${taskDetail.data.wait_seconds.toFixed(1)}s` : '—'}
            </Descriptions.Item>
            {taskDetail.data.error_type && (
              <Descriptions.Item label="错误类型" span={3}>
                <Tag color="red">{taskDetail.data.error_type}</Tag>
                <Text type="secondary">{taskDetail.data.error_message}</Text>
              </Descriptions.Item>
            )}
          </Descriptions>
        </Card>
      )}
    </Space>
  )
}
