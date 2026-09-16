"""前端枚举镜像的防漂移门禁（P2-10）。

为什么是 Python 测试，而不是前端测试
----------------------------------
本仓库的测试入口在 Python。放在这里意味着**不需要 Node 环境**就能跑这道门禁，
将来进 CI（P2-11）也不必先装前端依赖。
先例：`backend/tests/test_env_example.py`（同样是"防两份配置各自漂移"）。

这道门禁守的是什么
------------------
前端徽章、埋点、看板硬依赖这套取值，而"记得手动同步"是守不住的。
所以这里做**双向**比对：少一个、多一个、拼错都会红。

特别覆盖**派生集合**（terminal / cancellable / retryable）——
项目历史上两次近似事故正出在这里：
- `partial` 属于 `BatchStatus` 而**不属于** `TaskStatus`（曾把两者混成"8 个状态"）
- `canceled` **是可重试的**（PRD §5.4 T13 曾漏写，见契约 §1.1）

⚠️ 本文件**不校验展示层**（中文名、颜色）—— 那属于 `StatusBadge.tsx`。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from app.models.enums import AssetKind, BatchStatus, ErrorType, ModelKind, TaskStatus, UserRole
from app.models.event import EventName

WEB = Path(__file__).resolve().parents[2] / "web"
ENUMS_TS = WEB / "src" / "api" / "enums.ts"
PACKAGE_JSON = WEB / "package.json"

#: 失败信息里要指向改契约的流程，否则有人会先去改 TS 文件把测试改绿
_HOWTO = (
    "  → 若确实要改：先走 docs/sop/contracts.md §0.3（改文档 → 改后端 enums.py → 通知其它流），"
    "再同步本文件。**不要只改前端把测试改绿**。"
)

needs_web = pytest.mark.skipif(
    not ENUMS_TS.exists(), reason="web/src/api/enums.ts 尚未建立（P2-10 之前）"
)


# ============================================================ 解析 enums.ts


def _read() -> str:
    return ENUMS_TS.read_text(encoding="utf-8")


def _object_members(name: str) -> dict[str, str]:
    """`export const NAME = { KEY: "value" } as const` → {KEY: value}。"""
    m = re.search(
        rf"export const {name}\s*=\s*\{{(.*?)\}}\s*as const",
        _read(),
        re.DOTALL,
    )
    if m is None:
        pytest.fail(f"enums.ts 里找不到对象 `{name}`（被改名或删除了？）")
    return dict(re.findall(r"(\w+):\s*['\"]([^'\"]+)['\"]", m.group(1)))


def _array_members(name: str) -> list[str]:
    """`export const NAME: readonly T[] = ["a", "b"]` → ["a", "b"]。"""
    m = re.search(rf"export const {name}\b[^=]*=\s*\[(.*?)\]", _read(), re.DOTALL)
    if m is None:
        pytest.fail(f"enums.ts 里找不到数组 `{name}`（被改名或删除了？）")
    return re.findall(r"['\"]([^'\"]+)['\"]", m.group(1))


def _py_members(enum_cls: Any) -> dict[str, str]:
    return {e.name: e.value for e in enum_cls}


def _event_name_members() -> dict[str, str]:
    """`EventName` 是普通类（不是 Enum），取它的大写字符串常量。"""
    return {
        k: v for k, v in vars(EventName).items() if k.isupper() and isinstance(v, str)
    }


def _diff_report(label: str, ts: dict[str, str], py: dict[str, str]) -> str:
    missing = {k: v for k, v in py.items() if ts.get(k) != v}
    extra = {k: v for k, v in ts.items() if k not in py}
    return (
        f"{label} 与后端不一致：\n"
        f"  前端缺失/写错：{missing or '无'}\n"
        f"  前端多出：{extra or '无'}\n"
        f"  后端实际：{py}\n" + _HOWTO
    )


# ============================================================ 门禁自身可被检出


def test_guard_is_not_silently_disabled() -> None:
    """`web/` 建了脚手架却没有 enums.ts 时必须**报错**，而不是静默跳过。

    否则删掉那个文件就能让整套 parity 检查失效 —— 那正是"判据本身无法被检出"。
    """
    if PACKAGE_JSON.exists():
        assert ENUMS_TS.exists(), (
            "web/ 已有 package.json，但 src/api/enums.ts 不存在。"
            "枚举镜像不得被删除或改名。"
        )


# ============================================================ 取值集合


@needs_web
def test_task_status_parity() -> None:
    ts, py = _object_members("TASK_STATUS"), _py_members(TaskStatus)
    assert ts, "解析到 0 个成员 —— 解析器或文件格式出问题了"
    assert ts == py, _diff_report("TaskStatus", ts, py)


@needs_web
def test_batch_status_parity() -> None:
    ts, py = _object_members("BATCH_STATUS"), _py_members(BatchStatus)
    assert ts
    assert ts == py, _diff_report("BatchStatus", ts, py)


@needs_web
def test_error_type_parity() -> None:
    ts, py = _object_members("ERROR_TYPE"), _py_members(ErrorType)
    assert ts
    assert ts == py, _diff_report("ErrorType", ts, py)


@needs_web
def test_asset_kind_parity() -> None:
    ts, py = _object_members("ASSET_KIND"), _py_members(AssetKind)
    assert ts
    assert ts == py, _diff_report("AssetKind", ts, py)


@needs_web
def test_user_role_parity() -> None:
    ts, py = _object_members("USER_ROLE"), _py_members(UserRole)
    assert ts
    assert ts == py, _diff_report("UserRole", ts, py)


@needs_web
def test_model_kind_parity() -> None:
    ts, py = _object_members("MODEL_KIND"), _py_members(ModelKind)
    assert ts
    assert ts == py, _diff_report("ModelKind", ts, py)


@needs_web
def test_event_name_parity() -> None:
    ts, py = _object_members("EVENT_NAME"), _event_name_members()
    assert ts
    assert ts == py, _diff_report("EventName", ts, py)


# ============================================================ 派生集合（历史事故高发区）


@needs_web
def test_task_status_terminal_parity() -> None:
    ts = set(_array_members("TASK_STATUS_TERMINAL"))
    py = {s.value for s in TaskStatus.terminal()}
    assert ts == py, _diff_report("TaskStatus.terminal()", dict.fromkeys(ts), dict.fromkeys(py))


@needs_web
def test_task_status_cancellable_parity() -> None:
    ts = set(_array_members("TASK_STATUS_CANCELLABLE"))
    py = {s.value for s in TaskStatus.cancellable()}
    assert ts == py, _diff_report("TaskStatus.cancellable()", dict.fromkeys(ts), dict.fromkeys(py))


@needs_web
def test_batch_status_terminal_parity() -> None:
    """⚠️ `partial` 必须在这里、且**不在** TaskStatus 里。"""
    ts = set(_array_members("BATCH_STATUS_TERMINAL"))
    py = {s.value for s in BatchStatus.terminal()}
    assert ts == py, _diff_report("BatchStatus.terminal()", dict.fromkeys(ts), dict.fromkeys(py))
    assert "partial" in ts, "partial 是父任务终态，漏了会让用户以为批量任务永远没结束"


@needs_web
def test_error_type_retryable_parity() -> None:
    ts = set(_array_members("ERROR_TYPE_RETRYABLE"))
    py = {e.value for e in ErrorType.retryable()}
    assert ts == py, _diff_report("ErrorType.retryable()", dict.fromkeys(ts), dict.fromkeys(py))


@needs_web
def test_event_name_all_parity() -> None:
    ts = list(_array_members("EVENT_NAME_ALL"))
    py = list(EventName.all())
    assert ts == py, _diff_report("EventName.all()", dict.fromkeys(ts), dict.fromkeys(py))
