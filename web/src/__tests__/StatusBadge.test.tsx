import { render } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { BATCH_STATUS, TASK_STATUS } from '../api/enums'
import { BatchStatusBadge, TaskStatusBadge } from '../components/StatusBadge'

/**
 * 展示层的完备性检查。
 *
 * `StatusBadge` 用 `Record<TaskStatus, ...>` 已经能在**编译期**挡住遗漏，
 * 但那份保障只在"取值从 enums.ts 推导"时成立。这里再兜一层运行时的：
 * 若某状态没有对应文案，徽章会退化成显示原始英文值 ——
 * 那正是"加了状态但忘了配颜色/中文"的症状，测试应当抓住它。
 */

function labelOf(ui: React.ReactElement): string {
  const { container } = render(ui)
  return container.textContent ?? ''
}

describe('TaskStatusBadge', () => {
  it('每个任务状态都有中文文案（不会退化成显示原始英文值）', () => {
    for (const status of Object.values(TASK_STATUS)) {
      const text = labelOf(<TaskStatusBadge status={status} />)
      expect(text, `状态 ${status} 缺少展示文案`).not.toBe(status)
      expect(text.length).toBeGreaterThan(0)
    }
  })

  it('未知状态降级显示原始值，而不是崩掉', () => {
    // 后端若先上了新状态（契约变更未同步到前端），界面必须还能用
    expect(labelOf(<TaskStatusBadge status="brand_new_status" />)).toBe('brand_new_status')
  })
})

describe('BatchStatusBadge', () => {
  it('每个批量状态都有中文文案', () => {
    for (const status of Object.values(BATCH_STATUS)) {
      const text = labelOf(<BatchStatusBadge status={status} />)
      expect(text, `批量状态 ${status} 缺少展示文案`).not.toBe(status)
    }
  })

  it('partial 显示为「部分成功」——与 failed 视觉区分开', () => {
    // 500 张成功 3 张失败，标成"全部失败"会让用户以为全废
    expect(labelOf(<BatchStatusBadge status="partial" />)).toBe('部分成功')
  })
})

describe('任务与批量的状态集合不同', () => {
  it('partial 属于批量，不属于单张任务', () => {
    const taskValues: string[] = Object.values(TASK_STATUS)
    const batchValues: string[] = Object.values(BATCH_STATUS)

    expect(batchValues).toContain('partial')
    expect(taskValues).not.toContain('partial')
    // 历史上曾把两者混成"8 个状态"，导致前端按错误集合做判断
    expect(taskValues).toHaveLength(7)
    expect(batchValues).toHaveLength(7)
  })
})
