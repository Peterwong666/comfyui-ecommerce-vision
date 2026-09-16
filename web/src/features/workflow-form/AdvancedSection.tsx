import { Collapse } from 'antd'
import type { ReactNode } from 'react'

import type { ParamField } from './types'

interface AdvancedSectionProps {
  fields: ParamField[]
  /** 渲染单个字段（由 `WorkflowForm` 提供，保证与常显字段同一套渲染逻辑） */
  renderField: (field: ParamField) => ReactNode
  /** 默认是否展开。有校验错误时应展开，否则用户看不到是哪个字段红了 */
  defaultOpen?: boolean
}

/**
 * 「高级设置」折叠区。
 *
 * 为什么要有它：线框图 §0.2 的设计原则之一是
 * **「关键参数不超过 5 个可见」**（画像① 的痛点就是"参数看不懂"）。
 * 而真实注册表已经把 `negative_prompt` / `steps` / `cfg` / `sampler_name` /
 * `scheduler` 都标了 `advanced: true`，所以折叠之后
 * **只剩提示词、画布、随机种子可见** —— 这条设计原则自然成立，
 * 不需要为某个工作流做特判。
 *
 * `defaultOpen` 由调用方在有校验错误时置真：折叠区里藏着红字段是最气人的 bug。
 */
export function AdvancedSection({
  fields,
  renderField,
  defaultOpen = false,
}: AdvancedSectionProps) {
  if (fields.length === 0) return null

  return (
    <Collapse
      key={defaultOpen ? 'open' : 'closed'}
      defaultActiveKey={defaultOpen ? ['advanced'] : []}
      items={[
        {
          key: 'advanced',
          label: `高级设置（${fields.length} 项）`,
          children: <>{fields.map((field) => renderField(field))}</>,
        },
      ]}
    />
  )
}
