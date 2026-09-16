import { Tag } from 'antd'

import type { BatchStatus, TaskStatus } from '../api/enums'

/**
 * 状态徽章。
 *
 * ⚠️ **这里只放"展示"，不放"取值"**。取值集合在 `api/enums.ts`，
 * 而它是从后端 `models/enums.py` 镜像来的、由 parity 测试守着。
 * 两者分开的理由：混在一起会出现"加了状态但忘了配颜色"这类漏洞 ——
 * 分开之后，取值那边由机器守，展示这边由下面的完备性类型守。
 *
 * 配色取自线框图 §0.3：pending 灰 / queued 蓝 / running 蓝闪 /
 * retrying 橙 / succeeded 绿 / failed 红 / canceled 灰暗 / partial 橙。
 */

interface Presentation {
  label: string
  color: string
}

/** `Record<TaskStatus, ...>` 是**故意**的：少一个 key TS 就编译不过。 */
const TASK_PRESENTATION: Record<TaskStatus, Presentation> = {
  pending: { label: '待调度', color: 'default' },
  queued: { label: '排队中', color: 'blue' },
  running: { label: '执行中', color: 'processing' },
  retrying: { label: '重试中', color: 'orange' },
  succeeded: { label: '已完成', color: 'success' },
  failed: { label: '失败', color: 'error' },
  canceled: { label: '已取消', color: 'default' },
}

/** ⚠️ `partial` 只属于**批量**（父任务），不要往任务徽章里加。 */
const BATCH_PRESENTATION: Record<BatchStatus, Presentation> = {
  pending: { label: '待调度', color: 'default' },
  queued: { label: '排队中', color: 'blue' },
  running: { label: '执行中', color: 'processing' },
  succeeded: { label: '全部成功', color: 'success' },
  failed: { label: '全部失败', color: 'error' },
  canceled: { label: '已取消', color: 'default' },
  partial: { label: '部分成功', color: 'orange' },
}

function lookup(table: Record<string, Presentation>, status: string): Presentation {
  // 后端若先上了新状态（契约变更未同步到前端），降级显示原始值而不是崩掉
  return table[status] ?? { label: status, color: 'default' }
}

export function TaskStatusBadge({ status }: { status: string }) {
  const { label, color } = lookup(TASK_PRESENTATION, status)
  return <Tag color={color}>{label}</Tag>
}

export function BatchStatusBadge({ status }: { status: string }) {
  const { label, color } = lookup(BATCH_PRESENTATION, status)
  return <Tag color={color}>{label}</Tag>
}
