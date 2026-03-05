# -*- coding: utf-8 -*-
"""
Task Templates API — Full CRUD for DB-backed task templates.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from backend.core.database import get_db
from backend.models.job import TaskTemplate
from backend.api.routes.auth import get_current_active_user, User
from backend.api.schemas import (
    PaginatedResponse,
    TaskTemplateDBCreate,
    TaskTemplateDBUpdate,
    TaskTemplateDBOut,
)

router = APIRouter(prefix="/api/v1/templates", tags=["templates"])


def _tmpl_to_out(t: TaskTemplate) -> TaskTemplateDBOut:
    return TaskTemplateDBOut(
        id=t.id,
        name=t.name,
        task_type="WORKFLOW",
        description=None,
        params=t.pipeline_def if isinstance(t.pipeline_def, dict) else {},
        enabled=True,
        created_at=t.created_at,
    )


@router.get("", response_model=PaginatedResponse)
def list_templates(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    query = db.query(TaskTemplate).order_by(TaskTemplate.id.desc())
    total = query.count()
    items = query.offset(skip).limit(limit).all()
    return PaginatedResponse(items=[_tmpl_to_out(t) for t in items], total=total, skip=skip, limit=limit)


@router.get("/{template_id}", response_model=TaskTemplateDBOut)
def get_template(template_id: int, db: Session = Depends(get_db)):
    tmpl = db.get(TaskTemplate, template_id)
    if not tmpl:
        raise HTTPException(status_code=404, detail="模板不存在")
    return _tmpl_to_out(tmpl)


@router.post("", response_model=TaskTemplateDBOut)
def create_template(
    data: TaskTemplateDBCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    raise HTTPException(status_code=503, detail="模板创建已迁移至 /orchestration/workflows，请通过工作流编辑器管理模板")


@router.put("/{template_id}", response_model=TaskTemplateDBOut)
def update_template(
    template_id: int,
    data: TaskTemplateDBUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    raise HTTPException(status_code=503, detail="模板更新已迁移至 /orchestration/workflows")


@router.delete("/{template_id}")
def delete_template(
    template_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    raise HTTPException(status_code=503, detail="模板删除已迁移至 /orchestration/workflows")
