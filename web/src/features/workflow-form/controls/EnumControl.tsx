import { Select } from 'antd'

import type { ControlProps } from './types'

/**
 * `enum` —— 下拉选择。
 *
 * ⚠️ **渲染 `option.label`，不是 `option.value`。**
 * 真实数据里 `sampler_name` 有 45 个选项、`scheduler` 有 9 个，
 * 其中 `dpmpp_2m → "DPM++ 2M（推荐）"`、`karras → "Karras（推荐）"` 两者不同。
 * 只渲染 value 会丢掉"推荐"这个对用户最有价值的信息。
 *
 * 选项**完全来自 Schema**，不得在前端硬编码任何枚举取值（契约 §6 的 D 流边界）。
 */
export function EnumControl({ field, value, onChange, invalid, disabled }: ControlProps) {
  const options = (field.options ?? []).map((opt) => ({
    value: opt.value,
    label: opt.label ?? opt.value,
  }))

  return (
    <Select
      aria-label={field.label}
      style={{ width: '100%' }}
      value={typeof value === 'string' ? value : undefined}
      onChange={(next) => onChange(next)}
      options={options}
      status={invalid ? 'error' : undefined}
      disabled={disabled}
      showSearch
      optionFilterProp="label"
    />
  )
}
