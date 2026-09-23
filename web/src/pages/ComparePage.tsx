/**
 * A/B 对比工具（P8-07）。
 *
 * 同 seed 下对比不同模型 / 参数 / ControlNet 权重的差异。
 * 工作流：选择工作流 → 设置 seed → 添加变体 → 提交 → 查看结果。
 */

import { DeleteOutlined, ExperimentOutlined, PlusOutlined } from '@ant-design/icons'
import {
  Button,
  Card,
  Col,
  Empty,
  Form,
  Input,
  InputNumber,
  List,
  message,
  Row,
  Select,
  Space,
  Tag,
  Typography,
} from 'antd'
import { useState } from 'react'

import { useCompareGroups, useSubmitCompare, useWorkflows } from '../api/hooks'
import type { CompareGroup, CompareVariant } from '../api/types'

const { Text, Title } = Typography

/** 状态标签颜色。 */
function statusColor(s: string): string {
  if (s === 'succeeded') return 'green'
  if (s === 'failed') return 'red'
  if (s === 'running') return 'blue'
  if (s === 'queued' || s === 'pending') return 'orange'
  return 'default'
}

/** 创建空变体。 */
function emptyVariant(idx: number): CompareVariant {
  return { label: `变体 ${idx + 1}`, params: {} }
}

export function ComparePage() {
  const workflows = useWorkflows()
  const compareGroups = useCompareGroups()
  const submitCompare = useSubmitCompare()

  const [workflowName, setWorkflowName] = useState<string>('')
  const [seed, setSeed] = useState<number>(-1)
  const [variants, setVariants] = useState<CompareVariant[]>([emptyVariant(0), emptyVariant(1)])
  const [selectedGroup, setSelectedGroup] = useState<string | null>(null)

  const addVariant = () => {
    if (variants.length >= 6) {
      message.warning('最多 6 个变体')
      return
    }
    setVariants([...variants, emptyVariant(variants.length)])
  }

  const removeVariant = (idx: number) => {
    if (variants.length <= 2) {
      message.warning('至少需要 2 个变体')
      return
    }
    setVariants(variants.filter((_, i) => i !== idx))
  }

  const updateVariant = (idx: number, field: keyof CompareVariant, value: unknown) => {
    const next = [...variants]
    next[idx] = { ...next[idx], [field]: value }
    setVariants(next)
  }

  const handleSubmit = async () => {
    if (!workflowName) {
      message.warning('请选择工作流')
      return
    }
    try {
      const result = await submitCompare.mutateAsync({
        workflow_name: workflowName,
        seed,
        variants,
      })
      message.success(`对比组 ${result.id} 已创建`)
      setSelectedGroup(result.id)
    } catch {
      message.error('提交失败')
    }
  }

  const selectedGroupData = compareGroups.data?.find((g) => g.id === selectedGroup)

  return (
    <Space direction="vertical" size="large" style={{ width: '100%' }}>
      <Title level={4} style={{ margin: 0 }}>
        <ExperimentOutlined /> A/B 对比
      </Title>

      <Row gutter={24}>
        {/* ── 左侧：创建对比 ── */}
        <Col span={10}>
          <Card title="创建对比" size="small">
            <Form layout="vertical">
              <Form.Item label="工作流" required>
                <Select
                  placeholder="选择工作流"
                  value={workflowName || undefined}
                  onChange={setWorkflowName}
                  options={(workflows.data ?? []).map((w) => ({
                    value: w.name,
                    label: w.display_name,
                  }))}
                />
              </Form.Item>

              <Form.Item label="Seed（-1 = 随机）">
                <InputNumber
                  value={seed}
                  onChange={(v) => setSeed(v ?? -1)}
                  style={{ width: '100%' }}
                  min={-1}
                />
              </Form.Item>

              <Form.Item label="变体参数">
                <Space direction="vertical" style={{ width: '100%' }}>
                  {variants.map((v, idx) => (
                    <Card
                      key={idx}
                      size="small"
                      type="inner"
                      title={
                        <Space>
                          <Input
                            size="small"
                            value={v.label}
                            onChange={(e) => updateVariant(idx, 'label', e.target.value)}
                            style={{ width: 160 }}
                          />
                          <Button
                            size="small"
                            type="text"
                            danger
                            icon={<DeleteOutlined />}
                            onClick={() => removeVariant(idx)}
                            disabled={variants.length <= 2}
                          />
                        </Space>
                      }
                    >
                      <Input.TextArea
                        placeholder='JSON 参数，如 {"steps": 30, "cfg": 12}'
                        value={JSON.stringify(v.params, null, 0)}
                        onChange={(e) => {
                          try {
                            updateVariant(idx, 'params', JSON.parse(e.target.value))
                          } catch {
                            // ignore parse error while typing
                          }
                        }}
                        autoSize={{ minRows: 1, maxRows: 3 }}
                        style={{ fontFamily: 'monospace', fontSize: 12 }}
                      />
                    </Card>
                  ))}
                </Space>
                <Button
                  type="dashed"
                  onClick={addVariant}
                  icon={<PlusOutlined />}
                  style={{ marginTop: 8, width: '100%' }}
                  disabled={variants.length >= 6}
                >
                  添加变体
                </Button>
              </Form.Item>

              <Button
                type="primary"
                onClick={handleSubmit}
                loading={submitCompare.isPending}
                block
              >
                提交对比
              </Button>
            </Form>
          </Card>
        </Col>

        {/* ── 右侧：对比结果 ── */}
        <Col span={14}>
          {selectedGroupData ? (
            <CompareDetailView group={selectedGroupData} />
          ) : (
            <Card title="历史对比" size="small">
              {compareGroups.data && compareGroups.data.length > 0 ? (
                <List
                  dataSource={compareGroups.data}
                  renderItem={(group: CompareGroup) => (
                    <List.Item
                      onClick={() => setSelectedGroup(group.id)}
                      style={{ cursor: 'pointer' }}
                      actions={[
                        <Tag key="seed">seed={group.seed}</Tag>,
                        <Tag key="count">{group.variants.length} 变体</Tag>,
                      ]}
                    >
                      <List.Item.Meta
                        title={group.workflow_name}
                        description={`ID: ${group.id} · ${new Date(group.created_at).toLocaleString()}`}
                      />
                    </List.Item>
                  )}
                />
              ) : (
                <Empty description="暂无对比记录" />
              )}
            </Card>
          )}
        </Col>
      </Row>
    </Space>
  )
}

/** 对比详情视图。 */
function CompareDetailView({ group }: { group: CompareGroup }) {
  return (
    <Card
      title={`${group.workflow_name} · seed=${group.seed}`}
      size="small"
      extra={<Tag>{group.id}</Tag>}
    >
      <Row gutter={[16, 16]}>
        {group.variants.map((v, idx) => (
          <Col span={Math.min(24 / group.variants.length, 12)} key={idx}>
            <Card
              size="small"
              type="inner"
              title={v.label}
              extra={<Tag color={statusColor(v.status)}>{v.status}</Tag>}
            >
              <Space direction="vertical" size="small" style={{ width: '100%' }}>
                <Text type="secondary" copyable={{ text: JSON.stringify(v.params, null, 2) }}>
                  参数：{JSON.stringify(v.params)}
                </Text>
                {v.asset_id && (
                  <Tag color="green">产物 #{v.asset_id}</Tag>
                )}
                {v.duration_ms !== null && (
                  <Text type="secondary">耗时：{(v.duration_ms / 1000).toFixed(1)}s</Text>
                )}
                {v.gpu_seconds !== null && (
                  <Text type="secondary">GPU：{v.gpu_seconds.toFixed(1)}s</Text>
                )}
              </Space>
            </Card>
          </Col>
        ))}
      </Row>
    </Card>
  )
}
