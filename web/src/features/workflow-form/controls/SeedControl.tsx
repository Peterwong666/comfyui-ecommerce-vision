import { Button, InputNumber, Space, Tooltip } from 'antd'

import { SEED_RANDOM, SEED_SPACE } from '../types'
import type { ControlProps } from './types'

/**
 * `seed` —— 随机种子。
 *
 * ⚠️ 三条不能改的语义（`seed` 是唯一自带特殊语义的类型，不是"普通整数"）：
 *
 * 1. **没有 `min`/`max`**：真实 Schema 里 `seed` 字段不带边界
 *    （`engine/schema.py` 对 `seed` 单独处理，不走 `BOUNDED_TYPES`）。
 *    合法域是 `-1`（随机）或 `[0, 2**32)`。
 * 2. **`-1` 必须原样提交**，绝不归一成 0。
 * 3. **不要在前端生成随机数**。`-1` 交给服务端解析（`engine/render.py`），
 *    服务端会把真实值回写进任务与产物元数据 —— 这才能让用户"照 seed 复现那张图"
 *    （FR-5.5）。前端自己随机化会让元数据里的 seed 与实际出图不符。
 */
export function SeedControl({ value, onChange, invalid, disabled }: ControlProps) {
  const current = typeof value === 'number' ? value : SEED_RANDOM
  const isRandom = current === SEED_RANDOM

  return (
    <Space.Compact style={{ width: '100%' }}>
      <InputNumber
        aria-label="随机种子"
        style={{ width: '100%' }}
        value={current}
        onChange={(next) => onChange(next ?? SEED_RANDOM)}
        min={SEED_RANDOM}
        max={SEED_SPACE - 1}
        precision={0}
        status={invalid ? 'error' : undefined}
        disabled={disabled || isRandom}
        addonAfter={isRandom ? '随机' : undefined}
      />
      <Tooltip title="设为随机：真实种子由服务端在提交时确定，并写进产物元数据">
        <Button onClick={() => onChange(SEED_RANDOM)} disabled={disabled || isRandom}>
          🎲
        </Button>
      </Tooltip>
    </Space.Compact>
  )
}
