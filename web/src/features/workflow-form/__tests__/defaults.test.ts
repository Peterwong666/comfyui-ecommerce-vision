import { describe, expect, it } from 'vitest'

import inpaintSchema from '../../../test/fixtures/inpaint_v1.schema.json'
import kleinSchema from '../../../test/fixtures/flux2_klein_t2i_v1.schema.json'
import t2iSchema from '../../../test/fixtures/t2i_v1.schema.json'
import { groupFields, initialValues, isRequired, stepFor } from '../defaults'
import { SEED_RANDOM, type ParamField, type ParamSchema } from '../types'

const klein = kleinSchema as ParamSchema
const t2i = t2iSchema as ParamSchema
const inpaint = inpaintSchema as ParamSchema

describe('initialValue（经 initialValues）', () => {
  it('klein: seed 默认 -1 必须原样保留，不可归一成 0', () => {
    const values = initialValues(klein)
    // -1 = 随机，由服务端解析成真实值并回写；归一成 0 会让"随机"变成"固定第一张"
    expect(values.seed).toBe(SEED_RANDOM)
    expect(values.seed).not.toBe(0)
  })

  it('klein: 其余字段取 Schema 的 default', () => {
    const values = initialValues(klein)
    expect(values.width).toBe(1024)
    expect(values.height).toBe(1024)
    expect(values.steps).toBe(4)
    expect(values.cfg).toBe(1.0)
    expect(typeof values.prompt).toBe('string')
  })

  it('image 字段不可预填 0（default: 0 是"必填占位符"，不是默认值）', () => {
    const values = initialValues(inpaint)
    // 照抄成 0 的话，用户不选图也能提交 → 任务在素材解析处失败且【不可重试】
    expect(values.reference_image).toBeNull()
    expect(values.reference_image).not.toBe(0)
  })

  it('image_list 的初值是空数组', () => {
    const field: ParamField = {
      key: 'refs',
      label: '参考图',
      type: 'image_list',
      default: [],
      targets: [],
    }
    expect(initialValues({ schema_version: 1, fields: [field] }).refs).toEqual([])
  })

  it('bool 取布尔化后的默认值', () => {
    const field: ParamField = { key: 'flag', label: '开关', type: 'bool', default: 0, targets: [] }
    expect(initialValues({ schema_version: 1, fields: [field] }).flag).toBe(false)
  })
})

describe('stepFor', () => {
  it('int 缺 step 时兜底为 1（engine 只强制 float 带 step）', () => {
    const field: ParamField = { key: 'n', label: 'N', type: 'int', default: 1, targets: [] }
    expect(stepFor(field)).toBe(1)
  })

  it('float 缺 step 时按区间推导', () => {
    const field: ParamField = {
      key: 'f',
      label: 'F',
      type: 'float',
      default: 1,
      min: 0,
      max: 10,
      targets: [],
    }
    expect(stepFor(field)).toBeCloseTo(0.1)
  })

  it('声明了 step 就原样使用', () => {
    const field: ParamField = {
      key: 'w',
      label: 'W',
      type: 'int',
      default: 1024,
      min: 512,
      max: 2048,
      step: 64,
      targets: [],
    }
    expect(stepFor(field)).toBe(64)
  })
})

describe('isRequired', () => {
  it('图片类必填（Schema 里没有 required 字段，只能由前端判定）', () => {
    const image = inpaint.fields.find((f) => f.type === 'image') as ParamField
    expect(isRequired(image)).toBe(true)
  })

  it('其余字段都不必填（后端有 default 兜底）', () => {
    for (const field of klein.fields) {
      if (field.type === 'image' || field.type === 'image_list') continue
      expect(isRequired(field)).toBe(false)
    }
  })
})

describe('groupFields', () => {
  it('klein：按首次出现顺序分组，advanced 抽出后可见参数只剩 4 个', () => {
    const { groups, advanced } = groupFields(klein)

    expect(groups.map((g) => g.name)).toEqual(['提示词', '画布', '采样'])
    expect(advanced.map((f) => f.key)).toEqual(['steps', 'cfg'])

    // 线框图 §0.2 的设计原则「关键参数不超过 5 个可见」由此自然成立
    const visible = groups.flatMap((g) => g.fields.map((f) => f.key))
    expect(visible).toEqual(['prompt', 'width', 'height', 'seed'])
  })

  it('t2i：advanced 里含负向提示词与采样器/调度器', () => {
    const { advanced } = groupFields(t2i)
    const keys = advanced.map((f) => f.key)
    expect(keys).toContain('negative_prompt')
    expect(keys).toContain('sampler_name')
    expect(keys).toContain('scheduler')
  })

  it('没有 group 的字段归入「其他」，且不丢字段', () => {
    const schema: ParamSchema = {
      schema_version: 1,
      fields: [
        { key: 'a', label: 'A', type: 'int', default: 1, targets: [] },
        { key: 'b', label: 'B', type: 'int', default: 2, group: '编组', targets: [] },
      ],
    }
    const { groups, advanced } = groupFields(schema)
    expect(groups.map((g) => g.name)).toEqual(['其他', '编组'])
    expect(groups.flatMap((g) => g.fields).length + advanced.length).toBe(schema.fields.length)
  })

  it('advanced 缺省即常显（只有显式 true 才折叠）', () => {
    const schema: ParamSchema = {
      schema_version: 1,
      fields: [
        { key: 'a', label: 'A', type: 'int', default: 1, targets: [] },
        { key: 'b', label: 'B', type: 'int', default: 2, advanced: false, targets: [] },
        { key: 'c', label: 'C', type: 'int', default: 3, advanced: true, targets: [] },
      ],
    }
    const { advanced } = groupFields(schema)
    expect(advanced.map((f) => f.key)).toEqual(['c'])
  })
})
