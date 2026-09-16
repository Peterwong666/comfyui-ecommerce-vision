"""前端测试夹具与注册表的一致性门禁（P2-10）。

为什么需要它
------------
`web/src/test/fixtures/*.schema.json` 是注册表 `param_schema` 的**副本**，
给前端单测当输入。所有"副本"都会漂移 —— 一旦漂移，
前端测试就是在**对一份过期的 Schema** 做断言，绿得毫无意义。

所以这里断言副本与 `workflows/registry.yaml` **逐字节相等**。
夹具若需要更新，用 `tests` 之外的方式重新导出（见下方命令），不要手改。

重新导出：
    backend/.venv/bin/python - <<'PY'
    import json, pathlib
    from engine import Registry
    out = pathlib.Path("web/src/test/fixtures")
    for wid in ("t2i_v1", "flux2_klein_t2i_v1", "inpaint_v1"):
        s = Registry.load().require(wid).raw["param_schema"]
        (out / f"{wid}.schema.json").write_text(
            json.dumps(s, ensure_ascii=False, indent=2) + "\\n", encoding="utf-8")
    PY
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from engine import Registry

FIXTURES = Path(__file__).resolve().parents[2] / "web" / "src" / "test" / "fixtures"

#: 被前端单测引用的夹具。新增时记得同步 `web/src/**/*.test.tsx`。
FIXTURE_WORKFLOWS = ("t2i_v1", "flux2_klein_t2i_v1", "inpaint_v1")


def test_fixtures_exist_when_web_scaffold_exists() -> None:
    """`web/` 有脚手架时夹具不得缺失，否则下面的比对会静默跳过。"""
    if (FIXTURES.parents[2] / "package.json").exists():
        missing = [w for w in FIXTURE_WORKFLOWS if not (FIXTURES / f"{w}.schema.json").exists()]
        assert not missing, f"缺少前端测试夹具：{missing}（web/ 已建立脚手架）"


@pytest.mark.parametrize("workflow_id", FIXTURE_WORKFLOWS)
def test_fixture_matches_registry(workflow_id: str) -> None:
    path = FIXTURES / f"{workflow_id}.schema.json"
    if not path.exists():
        pytest.skip(f"{path} 不存在（web/ 尚未建立）")

    expected = Registry.load().require(workflow_id).raw["param_schema"]
    actual = json.loads(path.read_text(encoding="utf-8"))

    assert actual == expected, (
        f"{workflow_id} 的前端夹具与注册表不一致 —— 夹具是副本，会漂移。\n"
        "请按本文件 docstring 里的命令重新导出，不要手改。"
    )
