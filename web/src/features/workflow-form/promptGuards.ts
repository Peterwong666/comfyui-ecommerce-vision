/**
 * 提示词输入的**工作流级护栏**。
 *
 * 依据
 * ----
 * - `docs/sop/prompt_guide.md` §1.1（口径纪律）与 §5 待补齐项 #0
 * - 机器可读源：`assets/prompt_lib/scenes.yaml` 的 `weight_syntax_policy`（v1 文案）
 * - 源码级定论：`docs/sop/debug_log.md` **G7**
 *
 * ⚠️ 口径必须是「**严禁**」，**不能**写「无效」
 * ----------------------------------------------
 * 后者会让用户以为"写了也没关系"，而实际情况是**提示词被污染** ——
 * `KleinTokenizer` 显式 `disable_weights=True`，括号/冒号/数字会作为**字面 token**
 * 送进模型。这是两种完全不同的用户指引（prompt_guide.md §1.1 为此写错过两次）。
 *
 * ⚠️ 而且**只写「严禁」是不够的**
 * ------------------------------
 * 用户会不知道该怎么改。`scenes.yaml` 明确要求同时给**替代写法**，
 * 所以下面的文案带 examples。
 *
 * 为什么这张表在前端，而不是从接口取
 * ----------------------------------
 * 该约束写在 `workflows/registry.yaml` 的 `notes` 里，而 `notes` **没有 DB 列**
 * （被 `registry_loader` 报进 dropped 清单了），接口拿不到。
 * `prompt_guide.md` §5 正是把它指派给 **D 流（前端）+ C 流（文案）**。
 *
 * TODO(P3-10)：模板/提示词库页面接入后，应改为从提示词库读该策略，
 * 让文案有单一来源，而不是前端再维护一份。
 */

export interface PromptGuard {
  /** 警示级别。用 `warning` 而非 `error`：不拦截提交，但要显眼 */
  level: 'warning'
  /** 一句话结论，必须含「严禁」 */
  headline: string
  /** 为什么（讲清"污染"而不是"无效"） */
  reason: string
  /** 替代写法 —— 只给禁令不给方法等于把用户扔在原地 */
  alternatives: readonly { from: string; to: string }[]
}

/** 按**工作流名**索引。key 必须与注册表 id 一致。 */
const GUARDS: Readonly<Record<string, PromptGuard>> = {
  flux2_klein_t2i_v1: {
    level: 'warning',
    headline: '严禁使用权重语法，如 (white mug:1.5)',
    reason:
      '本工作流的分词器显式关闭了权重解析，括号、冒号与数字会作为**字面 token** 进入模型，' +
      '属于**污染提示词**，不是「写了无效」。',
    alternatives: [
      { from: '(white mug:1.5)', to: 'a prominently featured white mug' },
      { from: '(background:0.6)', to: 'a simple uncluttered background' },
      { from: '(logo:1.8)', to: 'the brand mark clearly visible and prominent' },
    ],
  },
  // t2i_v1（SDXL）**允许**权重语法：走 SD1 tokenizer 且未 disable。
  // 不为它配置护栏 —— 在能用权重的工作流上弹警告会训练用户忽略警告。
}

/** 取该工作流的提示词护栏；没有则返回 `undefined`（正常情况，不是错误）。 */
export function promptGuardFor(workflowName: string | null | undefined): PromptGuard | undefined {
  if (!workflowName) return undefined
  return GUARDS[workflowName]
}

/** 形如 `(word:1.2)` / `(word: 1.2)` 的权重写法。仅用于**提示**，不拦截提交。 */
const WEIGHT_SYNTAX = /\(\s*[^()]+?\s*:\s*-?\d+(?:\.\d+)?\s*\)/g

/** 检出权重写法。返回命中的片段（去重）。 */
export function findWeightSyntax(text: string): string[] {
  if (!text) return []
  return [...new Set(text.match(WEIGHT_SYNTAX) ?? [])]
}
