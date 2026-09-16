import { Card, Empty, Typography } from 'antd'

/**
 * 占位页面（模板库 / 批量向导 / 任务中心 / 画廊 / 管理后台）。
 *
 * 用一个组件 + props 而不是写 5 个几乎一样的文件：那 5 份的差异只有文案，
 * 复制粘贴的结果通常是改了一处忘了另一处。
 *
 * ⚠️ 这些页面**尚未实现**，对应 P7-03~P7-12。
 * 占位页明确写出"归属哪个任务"，避免访客以为是坏了。
 */
export function PlaceholderPage({
  title,
  taskId,
  note,
}: {
  title: string
  taskId: string
  note: string
}) {
  return (
    <Card>
      <Typography.Title level={4}>{title}</Typography.Title>
      <Empty
        description={
          <span>
            该页面尚未实现（{taskId}）
            <br />
            {note}
          </span>
        }
      />
    </Card>
  )
}
