import { Switch } from 'antd'

import type { ControlProps } from './types'

/** `bool` —— 开关。⚠️ 真实注册表里**零使用**，待真实数据验证。 */
export function SwitchControl({ field, value, onChange, disabled }: ControlProps) {
  return (
    <Switch
      aria-label={field.label}
      checked={value === true}
      onChange={(checked) => onChange(checked)}
      disabled={disabled}
    />
  )
}
