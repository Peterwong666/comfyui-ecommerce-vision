import { Input } from 'antd'

import type { ControlProps } from './types'

/** `str` —— 单行文本。⚠️ 真实注册表里**零使用**（仅测试夹具出现过），待真实数据验证。 */
export function TextControl({ field, value, onChange, invalid, disabled }: ControlProps) {
  return (
    <Input
      aria-label={field.label}
      value={typeof value === 'string' ? value : ''}
      onChange={(e) => onChange(e.target.value)}
      maxLength={field.max_length}
      status={invalid ? 'error' : undefined}
      disabled={disabled}
      allowClear
    />
  )
}
