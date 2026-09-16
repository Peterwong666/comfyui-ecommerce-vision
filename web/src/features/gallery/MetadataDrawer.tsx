import { CopyOutlined, ReloadOutlined } from '@ant-design/icons'
import { Button, Descriptions, Drawer, Space, Tag, Typography, message } from 'antd'
import { useNavigate } from 'react-router-dom'

import { ASSET_KIND } from '../../api/enums'
import type { AssetOut } from '../../api/types'

/**
 * 元数据抽屉（FR-5.4 · FR-5.5）。
 *
 * 这是**可复现性（C1）唯一的用户侧入口** —— 商用场景里客户追单说"再做一张上次那样的"，
 * 用户要能从这里拿到 seed / 参数并原样再跑一遍。
 *
 * ## 显示什么：只显示 `meta` 里**真实存在**的键
 *
 * 后端 `Asset.meta` 的键是固定的一组（见 `worker/tasks.py`）：
 * `seed` / `workflow` / `engine_prompt_id` / `params` / `filename` / `node_id` / `sha256`。
 *
 * ⚠️ 线框图 §6 里画了「模型 sd_xl_base_1.0 (sha256:a1b2..)」，但**后端没有**把模型
 * 写进 `meta`（模型在模型注册表与工作流定义里）。这里**不编造**这个字段 ——
 * 编一个空值或从参数里瞎猜，会让"可复现"这件事在关键时刻失真。
 * `params` 是**全量键值列表**，工作流将来若引入模型类参数，会自动显示出来。
 *
 * ## 「同参数再生成」做到了哪一步（重要，别当成已完成）
 *
 * 工作台（P7-02 的表单引擎）目前**没有接收外部预填参数的入口**，
 * 跨页传参要把工作台的 `WorkflowForm` 初值通道打通，那会动到 P7-02 的作业面。
 * 所以这里实现的是**「复制参数 + 跳转工作台」**：
 *   复制一份 JSON 参数 → 跳转工作台 → 用户自行粘贴 / 对照填写。
 * 这是**半个 FR-5.5**，完整版需要 P7-02 增加"外部初值"通道，已记入报告。
 */

/** 后端 `meta.params` 里的值可能是任意 JSON；统一转成可读文本，**不猜类型**。 */
function formatValue(value: unknown): string {
  if (value === null || value === undefined) return '—'
  if (typeof value === 'string') return value
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  return JSON.stringify(value)
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(2)} MB`
}

/** `meta.params` 是一个对象；取不到就当空，不要假设它总有值（上传素材就是 `{}`）。 */
function readParams(meta: Record<string, unknown>): Record<string, unknown> {
  const params = meta.params
  return typeof params === 'object' && params !== null && !Array.isArray(params)
    ? (params as Record<string, unknown>)
    : {}
}

/** 参数里没有分组的键（如 `sha256`）也一并展示，避免"漏掉某个键"这类静默缺口。 */
const META_SCALAR_KEYS = ['sha256', 'engine_prompt_id', 'filename', 'node_id'] as const

export function MetadataDrawer({
  asset,
  open,
  onClose,
}: {
  asset: AssetOut | null
  open: boolean
  onClose: () => void
}) {
  const navigate = useNavigate()

  if (!asset) return null

  const meta = asset.meta ?? {}
  const params = readParams(meta)
  const paramEntries = Object.entries(params)

  const copyParams = async () => {
    const text = JSON.stringify(params, null, 2)
    try {
      await navigator.clipboard.writeText(text)
      void message.success('参数已复制到剪贴板')
    } catch {
      // 非 HTTPS / 无剪贴板权限时会失败。不做静默处理，直接把内容给用户手动复制。
      void message.warning('浏览器拒绝了剪贴板访问，请从下方参数列表手动复制')
    }
  }

  const regenerate = async () => {
    // ⚠️ 见文件头："同参数再生成"当前只做到「复制参数 + 跳转工作台」。
    await copyParams()
    void message.info('已复制参数。工作台暂不支持自动回填，请粘贴到对应字段后重新生成。')
    navigate('/')
  }

  return (
    <Drawer
      title={`素材 #${asset.id} 元数据`}
      width={520}
      open={open}
      onClose={onClose}
      destroyOnClose
    >
      <Space direction="vertical" size="middle" style={{ width: '100%' }}>
        <Space wrap>
          <Tag color={asset.kind === ASSET_KIND.OUTPUT ? 'blue' : 'default'}>
            {asset.kind === ASSET_KIND.OUTPUT ? '生成产物' : '上传素材'}
          </Tag>
          {asset.is_adopted && <Tag color="gold">★ 已采纳</Tag>}
          {asset.is_favorite && <Tag color="red">已收藏</Tag>}
        </Space>

        <Descriptions size="small" column={1} bordered title="可复现性">
          <Descriptions.Item label="工作流">
            {formatValue(meta.workflow)}
            {/* 模型字段后端未提供：明说，而不是留空让人以为加载失败 */}
            <Typography.Text type="secondary" style={{ marginLeft: 8, fontSize: 12 }}>
              （后端 meta 未记录模型名）
            </Typography.Text>
          </Descriptions.Item>
          <Descriptions.Item label="seed">{formatValue(meta.seed)}</Descriptions.Item>
          {META_SCALAR_KEYS.map((key) => (
            <Descriptions.Item key={key} label={key}>
              {formatValue(meta[key])}
            </Descriptions.Item>
          ))}
        </Descriptions>

        <Descriptions size="small" column={1} bordered title="基本信息">
          <Descriptions.Item label="任务">
            {asset.task_id === null ? '—（上传素材没有任务）' : `#${asset.task_id}`}
          </Descriptions.Item>
          <Descriptions.Item label="尺寸">
            {asset.width && asset.height ? `${asset.width} × ${asset.height}` : '—'}
          </Descriptions.Item>
          <Descriptions.Item label="体积">{formatBytes(asset.size_bytes)}</Descriptions.Item>
          <Descriptions.Item label="类型">{asset.mime_type}</Descriptions.Item>
          <Descriptions.Item label="创建时间">
            {new Date(asset.created_at).toLocaleString()}
          </Descriptions.Item>
          <Descriptions.Item label="原始文件名">{asset.original_name ?? '—'}</Descriptions.Item>
        </Descriptions>

        <div>
          <Typography.Title level={5} style={{ marginTop: 0 }}>
            生效参数（{paramEntries.length} 项）
          </Typography.Title>
          {paramEntries.length === 0 ? (
            <Typography.Text type="secondary">
              该素材没有参数元数据（上传素材没有可复现性元数据）。
            </Typography.Text>
          ) : (
            <Descriptions size="small" column={1} bordered>
              {paramEntries.map(([key, value]) => (
                <Descriptions.Item key={key} label={key}>
                  <Typography.Text style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>
                    {formatValue(value)}
                  </Typography.Text>
                </Descriptions.Item>
              ))}
            </Descriptions>
          )}
        </div>

        <Space>
          <Button icon={<CopyOutlined />} onClick={() => void copyParams()} disabled={!paramEntries.length}>
            复制参数
          </Button>
          <Button
            type="primary"
            icon={<ReloadOutlined />}
            onClick={() => void regenerate()}
            disabled={!paramEntries.length}
          >
            同参数再生成
          </Button>
        </Space>
      </Space>
    </Drawer>
  )
}
