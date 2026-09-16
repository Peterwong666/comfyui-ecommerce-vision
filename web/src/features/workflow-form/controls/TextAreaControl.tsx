import { Input } from 'antd'

import type { ControlProps } from './types'

/**
 * `text` —— 多行文本（提示词的载体）。
 *
 * `showCount` 与 `maxLength` 都取自 Schema 的 `max_length`（真实数据里是 2000），
 * 而不是硬编码 —— 契约 §6 明令前端不得硬编码参数默认值。
 */
export function TextAreaControl({ field, value, onChange, invalid, disabled }: ControlProps) {
  return (
    <Input.TextArea
      // ⚠️ 必须显式给 aria-label：`Form.Item` 没有 `name`（值由我们自己管），
      // antd 就不会生成 `htmlFor`/`id` 的关联 —— 于是 label 与控件在
      // 无障碍树上**没有关系**，读屏软件读不出这个输入框叫什么。
      aria-label={field.label}
      value={typeof value === 'string' ? value : ''}
      onChange={(e) => onChange(e.target.value)}
      maxLength={field.max_length}
      showCount={field.max_length !== undefined}
      autoSize={{ minRows: 3, maxRows: 8 }}
      status={invalid ? 'error' : undefined}
      disabled={disabled}
    />
  )
}
