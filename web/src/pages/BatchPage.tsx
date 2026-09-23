/**
 * 批量出图向导（P7-06）。
 *
 * 工作流选择 → SKU 素材绑定 → 参数矩阵预览 → 预估 → 提交。
 */

import { CloudUploadOutlined, SendOutlined } from '@ant-design/icons'
import {
  Alert,
  Button,
  Card,
  Col,
  Empty,
  Form,
  Input,
  InputNumber,
  Row,
  Select,
  Space,
  Table,
  Typography,
  message,
} from 'antd'
import { useState } from 'react'

import { useWorkflows, useAssets } from '../api/hooks'
import { api } from '../api/client'
import { ASSET_KIND } from '../api/enums'
import type { BatchOut, WorkflowOut } from '../api/types'

const { Title } = Typography

export function BatchPage() {
  const workflows = useWorkflows()
  const assets = useAssets({ kind: ASSET_KIND.UPLOAD, limit: 200 })

  const [workflowName, setWorkflowName] = useState<string>('')
  const [batchName, setBatchName] = useState('')
  const [imagesPerSku, setImagesPerSku] = useState(1)
  const [skuAssets, setSkuAssets] = useState<Record<string, number[]>>({})
  const [currentSku, setCurrentSku] = useState('')
  const [selectedAssetIds, setSelectedAssetIds] = useState<number[]>([])
  const [submitting, setSubmitting] = useState(false)

  const addSku = () => {
    if (!currentSku.trim()) {
      message.warning('请输入 SKU 名称')
      return
    }
    if (selectedAssetIds.length === 0) {
      message.warning('请至少选择一张素材')
      return
    }
    setSkuAssets((prev) => ({
      ...prev,
      [currentSku.trim()]: selectedAssetIds,
    }))
    setCurrentSku('')
    setSelectedAssetIds([])
    message.success(`SKU「${currentSku.trim()}」已添加（${selectedAssetIds.length} 张素材）`)
  }

  const removeSku = (sku: string) => {
    setSkuAssets((prev) => {
      const next = { ...prev }
      delete next[sku]
      return next
    })
  }

  const totalImages = Object.values(skuAssets).reduce(
    (sum, ids) => sum + ids.length * imagesPerSku,
    0
  )

  const handleSubmit = async () => {
    if (!workflowName) {
      message.warning('请选择工作流')
      return
    }
    if (Object.keys(skuAssets).length === 0) {
      message.warning('请至少添加一个 SKU')
      return
    }
    setSubmitting(true)
    try {
      const result: BatchOut = await api.batches.submit({
        workflow_name: workflowName,
        name: batchName || undefined,
        sku_assets: skuAssets,
        images_per_sku: imagesPerSku,
      })
      message.success(`批量任务已提交，ID: ${result.id}，共 ${result.total_count} 张`)
    } catch (err: any) {
      message.error(err?.message ?? '提交失败')
    } finally {
      setSubmitting(false)
    }
  }

  const assetItems = assets.data?.items ?? []

  return (
    <Space direction="vertical" size="large" style={{ width: '100%' }}>
      <Title level={4} style={{ margin: 0 }}>批量出图</Title>

      <Row gutter={24}>
        {/* ── 左侧：配置 ── */}
        <Col span={14}>
          <Card title="批量配置" size="small">
            <Form layout="vertical">
              <Form.Item label="工作流" required>
                <Select
                  placeholder="选择工作流"
                  value={workflowName || undefined}
                  onChange={setWorkflowName}
                  options={(workflows.data ?? []).map((w: WorkflowOut) => ({
                    value: w.name,
                    label: w.display_name,
                  }))}
                />
              </Form.Item>

              <Form.Item label="批量名称（可选）">
                <Input
                  value={batchName}
                  onChange={(e) => setBatchName(e.target.value)}
                  placeholder="如：2026Q4 陶瓷系列"
                />
              </Form.Item>

              <Form.Item label="每 SKU 出图数">
                <InputNumber
                  value={imagesPerSku}
                  onChange={(v) => setImagesPerSku(v ?? 1)}
                  min={1}
                  max={20}
                  style={{ width: 120 }}
                />
              </Form.Item>
            </Form>
          </Card>

          {/* ── SKU 素材绑定 ── */}
          <Card title="SKU 素材绑定" size="small" style={{ marginTop: 16 }}>
            <Space direction="vertical" style={{ width: '100%' }}>
              <Space>
                <Input
                  value={currentSku}
                  onChange={(e) => setCurrentSku(e.target.value)}
                  placeholder="SKU 名称（如 SKU-001）"
                  style={{ width: 200 }}
                />
                <Button onClick={addSku} icon={<CloudUploadOutlined />}>
                  添加
                </Button>
              </Space>

              {assetItems.length === 0 ? (
                <Empty description="暂无上传素材，请先在工作台上传" />
              ) : (
                <Table
                  dataSource={assetItems}
                  rowKey="id"
                  size="small"
                  pagination={{ pageSize: 5 }}
                  rowSelection={{
                    selectedRowKeys: selectedAssetIds,
                    onChange: (keys) => setSelectedAssetIds(keys as number[]),
                  }}
                  columns={[
                    { title: 'ID', dataIndex: 'id', width: 60 },
                    { title: '文件名', dataIndex: 'original_name', ellipsis: true },
                    { title: '尺寸', key: 'size', render: (_: any, r: any) => r.width && r.height ? `${r.width}×${r.height}` : '—' },
                  ]}
                />
              )}
            </Space>
          </Card>
        </Col>

        {/* ── 右侧：预览 ── */}
        <Col span={10}>
          <Card title="参数矩阵预览" size="small">
            {Object.keys(skuAssets).length === 0 ? (
              <Empty description="添加 SKU 后预览" />
            ) : (
              <Space direction="vertical" style={{ width: '100%' }}>
                <Alert
                  type="info"
                  message={`共 ${Object.keys(skuAssets).length} 个 SKU，预计生成 ${totalImages} 张`}
                />
                <Table
                  dataSource={Object.entries(skuAssets).map(([sku, ids]) => ({
                    sku,
                    assetCount: ids.length,
                    total: ids.length * imagesPerSku,
                  }))}
                  rowKey="sku"
                  size="small"
                  pagination={false}
                  columns={[
                    { title: 'SKU', dataIndex: 'sku' },
                    { title: '素材数', dataIndex: 'assetCount', width: 80 },
                    { title: '出图数', dataIndex: 'total', width: 80 },
                    {
                      title: '',
                      key: 'remove',
                      width: 60,
                      render: (_: any, r: any) => (
                        <Button size="small" type="text" danger onClick={() => removeSku(r.sku)}>
                          删除
                        </Button>
                      ),
                    },
                  ]}
                />
              </Space>
            )}
          </Card>

          <Button
            type="primary"
            icon={<SendOutlined />}
            onClick={handleSubmit}
            loading={submitting}
            disabled={totalImages === 0}
            block
            style={{ marginTop: 16 }}
          >
            提交批量任务（{totalImages} 张）
          </Button>
        </Col>
      </Row>
    </Space>
  )
}
