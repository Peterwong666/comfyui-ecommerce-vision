import { Alert, Input } from 'antd'

import type { ControlProps } from './types'

/**
 * **未知类型**的兜底控件。
 *
 * 这条路径存在的唯一理由：`param_schema` 是后端返回的**原始 JSONB**，
 * 而 `schema_version` 将来会升。当后端新增了 `type` 而前端还没跟上时，
 * 有两条路：
 *
 * - 崩掉 / 整张表单不可用 ← 一个字段的类型不认识，就让整个工作流没法用
 * - **降级成"只读 + 明确告警"** ← 用户仍然能改其他参数，且知道发生了什么
 *
 * 选后者。注意它**不参与校验**（`validate.ts` 对未知类型放行）——
 * 因为前端根本不知道合法域是什么，报错只会更误导。
 */
export function FallbackControl({ field, value }: ControlProps) {
  return (
    <Alert
      type="warning"
      showIcon
      message={`不支持的参数类型：${String(field.type)}`}
      description={
        <div>
          <div style={{ marginBottom: 8 }}>
            该字段已降级为只读。可能是后端 Schema 已升级而前端尚未跟进 ——
            其余参数不受影响，仍可正常提交。
          </div>
          <Input value={JSON.stringify(value)} readOnly />
        </div>
      }
    />
  )
}
