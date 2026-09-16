import { describe, expect, it } from 'vitest'

import { controlRegistry, resolveControl } from '../controlRegistry'
import { FallbackControl } from '../controls/FallbackControl'
import { FIELD_TYPES } from '../types'

describe('controlRegistry', () => {
  it('契约 §3.2 的 9 种类型全部有控件', () => {
    expect(FIELD_TYPES).toHaveLength(9)
    for (const type of FIELD_TYPES) {
      expect(controlRegistry[type], `缺少 ${type} 对应的控件`).toBeTruthy()
    }
  })

  it('已知类型不会被 FallbackControl 顶替', () => {
    for (const type of FIELD_TYPES) {
      expect(resolveControl(type)).not.toBe(FallbackControl)
    }
  })

  it('未知类型落到 FallbackControl —— 后端先上新类型时不能崩', () => {
    // 这是 schema_version 升级时的前向兼容路径：
    // 一个字段不认识，不应该让整条工作流不可用
    expect(resolveControl('__not_a_type__')).toBe(FallbackControl)
    expect(resolveControl('')).toBe(FallbackControl)
  })
})
