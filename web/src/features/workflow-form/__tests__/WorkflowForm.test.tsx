import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import kleinSchema from '../../../test/fixtures/flux2_klein_t2i_v1.schema.json'
import inpaintSchema from '../../../test/fixtures/inpaint_v1.schema.json'
import t2iSchema from '../../../test/fixtures/t2i_v1.schema.json'
import type { ParamSchema } from '../types'
import { WorkflowForm } from '../WorkflowForm'

const klein = kleinSchema as ParamSchema
const t2i = t2iSchema as ParamSchema
const inpaint = inpaintSchema as ParamSchema

function renderForm(schema: ParamSchema, workflowName: string, onSubmit = vi.fn()) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={queryClient}>
      <WorkflowForm workflowName={workflowName} schema={schema} onSubmit={onSubmit} />
    </QueryClientProvider>,
  )
  return { onSubmit, user: userEvent.setup() }
}

const submitButton = () => screen.getByRole('button', { name: '提交生成' })
const promptBox = () => screen.getByRole('textbox', { name: '正向提示词' })

/**
 * 批量改文本用 `fireEvent.change` 而不是 `userEvent.type`。
 *
 * 原因不是图快：真实默认提示词有 200 个字符，而 `userEvent.type` 会**逐字符**
 * 触发输入事件，antd 的 `showCount` 每次都要重算并重渲染 —— 一个测试要 13 秒，
 * 直接撞上超时。`fireEvent.change` 只发一次 change 事件，语义上也正是我们要测的
 * "值变了 → 表单怎么反应"。
 *
 * 需要验证"逐字符"行为的场景才该用 `userEvent.type`。
 */
function setPrompt(text: string) {
  fireEvent.change(promptBox(), { target: { value: text } })
}

describe('分组与折叠', () => {
  it('分组按首次出现顺序渲染', () => {
    renderForm(klein, 'flux2_klein_t2i_v1')
    for (const name of ['提示词', '画布', '采样']) {
      expect(screen.getByText(name)).toBeInTheDocument()
    }
  })

  it('advanced 字段收进「高级设置」，默认不可见', async () => {
    renderForm(klein, 'flux2_klein_t2i_v1')
    // klein 的 steps 与 cfg 都是 advanced
    expect(screen.getByText('高级设置（2 项）')).toBeInTheDocument()
    expect(screen.queryByText('采样步数')).toBeNull()

    // 展开后可见 —— 证明不是"渲染丢了"，只是折叠了
    await userEvent.setup().click(screen.getByText('高级设置（2 项）'))
    await waitFor(() => expect(screen.getByText('采样步数')).toBeInTheDocument())
  })
})

describe('只提交改动过的字段（保护模板预设）', () => {
  it('未改动任何字段时提交空对象', async () => {
    const { onSubmit, user } = renderForm(klein, 'flux2_klein_t2i_v1')
    await user.click(submitButton())

    // 若把预填的默认值全量提交，params[prompt] 会覆盖掉模板的 preset_params，
    // 而模板是产品的关键转化点。所以"没改就不传"是硬要求。
    await waitFor(() => expect(onSubmit).toHaveBeenCalledWith({}))
  })

  it('只改提示词时，params 里只有提示词', async () => {
    const { onSubmit, user } = renderForm(klein, 'flux2_klein_t2i_v1')

    setPrompt('a red mug on wood')
    await user.click(submitButton())

    await waitFor(() => expect(onSubmit).toHaveBeenCalledWith({ prompt: 'a red mug on wood' }))
  })

  it('改回默认值不算改动，不提交', async () => {
    const { onSubmit, user } = renderForm(klein, 'flux2_klein_t2i_v1')
    const original = (promptBox() as HTMLTextAreaElement).value

    setPrompt('temp')
    setPrompt(original) // 回到原值
    await user.click(submitButton())

    await waitFor(() => expect(onSubmit).toHaveBeenCalledWith({}))
  })
})

describe('校验拦截', () => {
  it('缺参考图时不提交，并显示错误', async () => {
    const { onSubmit, user } = renderForm(inpaint, 'inpaint_v1')
    await user.click(submitButton())

    expect(onSubmit).not.toHaveBeenCalled()
    expect(screen.getByText(/必须选择图片/)).toBeInTheDocument()
  })

  it('清空提示词会被拦（空提示词会照常排队出图，白烧一次 GPU）', async () => {
    const { onSubmit, user } = renderForm(klein, 'flux2_klein_t2i_v1')
    setPrompt('')
    await user.click(submitButton())

    expect(onSubmit).not.toHaveBeenCalled()
    expect(screen.getByText(/不能为空/)).toBeInTheDocument()
  })
})

describe('提示词护栏（klein 严禁权重语法）', () => {
  it('klein 下的**结论**口径是「严禁」而不是「无效」', () => {
    renderForm(klein, 'flux2_klein_t2i_v1')

    const headline = screen.getByText(/严禁使用权重语法/)
    // 关键在结论的用词：说"无效"会让用户以为写了没关系，而实际是污染提示词。
    // （注意：正确的文案里也会出现"无效"二字 —— 用来说明二者的区别。所以
    //  这里断言的是**结论本身**不含"无效"，不是全页面不含。）
    expect(headline.textContent ?? '').not.toMatch(/无效/)

    // 而真实的后果必须讲清楚
    expect(screen.getByText(/污染提示词/)).toBeInTheDocument()
  })

  it('klein 下给出替代写法（只给禁令不给方法等于把用户扔在原地）', () => {
    renderForm(klein, 'flux2_klein_t2i_v1')
    expect(screen.getByText(/a prominently featured white mug/)).toBeInTheDocument()
  })

  it('SDXL（t2i_v1）不显示该警示 —— 它本来就能用权重', () => {
    renderForm(t2i, 't2i_v1')
    // 在能用的工作流上弹警告会训练用户忽略警告
    expect(screen.queryByText(/严禁/)).toBeNull()
  })

  it('输入里出现权重写法时给出实时提示', async () => {
    renderForm(klein, 'flux2_klein_t2i_v1')
    setPrompt('a (mug:1.5) on wood')
    await waitFor(() => expect(screen.getByText(/检测到权重写法/)).toBeInTheDocument())
  })
})
