/**
 * 初始值与分组规则。
 *
 * 这些规则**必须**在这里集中，因为其中几条是被真金白银的教训换来的
 * （见每个函数的注释）。散落到各控件里就守不住了。
 */

import { DEFAULT_GROUP, SEED_RANDOM, type ParamField, type ParamSchema } from './types'

function asNumber(v: unknown): number | undefined {
  return typeof v === 'number' && Number.isFinite(v) ? v : undefined
}

/**
 * 单个字段的初始值。
 *
 * ⚠️ 两条不能改的语义：
 *
 * 1. **`image` 的 `default: 0` 不是默认值，是"必填占位符"。**
 *    注册表里两处都写明了（`registry.yaml:372-374` / `:632-637`），
 *    契约 §3.1 也**没有** `required` 字段。
 *    若照抄成 `0`，用户不选图也能提交，任务会在素材解析处失败
 *    （`invalid_param`，且**不可重试**，白排一次队）。
 *    → 所以 `image` 的初始值是 `null`（未选择），并在 `validate.ts` 里当必填。
 *
 * 2. **`seed` 的 `-1` 必须原样保留**，绝不可归一成 0。
 *    `-1` 是"随机"，由**服务端**解析成真实整数并回写（`engine/render.py`），
 *    客户端自己随机化会毁掉 FR-5.5 的可复现性（元数据里就没有可复现的 seed 了）。
 */
export function initialValue(field: ParamField): unknown {
  switch (field.type) {
    case 'image':
      return null
    case 'image_list':
      return []
    case 'bool':
      return field.default === true
    case 'seed': {
      const raw = asNumber(field.default)
      return raw === undefined ? SEED_RANDOM : raw
    }
    default:
      return field.default
  }
}

/** 整张表单的初始值。 */
export function initialValues(schema: ParamSchema): Record<string, unknown> {
  const values: Record<string, unknown> = {}
  for (const field of schema.fields) {
    values[field.key] = initialValue(field)
  }
  return values
}

/**
 * 数值控件的步长。
 *
 * ⚠️ `int` **不保证有 `step`**：`engine/schema.py:150-151` 只对 `float` 强制要求 `step`。
 * 直接 `field.step!` 会得到 `undefined`，滑块的步进行为变成未定义。
 */
export function stepFor(field: ParamField): number {
  const declared = asNumber(field.step)
  if (declared !== undefined && declared > 0) return declared
  if (field.type === 'int') return 1
  const min = asNumber(field.min) ?? 0
  const max = asNumber(field.max) ?? 1
  return (max - min) / 100 || 0.01
}

/**
 * 是否必填。
 *
 * 目前只有图片类：Schema 里**没有** `required` 字段，
 * 而图片是"没有就必然失败"的输入。其余字段后端都有默认值兜底。
 */
export function isRequired(field: ParamField): boolean {
  return field.type === 'image' || field.type === 'image_list'
}

export interface FieldGroup {
  name: string
  fields: ParamField[]
}

/**
 * 按 `group` 分组，并把 `advanced: true` 的字段抽出来。
 *
 * 两条规则：
 * - **保持首次出现顺序**，不排序。注册表里的字段顺序是刻意设计的 UX 顺序，
 *   排序会把"提示词 → 画布 → 采样"这种叙事打乱。
 * - `advanced` 缺省即"常显"（`engine/schema.py:183`：默认 False）。
 *   真实数据里 `negative_prompt` / `steps` / `cfg` / `sampler_name` / `scheduler`
 *   都是 advanced，所以"关键参数不超过 5 个可见"这条设计原则自动成立。
 */
export function groupFields(schema: ParamSchema): {
  groups: FieldGroup[]
  advanced: ParamField[]
} {
  const groups: FieldGroup[] = []
  const byName = new Map<string, FieldGroup>()
  const advanced: ParamField[] = []

  for (const field of schema.fields) {
    if (field.advanced === true) {
      advanced.push(field)
      continue
    }
    const name = typeof field.group === 'string' && field.group ? field.group : DEFAULT_GROUP
    let group = byName.get(name)
    if (group === undefined) {
      group = { name, fields: [] }
      byName.set(name, group)
      groups.push(group)
    }
    group.fields.push(field)
  }

  return { groups, advanced }
}
