import { InputNumber } from 'antd'

import { stepFor } from '../defaults'
import type { ControlProps } from './types'

/**
 * `int` / `float` 的数值输入。
 *
 * ⚠️ `min` 与 `max` **可能为负**，也可能缺失：
 * - 负值：`inpaint_v1` 的 `mask_expand` 是 `min: -32`
 * - 缺失：`int` 不保证有 `min`/`max`（只有契约要求数值型带边界）
 *
 * 所以一律原样透传，不要在这里做 `Math.max(0, ...)` 之类的"顺手修正"。
 * `step` 同样可能缺失（`engine/schema.py` 只对 `float` 强制要求），走 `stepFor` 兜底。
 */
export function NumberControl({ field, value, onChange, invalid, disabled }: ControlProps) {
  return (
    <InputNumber
      aria-label={field.label}
      style={{ width: '100%' }}
      value={typeof value === 'number' ? value : null}
      onChange={(next) => onChange(next)}
      min={field.min}
      max={field.max}
      step={stepFor(field)}
      precision={field.type === 'int' ? 0 : undefined}
      status={invalid ? 'error' : undefined}
      disabled={disabled}
      // 让数字输入支持键盘上下键与滚轮，符合"调参"这个高频动作
      keyboard
      changeOnWheel
    />
  )
}
