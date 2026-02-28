"""Tool Catalog API — uses Phase 1 `tool` table (replaces legacy tools.py).

Provides the tool list used by Agent ToolRegistry.initialize().
Response includes `id`, `version`, `script_path`, `script_class` required by Agent.
"""

import logging
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.response import ApiResponse, ok
from backend.core.database import get_async_db
from backend.models.tool import Tool

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/tools", tags=["tools-v2"])


# ── Schemas ───────────────────────────────────────────────────────────────────

class ToolCreate(BaseModel):
    name: str
    version: str
    script_path: str
    script_class: str
    param_schema: dict = {}
    description: Optional[str] = None
    is_active: bool = True


class ToolUpdate(BaseModel):
    name: Optional[str] = None
    version: Optional[str] = None
    script_path: Optional[str] = None
    script_class: Optional[str] = None
    param_schema: Optional[dict] = None
    description: Optional[str] = None
    is_active: Optional[bool] = None


class ToolOut(BaseModel):
    id: int
    name: str
    version: str
    script_path: str
    script_class: str
    param_schema: dict
    is_active: bool
    description: Optional[str]
    created_at: datetime
    updated_at: datetime


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("", response_model=ApiResponse[List[ToolOut]])
async def list_tools(
    is_active: Optional[bool] = None,
    skip: int = 0,
    limit: int = 200,
    db: AsyncSession = Depends(get_async_db),
):
    """List tools. is_active=true returns only active tools (Agent default)."""
    q = select(Tool).order_by(Tool.name)
    if is_active is not None:
        q = q.where(Tool.is_active.is_(is_active))
    tools = (await db.execute(q.offset(skip).limit(limit))).scalars().all()
    return ok([_tool_out(t) for t in tools])


@router.post("", response_model=ApiResponse[ToolOut], status_code=201)
async def create_tool(
    payload: ToolCreate,
    db: AsyncSession = Depends(get_async_db),
):
    now = datetime.utcnow()
    tool = Tool(
        name=payload.name,
        version=payload.version,
        script_path=payload.script_path,
        script_class=payload.script_class,
        param_schema=payload.param_schema,
        description=payload.description,
        is_active=payload.is_active,
        created_at=now,
        updated_at=now,
    )
    db.add(tool)
    await db.commit()
    await db.refresh(tool)
    return ok(_tool_out(tool))


@router.get("/{tool_id}", response_model=ApiResponse[ToolOut])
async def get_tool(tool_id: int, db: AsyncSession = Depends(get_async_db)):
    tool = await db.get(Tool, tool_id)
    if tool is None:
        raise HTTPException(status_code=404, detail="tool not found")
    return ok(_tool_out(tool))


@router.put("/{tool_id}", response_model=ApiResponse[ToolOut])
async def update_tool(
    tool_id: int,
    payload: ToolUpdate,
    db: AsyncSession = Depends(get_async_db),
):
    tool = await db.get(Tool, tool_id)
    if tool is None:
        raise HTTPException(status_code=404, detail="tool not found")
    if payload.name is not None:
        tool.name = payload.name
    if payload.version is not None:
        tool.version = payload.version
    if payload.script_path is not None:
        tool.script_path = payload.script_path
    if payload.script_class is not None:
        tool.script_class = payload.script_class
    if payload.param_schema is not None:
        tool.param_schema = payload.param_schema
    if payload.description is not None:
        tool.description = payload.description
    if payload.is_active is not None:
        tool.is_active = payload.is_active
    tool.updated_at = datetime.utcnow()
    await db.commit()
    await db.refresh(tool)
    return ok(_tool_out(tool))


@router.delete("/{tool_id}", response_model=ApiResponse[dict])
async def deactivate_tool(tool_id: int, db: AsyncSession = Depends(get_async_db)):
    """Logical delete: set is_active=False. Preserves history for active pipeline_defs."""
    tool = await db.get(Tool, tool_id)
    if tool is None:
        raise HTTPException(status_code=404, detail="tool not found")
    tool.is_active = False
    tool.updated_at = datetime.utcnow()
    await db.commit()
    return ok({"deactivated": tool_id})


# ── Helper ────────────────────────────────────────────────────────────────────

def _tool_out(t: Tool) -> ToolOut:
    return ToolOut(
        id=t.id, name=t.name, version=t.version,
        script_path=t.script_path, script_class=t.script_class,
        param_schema=t.param_schema or {}, is_active=t.is_active,
        description=t.description, created_at=t.created_at, updated_at=t.updated_at,
    )
