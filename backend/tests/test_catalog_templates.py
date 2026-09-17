"""模板库（`AC-F6`：模板可用 + 按品类分组）。

**为什么之前没有用例**：`app/api/v1/catalog.py` 的 `/templates` 三条路由
（列表 / 品类 / 新建）在全仓库的 `test_*` 里**零覆盖** —— `AC-F6` 因此长期是
"实现有、证据无"。模板是 persona 旅程的**关键转化点**（3→4：上传→选模板），
它坏掉的症状是"页面空了/选不到模板"，用户不会看到任何报错，只会流失。

本文件钉住四件事
================

1. **列表只返回启用模板**，且按 `sort_order ↑ / usage_count ↓ / id ↑` 排序
   （`catalog.list_templates` 的排序是产品决定：用真实热度而非人工推荐）；
2. **品类筛选**（`?category=`）真的生效；
3. **`/templates/categories`** 返回去重、有序、非空的品类，且**只统计启用模板**；
4. **`POST /templates`**：管理员 201 且能查到；非管理员 403；工作流不存在 404。

⚠️ 排序口径写**字面量**断言（不引用实现里的排序键），这样"把三者顺序调换"的变异
会被检出 —— 顺序变了用户看到的推荐就变了。
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.enums import UserRole
from app.models.workflow import Template

# ---------------------------------------------------------------- 工具


def _make_template(
    db: Session,
    workflow,  # type: ignore[no-untyped-def]
    name: str,
    category: str,
    *,
    sort_order: int = 0,
    usage_count: int = 0,
    is_active: bool = True,
) -> Template:
    tpl = Template(
        name=name,
        category=category,
        workflow_id=workflow.id,
        preset_params={"steps": 25},
        sort_order=sort_order,
        usage_count=usage_count,
        is_active=is_active,
    )
    db.add(tpl)
    db.commit()
    return tpl


def _names(resp) -> list[str]:  # type: ignore[no-untyped-def]
    return [item["name"] for item in resp.json()]


# ---------------------------------------------------------------- 1. 列表


def test_list_templates_returns_active_ones_in_heat_order(client, db: Session, workflow) -> None:  # type: ignore[no-untyped-def]
    """列表：只含启用模板，顺序 = `sort_order` ↑ → `usage_count` ↓。

    构造（精心设计让三条排序键都能被区分）：
    `B` 与 `A` 的 `sort_order` 相同（0），`B` 热度更高 ⇒ 必须排在 `A` 前；
    `C` 的 `sort_order` 更大（2）⇒ 即使热度 100 也排最后；
    `D` 是停用模板、热度 999 ⇒ **完全不出现**（出现在榜首就说明漏了 is_active 过滤）。
    """
    _make_template(db, workflow, "A-低热度", "服饰", sort_order=0, usage_count=5)
    _make_template(db, workflow, "B-高热度", "服饰", sort_order=0, usage_count=9)
    _make_template(db, workflow, "C-靠后", "3C", sort_order=2, usage_count=100)
    _make_template(db, workflow, "D-停用", "服饰", sort_order=0, usage_count=999, is_active=False)

    resp = client.get("/api/v1/templates")

    assert resp.status_code == 200, resp.text
    assert _names(resp) == ["B-高热度", "A-低热度", "C-靠后"]


def test_list_templates_payload_shape(client, db: Session, workflow) -> None:  # type: ignore[no-untyped-def]
    """列表项必须带**前端渲染模板卡片**需要的字段（缺一个卡片就是残缺的）。

    尤其是 `example_asset_id`：模板"必须一眼看懂效果"（FR-6.1），示例图取不到时
    卡片会变成空白块 —— 那是最伤转化的一种静默失败。
    """
    _make_template(db, workflow, "白瓷杯主图", "家居", usage_count=3)

    item = client.get("/api/v1/templates").json()[0]

    assert set(item) == {
        "id",
        "name",
        "category",
        "description",
        "workflow_id",
        "preset_params",
        "example_asset_id",
        "usage_count",
        "rating",
        "sort_order",
    }
    assert item["category"] == "家居"
    assert item["workflow_id"] == workflow.id
    assert item["preset_params"] == {"steps": 25}
    assert item["usage_count"] == 3


def test_list_templates_filters_by_category(client, db: Session, workflow) -> None:  # type: ignore[no-untyped-def]
    """`?category=` 只返回该品类 —— 品类是模板库的**一级导航**，筛错等于点错标签。"""
    _make_template(db, workflow, "服饰-A", "服饰")
    _make_template(db, workflow, "服饰-B", "服饰")
    _make_template(db, workflow, "3C-A", "3C")

    resp = client.get("/api/v1/templates", params={"category": "服饰"})

    assert resp.status_code == 200, resp.text
    assert _names(resp) == ["服饰-A", "服饰-B"]


def test_list_templates_category_with_no_match_is_empty_not_error(client, workflow) -> None:  # type: ignore[no-untyped-def]
    """筛到一个没有模板的品类 ⇒ `[]` + 200（不是 404）。

    前端切标签时会出现"该品类暂时没有模板"，那是正常的空态、要能渲染空列表；
    报 404 会让页面进错误态。
    """
    resp = client.get("/api/v1/templates", params={"category": "不存在的品类"})

    assert resp.status_code == 200, resp.text
    assert resp.json() == []


def test_list_templates_pagination_is_bounded(client, workflow) -> None:  # type: ignore[no-untyped-def]
    """`limit` / `offset` 的上下界：`limit=0` 与 `limit=201` 都必须是 422。

    无界的分页参数会让一次请求把整张表读进内存（与 EX-4 同类）。
    """
    assert client.get("/api/v1/templates", params={"limit": 0}).status_code == 422
    assert client.get("/api/v1/templates", params={"limit": 201}).status_code == 422
    assert client.get("/api/v1/templates", params={"offset": -1}).status_code == 422


# ---------------------------------------------------------------- 2. 品类分组


def test_categories_are_distinct_sorted_and_from_active_templates_only(
    client, db: Session, workflow
) -> None:  # type: ignore[no-untyped-def]
    """`/templates/categories`：去重 + 升序 + **排除停用模板** + 过滤空品类。

    ① 去重与排序：分组导航的选项顺序必须稳定，否则每次刷新标签都在跳；
    ② 只统计启用的：与 `/templates` 用同一个 `is_active` 判据 —— 两边不一致会出现
       "品类里有『宠物』标签，点进去 0 个模板"这种自相矛盾的页面；
    ③ 空品类被过滤（`catalog.list_categories` 的 `if c`）：`category` 列可为空串，
       空标签在 UI 上就是一个点不动的按钮。
    """
    _make_template(db, workflow, "服饰-A", "服饰")
    _make_template(db, workflow, "服饰-B", "服饰")
    _make_template(db, workflow, "家居-A", "家居")
    _make_template(db, workflow, "3C-A", "3C")
    _make_template(db, workflow, "停用-宠物", "宠物", is_active=False)
    _make_template(db, workflow, "空品类", "")

    resp = client.get("/api/v1/templates/categories")

    assert resp.status_code == 200, resp.text
    assert resp.json() == ["3C", "家居", "服饰"]


def test_categories_is_empty_when_no_template(client) -> None:  # type: ignore[no-untyped-def]
    """库里没有模板时返回 `[]`（空态可渲染，不报错）。"""
    resp = client.get("/api/v1/templates/categories")

    assert resp.status_code == 200, resp.text
    assert resp.json() == []


# ---------------------------------------------------------------- 3. 新建（FR-6.5）


def test_create_template_as_admin_then_visible_in_the_list(
    client, db: Session, user, workflow
) -> None:  # type: ignore[no-untyped-def]
    """管理员新建：201 + 落库 + **立刻出现在列表里**（端到端闭环）。

    只断言 201 会漏掉"建了但列不出来"（例如列表按 `is_active` 过滤而新记录默认 False）——
    那对管理员是"我保存了但页面没有"，最容易被当成前端 bug。
    """
    user.role = UserRole.ADMIN.value
    db.commit()

    resp = client.post(
        "/api/v1/templates",
        json={
            "name": "节日促销主图",
            "category": "服饰",
            "workflow_id": workflow.id,
            "preset_params": {"steps": 30, "cfg": 7.5},
            "sort_order": 1,
            "description": "大促场景",
        },
    )

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["name"] == "节日促销主图"
    assert body["preset_params"] == {"steps": 30, "cfg": 7.5}

    stored = db.get(Template, body["id"])
    assert stored is not None
    assert stored.category == "服饰"

    assert "节日促销主图" in _names(client.get("/api/v1/templates"))
    assert "服饰" in client.get("/api/v1/templates/categories").json()


def test_create_template_requires_admin(client, db: Session, user, workflow) -> None:  # type: ignore[no-untyped-def]
    """普通用户新建 → **403**（契约 §2.2：403 = 权限不足）。

    与"越权读别人的资源返回 404"不同：模板是**公共目录**、资源本身不敏感，
    这里要回答的是"你有没有这个权限"，403 比 404 更准确（也更方便前端提示）。
    """
    assert user.role == UserRole.USER.value

    resp = client.post(
        "/api/v1/templates",
        json={"name": "越权模板", "category": "服饰", "workflow_id": workflow.id},
    )

    assert resp.status_code == 403, resp.text
    assert db.scalar(select(Template).where(Template.name == "越权模板")) is None


def test_create_template_with_unknown_workflow_is_404(client, db: Session, user) -> None:  # type: ignore[no-untyped-def]
    """工作流不存在 → 404（不能建出"指向虚空的模板"）。

    ⚠️ 这条**必须在管理员身份下测**：普通用户会在 403 就被拦住，
    测不到"工作流校验"这一段。
    """
    user.role = UserRole.ADMIN.value
    db.commit()

    resp = client.post(
        "/api/v1/templates",
        json={"name": "孤儿模板", "category": "服饰", "workflow_id": 999999},
    )

    assert resp.status_code == 404, resp.text
    assert db.scalar(select(Template).where(Template.name == "孤儿模板")) is None
