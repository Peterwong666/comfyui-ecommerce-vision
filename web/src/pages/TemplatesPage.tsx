/**
 * 模板库页面（P7-08）。
 *
 * 浏览、搜索、筛选模板，一键套用到工作台。
 * 数据源：`GET /api/v1/templates` + `GET /api/v1/templates/categories`。
 */

import { AppstoreOutlined, SearchOutlined } from '@ant-design/icons'
import { Card, Col, Empty, Input, Row, Select, Space, Spin, Tag, Typography, message } from 'antd'
import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'

import { useTemplates, useTemplateCategories, useWorkflows } from '../api/hooks'
import type { TemplateOut } from '../api/types'

const { Title, Text, Paragraph } = Typography

/** 模板卡片。 */
function TemplateCard({
  template,
  workflowName,
  onApply,
}: {
  template: TemplateOut
  workflowName: string | undefined
  onApply: (t: TemplateOut) => void
}) {
  return (
    <Card
      hoverable
      onClick={() => onApply(template)}
      style={{ height: '100%' }}
      actions={[<span key="apply">一键套用</span>]}
    >
      <Card.Meta
        title={
          <Space>
            <Text strong>{template.name}</Text>
            <Tag color="blue">{template.category}</Tag>
          </Space>
        }
        description={
          <Space direction="vertical" size="small" style={{ width: '100%' }}>
            <Paragraph type="secondary" ellipsis={{ rows: 2 }} style={{ margin: 0 }}>
              {template.description || '暂无描述'}
            </Paragraph>
            <Text type="secondary" style={{ fontSize: 12 }}>
              工作流：{workflowName ?? `#${template.workflow_id}`}
            </Text>
            {template.usage_count > 0 && (
              <Text type="secondary" style={{ fontSize: 12 }}>
                使用 {template.usage_count} 次
              </Text>
            )}
          </Space>
        }
      />
    </Card>
  )
}

export function TemplatesPage() {
  const navigate = useNavigate()
  const templates = useTemplates()
  const categories = useTemplateCategories()
  const workflows = useWorkflows()

  const [search, setSearch] = useState('')
  const [selectedCategory, setSelectedCategory] = useState<string | undefined>()

  // 工作流 ID → 名称映射
  const wfMap = useMemo(() => {
    const m = new Map<number, string>()
    for (const w of workflows.data ?? []) {
      m.set(w.id, w.display_name)
    }
    return m
  }, [workflows.data])

  // 筛选
  const filtered = useMemo(() => {
    let list = templates.data ?? []
    if (selectedCategory) {
      list = list.filter((t) => t.category === selectedCategory)
    }
    if (search.trim()) {
      const q = search.trim().toLowerCase()
      list = list.filter(
        (t) =>
          t.name.toLowerCase().includes(q) ||
          (t.description ?? '').toLowerCase().includes(q)
      )
    }
    return list
  }, [templates.data, selectedCategory, search])

  /** 一键套用：跳转工作台并预填模板参数。 */
  const handleApply = (t: TemplateOut) => {
    // 跳转到工作台，通过 state 传递模板信息
    navigate('/', { state: { templateId: t.id, workflowId: t.workflow_id } })
    message.success(`已套用模板「${t.name}」，请在工作台调整参数后提交`)
  }

  if (templates.isLoading) {
    return (
      <div style={{ textAlign: 'center', padding: 80 }}>
        <Spin size="large" tip="加载模板…" />
      </div>
    )
  }

  if (templates.isError) {
    return (
      <div style={{ textAlign: 'center', padding: 80 }}>
        <Text type="danger">加载失败，请检查后端连接。</Text>
      </div>
    )
  }

  return (
    <Space direction="vertical" size="large" style={{ width: '100%' }}>
      <Title level={4} style={{ margin: 0 }}>
        <AppstoreOutlined /> 模板库
      </Title>

      {/* ── 筛选栏 ── */}
      <Space wrap>
        <Input
          placeholder="搜索模板名称或描述"
          prefix={<SearchOutlined />}
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          style={{ width: 280 }}
          allowClear
        />
        <Select
          placeholder="全部品类"
          value={selectedCategory}
          onChange={setSelectedCategory}
          allowClear
          style={{ width: 160 }}
          options={(categories.data ?? []).map((c) => ({ value: c, label: c }))}
        />
      </Space>

      {/* ── 模板网格 ── */}
      {filtered.length === 0 ? (
        <Empty description="暂无匹配的模板" />
      ) : (
        <Row gutter={[16, 16]}>
          {filtered.map((t) => (
            <Col span={8} key={t.id}>
              <TemplateCard
                template={t}
                workflowName={wfMap.get(t.workflow_id)}
                onApply={handleApply}
              />
            </Col>
          ))}
        </Row>
      )}
    </Space>
  )
}
