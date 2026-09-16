import { Alert, Button, Space, Upload, message } from 'antd'
import { useMutation } from '@tanstack/react-query'

import { api } from '../../../api/client'
import { describeError } from '../../../api/errors'
import type { ControlProps } from './types'

/**
 * `image_list` —— 多张参考图。值是**素材 ID 数组**。
 *
 * ⚠️ **真实注册表里 `image_list` 出现 0 次**（只有 `image`），
 * 所以本控件属于「**待真实数据验证**」—— 不要声称它已在生产中用过。
 * 与 `ImageControl` 同样的两个约束：无取图接口（P6-10 未做）、上传依赖 MinIO。
 */
export function ImageListControl({ value, onChange, disabled }: ControlProps) {
  const ids = Array.isArray(value) ? value.filter((v): v is number => typeof v === 'number') : []

  const upload = useMutation({
    mutationFn: (files: File[]) =>
      Promise.all(files.map((file) => api.assets.upload(file).then((asset) => asset.id))),
    onSuccess: (newIds) => {
      onChange([...ids, ...newIds])
      void message.success(`已上传 ${newIds.length} 张素材`)
    },
    onError: (err) => {
      void message.error(describeError(err))
    },
  })

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="small">
      <Space wrap>
        <Upload
          accept="image/jpeg,image/png,image/webp"
          multiple
          showUploadList={false}
          disabled={disabled || upload.isPending}
          beforeUpload={(_file, fileList) => {
            // antd 会对每个文件各调一次；只在最后一批提交时上传整体
            if (fileList.length > 0 && _file === fileList[fileList.length - 1]) {
              void upload.mutateAsync(fileList as File[])
            }
            return false
          }}
        >
          <Button loading={upload.isPending} disabled={disabled}>
            添加图片
          </Button>
        </Upload>

        <span>
          已选 <strong>{ids.length}</strong> 张
        </span>

        {ids.length > 0 && (
          <Button size="small" onClick={() => onChange([])} disabled={disabled}>
            清空
          </Button>
        )}
      </Space>

      {ids.length > 0 && <span style={{ color: '#8c8c8c' }}>素材 ID：{ids.join(', ')}</span>}

      <Alert
        type="info"
        showIcon
        message="暂不支持预览"
        description="后端尚无图片下载接口（P6-10 未做）。本控件在真实工作流中尚未出现，属待验证状态。"
      />
    </Space>
  )
}
