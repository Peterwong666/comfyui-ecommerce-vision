/**
 * 客户端参数校验。
 *
 * ⚠️ **这份校验目前是多数字段的唯一提交前防线。**
 * 后端的 `_validate_params`（`backend/app/api/v1/tasks.py`）**只查三类东西**：
 * 顶层 `steps` 与 `cfg`，以及**工作流 Schema 里 `type: text` 字段的长度上界**
 * （`_validate_text_lengths`，即 `settings.max_prompt_length = 2000`；
 * 下界**不查** —— `param_schema` 里没有 `required` / `allow_empty` 标记）。
 * 其余 `width` / `height` / `denoise` / `scale_by` / `mask_expand` /
 * `sampler_name` / `scheduler` 后端一概不查 —— 它们要等到 worker 里渲染时才校验，
 * 那时失败是 EX-3 `invalid_param`，**不可重试**，用户白等一次排队。
 *
 * 所以**不要"简化"这个文件**。契约 §3.4 写着"前端校验只是体验，后端必须二次校验"，
 * 但那是**目标状态**，当前实现还没做到；本文件对 `prompt` 非空与其余数值的检查
 * 是**双保险**（后端只覆盖其中的一部分），把这段删掉等于把防线清空。
 *
 * 反过来也有纪律：**不发明后端没有的规则**。详见 `validateNumber` 里关于
 * `step` 对齐的说明。
 */

import { SEED_RANDOM, SEED_SPACE, type ParamField, type ParamSchema } from './types'

export interface FieldViolation {
  key: string
  reason: string
}

/**
 * 必须非空的文本字段。
 *
 * `prompt` 是唯一一个"空值必然浪费一次 GPU"的字段：空提示词不会报错，
 * 会照常排队、照常出图（出一张无意义的图）。
 *
 * ⚠️ **后端也拦这一条（2026-09-17）**：顶层 `prompt` 由 `TaskSubmitIn` 的
 * `_check_prompt` 按 `settings.min_prompt_length`（= 1）拒空串。
 * 但**只走 `params` 通道**传文本时，后端 `tasks._validate_text_lengths` 按工作流
 * Schema **只查上界** `max_prompt_length`（= 2000）、**不查下界** —— 因为
 * `param_schema` 没有 `required` / `allow_empty` 之类的标记，"哪个文本字段允许为空"
 * 没有事实来源（理由见 `_validate_text_lengths` 的 docstring）。
 *
 * 所以这里的检查是**双保险**（顶层通道前后端各拦一次），不再是唯一防线；
 * 但也**不要**据此推断"后端对空串一概放行"—— 顶层通道仍然会拒。
 * 别把它删了：能少一次"提交完才被拒"的往返，本地拦下来体验更好。
 *
 * 不把 `negative_prompt` 放进来：清空反向词是合法用法。
 * ⚠️ 后端**现在与这里一致**：`_validate_text_lengths` 不查下界，
 * 所以 `negative_prompt: ''` 会正常受理（此前后端更严、会返回 422，已按 2026-09-17
 * 的裁定改为只查上界）。
 */
const REQUIRED_TEXT_KEYS: readonly string[] = ['prompt']

function asNumber(v: unknown): number | undefined {
  return typeof v === 'number' && Number.isFinite(v) ? v : undefined
}

function validateNumber(field: ParamField, value: unknown): FieldViolation | null {
  const num = asNumber(value)
  if (num === undefined) {
    return { key: field.key, reason: '必须是数字' }
  }
  if (field.type === 'int' && !Number.isInteger(num)) {
    return { key: field.key, reason: '必须是整数' }
  }

  // ⚠️ **`min` 可以是负数**，不要假设 `min >= 0`：
  // `inpaint_v1` 的 `mask_expand` 是 `min: -32`（向外扩蒙版）。
  const min = asNumber(field.min)
  const max = asNumber(field.max)
  if (min !== undefined && num < min) {
    return { key: field.key, reason: `不能小于 ${min}` }
  }
  if (max !== undefined && num > max) {
    return { key: field.key, reason: `不能大于 ${max}` }
  }

  // 刻意**不校验 step 对齐**：
  // ① 后端与渲染器都不检查它，纯属自造规则；
  // ② 浮点步长不可靠（cfg 的 min=1.0 / step=0.1，1.1 在二进制里并不精确），
  //    严格对齐会把合法值误判成非法 —— 假报错比不报错更伤信任。
  // `step` 只用于滑块的步进粒度。
  return null
}

function validateTextField(field: ParamField, value: unknown): FieldViolation | null {
  if (typeof value !== 'string') {
    return { key: field.key, reason: '必须是文本' }
  }
  const maxLength = asNumber(field.max_length)
  if (maxLength !== undefined && value.length > maxLength) {
    return { key: field.key, reason: `最多 ${maxLength} 个字符（当前 ${value.length}）` }
  }
  if (REQUIRED_TEXT_KEYS.includes(field.key) && value.trim() === '') {
    return { key: field.key, reason: '不能为空 —— 空提示词会照常排队出图，白白浪费一次 GPU' }
  }
  return null
}

function validateEnum(field: ParamField, value: unknown): FieldViolation | null {
  const options = field.options ?? []
  const allowed = options.map((o) => o.value)
  if (typeof value !== 'string' || !allowed.includes(value)) {
    return { key: field.key, reason: '取值不在可选范围内' }
  }
  return null
}

function validateSeed(field: ParamField, value: unknown): FieldViolation | null {
  const num = asNumber(value)
  if (num === undefined || !Number.isInteger(num)) {
    return { key: field.key, reason: '必须是整数' }
  }
  if (num === SEED_RANDOM) return null // -1 = 随机，合法
  if (num < 0 || num >= SEED_SPACE) {
    return {
      key: field.key,
      reason: `只能是 -1（随机）或 0 ~ ${SEED_SPACE - 1} 之间的整数`,
    }
  }
  return null
}

function validateImage(field: ParamField, value: unknown): FieldViolation | null {
  // image 传的是 asset_id（正整数）。后端**没有** required 标记，
  // 但 default: 0 是"必填占位符"，所以这里必须拦。
  if (value === null || value === undefined) {
    return { key: field.key, reason: '必须选择图片' }
  }
  const num = asNumber(value)
  if (num === undefined || !Number.isInteger(num) || num <= 0) {
    return { key: field.key, reason: '必须选择图片（需要有效的素材 ID）' }
  }
  return null
}

function validateImageList(field: ParamField, value: unknown): FieldViolation | null {
  if (!Array.isArray(value) || value.length === 0) {
    return { key: field.key, reason: '至少选择一张图片' }
  }
  const bad = value.some((v) => {
    const num = asNumber(v)
    return num === undefined || !Number.isInteger(num) || num <= 0
  })
  return bad ? { key: field.key, reason: '存在无效的素材 ID' } : null
}

/** 校验单个字段。`null` 表示通过。 */
export function validateField(field: ParamField, value: unknown): FieldViolation | null {
  switch (field.type) {
    case 'int':
    case 'float':
      return validateNumber(field, value)
    case 'str':
    case 'text':
      return validateTextField(field, value)
    case 'bool':
      return typeof value === 'boolean' ? null : { key: field.key, reason: '必须是布尔值' }
    case 'enum':
      return validateEnum(field, value)
    case 'seed':
      return validateSeed(field, value)
    case 'image':
      return validateImage(field, value)
    case 'image_list':
      return validateImageList(field, value)
    default:
      // 未知类型：**不拦**。它由 FallbackControl 渲染且不可编辑，
      // 拦下来只会让"新增了一种类型"变成整张表单提交不了。
      return null
  }
}

/** 校验整张表单。 */
export function validateForm(
  schema: ParamSchema,
  values: Record<string, unknown>,
): FieldViolation[] {
  const violations: FieldViolation[] = []
  for (const field of schema.fields) {
    const violation = validateField(field, values[field.key])
    if (violation) violations.push(violation)
  }
  return violations
}

/** 便于把 violations 快速变成 `key → reason`（表单逐项显示用）。 */
export function violationsByKey(violations: FieldViolation[]): Record<string, string> {
  const out: Record<string, string> = {}
  for (const v of violations) out[v.key] = v.reason
  return out
}
