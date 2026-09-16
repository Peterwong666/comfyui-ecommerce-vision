import type { ParamField } from '../types'

/**
 * 所有控件的统一入参。
 *
 * 刻意保持**极窄**：控件只负责"把一个值渲染出来、把新值报上去"。
 * 标签、帮助文案、错误提示、分组折叠都由 `WorkflowForm` 统一处理 ——
 * 否则每个控件都要重写一遍同样的 `Form.Item` 包裹，且样式必然分叉。
 *
 * ⚠️ 控件**不要**自己读 `field.targets`（那是服务端的注入位置），
 * 也不要自己判断"这个字段对某工作流是否敏感" —— 那是 `WorkflowForm` 的职责。
 */
export interface ControlProps {
  field: ParamField
  value: unknown
  onChange: (value: unknown) => void
  /** 有内容表示该字段校验未通过，控件应显示为错误态 */
  invalid?: string
  disabled?: boolean
}
