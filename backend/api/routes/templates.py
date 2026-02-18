# -*- coding: utf-8 -*-
"""
Task Templates API — Full CRUD for DB-backed task templates.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from backend.core.database import get_db
from backend.models.schemas import TaskTemplate
from backend.api.routes.auth import get_current_active_user, User
from backend.api.schemas import (
    PaginatedResponse,
    TaskTemplateDBCreate,
    TaskTemplateDBUpdate,
    TaskTemplateDBOut,
)

router = APIRouter(prefix="/api/v1/templates", tags=["templates"])


@router.get("", response_model=PaginatedResponse)
def list_templates(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    """获取任务模板列表"""
    query = db.query(TaskTemplate).order_by(TaskTemplate.id.desc())
    total = query.count()
    items = query.offset(skip).limit(limit).all()
    result = [
        TaskTemplateDBOut.model_validate(t) if hasattr(TaskTemplateDBOut, "model_validate")
        else TaskTemplateDBOut.from_orm(t)
        for t in items
    ]
    return PaginatedResponse(items=result, total=total, skip=skip, limit=limit)


@router.get("/{template_id}", response_model=TaskTemplateDBOut)
def get_template(template_id: int, db: Session = Depends(get_db)):
    """获取任务模板详情"""
    tmpl = db.query(TaskTemplate).filter_by(id=template_id).first()
    if not tmpl:
        raise HTTPException(status_code=404, detail="模板不存在")
    return tmpl


@router.post("", response_model=TaskTemplateDBOut)
def create_template(
    data: TaskTemplateDBCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """创建任务模板"""
    existing = db.query(TaskTemplate).filter_by(name=data.name).first()
    if existing:
        raise HTTPException(status_code=400, detail="模板名称已存在")

    tmpl = TaskTemplate(
        name=data.name,
        type=data.type,
        description=data.description,
        default_params=data.default_params,
        enabled=data.enabled,
    )
    db.add(tmpl)
    db.commit()
    db.refresh(tmpl)
    return tmpl


@router.put("/{template_id}", response_model=TaskTemplateDBOut)
def update_template(
    template_id: int,
    data: TaskTemplateDBUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """更新任务模板"""
    tmpl = db.query(TaskTemplate).filter_by(id=template_id).first()
    if not tmpl:
        raise HTTPException(status_code=404, detail="模板不存在")

    if data.name is not None:
        if data.name != tmpl.name:
            existing = db.query(TaskTemplate).filter_by(name=data.name).first()
            if existing:
                raise HTTPException(status_code=400, detail="模板名称已存在")
        tmpl.name = data.name
    if data.type is not None:
        tmpl.type = data.type
    if data.description is not None:
        tmpl.description = data.description
    if data.default_params is not None:
        tmpl.default_params = data.default_params
    if data.enabled is not None:
        tmpl.enabled = data.enabled

    db.commit()
    db.refresh(tmpl)
    return tmpl


@router.delete("/{template_id}")
def delete_template(
    template_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """删除任务模板"""
    tmpl = db.query(TaskTemplate).filter_by(id=template_id).first()
    if not tmpl:
        raise HTTPException(status_code=404, detail="模板不存在")
    db.delete(tmpl)
    db.commit()
    return {"message": "删除成功"}
