"""Action 模板 API。

用于管理可复用的步骤模板，供工作流编辑器选择后展开为标准 step 字段。
"""

import re
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.response import ApiResponse, ok
from backend.core.database import get_async_db
from backend.models.action_template import ActionTemplate
from backend.models.tool import Tool

router = APIRouter(prefix="/api/v1/action-templates", tags=["action-templates"])

ACTION_PATTERN = re.compile(r"^(tool:\d+|builtin:.+|script:.+)$")


class ActionTemplateCreate(BaseModel):
    name: str
    description: Optional[str] = None
    action: str
    version: Optional[str] = None
    params: dict = Field(default_factory=dict)
    timeout_seconds: int = Field(default=300, ge=1)
    retry: int = Field(default=0, ge=0, le=10)
    is_active: bool = True


class ActionTemplateUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    action: Optional[str] = None
    version: Optional[str] = None
    params: Optional[dict] = None
    timeout_seconds: Optional[int] = Field(default=None, ge=1)
    retry: Optional[int] = Field(default=None, ge=0, le=10)
    is_active: Optional[bool] = None


class ActionTemplateOut(BaseModel):
    id: int
    name: str
    description: Optional[str]
    action: str
    version: Optional[str]
    params: dict
    timeout_seconds: int
    retry: int
    is_active: bool
    created_at: datetime
    updated_at: datetime


def _tool_id_from_action(action: str) -> Optional[int]:
    if not action.startswith("tool:"):
        return None
    try:
        return int(action.split(":", 1)[1])
    except (IndexError, ValueError):
        return None


async def _validate_template_payload(
    *,
    name: str,
    action: str,
    version: Optional[str],
    params: dict,
    timeout_seconds: int,
    retry: int,
    db: AsyncSession,
    current_id: Optional[int] = None,
) -> None:
    if not name.strip():
        raise HTTPException(status_code=422, detail="name cannot be empty")

    if not ACTION_PATTERN.match(action):
        raise HTTPException(
            status_code=422,
            detail="action must match 'builtin:<name>', 'tool:<id>', or 'script:<name>'",
        )

    if not isinstance(params, dict):
        raise HTTPException(status_code=422, detail="params must be an object")

    if timeout_seconds < 1:
        raise HTTPException(status_code=422, detail="timeout_seconds must be >= 1")
    if retry < 0 or retry > 10:
        raise HTTPException(status_code=422, detail="retry must be between 0 and 10")

    tool_id = _tool_id_from_action(action)
    if tool_id is not None:
        if not version:
            raise HTTPException(status_code=422, detail="version is required for tool action")
        tool = await db.get(Tool, tool_id)
        if tool is None or not tool.is_active:
            raise HTTPException(status_code=422, detail=f"tool not found or inactive: {tool_id}")
    elif action.startswith("script:"):
        if not version:
            raise HTTPException(status_code=422, detail="version is required for script action")
    elif version:
        raise HTTPException(status_code=422, detail="version is only allowed for tool or script action")

    q = select(ActionTemplate).where(ActionTemplate.name == name)
    if current_id is not None:
        q = q.where(ActionTemplate.id != current_id)
    existed = (await db.execute(q.limit(1))).scalars().first()
    if existed:
        raise HTTPException(status_code=409, detail=f"action template name already exists: {name}")


@router.get("", response_model=ApiResponse[List[ActionTemplateOut]])
async def list_action_templates(
    is_active: Optional[bool] = None,
    skip: int = 0,
    limit: int = 200,
    db: AsyncSession = Depends(get_async_db),
):
    q = select(ActionTemplate).order_by(ActionTemplate.updated_at.desc(), ActionTemplate.id.desc())
    if is_active is not None:
        q = q.where(ActionTemplate.is_active.is_(is_active))
    rows = (await db.execute(q.offset(skip).limit(limit))).scalars().all()
    return ok([_out(item) for item in rows])


@router.post("", response_model=ApiResponse[ActionTemplateOut], status_code=201)
async def create_action_template(
    payload: ActionTemplateCreate,
    db: AsyncSession = Depends(get_async_db),
):
    await _validate_template_payload(
        name=payload.name,
        action=payload.action,
        version=payload.version,
        params=payload.params,
        timeout_seconds=payload.timeout_seconds,
        retry=payload.retry,
        db=db,
    )

    now = datetime.now(timezone.utc)
    item = ActionTemplate(
        name=payload.name.strip(),
        description=payload.description,
        action=payload.action,
        version=payload.version,
        params=payload.params,
        timeout_seconds=payload.timeout_seconds,
        retry=payload.retry,
        is_active=payload.is_active,
        created_at=now,
        updated_at=now,
    )
    db.add(item)
    await db.commit()
    await db.refresh(item)
    return ok(_out(item))


@router.get("/{template_id}", response_model=ApiResponse[ActionTemplateOut])
async def get_action_template(template_id: int, db: AsyncSession = Depends(get_async_db)):
    item = await db.get(ActionTemplate, template_id)
    if item is None:
        raise HTTPException(status_code=404, detail="action template not found")
    return ok(_out(item))


@router.put("/{template_id}", response_model=ApiResponse[ActionTemplateOut])
async def update_action_template(
    template_id: int,
    payload: ActionTemplateUpdate,
    db: AsyncSession = Depends(get_async_db),
):
    item = await db.get(ActionTemplate, template_id)
    if item is None:
        raise HTTPException(status_code=404, detail="action template not found")

    fields_set = payload.model_fields_set if hasattr(payload, "model_fields_set") else payload.__fields_set__

    merged_name = payload.name.strip() if payload.name is not None else item.name
    merged_action = payload.action if payload.action is not None else item.action
    merged_version = payload.version if "version" in fields_set else item.version
    merged_params = payload.params if payload.params is not None else (item.params or {})
    merged_timeout = payload.timeout_seconds if payload.timeout_seconds is not None else item.timeout_seconds
    merged_retry = payload.retry if payload.retry is not None else item.retry

    await _validate_template_payload(
        name=merged_name,
        action=merged_action,
        version=merged_version,
        params=merged_params,
        timeout_seconds=merged_timeout,
        retry=merged_retry,
        db=db,
        current_id=template_id,
    )

    if payload.name is not None:
        item.name = merged_name
    if "description" in fields_set:
        item.description = payload.description
    if payload.action is not None:
        item.action = payload.action
    if "version" in fields_set:
        item.version = payload.version
    if payload.params is not None:
        item.params = payload.params
    if payload.timeout_seconds is not None:
        item.timeout_seconds = payload.timeout_seconds
    if payload.retry is not None:
        item.retry = payload.retry
    if payload.is_active is not None:
        item.is_active = payload.is_active
    item.updated_at = datetime.now(timezone.utc)

    await db.commit()
    await db.refresh(item)
    return ok(_out(item))


@router.delete("/{template_id}", response_model=ApiResponse[dict])
async def deactivate_action_template(template_id: int, db: AsyncSession = Depends(get_async_db)):
    item = await db.get(ActionTemplate, template_id)
    if item is None:
        raise HTTPException(status_code=404, detail="action template not found")
    item.is_active = False
    item.updated_at = datetime.now(timezone.utc)
    await db.commit()
    return ok({"deactivated": template_id})


def _out(item: ActionTemplate) -> ActionTemplateOut:
    return ActionTemplateOut(
        id=item.id,
        name=item.name,
        description=item.description,
        action=item.action,
        version=item.version,
        params=item.params or {},
        timeout_seconds=item.timeout_seconds,
        retry=item.retry,
        is_active=item.is_active,
        created_at=item.created_at,
        updated_at=item.updated_at,
    )
