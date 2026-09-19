"""任务接口（FR-2.5 / FR-3.4 / FR-4.1~4.5）。

本模块只做「落库 + 派发 + 查询」，**不做实际执行** ——
执行在 Celery worker（P6）。这样 API 的响应时间可与 GPU 完全解耦，
满足 NFR-1「提交响应 ≤200ms」。
"""

from __future__ import annotations

import math
from typing import Annotated, Any

# seed 的递增口径由工作流内核统一定义（契约 §2.4），A 流不自己算 ——
# 两边各有一套取模/边界语义迟早分叉。
from engine import RenderError, derive_seed
from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, select

from app.api.deps import CurrentUser, DbSession
from app.core.config import settings
from app.core.logging import bind_task, get_logger
from app.models.asset import Asset
from app.models.enums import BatchStatus, ErrorType, TaskStatus
from app.models.event import AuditLog, EventName
from app.models.task import Batch, Task
from app.models.workflow import Template, Workflow
from app.schemas.task import (
    BatchOut,
    BatchSubmitIn,
    CancelOut,
    TaskEstimateOut,
    TaskOut,
    TaskSubmitIn,
)
from app.services import content_safety, quota
from app.services import state_machine as sm
from app.services.dispatch import enqueue_task
from app.services.events import track
from app.services.image_probe import has_alpha
from app.services.storage import AssetStorage, MinioAssetStorage

log = get_logger(__name__)
router = APIRouter(tags=["tasks"])


# ---------------------------------------------------------------- 内部工具


def _storage() -> AssetStorage:
    """构造存储实例。

    抽成函数是为了让调用点只有一处 `MinioAssetStorage()` —— 测试通过替换本模块的
    `MinioAssetStorage` 属性注入内存实现（与 `assets.py` 的 `_storage` 同构）。
    """
    return MinioAssetStorage()


def _get_active_workflow(db: DbSession, name: str) -> Workflow:
    wf = db.scalar(
        select(Workflow)
        .where(Workflow.name == name, Workflow.is_active.is_(True))
        .order_by(Workflow.version.desc())
    )
    if wf is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"工作流 {name} 不存在或未启用",
        )
    return wf


# ---------------------------------------------------------------- 队列深度闸门（EX-5）


# 硬阈值 = `settings.queue_max_depth` × 本系数。
# ① 为什么复用同一个配置而不是新增一个：运维只需调**一个旋钮**。软/硬两层本来就该
#    同向变化，拆成两个配置只会带来「改了一个忘了另一个」的不一致状态。
# ② 为什么是 2 倍：给软层告警留出**从告警到熔断的缓冲带** —— 看到告警后运维还有
#    一倍的余量介入（扩容 / 查原因 / 限速上游），不会告警与熔断同时发生。
# ③ 将来若确实需要独立调节，把它提升为配置项是**低成本**的（下一行是这个系数
#    在全仓库的唯一读取点，改一处即可）。
_QUEUE_HARD_LIMIT_FACTOR = 2

# 「在途」= 已入队但尚未到达终态的三种状态。
# ⚠️ 与 `worker/tasks.py::requeue_orphans` 关注的状态集合保持同源（它看 `queued` /
# `retrying`），这里**多一个 `running`**：队列深度回答的是「还要等多久轮到我」，
# 正在跑的那张同样在占 GPU，不算进去就会低估等待时间。
_IN_FLIGHT_STATUSES = (
    TaskStatus.QUEUED.value,
    TaskStatus.RUNNING.value,
    TaskStatus.RETRYING.value,
)

# 单张基准耗时的兜底值（秒）：库里还没有成功样本时用哪个数来估等待。
# 与 `/tasks/estimate` **共用同一个常量**，不各写一份字面量。
_DEFAULT_SECONDS_PER_IMAGE = 20.0

# 结构化 `detail.code` —— 这两个都**不是** `ErrorType`（不对应 PRD §6.1 的任何 EX 编号），
# 按 `docs/sop/contracts.md` §2.2 的约定用**大写下划线**命名，与枚举值一眼可区分
# （枚举值是全小写，见 `app/models/enums.py`）。
# ⚠️ 不要把它们写成 `ErrorType` 的成员：枚举是跨流契约（前端镜像由
# `test_web_enum_parity.py` 双向守着），往里面塞非 EX 的码会造出第二个真相来源。
TOO_MANY_IN_FLIGHT_CODE = "TOO_MANY_IN_FLIGHT"
TEXT_LENGTH_OUT_OF_RANGE_CODE = "TEXT_LENGTH_OUT_OF_RANGE"
SIZE_OUT_OF_RANGE_CODE = "SIZE_OUT_OF_RANGE"
REQUIRES_ALPHA_CODE = "REQUIRES_ALPHA"


def _in_flight_task_count(db: DbSession) -> int:
    """当前**全局**在途任务数（不区分用户）。

    为什么按全局而不是按用户：GPU 并发固定为 1（NFR-2），所有用户共用同一条队列。
    按用户判定会得到「每个用户都没超限、合起来把队列撑爆」的结果，闸门形同虚设。
    """
    return int(
        db.scalar(
            select(func.count()).select_from(Task).where(Task.status.in_(_IN_FLIGHT_STATUSES))
        )
        or 0
    )


def _seconds_per_image(db: DbSession, workflow_id: int) -> float:
    """单张基准耗时（秒）—— **与 `/tasks/estimate` 共用同一口径**。

    历史成功任务的实测均值，没有样本时退回 `_DEFAULT_SECONDS_PER_IMAGE`。
    抽成函数而不是两处各写一遍：同一个事实算两遍，迟早会对不上
    （本项目已多次踩这个坑型）。
    """
    return _avg_seconds_per_image(db, workflow_id) or _DEFAULT_SECONDS_PER_IMAGE


def _guard_queue(db: DbSession, workflow_id: int) -> None:
    """EX-5 队列深度闸门：**软阈值告警 + 硬阈值熔断**。

    | 层 | 触发条件 | 行为 | 为什么 |
    |---|---|---|---|
    | 软 | 在途 > `settings.queue_max_depth` | `log.warning`（带当前深度），**照常接单（202）** | C12「排队比崩掉好」；且 `queue_full` 在契约 §1.5 里是**可重试**错误类型 —— 此时拒绝用户是错的 |
    | 硬 | 在途 > `2 × queue_max_depth` | **422 + `code=queue_full`**，`message` 给出排队数与预计等待 | 队列无限增长会拖垮 DB / 内存；让用户等到「天荒地老」不如明确拒绝并给出**真实的等待预估** |

    边界：**恰好等于硬阈值时放行** —— 两层都是「超过才动作」，与 PRD §6.2
    「队列深度 ≤ 上限」的闭区间口径一致（`test_queue_guard.py` 把它钉死）。

    ⚠️ **必须在 `_charge_quota` 之前调用**：被队列拒绝的请求**绝不能扣额度、
    也绝不能建任务** —— 否则用户「被拒还掉额度」。也正因为如此，本函数只读不写。
    """
    soft = settings.queue_max_depth
    hard = soft * _QUEUE_HARD_LIMIT_FACTOR
    depth = _in_flight_task_count(db)

    if depth > hard:
        wait_minutes = max(1, math.ceil(depth * _seconds_per_image(db, workflow_id) / 60))
        log.warning("queue.hard_limit_rejected depth=%s soft=%s hard=%s", depth, soft, hard)
        # 契约 §2.2：需要前端分支处理时 `detail` 用**对象形式**；`code` 取
        # `ErrorType` 的取值（唯一事实来源，见 models/enums.py），**不写字面量** ——
        # 否则同一个码会出现第二个真相来源。EX-5 的 `message` 必须含
        # 「当前排队 N 个」与「预计等待 X 分钟」两条用户可见信息（PRD §6.1）。
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": ErrorType.QUEUE_FULL.value,
                "message": f"当前排队 {depth} 个，预计等待 {wait_minutes} 分钟",
                "fields": [
                    {
                        "key": "queue_depth",
                        "reason": f"在途 {depth} 个，已超过硬上限 {hard} 个",
                    }
                ],
            },
        )

    if depth > soft:
        log.warning("queue.soft_limit_exceeded depth=%s soft=%s hard=%s", depth, soft, hard)


def _in_flight_task_count_for_user(db: DbSession, user_id: int) -> int:
    """**某一个用户**当前的在途任务数。

    与 `_in_flight_task_count`（全局）**共用 `_IN_FLIGHT_STATUSES`**：口径只有一处。
    「在途」= 已入队但未到终态（`queued` / `running` / `retrying`）；`pending` 不算
    （还没入队，不占队列），终态也不算。
    """
    return int(
        db.scalar(
            select(func.count())
            .select_from(Task)
            .where(Task.user_id == user_id, Task.status.in_(_IN_FLIGHT_STATUSES))
        )
        or 0
    )


def _guard_user_in_flight(db: DbSession, user: CurrentUser, needed: int) -> None:
    """公平性闸门：**每用户在途任务数上限**（`settings.max_in_flight_per_user`）。

    GPU 全局并发恒为 1（NFR-2），队列是**所有用户共用**的一条。全局队列熔断（EX-5）
    只在**整体**过载时拒人，拦不住"一个用户把队列占满" —— 别人要排在他那 1000 张后面，
    体验上等于服务不可用。本函数补的是**分配**这一侧。

    | 条件 | 行为 |
    |---|---|
    | `当前在途 + 本次需要 > 上限` | **422 + `code=TOO_MANY_IN_FLIGHT`** |
    | 恰好等于上限 | **放行**（与 `max_batch_size` / 队列硬阈值同为闭区间读法） |

    ⚠️ **整批判定**：判据用的是**本批的总张数**，不是"能塞几张算几张"。
    用户已有 999 在途、本次提交 2 张 → 拒绝（而不是放行 1 张）—— 与既有的
    「批量整体成功或整体失败」口径一致（`_charge_quota` 也是一次扣 `total`）。
    允许部分受理会让"提交了 2 张只出 1 张"，用户无从预期。

    ⚠️ **必须在 `_charge_quota` 之前调用**：被拒的请求**不扣额度、也不建任务**。
    本函数只读不写。

    ⚠️ **只加在 `POST /tasks` 与 `POST /batches`**：`retry` / `retry-failed` **不加** ——
    重试**不新增任务**（那批任务本来就在库里，提交时已经计过在途数了），
    对它们再加这道门等于**同一个任务被计两次**：用户有 999 个失败任务待重跑时，
    重试会被误判成"在途超限"。重试要防的是**队列总量**（走 `_guard_queue`），不是新增。
    """
    limit = settings.max_in_flight_per_user
    current = _in_flight_task_count_for_user(db, user.id)

    if current + needed > limit:
        log.warning(
            "user_in_flight.rejected user_id=%s current=%s needed=%s limit=%s",
            user.id,
            current,
            needed,
            limit,
        )
        # 契约 §2.2：需要前端分支处理时 `detail` 用**对象形式**。
        # `message` 必须是**具体**的（当前在途 N / 本次需要 M / 上限 L）——
        # 只写「超过上限」用户既不知道差多少，也不知道该等多久。
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": TOO_MANY_IN_FLIGHT_CODE,
                "message": (
                    f"在途任务过多：当前在途 {current} 个，本次需要 {needed} 张，"
                    f"上限 {limit} 张；请等已有任务跑完或取消后再提交"
                ),
                "fields": [
                    {
                        "key": "in_flight",
                        "reason": f"在途 {current} + 本次 {needed} > 上限 {limit}",
                    }
                ],
            },
        )


def _charge_quota(db: DbSession, user: CurrentUser, needed: int) -> None:
    """EX-12：扣减额度，不足则 402。

    **判定与扣减是同一条条件 UPDATE**（`app/services/quota.py`），不是
    「先读 `quota_remaining` 比较、再 `+=`」—— 后者是两个并发请求各读各写时的
    丢更新来源，会让额度被超发。这里只负责把「不够」翻译成 HTTP 错误，
    **不要在本函数之外再判一次额度**（两套判据迟早分叉）。

    ⚠️ **计额口径（FR-1.2）**：额度在**提交时预扣**，按**任务数（张数）**计；
    `retry` / `retry-failed` 按**新一次生成**计额（重跑会重新消耗 GPU）；
    **终态失败 / 取消不退还**。口径与理由见 `docs/prd/PRD_v1.md` FR-1.2 ——
    本函数只负责扣，不做任何退还分支。
    """
    if quota.charge(db, user_id=user.id, needed=needed):
        return

    # `charge()` 已经从库里回读过，这里拿到的是**真值**而不是内存里的旧值
    remaining = user.quota_remaining
    message = f"额度不足：需要 {needed}，剩余 {remaining}"
    # 契约 §2.2：需要前端分支处理时用**对象形式**的 detail。
    # `code` 取 `ErrorType` 的取值（唯一事实来源，见 models/enums.py），
    # 不写字面量 —— 否则同一个码会出现第二个真相来源。
    raise HTTPException(
        status_code=status.HTTP_402_PAYMENT_REQUIRED,
        detail={
            "code": ErrorType.QUOTA_EXCEEDED.value,
            "message": message,
            "fields": [{"key": "count", "reason": f"需要 {needed}，剩余 {remaining}"}],
        },
    )


def _text_fields(wf: Workflow) -> list[dict[str, Any]]:
    """工作流 `param_schema` 里声明为 `type == "text"` 的字段（原始 dict 列表）。

    **为什么不硬编码键名**：提示词字段的**名字与数量是工作流自己的事**。
    `workflows/registry.yaml` 里 5 个工作流各自声明了 `prompt` / `negative_prompt`，
    但没有任何东西保证"文本字段只有这两个" —— 硬编码键名就是**第二个真相来源**：
    以后某个工作流新增一个文本字段，前端按 Schema 渲染出输入框，后端却悄悄不校验它。
    这类"两边各读一套"的分叉**不会报错**，只会漏，正是本项目最怕的失效形态。
    """
    fields = (wf.param_schema or {}).get("fields") or []
    return [f for f in fields if isinstance(f, dict) and f.get("type") == "text"]


def _validate_text_lengths(wf: Workflow, payload: dict[str, Any]) -> None:
    """按工作流 Schema 对**文本字段**施加长度**上界**（§6.2 的 `max_prompt_length` = 2000）。

    修的是这个缺口：顶层 `prompt` 由 `TaskSubmitIn` 的 `max_length` 拦住了，
    但 `params["prompt"]` 是**另一条通道**（契约 §3.4 允许只传它），
    而 `_validate_params` 此前只看 `steps` / `cfg` —— 于是 2001 字符的
    `params.prompt` 一路落库，直到 worker 渲染时才被引擎以 EX-3 `invalid_param`
    拒掉（**不可重试**，用户白等一次排队）。

    ⚠️ **本函数在 `_merge_params` 之后调用**：顶层字段已收敛进 `params`
    （`submit_task` 里那段赋值），所以这里查 `params` 就**同时覆盖两条通道**，
    不需要在顶层再写一遍。

    ⚠️ **只查上界，不查下界**（2026-09-17 裁定）：
    - 2026-09-17 早先的版本**同时**施加了 `settings.min_prompt_length`（= 1），
      副作用是 `negative_prompt: ""`（清空反向词）会拿到 422。而前端
      `web/src/features/workflow-form/validate.ts` 的既有裁定是
      「**清空反向词是合法用法**」（它的 `REQUIRED_TEXT_KEYS` 只含 `prompt`）——
      同一条产品规则出现了两个相反的答案，必须收口。
    - **收口到「不查下界」而不是「按字段名豁免 `negative_prompt`」**，理由是事实来源：
      契约 §3 的参数 Schema **没有** `required` / `allow_empty` 之类的标记，
      因此「**哪个文本字段允许为空**」在数据里**没有事实来源**。按字段名硬编码
      `prompt` / `negative_prompt` 会在 `param_schema` 之外造出**第二份真相**
      （同 `_text_fields` 的说明）：以后某个工作流新增文本字段时两边必然分叉，
      而且这类分叉**不会报错，只会漏**。
    - ⚠️ **遗留项：下界目前无法表达，故未启用。** 现状是空串对**一般文本字段**
      是合法输入。等 `param_schema` 引入显式标记（如 `required` / `allow_empty`）后，
      再据此启用下界 —— **本函数不在契约 §3 的 Schema 格式定义之外自造标记**
      （加标记属于契约变更，不是实现变更）。
    - ⚠️ 顶层 `prompt` 的 `min_prompt_length = 1` 是**既有行为，保持不变**：那是
      顶层字段（`TaskSubmitIn._check_prompt`，`app/schemas/task.py`），不是 Schema 里
      的一般文本字段 ——「空提示词必然浪费一次 GPU」这条判断只对顶层通道成立。
    - **不要求字段出现**：字段不在 `params` 里就跳过。工作流的 `param_schema` 没有
      `required` 标记，而每个字段的 `default` 又由 `_merge_params` 兜底，
      所以"没传"本来就是合法状态 —— 这里**不会**把可选文本变成必填。
    - 非字符串**放行**：类型不对由引擎在渲染期拒；而且 `len()` 打在 `int` 上会
      把本该是 422 的输入变成 500。
    """
    hi = settings.max_prompt_length
    for field in _text_fields(wf):
        key = field.get("key")
        if not key or key not in payload:
            continue
        value = payload[key]
        if not isinstance(value, str):
            continue
        if len(value) > hi:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                # 契约 §2.2：非 `ErrorType` 的码用**大写下划线**（见模块顶部的常量说明）。
                detail={
                    "code": TEXT_LENGTH_OUT_OF_RANGE_CODE,
                    "message": f"{key} 长度不能超过 {hi} 个字符，当前 {len(value)}",
                    "fields": [
                        {
                            "key": key,
                            "reason": f"长度 {len(value)} 超过上限 {hi}",
                        }
                    ],
                },
            )


def _int_fields(wf: Workflow) -> list[dict[str, Any]]:
    """工作流 `param_schema` 里声明为 `type == "int"` 的字段（原始 dict 列表）。"""
    fields = (wf.param_schema or {}).get("fields") or []
    return [f for f in fields if isinstance(f, dict) and f.get("type") == "int"]


def _image_fields(wf: Workflow) -> list[dict[str, Any]]:
    """工作流 `param_schema` 里声明为 `type == "image"` 的字段（原始 dict 列表）。"""
    fields = (wf.param_schema or {}).get("fields") or []
    return [f for f in fields if isinstance(f, dict) and f.get("type") == "image"]


def _validate_size_bounds(wf: Workflow, payload: dict[str, Any]) -> None:
    """按工作流 Schema 对**整型字段**施加 min/max 边界（EX-3 的扩展）。

    前端已经拦过一次，但后端必须再校验。与 `_validate_params` 里的 `steps` / `cfg`
    不同：后者是**全局**边界，而这里按字段在 `param_schema` 里声明的 min/max 校验
    （如 i2i_v1 的 `width` / `height` 各是 512–2048）。

    ⚠️ 字段值必须是整数或能安全转成整数；非整数类型放行（类型不匹配由引擎渲染期
    以 EX-3 拒绝，这里避免把 422 变成 500）。
    """
    for field in _int_fields(wf):
        key = field.get("key")
        if not key or key not in payload:
            continue
        value = payload[key]
        try:
            numeric = int(value)
        except (TypeError, ValueError):
            continue

        lo = field.get("min")
        hi = field.get("max")
        if (lo is not None and numeric < lo) or (hi is not None and numeric > hi):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": SIZE_OUT_OF_RANGE_CODE,
                    "message": f"{key} 需在 {lo}-{hi} 之间，当前 {numeric}",
                    "fields": [
                        {
                            "key": key,
                            "reason": f"值 {numeric} 超出范围 [{lo}, {hi}]",
                        }
                    ],
                },
            )


def _validate_image_requirements(
    wf: Workflow, payload: dict[str, Any], db: DbSession, user: CurrentUser
) -> None:
    """按工作流 Schema 对**图片字段**施加额外要求（当前仅 alpha 通道）。

    例如 inpaint_v1 的 `reference_image` 声明了 `requires_alpha: true`：
    用户上传普通 RGB 时，ComfyUI 的 `LoadImage` 会导出全零 mask，导致局部重绘
    退化成「原样输出」的静默 no-op。后端在提交阶段必须拒绝这种输入。

    校验链路：
      1. 取 params 里的 asset id（image 字段的值）
      2. 查 DB 确认素材存在、属于当前用户、未被软删除
      3. 从对象存储读原始字节
      4. 用 `image_probe.has_alpha()` 判是否有 alpha（只读头部，无 Pillow 依赖）
    """
    for field in _image_fields(wf):
        if not field.get("requires_alpha"):
            continue
        key = field.get("key")
        if not key or key not in payload:
            continue
        asset_id = payload[key]
        try:
            asset_id = int(asset_id)
        except (TypeError, ValueError):
            # 非整数交给引擎层以 EX-3 处理；这里不抛 500。
            continue

        asset = db.scalar(
            select(Asset).where(
                Asset.id == asset_id,
                Asset.user_id == user.id,
                Asset.is_deleted.is_(False),
            )
        )
        if asset is None:
            # 素材不存在/越权/已删除：按 404 处理，与 assets.py 的 `_owned_asset` 口径一致
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"素材不存在：{key}={asset_id}",
            )

        try:
            with _storage() as storage:
                data = storage.get(asset.object_key)
        except Exception as exc:  # noqa: BLE001 - 存储实现多样，统一转成可读错误
            log.error("task.image_requirements.get_failed asset_id=%s err=%s", asset.id, exc)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"读取素材失败：{key}，请稍后重试",
            ) from exc

        if not has_alpha(data):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": REQUIRES_ALPHA_CODE,
                    "message": f"{key} 必须使用带透明通道（alpha）的 PNG",
                    "fields": [
                        {
                            "key": key,
                            "reason": "图片无 alpha 通道，局部重绘无法确定蒙版区",
                        }
                    ],
                },
            )


def _validate_params(
    wf: Workflow, payload: dict[str, Any], db: DbSession, user: CurrentUser
) -> None:
    """参数二次校验（EX-3）。

    前端已经拦过一次，但后端必须再校验 —— 前端校验可被绕过，
    而非法参数进入工作流会导致 ComfyUI 报难以理解的错误。

    ⚠️ 传 `wf` 是为了按**工作流的 Schema** 校验文本字段长度
    （见 `_validate_text_lengths`：`steps` / `cfg` 是全局边界值，文本长度则取决于
    该工作流声明了哪些 `type: text` 字段）。
    """
    steps = payload.get("steps")
    if steps is not None and not (settings.min_steps <= int(steps) <= settings.max_steps):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"步数需在 {settings.min_steps}-{settings.max_steps}",
        )
    cfg = payload.get("cfg")
    if cfg is not None and not (settings.min_cfg <= float(cfg) <= settings.max_cfg):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"CFG 需在 {settings.min_cfg}-{settings.max_cfg}",
        )
    _validate_text_lengths(wf, payload)
    _validate_size_bounds(wf, payload)
    _validate_image_requirements(wf, payload, db, user)


def _merge_params(
    wf: Workflow, template: Template | None, payload: dict[str, Any]
) -> dict[str, Any]:
    """参数优先级：工作流默认 < 模板预设 < 用户显式传入。"""
    merged: dict[str, Any] = {}
    for field in (wf.param_schema or {}).get("fields", []):
        if isinstance(field, dict) and "key" in field and "default" in field:
            merged[field["key"]] = field["default"]
    if template is not None:
        merged.update(template.preset_params or {})
    merged.update({k: v for k, v in (payload or {}).items() if v is not None})
    return merged


# ---------------------------------------------------------------- 内容安全（P6-13）


def _screen_content(params: dict[str, Any]) -> dict[str, list[content_safety.RuleHit]]:
    """输入侧内容安全检查（P6-13 / FR-9.4 的输入侧部分）。

    ⚠️ 必须在 `_merge_params` **之后**调用：`params` 是提示词的唯一事实来源
    （契约 §3.4），顶层 `prompt` / `negative_prompt` 都已收敛进去。只查顶层字段会漏掉
    `params["prompt"]` 这条通道 —— 而它此前**没有任何**长度或内容校验（顶层字段至少还有
    `max_length`）。

    命中硬红线 → **422**。不发明 451/403-内容码：契约 §2.2 的状态码表里 422 就是
    「参数校验失败」，多一个表外的码就多一条前端分支 —— 这与上传校验因表里没有 413 而
    统一用 422 是同一个取舍（见 `assets.py` 头部说明）。
    命中 warn → **放行**，命中清单返回给调用方写审计（`_record_content_warnings`）。

    开关 `settings.content_safety_enabled=False` 时**完全不检查**（不拦、也不留痕）。
    """
    if not settings.content_safety_enabled:
        return {}

    hits_by_field = content_safety.check_params(params)
    if any(hit.blocks for hits in hits_by_field.values() for hit in hits):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=content_safety.blocked_detail(hits_by_field),
        )
    return hits_by_field


def _record_content_warnings(
    db: DbSession,
    user: CurrentUser,
    warned: dict[str, list[content_safety.RuleHit]],
    params: dict[str, Any],
    *,
    target_type: str,
    target_id: int,
) -> None:
    """给 warn 级命中留痕（FR-1.4 审计）。

    🔴 **隐私红线**：`detail` 里只有规则 id / hash / 长度 / 阶段 / 字段名，
    **没有命中的原词、也没有提示词全文** —— `tracking_plan.md` §1.2 规定提示词可能含
    商品名/品牌等敏感信息，**只记长度与 hash**。payload 一律由
    `content_safety.warn_detail` 构造，本函数不做任何字符串拼接：这样就没有一个
    "顺手把 `params[field]` 塞进去"的位置。
    """
    for field, hits in warned.items():
        db.add(
            AuditLog(
                user_id=user.id,
                action=content_safety.WARN_AUDIT_ACTION,
                target_type=target_type,
                target_id=str(target_id),
                detail=content_safety.warn_detail(field, hits, params[field]),
            )
        )


# ---------------------------------------------------------------- 单次生成


@router.post(
    "/tasks",
    response_model=TaskOut,
    status_code=status.HTTP_202_ACCEPTED,
    summary="提交单次生成（FR-2.5）",
)
def submit_task(payload: TaskSubmitIn, user: CurrentUser, db: DbSession) -> Task:
    wf = _get_active_workflow(db, payload.workflow_name)
    # EX-5 队列闸门**必须先于扣额度**：被队列拒绝的请求不能扣额、也不能建任务。
    _guard_queue(db, wf.id)
    # 公平性闸门（每用户在途上限），同样**必须先于扣额度**：被拒 = 不扣额 + 不建任务。
    _guard_user_in_flight(db, user, 1)
    # 计额口径（FR-1.2）：提交时预扣 1 张；终态失败/取消不退（见 _charge_quota 的说明）
    _charge_quota(db, user, 1)

    template = None
    if payload.template_id is not None:
        template = db.get(Template, payload.template_id)
        if template is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="模板不存在")

    params = _merge_params(wf, template, payload.params)
    if payload.steps is not None:
        params["steps"] = payload.steps
    if payload.cfg is not None:
        params["cfg"] = payload.cfg
    if payload.seed is not None:
        params["seed"] = payload.seed
    # 提示词收敛为**单通道**（契约 §3.4：顶层优先）。
    # 此前顶层 prompt 只落到 Task.prompt 列、params["prompt"] 另行合并，
    # 两条通道可以传出不同的值，注入工作流的那个与落库的那个会不一致。
    # 现在以 params["prompt"] 为唯一事实来源，Task.prompt 只是它的冗余副本。
    if payload.prompt is not None:
        params["prompt"] = payload.prompt
    if payload.negative_prompt is not None:
        params["negative_prompt"] = payload.negative_prompt
    # 在参数合并**之后**校验：顶层 prompt / negative_prompt 已收敛进 params，
    # 所以这里一次就覆盖两条通道（见 `_validate_text_lengths`）。
    _validate_params(wf, params, db, user)
    # 内容安全放在参数合并之后：两条提示词通道（顶层 / params）都已收敛进 params
    warned = _screen_content(params)

    task = sm.create_task(
        db,
        user_id=user.id,
        workflow_id=wf.id,
        template_id=template.id if template else None,
        params=params,
        # 从 params 回读，保证「落库的」与「注入图的」永远是同一个值
        prompt=params.get("prompt"),
        negative_prompt=params.get("negative_prompt"),
        # ⚠️ `Task.seed` 是 `params["seed"]` 的**派生投影，不是第二条输入通道**
        # （契约 §3.4：params 是唯一事实来源）。
        # 它保留成独立列，是因为它是一等可查询属性（列表筛选、看板统计要用）；
        # 但**任何写入都必须来自 params**，不要再往它写别的值 ——
        # 曾经 `Task.seed` 与 `params["seed"]` 是两条独立通道，可以传出不一致的结果。
        seed=params.get("seed"),
        priority=settings.default_priority,
        idempotency_key=payload.idempotency_key,
    )

    sm.enqueue(db, task)

    track(
        db,
        EventName.TASK_SUBMITTED,
        user_id=user.id,
        task_id=task.id,
        workflow=wf.name,
        batch_size=1,
        mode="single",
        template_id=template.id if template else None,
    )
    db.add(
        AuditLog(user_id=user.id, action="task.submit", target_type="task", target_id=str(task.id))
    )
    _record_content_warnings(
        db, user, warned, params, target_type="task", target_id=task.id
    )
    db.commit()

    # 派发放在 commit 之后：如果先派发后提交失败，worker 会拿不到任务
    enqueue_task(task.id, task.priority)
    return task


@router.post("/tasks/estimate", response_model=TaskEstimateOut, summary="提交前预估（FR-3.3）")
def estimate(payload: BatchSubmitIn, user: CurrentUser, db: DbSession) -> TaskEstimateOut:
    """给出张数 / 预计耗时 / 预计成本。

    设计师的核心风险是「跑完才发现成本超预算」，所以预估必须在提交之前。
    """
    wf = _get_active_workflow(db, payload.workflow_name)
    total = payload.total_images()

    # 基准耗时：优先用工作流实测的 avg_duration，没有则用保守经验值。
    # 口径抽在 `_seconds_per_image` 里，与 EX-5 队列闸门的等待预估**共用同一份**。
    per_image_seconds = _seconds_per_image(db, wf.id)
    # GPU 并发固定为 1，所以总耗时 = 单张 × 张数（串行）
    estimated = per_image_seconds * total

    from app.engine.driver import ComfyUIClient, ComfyUIError

    queue_depth: int | None = None
    try:
        with ComfyUIClient() as client:
            depth = client.queue_depth()
            queue_depth = depth if depth >= 0 else None
    except ComfyUIError:
        queue_depth = None

    est_wait = (queue_depth * per_image_seconds) if queue_depth is not None else None
    est_cost = None
    if settings.gpu_cost_per_hour > 0:
        est_cost = estimated * settings.gpu_cost_per_second

    return TaskEstimateOut(
        total_images=total,
        estimated_seconds=estimated,
        estimated_cost_yuan=est_cost,
        gpu_concurrency=1,
        queue_depth=queue_depth,
        estimated_wait_seconds=est_wait,
    )


def _batch_seed_base(base_seed: Any) -> int | None:
    """把批次的 seed 基准解析成**具体整数**（`None` = 不指定，交给 Schema 默认值）。

    递增口径**由 B 流内核的 `derive_seed()` 定义，A 流不自己算** ——
    否则两边各有一套取模/边界语义，迟早分叉（本项目已多次踩这个坑型）。

    | 输入 | 结果 |
    |---|---|
    | `None` | `None`，不生成 |
    | `-1` | 内核随机出一个基准 |
    | 具体值 | 校验取值域后原样返回 |

    ⚠️ **`-1` 必须在整批上只随机一次**。`derive_seed(base, idx)` 在 `base == -1` 时
    会**每次都重新随机**，若逐张调用就得到一批互不相关的随机 seed ——
    「同批次风格一致但每张不同」（契约 §2.4）会**静默失效**：产物看着正常，
    但批内不再有任何递增关系，用户无法按 seed 复现某一批。
    （2026-09-16 由测试发现：`derive_seed(-1, idx-1)` 逐张调用得到 3.3e9 级别的乱序值。）

    这里借 `derive_seed(x, 0)` 取基准（`idx=0` 时结果就是基准本身），
    保证与内核共用同一套取值域与随机源口径，而不是自己 `randrange`。
    """
    if base_seed is None:
        return None
    try:
        numeric = int(base_seed)
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"seed 必须是整数，实际 {base_seed!r}",
        ) from exc
    try:
        return derive_seed(numeric, 0)
    except RenderError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"seed 非法：{exc}"
        ) from exc


def _avg_seconds_per_image(db: DbSession, workflow_id: int) -> float | None:
    """从历史成功任务里算平均执行时长，让预估随实测数据收敛。"""
    row = db.execute(
        select(func.avg(Task.duration_ms)).where(
            Task.workflow_id == workflow_id, Task.status == TaskStatus.SUCCEEDED.value
        )
    ).scalar()
    if row is None:
        return None
    return float(row) / 1000.0


# ---------------------------------------------------------------- 批量


@router.post(
    "/batches",
    response_model=BatchOut,
    status_code=status.HTTP_202_ACCEPTED,
    summary="提交批量任务（FR-3.4）",
)
def submit_batch(payload: BatchSubmitIn, user: CurrentUser, db: DbSession) -> Batch:
    wf = _get_active_workflow(db, payload.workflow_name)
    total = payload.total_images()
    if total > settings.max_batch_size:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"单任务最多 {settings.max_batch_size} 张，当前 {total}",
        )
    # EX-5 队列闸门：与 `POST /tasks` 同一道门，**必须先于扣额度** ——
    # 批量扣的是 `total`，被拒时一个额度都不能掉。
    _guard_queue(db, wf.id)
    # 公平性闸门：判据是**整批**（`total` 张），不是"能塞几张算几张"。
    # 同样在扣额之前 —— 被拒时一个额度都不能掉、一条子任务都不能建。
    _guard_user_in_flight(db, user, total)
    # 批量是**整体成功或整体失败**：一条条件 UPDATE 扣 `total`，
    # 剩余不足时 rowcount=0 → 402，不会出现「扣了一半」的中间态。
    # 计额口径（FR-1.2）：提交时按**张数**预扣 `total`；终态失败/取消不退。
    _charge_quota(db, user, total)

    template = db.get(Template, payload.template_id) if payload.template_id else None
    common = _merge_params(wf, template, payload.common_params)
    _validate_params(wf, common, db, user)
    # 批量与单任务走同一套检查：批量的提示词只可能来自 common_params
    # （`BatchSubmitIn` 没有顶层 prompt 字段），所以查 common 即可覆盖整批。
    warned = _screen_content(common)

    batch = Batch(
        user_id=user.id,
        name=payload.name,
        status=BatchStatus.PENDING.value,
        workflow_id=wf.id,
        template_id=template.id if template else None,
        common_params=common,
        total_count=total,
    )
    db.add(batch)
    db.flush()

    # 子任务粒度 = 一张图（PRD §3.1 决策）：
    # 这样失败能精确隔离，断点续跑才能只重跑失败的那几张。
    idx = 0
    # 整批共用一个 seed 基准（`-1` 只在这里随机一次，见 _batch_seed_base 的说明）
    seed_base = _batch_seed_base(common.get("seed"))
    for sku, asset_ids in payload.sku_assets.items():
        for _ in range(payload.images_per_sku):
            idx += 1
            params = {
                **common,
                "sku": sku,
                "upload_asset_ids": asset_ids,
                # 全局序号 idx（不是 SKU 内序号）递增：用后者会让不同 SKU 的第 k 张撞同一 seed
                "seed": None if seed_base is None else derive_seed(seed_base, idx - 1),
            }
            child = sm.create_task(
                db,
                user_id=user.id,
                batch_id=batch.id,
                idx=idx,
                workflow_id=wf.id,
                template_id=template.id if template else None,
                params=params,
                # 同单任务：`Task.seed` 是 params 的派生投影，不是第二条通道
                seed=params.get("seed"),
                priority=settings.default_priority,
            )
            # T2：入队。**漏掉这一步会让整个批量功能失效** ——
            # 子任务停在 `pending`，而 worker 只接受 `queued`（会直接跳过），
            # 于是批量提交后一张图都不会跑。单任务路径（submit_task）一直是这么做的，
            # 批量路径此前漏了（2026-09-16 由端到端测试发现）。
            sm.enqueue(db, child)

    db.flush()
    sm.recompute_batch_counts(db, batch)

    track(
        db,
        EventName.TASK_SUBMITTED,
        user_id=user.id,
        batch_id=batch.id,
        workflow=wf.name,
        batch_size=total,
        mode="batch",
        template_id=template.id if template else None,
    )
    db.add(
        AuditLog(
            user_id=user.id, action="batch.submit", target_type="batch", target_id=str(batch.id)
        )
    )
    # 留痕挂在 **batch** 上（不是某一张子任务）：提示词是整批共用的，
    # 逐张写会在一个 500 张的批量里留下 500 条一模一样的审计。
    _record_content_warnings(
        db, user, warned, common, target_type="batch", target_id=batch.id
    )
    db.commit()

    for task in batch.tasks:
        enqueue_task(task.id, task.priority)

    log.info("batch.submitted", extra=bind_task(batch_id=batch.id, total=total))
    return batch


# ---------------------------------------------------------------- 查询


@router.get("/tasks", response_model=list[TaskOut], summary="任务列表（FR-4.1）")
def list_tasks(
    user: CurrentUser,
    db: DbSession,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    batch_id: int | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[Task]:
    stmt = select(Task).where(Task.user_id == user.id)
    if status_filter:
        stmt = stmt.where(Task.status == status_filter)
    if batch_id is not None:
        stmt = stmt.where(Task.batch_id == batch_id)
    stmt = stmt.order_by(Task.id.desc()).limit(limit).offset(offset)
    return list(db.scalars(stmt).all())


@router.get("/tasks/{task_id}", response_model=TaskOut, summary="任务详情（FR-4.2）")
def get_task(task_id: int, user: CurrentUser, db: DbSession) -> Task:
    task = _owned_task(db, task_id, user.id)
    return task


@router.get("/batches", response_model=list[BatchOut], summary="批量任务列表")
def list_batches(
    user: CurrentUser,
    db: DbSession,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[Batch]:
    stmt = select(Batch).where(Batch.user_id == user.id)
    if status_filter:
        stmt = stmt.where(Batch.status == status_filter)
    stmt = stmt.order_by(Batch.id.desc()).limit(limit).offset(offset)
    return list(db.scalars(stmt).all())


@router.get("/batches/{batch_id}", response_model=BatchOut, summary="批量任务详情")
def get_batch(batch_id: int, user: CurrentUser, db: DbSession) -> Batch:
    batch = db.scalar(select(Batch).where(Batch.id == batch_id, Batch.user_id == user.id))
    if batch is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="批量任务不存在")
    return batch


# ---------------------------------------------------------------- 操作


@router.post("/tasks/{task_id}/cancel", response_model=CancelOut, summary="取消任务（FR-4.3）")
def cancel_task(task_id: int, user: CurrentUser, db: DbSession) -> CancelOut:
    task = _owned_task(db, task_id, user.id)
    if task.status not in TaskStatus.cancellable():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"当前状态 {task.status} 不可取消",
        )

    was_running = task.status == TaskStatus.RUNNING.value
    engine_prompt_id = task.engine_prompt_id
    sm.mark_canceled(db, task)
    db.commit()

    if engine_prompt_id or was_running:
        # 执行中的任务需要通知引擎停止。
        #
        # 用 `cancel()` 而不是裸 `interrupt()`：interrupt 只对"正在执行"的任务有效，
        # 若任务已提交但还待在引擎队列里，它是**空操作** —— 那张图随后仍会被跑出来，
        # 白烧一次 GPU。`cancel()` 会先判断在队列还是在执行，再决定"整条删除"
        # 还是"步间隙中断"。
        #
        # ComfyUI 的 /interrupt 是全局的；因为 GPU 并发固定为 1（NFR-2），
        # 语义上安全。若未来多实例并发（E10），必须改为按实例路由。
        from app.engine.driver import ComfyUIClient, ComfyUIError

        try:
            with ComfyUIClient() as client:
                if engine_prompt_id:
                    outcome = client.cancel(engine_prompt_id)
                    log.info(
                        "cancel.engine prompt_id=%s outcome=%s",
                        engine_prompt_id,
                        outcome.value,
                    )
                else:
                    client.interrupt()
        except ComfyUIError as exc:
            # 引擎不可达不应让取消操作失败：状态已落库为 canceled，
            # worker 轮询时也会看到并主动停止。
            log.warning("cancel.interrupt_failed task_id=%s err=%s", task_id, exc)

    return CancelOut(task_id=task.id, status=task.status, message="已取消")


@router.post("/tasks/{task_id}/retry", response_model=TaskOut, summary="重试任务（FR-4.4）")
def retry_task(task_id: int, user: CurrentUser, db: DbSession) -> Task:
    """手动重试单个任务（FR-4.4）。

    ⚠️ **计额口径**：这是一次**新的生成**，所以按 1 张**重新计额**
    （下方 `_charge_quota(db, user, 1)`）—— 重跑会重新占用 GPU，
    不重新计额等于给「反复重试」开一个免费口子。若原任务已成功则根本不可重试
    （状态闸门拦住），所以不存在「为同一张产物重复收两次」的情况。
    口径与理由见 `docs/prd/PRD_v1.md` FR-1.2。
    """
    task = _owned_task(db, task_id, user.id)
    if task.status not in (TaskStatus.FAILED.value, TaskStatus.CANCELED.value):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"当前状态 {task.status} 不可重试",
        )
    # 全局队列闸门（EX-5）也管重试：重试会把任务**重新送回**队列，队列该爆还是会爆。
    # 与两条提交路径**复用同一个 `_guard_queue`**（不另写一份判据），
    # 位置同样在扣额之前 —— 被队列拒绝的请求不能扣额。
    #
    # ⚠️ 这里**不**加每用户在途上限（`_guard_user_in_flight`）：重试**不新增任务** ——
    # 这个任务本来就在库里、提交时已经计过在途数，再加一道门等于同一个任务被计两次。
    # 详见 `_guard_user_in_flight` 的 docstring。
    _guard_queue(db, task.workflow_id)
    # 计额口径同 FR-1.2：重试按**新一次生成**计额（提交时预扣，失败/取消不退）
    _charge_quota(db, user, 1)

    sm.reset_for_retry(db, task)
    sm.enqueue(db, task)
    db.commit()

    enqueue_task(task.id, task.priority)
    return task


@router.post(
    "/batches/{batch_id}/retry-failed",
    response_model=BatchOut,
    summary="断点续跑：只重跑失败项（FR-3.5）",
)
def retry_failed(batch_id: int, user: CurrentUser, db: DbSession) -> Batch:
    """批量失败后只重跑失败的那些。

    这是画像②最痛的点：「批量跑 50 张，跑到第 30 张崩了，前功尽弃」。

    ⚠️ **省额度省在哪里**（这句话极易被误读成「重试不计额」，所以写清楚）：

    - 这里**绝不重跑已成功的那 20 张** —— 省下的是**那部分不重复计额**。
      全量重跑要计 50 张，现在只计 30 张。
    - **重跑的这 30 张仍然按「新一次生成」计额**（下方 `_charge_quota(..., len(failed))`），
      它们会重新占用 GPU。重试**不是免费的**，只是「不必为已成功的那部分再付一次」。

    完整口径见 `docs/prd/PRD_v1.md` FR-1.2（提交时预扣、按张数计、
    终态失败/取消不退还）。
    """
    batch = db.scalar(select(Batch).where(Batch.id == batch_id, Batch.user_id == user.id))
    if batch is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="批量任务不存在")

    failed = list(
        db.scalars(
            select(Task).where(
                Task.batch_id == batch.id,
                Task.status.in_([TaskStatus.FAILED.value, TaskStatus.CANCELED.value]),
            )
        ).all()
    )
    if not failed:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="没有失败的任务可重跑")

    # 全局队列闸门（EX-5）：与 `retry` 同一条理由与同一份实现 —— 重跑会把 `len(failed)`
    # 张重新送回队列，队列该爆还是会爆。位置在扣额之前。
    # ⚠️ 同样**不**加每用户在途上限：这些任务已在库里、已计过在途数（见 `retry` 处的说明）。
    _guard_queue(db, batch.workflow_id)
    # 计额口径同 FR-1.2：只重跑的这些按**新一次生成**计额（`len(failed)` 张）
    _charge_quota(db, user, len(failed))
    for task in failed:
        sm.reset_for_retry(db, task)
        sm.enqueue(db, task)
    sm.recompute_batch_counts(db, batch)
    db.commit()

    for task in failed:
        enqueue_task(task.id, task.priority)

    log.info("batch.retry_failed", extra=bind_task(batch_id=batch.id, count=len(failed)))
    return batch


# ---------------------------------------------------------------- 辅助


def _owned_task(db: DbSession, task_id: int, user_id: int) -> Task:
    """带归属校验的取任务（FR-1.3 数据隔离）。"""
    task = db.scalar(select(Task).where(Task.id == task_id, Task.user_id == user_id))
    if task is None:
        # 用 404 而不是 403：不泄露「该任务存在但不属于你」
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="任务不存在")
    return task
