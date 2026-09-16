import type { ComponentType } from 'react'

import { EnumControl } from './controls/EnumControl'
import { FallbackControl } from './controls/FallbackControl'
import { ImageControl } from './controls/ImageControl'
import { ImageListControl } from './controls/ImageListControl'
import { NumberControl } from './controls/NumberControl'
import { SeedControl } from './controls/SeedControl'
import { SwitchControl } from './controls/SwitchControl'
import { TextAreaControl } from './controls/TextAreaControl'
import { TextControl } from './controls/TextControl'
import type { ControlProps } from './controls/types'
import type { FieldType } from './types'

/**
 * **类型 → 控件**的注册表。
 *
 * 这是 P7-02「新增工作流不改前端」的落脚点：`WorkflowForm` **从不** `switch (field.type)`，
 * 一律查这张表。于是：
 *
 * | 变化 | 需要改的东西 |
 * |---|---|
 * | 工作流新增字段（用的是已有类型） | **零改动** ✅ |
 * | 新增一种 `type` | 这里加一行 + `types.ts` 的联合类型加一项 |
 * | 后端先上了新类型 | 走 `FallbackControl`，**不崩** |
 */
export const controlRegistry: Record<FieldType, ComponentType<ControlProps>> = {
  int: NumberControl,
  float: NumberControl,
  str: TextControl,
  text: TextAreaControl,
  bool: SwitchControl,
  enum: EnumControl,
  seed: SeedControl,
  image: ImageControl,
  image_list: ImageListControl,
}

/**
 * 解析控件。**唯一**允许把"未知类型"变成可渲染组件的地方。
 *
 * 入参刻意收成 `string` 而不是 `FieldType`：运行时从 JSON 来的值不受 TS 保护，
 * 若签名只收 `FieldType`，就会有人写出 `controlRegistry[field.type]` 然后拿到
 * `undefined`，在渲染时才炸。
 */
export function resolveControl(type: string): ComponentType<ControlProps> {
  return controlRegistry[type as FieldType] ?? FallbackControl
}
