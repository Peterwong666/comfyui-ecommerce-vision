/**
 * 参数 Schema 的类型（契约 §3）。
 *
 * 这份 Schema 是实现 **P7-02「新增工作流不改前端」** 的全部依据：
 * 后端把工作流的可调参数描述成 `param_schema`，前端据此生成表单。
 * 所以这里**不能**出现任何针对具体工作流的特判（klein 的例外见 `promptGuards.ts`，
 * 那是提示词文案而非表单结构）。
 */

/**
 * 契约 §3.2 的 9 种取值。
 *
 * ⚠️ **真实数据只用到 6 种**：`text` / `int` / `float` / `enum` / `seed` / `image`。
 * `str` / `bool` / `image_list` 在 `workflows/registry.yaml` 里**出现 0 次**，
 * 只在测试夹具里出现过 `str`。这三者的控件仍然实现（为了 schema_version 前向兼容），
 * 但它们的状态是 **「待真实数据验证」** —— 不要写成"已验证"。
 */
export type FieldType =
  'int' | 'float' | 'str' | 'text' | 'bool' | 'enum' | 'seed' | 'image' | 'image_list'

/** 运行时可见的类型清单（控件注册表与测试用它做穷尽性检查）。 */
export const FIELD_TYPES: readonly FieldType[] = [
  'int',
  'float',
  'str',
  'text',
  'bool',
  'enum',
  'seed',
  'image',
  'image_list',
]

export interface ParamOption {
  value: string
  /**
   * 显示名。
   *
   * ⚠️ **渲染这个，不是 `value`**。真实数据里 `sampler_name` 有 45 个选项、
   * `scheduler` 有 9 个，其中 `dpmpp_2m → "DPM++ 2M（推荐）"`、
   * `karras → "Karras（推荐）"` 的 label 与 value **不同**。
   * 只渲染 value 会丢掉"推荐"这个关键提示。
   */
  label?: string
}

/**
 * 注入位置（契约 §3.3）—— **服务端专用**。
 *
 * 前端只做类型容错，**绝不解读 `node_id`**：节点 ID 会随工作流演进变化，
 * 把它写进前端逻辑等于把内部实现变成接口契约。
 */
export interface ParamTarget {
  node_id: string
  input: string
  transform?: 'ref_to_filename' | 'join_lines' | 'json_str'
}

export interface ParamField {
  /** 唯一键，即提交时 `params` 里的键名 */
  key: string
  label: string
  type: FieldType
  /** 契约 §3.1：**必填**，是参数合并的兜底值 */
  default: unknown
  min?: number
  max?: number
  step?: number
  options?: ParamOption[]
  /** 表单分组名。按**首次出现顺序**渲染，不要排序 —— 注册表的顺序是刻意设计的 UX 顺序 */
  group?: string | null
  help?: string
  /** 默认 `false`；只有显式 `true` 才折叠进「高级设置」 */
  advanced?: boolean
  /**
   * 条件显隐。
   *
   * ⚠️ **惰性字段，本版本不实现**：真实注册表里出现 0 次，
   * 且 `engine/schema.py` 只校验它是对象、没有任何语义。
   * 这里保留类型是为了容忍该键存在（不崩），不是为了用它。
   * 要真正启用它，需先走契约变更 + 补服务端语义。
   */
  visible_when?: { key: string; equals: unknown }
  max_length?: number
  targets?: ParamTarget[]
  /** 容忍未知键（后端返回的是原始 JSONB，`_comment` 之类会原样过来） */
  [key: string]: unknown
}

export interface ParamSchema {
  schema_version: number
  fields: ParamField[]
}

// ---------------------------------------------------------------- 常量

/** `seed` 的随机哨兵值。**绝不可归一成 0** —— 那会让"随机"变成"固定第一张"。 */
export const SEED_RANDOM = -1

/** `seed` 的合法上界（`engine/schema.py` 的 `SEED_SPACE = 2**32`）。 */
export const SEED_SPACE = 2 ** 32

/** 字段没有 `group` 时归入的面板名。 */
export const DEFAULT_GROUP = '其他'
