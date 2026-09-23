/**
 * 质量看板（P8-05 · FR-7.2）。
 *
 * 展示全局质量指标、按工作流维度下钻、任务统计、缺陷排行。
 * 数据源：`GET /api/v1/stats/quality`。
 */

import {
  CheckCircleOutlined,
  CloseCircleOutlined,
  ClockCircleOutlined,
  EyeOutlined,
  PictureOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons'
import { Card, Col, Row, Space, Spin, Statistic, Table, Tag, Typography } from 'antd'

import { useQualityDashboard } from '../api/hooks'
import type { DefectStats, WorkflowQuality } from '../api/types'

const { Title, Text } = Typography

/** 格式化毫秒为可读时间。 */
function fmtDuration(ms: number | null): string {
  if (ms === null) return '—'
  if (ms < 1000) return `${Math.round(ms)}ms`
  return `${(ms / 1000).toFixed(1)}s`
}

/** 格式化百分比。 */
function fmtPercent(rate: number | null): string {
  if (rate === null) return '—'
  return `${(rate * 100).toFixed(1)}%`
}

/** 良品率颜色。 */
function yieldColor(rate: number | null): string {
  if (rate === null) return '#999'
  if (rate >= 0.8) return '#52c41a'
  if (rate >= 0.5) return '#faad14'
  return '#ff4d4f'
}

/** 工作流质量表格列。 */
const workflowColumns = [
  {
    title: '工作流',
    dataIndex: 'display_name',
    key: 'display_name',
    render: (name: string, row: WorkflowQuality) => (
      <Space direction="vertical" size={0}>
        <Text strong>{name}</Text>
        <Text type="secondary" style={{ fontSize: 12 }}>{row.workflow_name}</Text>
      </Space>
    ),
  },
  {
    title: '产物数',
    dataIndex: 'total_outputs',
    key: 'total_outputs',
    align: 'right' as const,
  },
  {
    title: '已采纳',
    dataIndex: 'adopted_count',
    key: 'adopted_count',
    align: 'right' as const,
    render: (n: number) => <Tag color="blue">{n}</Tag>,
  },
  {
    title: '良品率',
    dataIndex: 'yield_rate',
    key: 'yield_rate',
    align: 'right' as const,
    render: (rate: number | null) => (
      <Text style={{ color: yieldColor(rate), fontWeight: 600 }}>{fmtPercent(rate)}</Text>
    ),
  },
  {
    title: '成功率',
    dataIndex: 'success_rate',
    key: 'success_rate',
    align: 'right' as const,
    render: (rate: number | null) => fmtPercent(rate),
  },
  {
    title: '平均耗时',
    dataIndex: 'avg_duration_ms',
    key: 'avg_duration_ms',
    align: 'right' as const,
    render: (ms: number | null) => fmtDuration(ms),
  },
  {
    title: '画质评分',
    dataIndex: 'eval_score',
    key: 'eval_score',
    align: 'right' as const,
    render: (score: number | null) => (score !== null ? score.toFixed(1) : '—'),
  },
]

/** 缺陷排行表格列。 */
const defectColumns = [
  {
    title: '缺陷类型',
    dataIndex: 'defect_type',
    key: 'defect_type',
  },
  {
    title: '命中次数',
    dataIndex: 'hit_count',
    key: 'hit_count',
    align: 'right' as const,
    sorter: (a: DefectStats, b: DefectStats) => a.hit_count - b.hit_count,
    defaultSortOrder: 'descend' as const,
  },
]

export function QualityDashboardPage() {
  const { data, isLoading, isError } = useQualityDashboard()

  if (isLoading) {
    return (
      <div style={{ textAlign: 'center', padding: 80 }}>
        <Spin size="large" tip="加载质量数据…" />
      </div>
    )
  }

  if (isError || !data) {
    return (
      <div style={{ textAlign: 'center', padding: 80 }}>
        <Text type="danger">加载失败，请检查后端连接。</Text>
      </div>
    )
  }

  const { overview, by_workflow, task_stats, top_defects } = data

  return (
    <Space direction="vertical" size="large" style={{ width: '100%' }}>
      <Title level={4} style={{ margin: 0 }}>质量看板</Title>

      {/* ── 总览卡片 ── */}
      <Row gutter={[16, 16]}>
        <Col span={6}>
          <Card size="small">
            <Statistic
              title="总产物"
              value={overview.total_outputs}
              prefix={<PictureOutlined />}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card size="small">
            <Statistic
              title="已采纳"
              value={overview.total_adopted}
              prefix={<CheckCircleOutlined style={{ color: '#52c41a' }} />}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card size="small">
            <Statistic
              title="良品率"
              value={overview.yield_rate !== null ? (overview.yield_rate * 100).toFixed(1) : '—'}
              suffix={overview.yield_rate !== null ? '%' : ''}
              valueStyle={{ color: yieldColor(overview.yield_rate) }}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card size="small">
            <Statistic
              title="北极星（下载）"
              value={overview.downloaded_events}
              prefix={<EyeOutlined style={{ color: '#1677ff' }} />}
            />
          </Card>
        </Col>
      </Row>

      {/* ── 任务统计 ── */}
      <Card title="任务统计" size="small">
        <Row gutter={[24, 16]}>
          <Col span={4}>
            <Statistic title="总计" value={task_stats.total} />
          </Col>
          <Col span={4}>
            <Statistic
              title="成功"
              value={task_stats.succeeded}
              valueStyle={{ color: '#52c41a' }}
              prefix={<CheckCircleOutlined />}
            />
          </Col>
          <Col span={4}>
            <Statistic
              title="失败"
              value={task_stats.failed}
              valueStyle={{ color: '#ff4d4f' }}
              prefix={<CloseCircleOutlined />}
            />
          </Col>
          <Col span={4}>
            <Statistic title="取消" value={task_stats.canceled} />
          </Col>
          <Col span={4}>
            <Statistic
              title="排队中"
              value={task_stats.queued + task_stats.running}
              prefix={<ClockCircleOutlined />}
            />
          </Col>
          <Col span={4}>
            <Statistic
              title="成功率"
              value={task_stats.success_rate !== null ? (task_stats.success_rate * 100).toFixed(1) : '—'}
              suffix={task_stats.success_rate !== null ? '%' : ''}
            />
          </Col>
        </Row>
        <Row gutter={[24, 16]} style={{ marginTop: 16 }}>
          <Col span={8}>
            <Statistic
              title="平均耗时"
              value={fmtDuration(task_stats.avg_duration_ms)}
            />
          </Col>
          <Col span={8}>
            <Statistic
              title="平均 GPU 耗时"
              value={task_stats.avg_gpu_seconds !== null ? `${task_stats.avg_gpu_seconds.toFixed(1)}s` : '—'}
              prefix={<ThunderboltOutlined />}
            />
          </Col>
          <Col span={8}>
            <Statistic
              title="累计 GPU 耗时"
              value={`${(task_stats.total_gpu_seconds / 3600).toFixed(2)}h`}
            />
          </Col>
        </Row>
      </Card>

      {/* ── 按工作流维度 ── */}
      <Card title="按工作流质量" size="small">
        <Table
          dataSource={by_workflow}
          columns={workflowColumns}
          rowKey="workflow_id"
          pagination={false}
          size="small"
        />
      </Card>

      {/* ── 缺陷排行 ── */}
      {top_defects.length > 0 && (
        <Card title="缺陷排行" size="small">
          <Table
            dataSource={top_defects}
            columns={defectColumns}
            rowKey="defect_type"
            pagination={false}
            size="small"
          />
        </Card>
      )}
    </Space>
  )
}
