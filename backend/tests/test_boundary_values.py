"""PRD §6.2「边界值清单」的后端侧用例（AC-6.6 / AC-N7）。

为什么单独一个文件
------------------
§6.2 的十项此前散落在 `test_assets.py` / `test_schemas.py` / `test_api_tasks.py` /
`test_state_machine.py` 里，**没有任何一处能回答「十项都盖到了吗」**。AC-6.6 的
验收方式是「测试用例评审」——评审需要一个能逐项对照的落点，本文件就是那个落点。
对应的覆盖表（含「哪几项只有用例、哪几项连实现都没有」）在 `tests/README.md`。

本文件守什么 / 不守什么
----------------------
守：**API 与 Schema 层**能拦住哪些非法输入 —— 这是平台的第一道闸，也是用户唯一
    能看到明确报错的地方（`422` + 具体原因）。
不守：**渲染期**的边界（`engine/validate.py` 的 schema-⊆-settings 检查、
    `engine/schema.py` 的 `field.validate`）在 `engine/tests/test_engine.py` 里，
    本文件只引用、不重复。

⚠️ **提示词长度走 `params` 通道的缺口已于 2026-09-17 修复**：`tasks._validate_text_lengths`
按工作流 `param_schema` 里 `type == "text"` 的字段施加 `max_prompt_length`（**只查上界**），
且跑在 `_merge_params` **之后**（顶层与 `params` 两条通道一起覆盖）。
**原本"钉住缺口"的两条用例已翻成"钉住已修复"（断言 202 → 422）；其中"空串被拒"那条
又按 2026-09-17 的第二次裁定翻回「空串被接受」** —— 下界（`min_prompt_length`）在
`param_schema` 里**没有事实来源**可依（Schema 无 `required` / `allow_empty` 标记），
而「清空反向词」是前端早已认定的合法用法，详见 `tasks._validate_text_lengths` 的 docstring。

⚠️ **仍有一处「钉住缺口」的用例**：生成分辨率走 `params` 通道时 API 层不拒
（见第四节的 `test_generation_side_out_of_bounds_is_not_rejected_by_the_api`）。
后端在 `params` 通道上不校验它，值要等到 worker 渲染时才由引擎拒掉，那时是 EX-3
`invalid_param`、**不可重试**（用户白等一次排队）。缺口清单见 `tests/README.md`；
**修好之后请把断言从 `202` 改成 `422`** —— 这条注释指的是分辨率那一处，**尚未过期**，
不要因为"看到这句话"就删掉它。

⚠️ 关于「可被破坏后检出」：本文件每条用例都做过变异验证 —— 去掉 `TaskSubmitIn.prompt`
的 `max_length`、去掉 `_validate_params` 里的 steps/cfg 分支、把 `max_batch_size`
的 `>` 改成 `>=`、改动 `config.py` 里任一 §6.2 数值，都会有对应用例变红（见交付报告）。
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.v1 import tasks as api_tasks
from app.core.config import settings
from app.models.task import Batch, Task

# ---------------------------------------------------------------- 工具

#: 走**逻辑层**（Schema / 参数合并）的提交路径。
#: 顶层字段（`prompt` / `steps` / `cfg`）由 Pydantic 在进入路由前就校验；
#: `params` 里的同名字段由 `tasks._validate_params` 在合并之后校验 —— 两条通道
#: 的守卫不同，所以下面每条边界都分两个通道各测一次。
_TOP = "顶层字段"
_INNER = "params 通道"


def _submit(client, **body):  # type: ignore[no-untyped-def]
    return client.post("/api/v1/tasks", json={"workflow_name": "t2i_v1", **body})


def _count_tasks(db: Session) -> int:
    """当前库里的 `Task` 行数（用来断言"拒绝了就一张都没建"）。"""
    return db.scalar(select(func.count()).select_from(Task)) or 0


# ================================================================
# 一、§6.2 的十个数值 ↔ settings 的逐项钉死
# ================================================================


def test_settings_match_prd_section_6_2_literals() -> None:
    """§6.2 的每一个数字都必须与 `settings` 的默认值逐字对上。

    **为什么值得写**：`config.py` 的这些默认值就是「PRD 的边界值」在代码里的唯一
    真相。改掉其中任何一个，就等于**静默修改了产品边界** —— 而且不会有任何测试变红，
    因为别处的用例都是**引用** `settings` 的（例如「上限+1 被拒」写成
    `settings.max_steps + 1`），settings 改了它们跟着一起漂。

    所以这里把 PRD 的字面值**硬编码**一遍：这是刻意的"双写"，作用是让
    「PRD 改了但代码没改」和「代码改了但 PRD 没改」都能被检出。
    """
    # 上传图片：大小 100KB – 20MB
    assert settings.max_upload_min_kb == 100
    assert settings.max_upload_size_mb == 20
    # 上传图片：分辨率 64×64 – 8192×8192
    assert settings.min_image_side == 64
    assert settings.max_image_side == 8192
    # 批量规模：单任务张数 1 – 1000（>1000 拒绝）
    assert settings.max_batch_size == 1000
    # 提示词长度：1 – 2000 字符
    assert settings.min_prompt_length == 1
    assert settings.max_prompt_length == 2000
    # steps：1 – 100
    assert settings.min_steps == 1
    assert settings.max_steps == 100
    # CFG：1.0 – 20.0
    assert settings.min_cfg == 1.0
    assert settings.max_cfg == 20.0
    # 生成分辨率长边：512 – 2048（V1）
    assert settings.min_gen_side == 512
    assert settings.max_gen_side == 2048
    # 重试次数：默认 3，上限 5
    assert settings.task_max_retries == 3
    assert settings.task_max_retries_hard_limit == 5
    # 单张超时：60 – 600，默认 300
    assert settings.task_timeout_min == 60
    assert settings.task_timeout_seconds == 300
    assert settings.task_timeout_max == 600
    # 队列深度：软阈值 10000（超过 → 告警仍接单）；硬阈值 = 2 × 软阈值 = 20000（超过 → 拒收）
    assert settings.queue_max_depth == 10000


def test_timeout_triple_is_ordered() -> None:
    """下限 ≤ 默认 ≤ 上限。三条任一被改成自相矛盾的值都会在这里变红。"""
    assert (
        settings.task_timeout_min
        <= settings.task_timeout_seconds
        <= settings.task_timeout_max
    )


def test_retry_hard_limit_is_a_ceiling_over_the_default() -> None:
    """PRD §6.2「默认 3，上限 5」的两个数之间的关系：默认必须落在上限之内。

    ⚠️ **诚实标注（缺口）**：`task_max_retries_hard_limit` 目前**没有任何代码读取**。
    除 `config.py` 的定义与**测试自身**外，全仓库只有 `contracts.md` §1.1 的一句声明
    （`grep -rn task_max_retries_hard_limit` 即证据 —— **不要去数"共几处"**，
    那个数会随测试文件的增删而变，数出来的结论随时会过期）。
    也就是说「上限 5」在实现里**不是一个可执行的约束**，只是一份配置声明 ——
    `mark_retrying`（`app/services/state_machine.py:175`）只看 `task_max_retries`。

    因此本用例能钉住的只有「配置自身的自洽」这一层，**不能**读成
    「重试被硬上限拦住」。缺口记在 `tests/README.md` 的边界值覆盖表里。
    """
    assert settings.task_max_retries <= settings.task_max_retries_hard_limit


# ================================================================
# 二、提示词长度 1 – 2000（§6.2）
#
# 两条通道的守卫**不同**，所以都要测：
# - 顶层：`TaskSubmitIn.prompt` 的 `max_length` + `_check_prompt` 下限
#   （`app/schemas/task.py:23-45`，Pydantic 在进路由前就拦）—— **上下界都查**；
# - `params`：`tasks._validate_text_lengths` 在**参数合并之后**按工作流 Schema 的
#   `type == "text"` 字段判（它同时覆盖顶层通道 —— 顶层值会收敛进 `params`）。
#   ⚠️ **只查上界**：下界在 `param_schema` 里没有事实来源，故不施加
#   （裁定与理由见 `tasks._validate_text_lengths` 的 docstring）。
#
# ⚠️ `params` 侧的判据是**工作流 Schema 驱动**的，所以必须用一个**声明了 text 字段**的
# 工作流来测。`conftest.workflow`（`t2i_v1`）的最小 Schema 里没有 text 字段，
# 拿它测只会得到"没声明就不查"的结论 —— 测不到修复本身。
# ================================================================

#: 声明了文本字段的工作流名（见下面的 `text_workflow` fixture）。
_TEXT_WF = "t2i_text_v1"


@pytest.fixture
def text_workflow(db: Session):  # type: ignore[no-untyped-def]
    """一个 `param_schema` 里声明了 `type: text` 字段的工作流。

    第二个文本字段的键名**故意不叫** `prompt` / `negative_prompt`（叫 `caption`）：
    这样"后端是按 Schema 的 `type` 判、还是按写死的键名判"就变成一个**可证伪**的问题
    —— 写死键名的实现会让 `caption` 那条用例变绿（本该变红）。
    """
    from app.models.workflow import Workflow

    wf = Workflow(
        name=_TEXT_WF,
        version=1,
        display_name="文本字段边界",
        definition={"_meta": {}, "1": {"class_type": "KSampler", "inputs": {"steps": 20}}},
        param_schema={
            "schema_version": 1,
            "fields": [
                {
                    "key": "prompt",
                    "label": "正向提示词",
                    "type": "text",
                    "default": "默认提示词",
                    "max_length": 2000,
                    "targets": [],
                },
                {
                    "key": "caption",
                    "label": "另一段文本（键名与 prompt 无关）",
                    "type": "text",
                    "default": "默认文案",
                    "max_length": 2000,
                    "targets": [],
                },
            ],
        },
        is_active=True,
    )
    db.add(wf)
    db.commit()
    return wf


def _submit_text(client, **body):  # type: ignore[no-untyped-def]
    """走**声明了 text 字段**的工作流提交。"""
    return client.post("/api/v1/tasks", json={"workflow_name": _TEXT_WF, **body})


@pytest.mark.parametrize("length", [settings.min_prompt_length, settings.max_prompt_length])
def test_top_level_prompt_at_bounds_is_accepted(client, length: int) -> None:  # type: ignore[no-untyped-def]
    """闭区间：1 与 2000 **本身**必须通过（PRD 写的是「1 – 2000」）。"""
    resp = _submit(client, prompt="p" * length)
    assert resp.status_code == 202, resp.text


def test_top_level_prompt_over_max_length_is_rejected(client) -> None:  # type: ignore[no-untyped-def]
    """上界 +1（2001 字符）必须被拒。"""
    resp = _submit(client, prompt="p" * (settings.max_prompt_length + 1))
    assert resp.status_code == 422, resp.text


def test_top_level_empty_prompt_is_rejected(client) -> None:  # type: ignore[no-untyped-def]
    """下界 -1（0 字符 = 空串）必须被拒。

    空提示词是**唯一一种"必然浪费一次 GPU"**的输入：它不报错，会照常排队、照常出图，
    只是出的是一张无意义的图（这个判断写在前端 `validate.ts` 的注释里）。
    """
    resp = _submit(client, prompt="")
    assert resp.status_code == 422, resp.text


# --- `params` 通道（2026-09-17 修复：此前这里断言 202，是「钉住缺口」） ----------


def test_params_prompt_over_max_length_is_rejected(
    client, db: Session, text_workflow
) -> None:  # type: ignore[no-untyped-def]
    """✅ **已修复**：`params["prompt"]` 上界 +1（2001 字符）→ **422**（原来是 202）。

    修复前的事实（当时本用例断言 202）：`TaskSubmitIn.params` 是裸的
    `dict[str, Any]`，`_validate_params` 又只看 `steps` / `cfg`，于是 2001 字符
    **真的落库**、直到 worker 渲染时才被引擎拒（EX-3，不可重试，用户白等一次排队）。

    现在由 `tasks._validate_text_lengths` 按工作流 Schema 在合并**之后**拦下。
    断言三件事，缺一不可：
    1. 422；
    2. **结构化 detail**（`code` 用大写下划线、`fields` 指到具体字段名）；
    3. **一张任务都没建**（不能"拒了还落库"）。

    ⚠️ 期望值写字面量（`"长度 2001 超过上限 2000"`），不从实现里的 f-string 推导 ——
    否则实现改错文案时用例会跟着一起漂。
    """
    too_long = "p" * (settings.max_prompt_length + 1)
    resp = _submit_text(client, params={"prompt": too_long})

    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    assert isinstance(detail, dict), f"detail 不是对象形式：{detail!r}"
    # 字面量 + 模块常量各断言一次：既钉住规格本身，也钉住"码只有一处定义"
    assert detail["code"] == "TEXT_LENGTH_OUT_OF_RANGE"
    assert detail["code"] == api_tasks.TEXT_LENGTH_OUT_OF_RANGE_CODE
    assert detail["fields"] == [
        {"key": "prompt", "reason": "长度 2001 超过上限 2000"}
    ]
    assert db.scalar(select(func.count()).select_from(Task)) == 0, "被拒却落了库"


def test_params_prompt_empty_is_accepted(client, db: Session, text_workflow) -> None:  # type: ignore[no-untyped-def]
    """⭐ `params["prompt"] = ""` **被接受**（2026-09-17 第二次裁定，此前断言 422）。

    「下界不可表达」：`_validate_text_lengths` **只查上界** ——
    `param_schema` 里没有 `required` / `allow_empty` 之类的标记，所以
    "哪个文本字段允许为空"**没有事实来源**，按字段名硬编码会造出第二份真相。
    详见 `tasks._validate_text_lengths` 的 docstring。

    ⚠️ 不要据此认为"空提示词没问题"：这条钉的是**一般文本字段**（`params` 通道）。
    顶层 `prompt` 的 `min_prompt_length = 1` 是既有行为，仍会拒空串
    （`test_top_level_empty_prompt_is_rejected`），那条**没有变**。
    ⚠️ 也**不是**「字段必填」：字段**不传**同样合法（`_merge_params` 用 `default` 兜底）。
    """
    resp = _submit_text(client, params={"prompt": ""})

    assert resp.status_code == 202, resp.text


@pytest.mark.parametrize("key", ["prompt", "caption"])
def test_params_text_field_at_upper_bound_is_accepted(
    client, key: str, text_workflow
) -> None:  # type: ignore[no-untyped-def]
    """闭区间：文本字段取 `max_prompt_length`（= 2000）本身必须通过（两个键名各测一遍）。

    只测 2001 被拒会漏掉一类错误：把比较写成 `>=`（或把 `<` 用反）的实现
    会连 2000 一起拒 —— 那是个"边界比 PRD 更严"的静默收紧。

    ⚠️ 这里**不再**参数化 `min_prompt_length`：下界已不由本函数施加
    （见 `tasks._validate_text_lengths`），把 1 当成"下界"来测会把一个
    **已不存在的规则**写成判据。空串（0）的用例是上面那条 `..._empty_is_accepted`。
    """
    resp = _submit_text(client, params={key: "p" * settings.max_prompt_length})
    assert resp.status_code == 202, f"{key} 长度 {settings.max_prompt_length}: {resp.text}"


def test_params_caption_over_max_length_is_rejected(
    client, db: Session, text_workflow
) -> None:  # type: ignore[no-untyped-def]
    """⭐ **键名无关**：`caption`（不是 `prompt` / `negative_prompt`）同样受长度约束。

    这条是"实现到底是不是 Schema 驱动"的判据：如果把它写成按 **写死的键名**
    （`payload.get("prompt")` / `payload.get("negative_prompt")`）判，`caption` 会漏过去，
    本用例变红。字段名在 `param_schema` 里，不在代码里 —— 后者是第二份真相。
    """
    resp = _submit_text(
        client, params={"caption": "c" * (settings.max_prompt_length + 1)}
    )

    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["fields"] == [
        {"key": "caption", "reason": "长度 2001 超过上限 2000"}
    ]


def test_params_negative_prompt_can_be_cleared(
    client, db: Session, user, text_workflow
) -> None:  # type: ignore[no-untyped-def]
    """⭐ **回归钉**：`params["negative_prompt"] = ""`（清空反向词）**必须 202**。

    这是本次裁定要保住的那条**产品行为** —— 前端
    `web/src/features/workflow-form/validate.ts` 的 `REQUIRED_TEXT_KEYS` 只含 `prompt`，
    并注明"清空反向词是合法用法"。此前后端对**所有**文本字段施加 `min_prompt_length`，
    导致同一条规则在后端得到**相反**的答案（422）。

    断言"真的建了任务"而不只是状态码：若哪天有人把下界加回来，这条会变红。
    """
    resp = _submit_text(client, params={"negative_prompt": ""})

    assert resp.status_code == 202, resp.text
    assert db.scalar(select(func.count()).select_from(Task)) == 1, "放行了却没建任务"


def test_params_prompt_length_is_checked_on_the_batch_channel(
    client, db: Session, user, text_workflow
) -> None:  # type: ignore[no-untyped-def]
    """批量提交同样受约束（提示词只可能来自 `common_params`），且**整批被拒**。

    批量的提示词是整批共用的，所以校验点只有 `common_params` 一处；
    但批量路径是**另一段代码**（`submit_batch` 里那次 `_validate_params` 调用），
    只测单任务路径会漏掉它。
    """
    user.quota_total = 100
    db.commit()

    resp = client.post(
        "/api/v1/batches",
        json={
            "workflow_name": _TEXT_WF,
            "sku_assets": {"SKU-A": []},
            "images_per_sku": 2,
            "common_params": {"prompt": "p" * (settings.max_prompt_length + 1)},
        },
    )

    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["code"] == "TEXT_LENGTH_OUT_OF_RANGE"
    assert db.scalar(select(func.count()).select_from(Task)) == 0, "被拒却落了库"
    assert db.scalar(select(func.count()).select_from(Batch)) == 0, "被拒却建了批量"


def test_workflow_without_text_field_is_not_checked(client, db: Session) -> None:  # type: ignore[no-untyped-def]
    """Schema 驱动的另一面：**工作流没声明 text 字段时，这个键不受长度约束**。

    这不是"漏"，而是设计：没有 target 的参数**根本进不了图**（`engine/render.py`
    只按 `param_schema.targets` 注入），校验一个惰性键只会让调用方莫名其妙被拒。
    `conftest.workflow`（`t2i_v1`）的最小 Schema 里恰好没有任何 text 字段，
    正好用来钉这条边界。

    ⚠️ **它同时是"有没有写死键名"的反向判据**：把实现改成无条件检查 `prompt` 键，
    本用例会变红（因为 `t2i_v1` 没有声明它）。
    ⚠️ 注册表里的 5 个真实工作流**都**声明了 `type: text` 的 `prompt` /`negative_prompt`
    （`workflows/registry.yaml`），所以这条边界不影响真实产品路径。
    """
    resp = _submit(client, params={"prompt": "p" * (settings.max_prompt_length + 1)})
    assert resp.status_code == 202, resp.text


# ================================================================
# 三、steps 1 – 100 ／ CFG 1.0 – 20.0（§6.2）
#
# 这两项是**两条通道都守着**的：顶层由 Pydantic 的 `ge`/`le` 拦
# （`app/schemas/task.py:33-34`），`params` 通道由 `tasks._validate_params` 拦。
# 所以这里两通道各测一遍 —— 只测一条会漏掉另一条的回归。
# ================================================================


@pytest.mark.parametrize(
    ("field", "lo", "hi"),
    [
        ("steps", settings.min_steps, settings.max_steps),
        ("cfg", settings.min_cfg, settings.max_cfg),
    ],
)
def test_top_level_numeric_at_bounds_is_accepted(client, field: str, lo, hi) -> None:  # type: ignore[no-untyped-def]
    """闭区间：下界与上界本身必须通过（顶层通道）。"""
    for value in (lo, hi):
        resp = _submit(client, **{field: value})
        assert resp.status_code == 202, f"{field}={value}: {resp.text}"


@pytest.mark.parametrize(
    ("field", "lo", "hi"),
    [
        ("steps", settings.min_steps, settings.max_steps),
        ("cfg", settings.min_cfg, settings.max_cfg),
    ],
)
def test_top_level_numeric_out_of_bounds_is_rejected(client, field: str, lo, hi) -> None:  # type: ignore[no-untyped-def]
    """越界两侧（下界-1 / 上界+1）必须被拒（顶层通道）。"""
    for value in (lo - 1, hi + 1):
        resp = _submit(client, **{field: value})
        assert resp.status_code == 422, f"{field}={value}: {resp.text}"


@pytest.mark.parametrize(
    ("field", "lo", "hi"),
    [
        ("steps", settings.min_steps, settings.max_steps),
        ("cfg", settings.min_cfg, settings.max_cfg),
    ],
)
def test_params_numeric_at_bounds_is_accepted(client, field: str, lo, hi) -> None:  # type: ignore[no-untyped-def]
    """闭区间（`params` 通道）：`_validate_params` 必须放行边界值本身。

    若有人把 `_validate_params` 的比较写成开区间（`<` / `>` 用反），
    或把 `<=` 写成 `<`，这一条会变红。
    """
    for value in (lo, hi):
        resp = _submit(client, params={field: value})
        assert resp.status_code == 202, f"params.{field}={value}: {resp.text}"


@pytest.mark.parametrize(
    ("field", "lo", "hi"),
    [
        ("steps", settings.min_steps, settings.max_steps),
        ("cfg", settings.min_cfg, settings.max_cfg),
    ],
)
def test_params_numeric_out_of_bounds_is_rejected(client, field: str, lo, hi) -> None:  # type: ignore[no-untyped-def]
    """越界两侧必须被拒（`params` 通道）。

    这是**防绕过的关键一条**：前端 `validate.ts` 也检查 steps/cfg，但前端可被绕过
    （直接打 API）。去掉 `_validate_params` 里的 steps 或 cfg 分支，本用例立刻变红。
    """
    for value in (lo - 1, hi + 1):
        resp = _submit(client, params={field: value})
        assert resp.status_code == 422, f"params.{field}={value}: {resp.text}"


# ================================================================
# 四、生成分辨率长边 512 – 2048（§6.2）
# ================================================================


@pytest.mark.parametrize("side", [settings.min_gen_side, settings.max_gen_side])
def test_generation_side_at_bounds_is_accepted(client, side: int) -> None:  # type: ignore[no-untyped-def]
    """边界值本身必须通过 —— 这一半是**期望**，可以照着修。"""
    resp = _submit(client, params={"width": side, "height": side})
    assert resp.status_code == 202, resp.text


def test_generation_side_out_of_bounds_is_not_rejected_by_the_api(client) -> None:  # type: ignore[no-untyped-def]
    """🔴 **缺口用例（钉现状）**：生成分辨率越界时，**API 层不会拒**。

    `min_gen_side` / `max_gen_side`（`config.py:98-99`）在全仓库的唯一读取点是
    `engine/validate.py:422-423` 的 `SETTINGS_BOUNDS_MAPPING` —— 它把这两个数变成
    **工作流 param_schema 的一致性地板**（Schema 可以更严，不可以更宽），
    并**不**拿用户传来的 `params["width"]` 去比。

    所以 `width=4096`（PRD 的上界是 2048）会一路走到落库，直到 worker 渲染时
    才被 `engine/schema.py` 的 `field.validate` 以 `RenderError` 拒掉 ——
    那是 EX-3 `invalid_param`、**不可重试**，用户白等一次排队。

    这与前端 `web/src/features/workflow-form/validate.ts` 头部的自述完全一致：
    「后端的 `_validate_params` **只查顶层 `steps` 与 `cfg`**，`width` / `height` /
    `denoise` / `scale_by` / … 一概不查」—— 本次实测把这句话钉成了可执行判据。

    ⚠️ 修好之后请把断言改成 `422`。
    """
    for value in (settings.min_gen_side - 1, settings.max_gen_side + 1, 4096):
        resp = _submit(client, params={"width": value, "height": value})
        assert resp.status_code == 202, f"width={value}: {resp.text}"


# ================================================================
# 五、批量规模：单任务张数 1 – 1000（§6.2）
#
# `max_batch_size`（**总张数**）与 `max_sku_count`（**SKU 数**）是两个维度，
# SKU 侧已由 `test_schemas.py` / `test_api_tasks.py` 覆盖，本文件只补总张数的
# **边界值本身**（既有的两条用例都是把配置改小来测判据，没测过 1000 这个数）。
# ================================================================


def test_batch_total_images_at_upper_bound_is_accepted(client, db: Session, user, no_broker) -> None:  # type: ignore[no-untyped-def]
    """恰好 `max_batch_size` 张（1000）必须通过 —— 闭区间的上界本身。

    形如 `10 SKU × 100 张` 的构造会撞 `images_per_sku` 自己的 `le=20`，
    所以这里用 `100 SKU × 10 张 = 1000`。

    断言不只看 202：还要求**真的落库 1000 行、真的派发 1000 次**。
    只看状态码会漏掉"接了但没建任务"这类回归。
    """
    user.quota_total = settings.max_batch_size
    db.commit()

    sku_count = 100
    per_sku = settings.max_batch_size // sku_count  # 10
    resp = client.post(
        "/api/v1/batches",
        json={
            "workflow_name": "t2i_v1",
            "sku_assets": {f"SKU-{i}": [] for i in range(sku_count)},
            "images_per_sku": per_sku,
        },
    )
    assert resp.status_code == 202, resp.text
    assert resp.json()["total_count"] == settings.max_batch_size
    assert len(no_broker) == settings.max_batch_size

    stored = db.scalar(
        select(func.count()).select_from(Task).where(Task.batch_id == resp.json()["id"])
    )
    assert stored == settings.max_batch_size


def test_batch_total_images_over_upper_bound_is_rejected(client, db: Session, user) -> None:  # type: ignore[no-untyped-def]
    """上界 +1（1001 张）必须被拒，且**一张都不建**（不留半截批量）。

    ⚠️ 构造上有两个坑，写在这里免得后人踩：
    ① 1001 不是 SKU 数 × `images_per_sku` 的任意组合都能凑出来 ——
       `images_per_sku` 上限是 20，所以用 `143 SKU × 7 = 1001`；
    ② 额度必须**故意给够**（`>= 1001`）。否则先撞 402 配额，
       这条用例就变成"在测配额"而不是"在测张数上限"，
       而且会掩盖真正的回归。
    """
    user.quota_total = settings.max_batch_size + 100
    db.commit()

    sku_count, per_sku = 143, 7
    assert sku_count * per_sku == settings.max_batch_size + 1

    before = _count_tasks(db)
    resp = client.post(
        "/api/v1/batches",
        json={
            "workflow_name": "t2i_v1",
            "sku_assets": {f"SKU-{i}": [] for i in range(sku_count)},
            "images_per_sku": per_sku,
        },
    )
    assert resp.status_code == 422, resp.text
    assert str(settings.max_batch_size) in resp.text
    assert _count_tasks(db) == before


def test_batch_single_image_is_accepted(client, db: Session, user) -> None:  # type: ignore[no-untyped-def]
    """下界本身（1 张）必须通过。"""
    user.quota_total = 10
    db.commit()
    resp = client.post(
        "/api/v1/batches",
        json={"workflow_name": "t2i_v1", "sku_assets": {"SKU-A": []}, "images_per_sku": 1},
    )
    assert resp.status_code == 202, resp.text
    assert resp.json()["total_count"] == 1


# ================================================================
# 六、队列深度（§6.2）
#
# ⚠️ 本节**只有配置值钉死**（上面那条 `test_settings_match_prd_section_6_2_literals`）。
# **行为用例在 `backend/tests/test_queue_guard.py`** —— 2026-09-17 起 `queue_max_depth`
# 有两个消费点（软阈值告警 / 硬阈值熔断，见 `app/api/v1/tasks.py::_guard_queue`），
# 所以「全仓库无读取点」这句当时为真的话**已经失效**，此处不再重复它。
# 本节刻意**不写**一条"看起来在测队列深度"的假用例：真正的闸门判据（深度与阈值的
# 大小关系、检查排在扣额之前）全部在 `test_queue_guard.py` 里钉。
# ================================================================
