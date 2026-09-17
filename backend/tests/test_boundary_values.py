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

⚠️ **本文件同时钉住两处「后端没拦」的缺口**（提示词长度 / 生成分辨率走 `params`
通道）。这两条用例**不是在声明期望，而是在钉住现状**：后端在 `params` 通道上不校验
这两项，值要等到 worker 渲染时才由引擎拒掉，那时是 EX-3 `invalid_param`、**不可重试**
（用户白等一次排队）。缺口清单见 `tests/README.md`；**修好之后请把断言从 `202`
改成 `422`**，不要留下一条"为了绿而绿"的用例。

⚠️ 关于「可被破坏后检出」：本文件每条用例都做过变异验证 —— 去掉 `TaskSubmitIn.prompt`
的 `max_length`、去掉 `_validate_params` 里的 steps/cfg 分支、把 `max_batch_size`
的 `>` 改成 `>=`、改动 `config.py` 里任一 §6.2 数值，都会有对应用例变红（见交付报告）。
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.task import Task

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
    # 队列深度：≤ 10000
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
    全仓库检索只有两处：`config.py` 的定义、`contracts.md` §1.1 的一句声明。
    也就是说「上限 5」在实现里**不是一个可执行的约束**，只是一份配置声明 ——
    `mark_retrying`（`app/services/state_machine.py:175`）只看 `task_max_retries`。

    因此本用例能钉住的只有「配置自身的自洽」这一层，**不能**读成
    「重试被硬上限拦住」。缺口记在 `tests/README.md` 的边界值覆盖表里。
    """
    assert settings.task_max_retries <= settings.task_max_retries_hard_limit


# ================================================================
# 二、提示词长度 1 – 2000（§6.2）
#
# `TaskSubmitIn.prompt` 有 `max_length=settings.max_prompt_length` +
# `_check_prompt` 的下限校验（`app/schemas/task.py:23-45`）；
# 而 `params["prompt"]` 是**另一条通道**（契约 §3.4 允许只传它），
# 它绕过了 Pydantic 字段级约束 —— 见本节后半的缺口用例。
# ================================================================


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


def test_params_prompt_channel_ignores_max_length(client, db: Session) -> None:  # type: ignore[no-untyped-def]
    """🔴 **缺口用例（钉现状，不是期望）**：`params["prompt"]` 超长**不会被拒**。

    `TaskSubmitIn.params` 的声明是裸的 `dict[str, Any]`（`app/schemas/task.py:30`），
    没有任何逐键约束；`tasks._validate_params`（`app/api/v1/tasks.py:86-103`）
    只查 `steps` 与 `cfg`。于是走 `params` 通道时，`max_prompt_length` 这条边界
    **在后端完全不存在**。

    这条不是推断，是实测：`2001` 字符经校验后**真的落库**了（下面断言 `Task` 行存在
    且长度就是 2001）—— 所以它不是"被别处拦住了只是状态码不同"。

    ⚠️ 修好之后请把本用例的断言改成 `422`（并删掉这一段说明），
    同时更新 `tests/README.md` 的边界值覆盖表。
    """
    too_long = "p" * (settings.max_prompt_length + 1)
    resp = _submit(client, params={"prompt": too_long})
    assert resp.status_code == 202, resp.text

    task = db.get(Task, resp.json()["id"])
    assert task is not None
    assert len(task.params["prompt"]) == settings.max_prompt_length + 1


def test_params_prompt_channel_ignores_min_length(client) -> None:  # type: ignore[no-untyped-def]
    """🔴 **缺口用例（钉现状）**：`params["prompt"] = ""` 同样不会被拒。

    与上一条同源（`_validate_params` 不看提示词）。空串经 `_merge_params` 的
    `if v is not None` 判断会被保留（`""` 不是 `None`），因此一路走到落库。
    """
    resp = _submit(client, params={"prompt": ""})
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
# 六、队列深度 ≤ 10000（§6.2）
#
# ⚠️ **本项没有行为用例**：`queue_max_depth` 在全仓库没有任何读取点，
# 限流（含队列深度）是**未实现**的能力 —— `todolist.md` P6-08 行与
# `项目进展.md` 都把它列在「明确未做」里。所以本节唯一能测的就是上面那条
# 配置值钉死（`test_settings_match_prd_section_6_2_literals`）。
# 这里刻意**不写**一条"看起来在测队列深度"的假用例。
# ================================================================
