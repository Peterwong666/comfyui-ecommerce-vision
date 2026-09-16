import {
  DeleteOutlined,
  DownloadOutlined,
  EyeOutlined,
  FileZipOutlined,
  InfoCircleOutlined,
  StarFilled,
  StarOutlined,
} from '@ant-design/icons'
import {
  Alert,
  Button,
  Card,
  Checkbox,
  Empty,
  Modal,
  Pagination,
  Popconfirm,
  Select,
  Space,
  Spin,
  Tooltip,
  Typography,
  message,
} from 'antd'
import { useMutation } from '@tanstack/react-query'
import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'

import { api } from '../api/client'
import { ASSET_KIND } from '../api/enums'
import { describeError } from '../api/errors'
import { useAssets, useDeleteAssets, usePackAssets, useTasks, useUpdateAsset } from '../api/hooks'
import type { AssetListQuery, AssetOut } from '../api/types'
import { AssetImage } from '../features/gallery/AssetImage'
import { MetadataDrawer } from '../features/gallery/MetadataDrawer'

/**
 * 画廊与素材库（P7-05，线框图 §6）。
 *
 * 覆盖：FR-5.1 列表（缩略图 + 筛选 + 分页）/ FR-5.2 · FR-3.7 批量下载与打包 zip /
 * FR-5.3 收藏与软删除 / FR-5.4 元数据抽屉 / FR-5.5 同参数再生成 / FR-2.7 采纳标记。
 *
 * ## 三个必须在这里讲清楚的取舍
 *
 * ### 1. 图片为什么不是 `<img src="/api/v1/assets/1/content">`
 *
 * 鉴权走 `Authorization` **请求头**，而 `<img>` 请求不带自定义头 → 后端 401。
 * 本地开发时若恰好能匿名读到对象，这个写法**看起来是对的**，上线后全白且不报错。
 * 因此所有图都经 `AssetImage`：取 `Blob` → `createObjectURL` → `<img>`，卸载时释放。
 * 细节与内存泄漏的成因见 `features/gallery/AssetImage.tsx` 的模块注释。
 *
 * ### 2. 原图不是缩略图
 *
 * 后端**没有缩略图接口**，`/content` 返回原图（1–2MB 一张）。所以：
 * 视口懒加载（`IntersectionObserver`）+ 并发上限 6（`api/concurrency.ts`），
 * 而不是一页几十张一起拉。这是**前端侧的缓解**，真正的解法（后端出缩略图）
 * 属于 P6-10 的遗留项，已登记。
 *
 * ### 3. 「未采纳」筛选是**页内**筛选
 *
 * 后端 `/assets` 只有 `adopted_only`（正选），**没有**「未采纳」参数。
 * 做成客户端筛选就必然与分页打架（第 1 页全被过滤掉时会显示空，但后面几页有）。
 * 与其"看起来能筛"，不如把口径写清楚：界面在选中「未采纳」时显式提示这一点。
 * 正确的做法是后端加 `adopted_only=false` 语义或 `exclude_adopted` 参数 —— 已记入报告。
 */

/** 每页张数。24 = 6 列 × 4 行，正好铺满线框图 §6 的网格且不触发长滚动。 */
const PAGE_SIZE = 24

type Facet = 'all' | 'adopted' | 'not_adopted' | 'favorite'

const FACETS: ReadonlyArray<{ value: Facet; label: string }> = [
  { value: 'all', label: '全部' },
  { value: 'adopted', label: '已采纳' },
  { value: 'not_adopted', label: '未采纳' },
  { value: 'favorite', label: '已收藏' },
]

/** 时间筛选用**预设区间**而不是日期选择器：线框图 §6 画的是 `时间[全部▾]`（下拉）。 */
const TIME_RANGES: ReadonlyArray<{ value: number; label: string }> = [
  { value: 1, label: '近 24 小时' },
  { value: 7, label: '近 7 天' },
  { value: 30, label: '近 30 天' },
]

/**
 * 「不限」的哨兵值。
 *
 * ⚠️ 不能直接用 `null` 当 Select 的 value：antd 把 `null` 当作"没有选中"，
 * 会把已选中的标签换成 placeholder（这里没给 placeholder，于是那格看起来是空的）。
 * 用一个不与任何真实取值冲突的字符串哨兵，比给每个 Select 都配 placeholder 更不容易漏。
 */
const ALL = 'all'

const DAY_MS = 24 * 60 * 60 * 1000

/**
 * 下载扩展名。从 `mime_type` 推导（它在契约里），**不从 `original_name` 取** ——
 * 后者是用户可控字符串，拿它拼文件名就是把用户输入放进本地文件系统。
 */
const EXT_BY_MIME: Record<string, string> = {
  'image/png': 'png',
  'image/jpeg': 'jpg',
  'image/webp': 'webp',
}

/**
 * 把 Blob 存到本地。
 *
 * ⚠️ object URL 一定要释放：zip 包可能几十 MB，不释放就是把整包钉在内存里。
 * 用 `setTimeout` 而不是立刻释放，是因为部分浏览器在 `click()` 返回后才真正开始
 * 读取 URL —— 同步释放会得到一个 0 字节的文件（这类错误很难归因）。
 */
function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  window.setTimeout(() => URL.revokeObjectURL(url), 0)
}

/**
 * 卡片下方显示什么。
 *
 * **只从 `meta` 里已存在的键派生**，取不到就退到 id / 时间 ——
 * 绝不编造"场景名""模板名"这类后端没给的字段（线框图 §6 画的是示意值）。
 */
function captionOf(asset: AssetOut): { primary: string; secondary: string } {
  const meta = asset.meta ?? {}
  const params =
    typeof meta.params === 'object' && meta.params !== null && !Array.isArray(meta.params)
      ? (meta.params as Record<string, unknown>)
      : {}
  // 批量场景的 SKU 若被写进参数就显示它（目前单任务路径不会写，属兜底）
  const sku = params.sku
  const workflow = meta.workflow
  const primary =
    typeof sku === 'string' && sku
      ? sku
      : typeof workflow === 'string' && workflow
        ? workflow
        : `#${asset.id}`
  const seed = typeof meta.seed === 'number' ? `seed ${meta.seed}` : null
  const secondary = seed ?? new Date(asset.created_at).toLocaleDateString()
  return { primary, secondary }
}

export function GalleryPage() {
  const navigate = useNavigate()

  const [facet, setFacet] = useState<Facet>('all')
  const [kind, setKind] = useState<string>(ASSET_KIND.OUTPUT)
  const [taskId, setTaskId] = useState<number | null>(null)
  const [days, setDays] = useState<number | null>(null)
  const [page, setPage] = useState(1)
  const [selected, setSelected] = useState<ReadonlySet<number>>(new Set())
  const [metaAsset, setMetaAsset] = useState<AssetOut | null>(null)
  const [previewAsset, setPreviewAsset] = useState<AssetOut | null>(null)

  const query: AssetListQuery = useMemo(
    () => ({
      // ⚠️ 「全部类型」必须**不传** kind，而不是传 `kind=all` ——
      // 后端会拿它去做等值比较，`kind=all` 的结果是"0 条"，不是"不过滤"。
      kind: kind === ALL ? undefined : (kind as AssetListQuery['kind']),
      adopted_only: facet === 'adopted' ? true : undefined,
      favorite_only: facet === 'favorite' ? true : undefined,
      task_id: taskId ?? undefined,
      // ⚠️ 必须带时区（`...Z`）：库里存 UTC，后端把 naive 串按 UTC 解释，
      // 传本地裸时间会静默错一个时区。
      created_from: days === null ? undefined : new Date(Date.now() - days * DAY_MS).toISOString(),
      limit: PAGE_SIZE,
      offset: (page - 1) * PAGE_SIZE,
    }),
    [kind, facet, taskId, days, page],
  )

  const assets = useAssets(query)
  // 「来源」下拉的候选项来自任务列表；它挂了不该让整页不可用，故失败时降级为空列表
  const tasks = useTasks()
  const update = useUpdateAsset()
  const remove = useDeleteAssets()
  const pack = usePackAssets()

  const downloadOne = useMutation({
    mutationFn: (asset: AssetOut) => api.assets.content(asset.id, { download: true }),
    onSuccess: (blob, asset) => {
      saveBlob(blob, `asset_${asset.id}.${EXT_BY_MIME[asset.mime_type] ?? 'bin'}`)
    },
    onError: (err) => void message.error(describeError(err)),
  })

  const items = assets.data?.items ?? []
  const total = assets.data?.total ?? 0

  /**
   * ⚠️ 「未采纳」这一支是**纯客户端**过滤（后端无该参数）。见文件头第 3 条。
   */
  const visibleItems = facet === 'not_adopted' ? items.filter((a) => !a.is_adopted) : items

  // 换筛选条件后，旧的页码/选择都不再对应同一批数据 —— 不重置会出现"翻到第 5 页
  // 但结果只有 1 页"这种空白页。
  useEffect(() => {
    setPage(1)
  }, [kind, facet, taskId, days])

  useEffect(() => {
    setSelected(new Set())
  }, [kind, facet, taskId, days, page])

  const selectedIds = useMemo(() => [...selected].sort((a, b) => a - b), [selected])
  const allVisibleSelected = visibleItems.length > 0 && visibleItems.every((a) => selected.has(a.id))
  const someVisibleSelected = visibleItems.some((a) => selected.has(a.id))

  const toggleOne = (id: number, checked: boolean) => {
    setSelected((prev) => {
      const next = new Set(prev)
      if (checked) next.add(id)
      else next.delete(id)
      return next
    })
  }

  const toggleAll = (checked: boolean) => {
    setSelected((prev) => {
      const next = new Set(prev)
      for (const asset of visibleItems) {
        if (checked) next.add(asset.id)
        else next.delete(asset.id)
      }
      return next
    })
  }

  const adopt = (asset: AssetOut) => {
    // 后端对上传素材的采纳请求返回 409。前端在入口就挡住，不靠 409 兜底 ——
    // 让用户点一个必然失败的操作是纯粹的浪费。
    if (asset.kind !== ASSET_KIND.OUTPUT) return
    update.mutate(
      { assetId: asset.id, body: { is_adopted: !asset.is_adopted } },
      { onError: (err) => void message.error(describeError(err)) },
    )
  }

  const favorite = (asset: AssetOut) => {
    update.mutate(
      { assetId: asset.id, body: { is_favorite: !asset.is_favorite } },
      { onError: (err) => void message.error(describeError(err)) },
    )
  }

  const packSelected = () => {
    pack.mutate(
      { asset_ids: selectedIds },
      {
        onSuccess: (blob) => saveBlob(blob, `images-${Date.now()}.zip`),
        onError: (err) => void message.error(describeError(err)),
      },
    )
  }

  const deleteSelected = () => {
    remove.mutate(selectedIds, {
      onSuccess: () => void message.success(`已删除 ${selectedIds.length} 张`),
      onError: (err) => void message.error(describeError(err)),
    })
  }

  const hasSelection = selectedIds.length > 0
  const canDownloadSingle = selectedIds.length === 1

  return (
    <Space direction="vertical" size="middle" style={{ width: '100%' }}>
      <Space direction="vertical" size={0}>
        <Typography.Title level={4} style={{ margin: 0 }}>
          画廊
        </Typography.Title>
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          缩略图当前加载的是<strong>原图</strong>（后端暂无缩略图接口）：进入视口才加载，且同时最多 6
          个请求在飞。
        </Typography.Text>
      </Space>

      {/* ------------------------------------------------ 筛选 */}
      <Card size="small">
        <Space wrap size="middle">
          <Space size={4}>
            {FACETS.map((item) => (
              <Button
                key={item.value}
                size="small"
                type={facet === item.value ? 'primary' : 'default'}
                // ⚠️ 必须带「筛选：」前缀：卡片上的采纳角标 aria-label 就是「已采纳」，
                // 两处同名会让读屏用户（和测试）分不清哪个是筛选、哪个是状态。
                aria-label={`筛选：${item.label}`}
                aria-pressed={facet === item.value}
                onClick={() => setFacet(item.value)}
              >
                {item.label}
                {item.value === 'adopted' ? ' ★' : ''}
              </Button>
            ))}
          </Space>

          <Space size={4}>
            <Typography.Text type="secondary">类型</Typography.Text>
            <Select
              size="small"
              style={{ width: 130 }}
              aria-label="类型筛选"
              value={kind}
              onChange={setKind}
              options={[
                { value: ASSET_KIND.OUTPUT, label: '生成产物' },
                { value: ASSET_KIND.UPLOAD, label: '上传素材' },
                { value: ALL, label: '全部类型' },
              ]}
            />
          </Space>

          <Space size={4}>
            <Typography.Text type="secondary">来源</Typography.Text>
            <Select
              size="small"
              style={{ width: 180 }}
              aria-label="来源筛选"
              allowClear
              showSearch
              optionFilterProp="label"
              placeholder="全部任务"
              value={taskId ?? undefined}
              onChange={(value) => setTaskId(value ?? null)}
              options={(tasks.data ?? []).map((task) => ({
                value: task.id,
                label: `任务 #${task.id}`,
              }))}
            />
          </Space>

          <Space size={4}>
            <Typography.Text type="secondary">时间</Typography.Text>
            <Select
              size="small"
              style={{ width: 140 }}
              aria-label="时间筛选"
              value={days === null ? ALL : days}
              onChange={(value) => setDays(value === ALL ? null : Number(value))}
              options={[
                { value: ALL, label: '全部时间' },
                ...TIME_RANGES.map((range) => ({ value: range.value, label: range.label })),
              ]}
            />
          </Space>
        </Space>

        {facet === 'not_adopted' && (
          <Alert
            style={{ marginTop: 12 }}
            type="info"
            showIcon
            message="「未采纳」只在当前页内筛选"
            description="后端 /assets 只有 adopted_only（只看已采纳）这一个参数，没有「未采纳」。翻到别的页时筛选范围也随之变化，因此本视图的总条数不代表全部未采纳产物。"
          />
        )}
      </Card>

      {/* ------------------------------------------------ 操作条 */}
      <Card size="small">
        <Space wrap size="middle">
          <Checkbox
            checked={allVisibleSelected}
            indeterminate={someVisibleSelected && !allVisibleSelected}
            disabled={visibleItems.length === 0}
            onChange={(e) => toggleAll(e.target.checked)}
          >
            全选
          </Checkbox>

          <Typography.Text>已选 {selectedIds.length} 张</Typography.Text>

          <Tooltip title={canDownloadSingle ? '' : '「下载选中」一次只处理一张，多张请用「打包 zip」'}>
            <Button
              icon={<DownloadOutlined />}
              // antd 图标自带 `aria-label="download"`，会拼进按钮的可访问名
              // （读屏会念成 "download 下载选中"）。显式覆盖成纯中文。
              aria-label="下载选中"
              disabled={!canDownloadSingle}
              loading={downloadOne.isPending}
              onClick={() => {
                const asset = visibleItems.find((a) => a.id === selectedIds[0])
                if (asset) downloadOne.mutate(asset)
              }}
            >
              下载选中
            </Button>
          </Tooltip>

          <Button
            icon={<FileZipOutlined />}
            aria-label="打包 zip"
            disabled={!hasSelection}
            loading={pack.isPending}
            onClick={packSelected}
          >
            打包 zip
          </Button>

          <Popconfirm
            title={`确认删除选中的 ${selectedIds.length} 张？`}
            description="删除后不会出现在画廊里（软删除，不立即物理清除）。"
            okText="删除"
            okButtonProps={{ danger: true }}
            cancelText="取消"
            disabled={!hasSelection}
            onConfirm={deleteSelected}
          >
            <Button
              danger
              icon={<DeleteOutlined />}
              aria-label="删除"
              disabled={!hasSelection}
              loading={remove.isPending}
            >
              删除
            </Button>
          </Popconfirm>
        </Space>
      </Card>

      {/* ------------------------------------------------ 网格 / 状态 */}
      {assets.isError && (
        <Alert
          type="error"
          showIcon
          message="产物列表加载失败"
          description={describeError(assets.error)}
          action={
            <Button size="small" onClick={() => void assets.refetch()}>
              重试
            </Button>
          }
        />
      )}

      {assets.isLoading && (
        <div style={{ textAlign: 'center', padding: 48 }}>
          <Spin />
        </div>
      )}

      {!assets.isLoading && !assets.isError && total === 0 && (
        <Card>
          <Empty
            description={
              <span>
                还没有产物。去工作台生成第一张吧 —— 出图后会自动出现在这里。
                <br />
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  （当前筛选：{FACETS.find((f) => f.value === facet)?.label ?? '全部'} ·{' '}
                  {kind === ASSET_KIND.OUTPUT ? '生成产物' : kind === ASSET_KIND.UPLOAD ? '上传素材' : '全部类型'}）
                </Typography.Text>
              </span>
            }
          >
            <Button type="primary" onClick={() => navigate('/')}>
              去工作台
            </Button>
          </Empty>
        </Card>
      )}

      {!assets.isLoading && !assets.isError && total > 0 && visibleItems.length === 0 && (
        <Card>
          <Empty description="当前页没有符合条件的产物（「未采纳」只在当前页内筛选，翻页看看）" />
        </Card>
      )}

      {visibleItems.length > 0 && (
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fill, minmax(180px, 1fr))',
            gap: 16,
          }}
        >
          {visibleItems.map((asset) => {
            const canAdopt = asset.kind === ASSET_KIND.OUTPUT
            const { primary, secondary } = captionOf(asset)
            return (
              <Card
                key={asset.id}
                data-testid="asset-card"
                aria-label={`素材卡片 ${asset.id}`}
                size="small"
                styles={{ body: { padding: 8 } }}
                cover={
                  <div style={{ position: 'relative' }}>
                    <AssetImage asset={asset} />

                    <Checkbox
                      aria-label={`选择素材 ${asset.id}`}
                      checked={selected.has(asset.id)}
                      onChange={(e) => toggleOne(asset.id, e.target.checked)}
                      style={{ position: 'absolute', top: 8, left: 8 }}
                    />

                    {asset.is_adopted && (
                      <span
                        aria-label="已采纳"
                        style={{
                          position: 'absolute',
                          top: 8,
                          right: 8,
                          background: '#faad14',
                          color: '#fff',
                          borderRadius: 10,
                          padding: '0 8px',
                          fontSize: 12,
                          lineHeight: '20px',
                        }}
                      >
                        ★ 已采纳
                      </span>
                    )}
                  </div>
                }
                actions={[
                  <Tooltip key="download" title="下载这一张">
                    <Button
                      type="text"
                      aria-label="下载"
                      icon={<DownloadOutlined />}
                      onClick={() => downloadOne.mutate(asset)}
                    />
                  </Tooltip>,
                  <Tooltip
                    key="adopt"
                    title={
                      canAdopt
                        ? asset.is_adopted
                          ? '取消采纳'
                          : '标记采纳'
                        : '只有生成产物能标记采纳（上传素材没有「生成得好不好」的语义）'
                    }
                  >
                    <Button
                      type="text"
                      aria-label="标记采纳"
                      aria-pressed={asset.is_adopted}
                      disabled={!canAdopt}
                      icon={
                        asset.is_adopted ? (
                          <StarFilled style={{ color: '#faad14' }} />
                        ) : (
                          <StarOutlined />
                        )
                      }
                      onClick={() => adopt(asset)}
                    />
                  </Tooltip>,
                  <Tooltip key="favorite" title={asset.is_favorite ? '取消收藏' : '收藏'}>
                    <Button
                      type="text"
                      aria-label="收藏"
                      aria-pressed={asset.is_favorite}
                      style={asset.is_favorite ? { color: '#eb2f96' } : undefined}
                      icon={<StarOutlined />}
                      onClick={() => favorite(asset)}
                    />
                  </Tooltip>,
                  <Tooltip key="preview" title="查看大图">
                    <Button
                      type="text"
                      aria-label="查看大图"
                      icon={<EyeOutlined />}
                      onClick={() => setPreviewAsset(asset)}
                    />
                  </Tooltip>,
                  <Tooltip key="meta" title="元数据（seed / 参数）">
                    <Button
                      type="text"
                      aria-label="查看元数据"
                      icon={<InfoCircleOutlined />}
                      onClick={() => setMetaAsset(asset)}
                    />
                  </Tooltip>,
                ]}
              >
                <Typography.Text ellipsis style={{ display: 'block', fontSize: 13 }}>
                  {primary}
                </Typography.Text>
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  {secondary}
                </Typography.Text>
              </Card>
            )
          })}
        </div>
      )}

      {total > PAGE_SIZE && (
        <div style={{ textAlign: 'right' }}>
          <Pagination
            current={page}
            pageSize={PAGE_SIZE}
            total={total}
            showSizeChanger={false}
            onChange={setPage}
          />
        </div>
      )}

      {/* ------------------------------------------------ 大图预览 / 元数据 */}
      <Modal
        open={previewAsset !== null}
        title={previewAsset ? `素材 #${previewAsset.id}` : ''}
        footer={null}
        width={880}
        onCancel={() => setPreviewAsset(null)}
        destroyOnClose
      >
        {previewAsset && <AssetImage asset={previewAsset} height={560} objectFit="contain" />}
      </Modal>

      <MetadataDrawer
        asset={metaAsset}
        open={metaAsset !== null}
        onClose={() => setMetaAsset(null)}
      />
    </Space>
  )
}
