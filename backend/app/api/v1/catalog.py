"""模板、工作流与模型接口（FR-2.2 / FR-6.1 / FR-9.1）。

模板库是 persona 旅程的**关键转化点**（3→4：上传→选模板），
所以这个接口的列表排序与示例图是产品问题，不只是技术问题。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select

from app.api.deps import AdminUser, CurrentUser, DbSession
from app.models.registry import ModelRegistry
from app.models.workflow import Template, Workflow
from app.schemas.workflow import (
    ModelRegistryOut,
    TemplateCreateIn,
    TemplateOut,
    WorkflowDetailOut,
    WorkflowOut,
)

router = APIRouter(tags=["catalog"])


# ---------------------------------------------------------------- 工作流


@router.get("/workflows", response_model=list[WorkflowOut], summary="工作流列表（FR-6.2）")
def list_workflows(_: CurrentUser, db: DbSession) -> list[Workflow]:
    """只返回启用中的版本。版本化与回滚在 P6 落地（FR-6.3）。"""
    stmt = (
        select(Workflow)
        .where(Workflow.is_active.is_(True))
        .order_by(Workflow.name, Workflow.version.desc())
    )
    return list(db.scalars(stmt).all())


@router.get(
    "/workflows/{name}/schema",
    response_model=WorkflowDetailOut,
    summary="获取参数 Schema，供前端渲染动态表单（FR-2.3）",
)
def get_workflow_schema(name: str, _: CurrentUser, db: DbSession) -> Workflow:
    """前端拿这个 schema 自动生成表单，**新增工作流不需要改前端代码**（ADR-004 约束 2）。

    注意返回体不含 `definition`（ComfyUI 节点图）—— 避免暴露内部实现与模型路径。
    """
    wf = db.scalar(
        select(Workflow)
        .where(Workflow.name == name, Workflow.is_active.is_(True))
        .order_by(Workflow.version.desc())
    )
    if wf is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"工作流 {name} 不存在")
    return wf


# ---------------------------------------------------------------- 模板


@router.get("/templates", response_model=list[TemplateOut], summary="模板库（FR-6.1）")
def list_templates(
    _: CurrentUser,
    db: DbSession,
    category: Annotated[str | None, Query(description="品类，如 服饰/3C/家居")] = None,
    limit: int = Query(default=60, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[Template]:
    """按品类分组 + 按热度排序。

    排序用 usage_count 与 rating 而非人工指定，避免管理员主观推荐
    （对应线框图中「用真实数据排序」的设计）。
    """
    stmt = select(Template).where(Template.is_active.is_(True))
    if category:
        stmt = stmt.where(Template.category == category)
    stmt = stmt.order_by(
        Template.sort_order.asc(), Template.usage_count.desc(), Template.id.asc()
    ).limit(limit).offset(offset)
    return list(db.scalars(stmt).all())


@router.get("/templates/categories", response_model=list[str], summary="模板品类列表")
def list_categories(_: CurrentUser, db: DbSession) -> list[str]:
    stmt = (
        select(Template.category)
        .where(Template.is_active.is_(True))
        .group_by(Template.category)
        .order_by(Template.category)
    )
    return [c for c in db.scalars(stmt).all() if c]


@router.post("/templates", response_model=TemplateOut, status_code=status.HTTP_201_CREATED,
             summary="新增模板（管理员，FR-6.5）")
def create_template(payload: TemplateCreateIn, _: AdminUser, db: DbSession) -> Template:
    if db.get(Workflow, payload.workflow_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="工作流不存在")

    tpl = Template(**payload.model_dump())
    db.add(tpl)
    db.commit()
    return tpl


# ---------------------------------------------------------------- 模型 registry


@router.get("/models", response_model=list[ModelRegistryOut],
            summary="模型清单（含商用许可，FR-9.1）")
def list_models(
    _: CurrentUser,
    db: DbSession,
    commercial_only: Annotated[bool, Query(description="只看可商用模型")] = False,
    kind: str | None = None,
) -> list[ModelRegistry]:
    """把 `is_commercial_ok` 暴露给前端是刻意的 —— 许可矩阵（C9）应当可见，
    而不是藏在文档里。线框图中管理后台的模型列表就直接显示许可列。
    """
    stmt = select(ModelRegistry)
    if commercial_only:
        stmt = stmt.where(ModelRegistry.is_commercial_ok.is_(True))
    if kind:
        stmt = stmt.where(ModelRegistry.kind == kind)
    stmt = stmt.order_by(ModelRegistry.kind, ModelRegistry.sort_order, ModelRegistry.id)
    return list(db.scalars(stmt).all())
