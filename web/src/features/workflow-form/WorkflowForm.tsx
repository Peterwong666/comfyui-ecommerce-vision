import { Alert, Button, Collapse, Form, Space } from 'antd'
import { useEffect, useMemo, useState } from 'react'

import { AdvancedSection } from './AdvancedSection'
import { resolveControl } from './controlRegistry'
import { groupFields, initialValues, isRequired } from './defaults'
import { findWeightSyntax, promptGuardFor } from './promptGuards'
import type { ParamField, ParamSchema } from './types'
import { validateForm, violationsByKey } from './validate'

export interface WorkflowFormProps {
  workflowName: string
  schema: ParamSchema
  submitting?: boolean
  /** 只回传**改动过**的参数（见下方 `collectDirtyParams` 的说明） */
  onSubmit: (params: Record<string, unknown>) => void
}

function sameValue(a: unknown, b: unknown): boolean {
  if (a === b) return true
  if (Array.isArray(a) && Array.isArray(b)) {
    return a.length === b.length && a.every((v, i) => sameValue(v, b[i]))
  }
  return false
}

/**
 * 只收集**用户真正改过**的字段。
 *
 * ⚠️ 这条规则不这样做会**静默破坏模板**：
 * 后端合并优先级是 `workflow fields[].default` < `template.preset_params` < `请求 params`
 * （契约 §3.4）。若把表单里预填的默认值全量提交，`params[prompt]` 会**覆盖掉模板预设**，
 * 而模板恰恰是产品的关键转化点（线框图 §2 的 ② 区块）。
 *
 * 判据是「touched 且值确实变了」——用户改回默认值的字段不算改动。
 */
function collectDirtyParams(
  schema: ParamSchema,
  values: Record<string, unknown>,
  touched: ReadonlySet<string>,
): Record<string, unknown> {
  const initial = initialValues(schema)
  const params: Record<string, unknown> = {}
  for (const field of schema.fields) {
    if (!touched.has(field.key)) continue
    if (sameValue(values[field.key], initial[field.key])) continue
    params[field.key] = values[field.key]
  }
  return params
}

/**
 * **由 Schema 驱动的动态表单**（P7-02）。
 *
 * 这个组件**从不** `switch (field.type)`，一律经 `resolveControl` 查注册表 ——
 * 因此「新增一条工作流」不需要改这里一行代码，那是 P7-02 的验收标准。
 *
 * 分组与折叠规则见 `defaults.ts`：按 `group` 首次出现顺序分组，
 * `advanced: true` 抽进「高级设置」（真实数据下这会让可见参数自动收敛到
 * 提示词 / 画布 / 种子，符合线框图"关键参数不超过 5 个"的设计原则）。
 */
export function WorkflowForm({ workflowName, schema, submitting, onSubmit }: WorkflowFormProps) {
  const [values, setValues] = useState<Record<string, unknown>>(() => initialValues(schema))
  const [touched, setTouched] = useState<ReadonlySet<string>>(() => new Set<string>())
  const [violations, setViolations] = useState<Record<string, string>>({})

  // 切换工作流（或 Schema 变更）时重置，避免把上一条工作流的参数带过去
  useEffect(() => {
    setValues(initialValues(schema))
    setTouched(new Set<string>())
    setViolations({})
  }, [schema])

  const { groups, advanced } = useMemo(() => groupFields(schema), [schema])
  const guard = promptGuardFor(workflowName)
  const promptText = typeof values.prompt === 'string' ? values.prompt : ''
  const detectedWeightSyntax = guard ? findWeightSyntax(promptText) : []

  const handleChange = (key: string, value: unknown) => {
    setValues((prev) => ({ ...prev, [key]: value }))
    setTouched((prev) => new Set(prev).add(key))
    setViolations((prev) => {
      if (!(key in prev)) return prev
      const next = { ...prev }
      delete next[key]
      return next
    })
  }

  const handleSubmit = () => {
    const found = validateForm(schema, values)
    if (found.length > 0) {
      setViolations(violationsByKey(found))
      return
    }
    setViolations({})
    onSubmit(collectDirtyParams(schema, values, touched))
  }

  const renderField = (field: ParamField) => {
    const Control = resolveControl(String(field.type))
    const violation = violations[field.key]

    return (
      <Form.Item
        key={field.key}
        label={field.label}
        help={field.help}
        required={isRequired(field)}
        validateStatus={violation ? 'error' : undefined}
        extra={violation}
      >
        {field.key === 'prompt' && guard && (
          <Alert
            type="warning"
            showIcon
            style={{ marginBottom: 8 }}
            message={guard.headline}
            description={
              <>
                <div>{guard.reason}</div>
                {/* ⚠️ 只写「严禁」是不够的：用户得知道该怎么改（scenes.yaml 的明确要求） */}
                <div style={{ marginTop: 8 }}>改用陈述句把「强调」表达出来：</div>
                <ul style={{ margin: '4px 0 0', paddingLeft: 20 }}>
                  {guard.alternatives.map((alt) => (
                    <li key={alt.from}>
                      <code>{alt.from}</code> → {alt.to}
                    </li>
                  ))}
                </ul>
                {detectedWeightSyntax.length > 0 && (
                  <div style={{ marginTop: 8, color: '#ff4d4f' }}>
                    检测到权重写法：{detectedWeightSyntax.join('、')} —— 提交前请改为陈述句
                  </div>
                )}
              </>
            }
          />
        )}

        <Control
          field={field}
          value={values[field.key]}
          onChange={(next) => handleChange(field.key, next)}
          invalid={violation}
          disabled={submitting}
        />
      </Form.Item>
    )
  }

  const hasViolationInAdvanced = advanced.some((f) => violations[f.key])

  return (
    <Form layout="vertical" onFinish={handleSubmit}>
      <Collapse
        defaultActiveKey={groups.map((g) => g.name)}
        items={groups.map((group) => ({
          key: group.name,
          label: group.name,
          children: <>{group.fields.map(renderField)}</>,
        }))}
      />

      <div style={{ marginTop: 16 }}>
        <AdvancedSection
          fields={advanced}
          renderField={renderField}
          defaultOpen={hasViolationInAdvanced}
        />
      </div>

      <Space style={{ marginTop: 16 }}>
        <Button type="primary" htmlType="submit" loading={submitting}>
          提交生成
        </Button>
        <Button
          onClick={() => {
            setValues(initialValues(schema))
            setTouched(new Set<string>())
            setViolations({})
          }}
          disabled={submitting}
        >
          重置
        </Button>
      </Space>
    </Form>
  )
}
