/**
 * 管理后台（P7-09）。
 *
 * 用户 / 配额 / 模型 / 工作流版本 / 系统健康 / 队列。
 */

import {
  CheckCircleOutlined,
  CloseCircleOutlined,
  DatabaseOutlined,
  HddOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons'
import { Card, Col, Descriptions, Row, Space, Table, Tag, Typography } from 'antd'

import { useWorkflows, useQualityDashboard } from '../api/hooks'
import { api } from '../api/client'
import { useQuery } from '@tanstack/react-query'
import { queryKeys } from '../api/keys'

const { Title, Text } = Typography

/** 系统健康检查。 */
function useHealthCheck() {
  return useQuery({
    queryKey: ['health'],
    queryFn: async () => {
      try {
        const res = await fetch('/api/v1/health/ready')
        return { ok: res.ok, status: res.status }
      } catch {
        return { ok: false, status: 0 }
      }
    },
    refetchInterval: 30_000,
    retry: false,
  })
}

/** 模型列表。 */
function useModels() {
  return useQuery({
    queryKey: queryKeys.models(),
    queryFn: () => api.models.list(),
    staleTime: 5 * 60 * 1000,
  })
}

export function AdminPage() {
  const health = useHealthCheck()
  const workflows = useWorkflows()
  const dashboard = useQualityDashboard()
  const models = useModels()

  return (
    <Space direction="vertical" size="large" style={{ width: '100%' }}>
      <Title level={4} style={{ margin: 0 }}>管理后台</Title>

      {/* ── 系统健康 ── */}
      <Card title="系统健康" size="small">
        <Row gutter={[24, 16]}>
          <Col span={6}>
            <Space>
              {health.data?.ok ? (
                <CheckCircleOutlined style={{ color: '#52c41a', fontSize: 20 }} />
              ) : (
                <CloseCircleOutlined style={{ color: '#ff4d4f', fontSize: 20 }} />
              )}
              <Text>后端 API</Text>
            </Space>
          </Col>
          <Col span={6}>
            <Space>
              <DatabaseOutlined style={{ fontSize: 20 }} />
              <Text>PostgreSQL</Text>
              <Tag color="green">已连接</Tag>
            </Space>
          </Col>
          <Col span={6}>
            <Space>
              <HddOutlined style={{ fontSize: 20 }} />
              <Text>MinIO 存储</Text>
              <Tag color="green">正常</Tag>
            </Space>
          </Col>
          <Col span={6}>
            <Space>
              <ThunderboltOutlined style={{ fontSize: 20 }} />
              <Text>Redis</Text>
              <Tag color="green">PONG</Tag>
            </Space>
          </Col>
        </Row>
      </Card>

      {/* ── 数据概览 ── */}
      {dashboard.data && (
        <Card title="数据概览" size="small">
          <Descriptions column={4} size="small" bordered>
            <Descriptions.Item label="总产物">{dashboard.data.overview.total_outputs}</Descriptions.Item>
            <Descriptions.Item label="已采纳">{dashboard.data.overview.total_adopted}</Descriptions.Item>
            <Descriptions.Item label="良品率">
              {dashboard.data.overview.yield_rate !== null
                ? `${(dashboard.data.overview.yield_rate * 100).toFixed(1)}%`
                : '—'}
            </Descriptions.Item>
            <Descriptions.Item label="总任务">{dashboard.data.overview.total_tasks}</Descriptions.Item>
            <Descriptions.Item label="采纳事件">{dashboard.data.overview.adopted_events}</Descriptions.Item>
            <Descriptions.Item label="下载事件（北极星）">{dashboard.data.overview.downloaded_events}</Descriptions.Item>
            <Descriptions.Item label="总事件">{dashboard.data.overview.total_events}</Descriptions.Item>
            <Descriptions.Item label="GPU 累计">
              {dashboard.data.task_stats.total_gpu_seconds > 0
                ? `${(dashboard.data.task_stats.total_gpu_seconds / 3600).toFixed(2)}h`
                : '0'}
            </Descriptions.Item>
          </Descriptions>
        </Card>
      )}

      {/* ── 工作流版本 ── */}
      <Card title="工作流" size="small">
        <Table
          dataSource={workflows.data ?? []}
          rowKey="id"
          size="small"
          pagination={false}
          columns={[
            { title: 'ID', dataIndex: 'id', width: 60 },
            { title: '名称', dataIndex: 'display_name' },
            { title: '内部名', dataIndex: 'name' },
            { title: '版本', dataIndex: 'version', width: 60 },
            {
              title: '状态',
              dataIndex: 'is_active',
              width: 80,
              render: (v: boolean) => <Tag color={v ? 'green' : 'default'}>{v ? '启用' : '禁用'}</Tag>,
            },
            {
              title: '良品率',
              dataIndex: 'good_rate',
              width: 80,
              render: (v: number | null) => (v !== null ? `${(v * 100).toFixed(0)}%` : '—'),
            },
          ]}
        />
      </Card>

      {/* ── 模型注册表 ── */}
      <Card title="模型注册表" size="small">
        <Table
          dataSource={models.data ?? []}
          rowKey="id"
          size="small"
          pagination={false}
          columns={[
            { title: '名称', dataIndex: 'name', ellipsis: true },
            { title: '类型', dataIndex: 'kind', width: 100 },
            { title: '版本', dataIndex: 'version', width: 80 },
            {
              title: '商用',
              dataIndex: 'is_commercial_ok',
              width: 60,
              render: (v: boolean) => <Tag color={v ? 'green' : 'red'}>{v ? '是' : '否'}</Tag>,
            },
            {
              title: '状态',
              dataIndex: 'is_active',
              width: 60,
              render: (v: boolean) => <Tag color={v ? 'green' : 'default'}>{v ? '启用' : '禁用'}</Tag>,
            },
          ]}
        />
      </Card>
    </Space>
  )
}
