import { describe, expect, it } from 'vitest'

import inpaintSchema from '../../../test/fixtures/inpaint_v1.schema.json'
import kleinSchema from '../../../test/fixtures/flux2_klein_t2i_v1.schema.json'
import t2iSchema from '../../../test/fixtures/t2i_v1.schema.json'
import { initialValues } from '../defaults'
import { SEED_SPACE, type ParamField, type ParamSchema } from '../types'
import { validateField, validateForm, violationsByKey } from '../validate'

const klein = kleinSchema as ParamSchema
const t2i = t2iSchema as ParamSchema
const inpaint = inpaintSchema as ParamSchema

function field(schema: ParamSchema, key: string): ParamField {
  const found = schema.fields.find((f) => f.key === key)
  if (!found) throw new Error(`fixture 里没有字段 ${key}`)
  return found
}

describe('seed —— 合法域是 -1 或 [0, 2^32)', () => {
  const seed = field(klein, 'seed')

  it('-1（随机）合法', () => {
    expect(validateField(seed, -1)).toBeNull()
  })

  it('0 合法（不是"未设置"的意思）', () => {
    expect(validateField(seed, 0)).toBeNull()
  })

  it('上界内合法', () => {
    expect(validateField(seed, SEED_SPACE - 1)).toBeNull()
  })

  it('上界外非法', () => {
    expect(validateField(seed, SEED_SPACE)).not.toBeNull()
  })

  it('-1 以下的负数非法', () => {
    expect(validateField(seed, -2)).not.toBeNull()
  })

  it('小数非法', () => {
    expect(validateField(seed, 1.5)).not.toBeNull()
  })
})

describe('数值边界', () => {
  it('mask_expand 的 min 是负数（-32），必须按 Schema 取而不是假设 min>=0', () => {
    const maskExpand = field(inpaint, 'mask_expand')
    expect(maskExpand.min).toBeLessThan(0)
    expect(validateField(maskExpand, -32)).toBeNull() // 下界本身合法
    expect(validateField(maskExpand, -33)).not.toBeNull() // 越界
    expect(validateField(maskExpand, 0)).toBeNull()
  })

  it('width 越下界被拦', () => {
    const width = field(klein, 'width')
    expect(validateField(width, 512)).toBeNull()
    expect(validateField(width, 256)).not.toBeNull()
    expect(validateField(width, 4096)).not.toBeNull()
  })

  it('int 不接受小数', () => {
    expect(validateField(field(klein, 'width'), 1024.5)).not.toBeNull()
  })

  it('float 的小数字段不接受超出 [min,max]', () => {
    const cfg = field(klein, 'cfg')
    expect(validateField(cfg, 1.0)).toBeNull()
    expect(validateField(cfg, 2.0)).toBeNull()
    expect(validateField(cfg, 2.1)).not.toBeNull()
    expect(validateField(cfg, 0.9)).not.toBeNull()
  })

  it('刻意不校验 step 对齐：合法值不会被浮点误差误杀', () => {
    // cfg: min=1.0 / step=0.1。1.1 在二进制里并不精确，
    // 若按 step 对齐校验会把这个完全合法的值判为非法 —— 假报错比不报错更伤信任
    const cfg = field(klein, 'cfg')
    expect(validateField(cfg, 1.1)).toBeNull()
    expect(validateField(cfg, 1.3)).toBeNull()
  })

  it('非数字被拦', () => {
    expect(validateField(field(klein, 'width'), '1024')).not.toBeNull()
    expect(validateField(field(klein, 'width'), null)).not.toBeNull()
    expect(validateField(field(klein, 'width'), Number.NaN)).not.toBeNull()
  })
})

describe('图片类', () => {
  const image = field(inpaint, 'reference_image')

  it('null（未选择）被拦 —— 这是"必填"的实现方式', () => {
    expect(validateField(image, null)).not.toBeNull()
  })

  it('0 被拦（default 里的 0 是占位符，不是合法素材 ID）', () => {
    expect(validateField(image, 0)).not.toBeNull()
  })

  it('正整数的素材 ID 通过', () => {
    expect(validateField(image, 42)).toBeNull()
  })

  it('image_list 空数组被拦，非空通过', () => {
    const list: ParamField = {
      key: 'refs',
      label: '参考图',
      type: 'image_list',
      default: [],
      targets: [],
    }
    expect(validateField(list, [])).not.toBeNull()
    expect(validateField(list, [1, 2])).toBeNull()
    expect(validateField(list, [0])).not.toBeNull()
  })
})

describe('文本', () => {
  it('prompt 为空被拦 —— 空提示词会照常排队出图，白烧一次 GPU', () => {
    const prompt = field(klein, 'prompt')
    expect(validateField(prompt, '')).not.toBeNull()
    expect(validateField(prompt, '   ')).not.toBeNull()
    expect(validateField(prompt, 'a mug')).toBeNull()
  })

  it('negative_prompt 允许为空（清空反向词是合法用法）', () => {
    const neg = field(t2i, 'negative_prompt')
    expect(validateField(neg, '')).toBeNull()
  })

  it('超过 max_length 被拦', () => {
    const prompt = field(klein, 'prompt')
    expect(prompt.max_length).toBeGreaterThan(0)
    expect(validateField(prompt, 'x'.repeat((prompt.max_length as number) + 1))).not.toBeNull()
  })
})

describe('enum', () => {
  const sampler = field(t2i, 'sampler_name')

  it('合法取值通过', () => {
    expect(validateField(sampler, sampler.options?.[0].value)).toBeNull()
  })

  it('不在 options 里的取值被拦', () => {
    expect(validateField(sampler, '__not_a_sampler__')).not.toBeNull()
  })
})

describe('未知类型放行', () => {
  it('不认识的 type 不拦（它由 FallbackControl 只读渲染；拦了会让整张表单提交不了）', () => {
    const weird: ParamField = {
      key: 'x',
      label: 'X',
      type: 'quantum' as ParamField['type'],
      default: 1,
      targets: [],
    }
    expect(validateField(weird, undefined)).toBeNull()
  })
})

describe('validateForm', () => {
  it('整表通过：klein 的初始值（seed=-1、无图片字段）', () => {
    expect(validateForm(klein, initialValues(klein))).toEqual([])
  })

  it('整表失败：inpaint 初始值缺参考图', () => {
    const violations = validateForm(inpaint, initialValues(inpaint))
    expect(violations.map((v) => v.key)).toContain('reference_image')
  })

  it('violationsByKey 便于逐字段显示', () => {
    const map = violationsByKey(validateForm(inpaint, initialValues(inpaint)))
    expect(map.reference_image).toBeTruthy()
  })
})
