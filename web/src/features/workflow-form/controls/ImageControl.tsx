import { Alert, Button, Space, Upload, message } from 'antd'
import { useMutation } from '@tanstack/react-query'
import { useState } from 'react'

import { api } from '../../../api/client'
import { describeError } from '../../../api/errors'
import type { ControlProps } from './types'

/**
 * `image` —— 单张参考图。值是**素材 ID（整数）**，不是 URL。
 *
 * ⚠️ 三个必须知道的现实约束：
 *
 * 1. **没有图片下载/预览接口**（P6-10 未做，`AssetOut` 里连 `object_key` 都没有）。
 *    所以提交后**无法回显**已选的图。这里用 `URL.createObjectURL` 显示**本地**预览，
 *    并明确标注它没有从服务端取回 —— 而不是假装限制不存在。
 * 2. **上传依赖 MinIO**（`POST /assets` 写对象存储）。没装 MinIO 时会返回 500，
 *    本控件会把真实错误显示出来。该路径的状态是「**待 MinIO 验证**」。
 * 3. 值最终放进 `params[field.key]`（真实键名 `reference_image`），
 *    **不要**用顶层 `upload_asset_ids` —— 单任务路径从不读它。
 */
// ⚠️ 不再自己渲染错误文案：`WorkflowForm` 会通过 `Form.Item` 的 `extra`
// 显示它。两处都渲染会让同一条错误在界面上出现两遍（曾经就是如此）。
export function ImageControl({ field, value, onChange, disabled }: ControlProps) {
  const [localPreview, setLocalPreview] = useState<string | null>(null)
  const assetId = typeof value === 'number' && value > 0 ? value : null

  const upload = useMutation({
    mutationFn: (file: File) => api.assets.upload(file),
    onSuccess: (asset) => {
      onChange(asset.id)
      void message.success(`已上传素材 #${asset.id}`)
    },
    onError: (err) => {
      void message.error(describeError(err))
    },
  })

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="small">
      {localPreview && (
        <img
          src={localPreview}
          alt="本地预览（未从服务端取回）"
          style={{
            maxWidth: 160,
            maxHeight: 160,
            objectFit: 'contain',
            border: '1px solid #f0f0f0',
          }}
        />
      )}

      <Space wrap>
        <Upload
          accept="image/jpeg,image/png,image/webp"
          maxCount={1}
          showUploadList={false}
          disabled={disabled || upload.isPending}
          beforeUpload={(file) => {
            const url = URL.createObjectURL(file)
            setLocalPreview((prev) => {
              if (prev) URL.revokeObjectURL(prev)
              return url
            })
            upload.mutate(file)
            return false // 由我们自己的 mutation 上传；不要 antd 再发一次
          }}
        >
          <Button loading={upload.isPending} disabled={disabled}>
            {assetId === null ? '选择并上传图片' : '重新选择'}
          </Button>
        </Upload>

        {assetId !== null ? (
          <span>
            已选素材 <strong>#{assetId}</strong>
          </span>
        ) : (
          <span style={{ color: '#8c8c8c' }}>尚未选择（必填）</span>
        )}

        {assetId !== null && (
          <Button
            size="small"
            onClick={() => onChange(null)}
            disabled={disabled}
            aria-label={`清除${field.label}`}
          >
            清除
          </Button>
        )}
      </Space>

      <Alert
        type="info"
        showIcon
        message="本地预览，未从服务端取回"
        description="后端尚无图片下载/签名 URL 接口（P6-10 未做），素材库中的图无法生成缩略图。上传成功后提交使用的是素材 ID。"
      />
    </Space>
  )
}
