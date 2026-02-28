# -*- coding: utf-8 -*-
"""
工具管理 API 路由
"""

from typing import List
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.core.database import get_db
from backend.core.tool_bootstrap import ensure_monkey_aee_tool
from backend.core.audit import record_audit
from backend.models.schemas import ToolCategory, Tool
from backend.api.routes.auth import get_current_active_user, User
from backend.api.schemas import (
    PaginatedResponse,
    ToolCategoryCreate,
    ToolCategoryOut,
    ToolCreate,
    ToolOut,
    ToolRunCreate,
    ToolRunOut,
)

router = APIRouter(prefix="/api/v1/tools", tags=["tools"])


# ==================== 专项分类管理 ====================

@router.get("/categories", response_model=PaginatedResponse)
def list_categories(
    db: Session = Depends(get_db),
    enabled: bool = Query(None, description="过滤启用的分类"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
):
    """获取专项分类列表"""
    query = db.query(ToolCategory)
    if enabled is not None:
        query = query.filter(ToolCategory.enabled == enabled)
    query = query.order_by(ToolCategory.order, ToolCategory.name)

    total = query.count()
    categories = query.offset(skip).limit(limit).all()

    # 统计每个分类的工具数量 (single GROUP BY instead of N+1)
    tool_counts = dict(
        db.query(Tool.category_id, func.count(Tool.id))
        .filter(Tool.enabled == True)
        .group_by(Tool.category_id)
        .all()
    )

    result = []
    for cat in categories:
        result.append(ToolCategoryOut(
            id=cat.id,
            name=cat.name,
            description=cat.description,
            icon=cat.icon,
            order=cat.order,
            enabled=cat.enabled,
            created_at=cat.created_at,
            tools_count=tool_counts.get(cat.id, 0),
        ))

    return PaginatedResponse(items=result, total=total, skip=skip, limit=limit)


@router.post("/categories", response_model=ToolCategoryOut)
def create_category(
    data: ToolCategoryCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    request: Request = None,
):
    """创建专项分类"""
    # 检查名称是否已存在
    existing = db.query(ToolCategory).filter_by(name=data.name).first()
    if existing:
        raise HTTPException(status_code=400, detail="分类名称已存在")

    category = ToolCategory(
        name=data.name,
        description=data.description,
        icon=data.icon,
        order=data.order,
        enabled=data.enabled,
    )
    db.add(category)
    db.flush()
    record_audit(
        db,
        action="create",
        resource_type="tool_category",
        resource_id=category.id,
        details={"name": category.name, "enabled": category.enabled},
        user_id=current_user.id,
        username=current_user.username,
        request=request,
    )
    db.commit()
    db.refresh(category)

    return ToolCategoryOut(
        id=category.id,
        name=category.name,
        description=category.description,
        icon=category.icon,
        order=category.order,
        enabled=category.enabled,
        created_at=category.created_at,
        tools_count=0,
    )


@router.put("/categories/{category_id}", response_model=ToolCategoryOut)
def update_category(
    category_id: int,
    data: ToolCategoryCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    request: Request = None,
):
    """更新专项分类"""
    category = db.query(ToolCategory).filter_by(id=category_id).first()
    if not category:
        raise HTTPException(status_code=404, detail="分类不存在")

    # 检查名称是否与其他分类冲突
    if data.name != category.name:
        existing = db.query(ToolCategory).filter_by(name=data.name).first()
        if existing:
            raise HTTPException(status_code=400, detail="分类名称已存在")

    category.name = data.name
    category.description = data.description
    category.icon = data.icon
    category.order = data.order
    category.enabled = data.enabled

    record_audit(
        db,
        action="update",
        resource_type="tool_category",
        resource_id=category.id,
        details={"name": category.name, "enabled": category.enabled},
        user_id=current_user.id,
        username=current_user.username,
        request=request,
    )
    db.commit()
    db.refresh(category)

    tool_count = db.query(Tool).filter(
        Tool.category_id == category.id,
        Tool.enabled == True
    ).count()

    return ToolCategoryOut(
        id=category.id,
        name=category.name,
        description=category.description,
        icon=category.icon,
        order=category.order,
        enabled=category.enabled,
        created_at=category.created_at,
        tools_count=tool_count,
    )


@router.delete("/categories/{category_id}")
def delete_category(
    category_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    request: Request = None,
):
    """删除专项分类（同时删除分类下的工具）"""
    category = db.query(ToolCategory).filter_by(id=category_id).first()
    if not category:
        raise HTTPException(status_code=404, detail="分类不存在")

    cat_name = category.name
    tool_count = db.query(Tool).filter(Tool.category_id == category_id).count()

    # 删除分类下的所有工具
    db.query(Tool).filter_by(category_id=category_id).delete()

    # 删除分类
    db.delete(category)
    record_audit(
        db,
        action="delete",
        resource_type="tool_category",
        resource_id=category_id,
        details={"name": cat_name, "tools_deleted_count": tool_count},
        user_id=current_user.id,
        username=current_user.username,
        request=request,
    )
    db.commit()

    return {"message": "删除成功"}


# ==================== 工具管理 ====================

@router.get("", response_model=PaginatedResponse)
def list_tools(
    category_id: int = Query(None, description="分类ID"),
    enabled: bool = Query(None, description="过滤启用的工具"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    """获取工具列表"""
    query = db.query(Tool)
    if category_id is not None:
        query = query.filter(Tool.category_id == category_id)
    if enabled is not None:
        query = query.filter(Tool.enabled == enabled)

    query = query.order_by(Tool.name)

    total = query.count()
    tools = query.offset(skip).limit(limit).all()

    # Prefetch category names in one query (avoid N+1)
    category_ids = {t.category_id for t in tools if t.category_id}
    cat_map = {}
    if category_ids:
        cats = db.query(ToolCategory).filter(ToolCategory.id.in_(category_ids)).all()
        cat_map = {c.id: c.name for c in cats}

    result = []
    for tool in tools:
        result.append(ToolOut(
            id=tool.id,
            category_id=tool.category_id,
            category_name=cat_map.get(tool.category_id),
            name=tool.name,
            description=tool.description,
            script_path=tool.script_path,
            script_class=tool.script_class,
            script_type=tool.script_type,
            default_params=tool.default_params,
            param_schema=tool.param_schema,
            timeout=tool.timeout,
            need_device=tool.need_device,
            enabled=tool.enabled,
            created_at=tool.created_at,
            updated_at=tool.updated_at,
        ))

    return PaginatedResponse(items=result, total=total, skip=skip, limit=limit)


@router.get("/{tool_id}", response_model=ToolOut)
def get_tool(
    tool_id: int,
    db: Session = Depends(get_db),
):
    """获取工具详情"""
    tool = db.query(Tool).filter_by(id=tool_id).first()
    if not tool:
        raise HTTPException(status_code=404, detail="工具不存在")

    category = db.query(ToolCategory).filter_by(id=tool.category_id).first()

    return ToolOut(
        id=tool.id,
        category_id=tool.category_id,
        category_name=category.name if category else None,
        name=tool.name,
        description=tool.description,
        script_path=tool.script_path,
        script_class=tool.script_class,
        script_type=tool.script_type,
        default_params=tool.default_params,
        param_schema=tool.param_schema,
        timeout=tool.timeout,
        need_device=tool.need_device,
        enabled=tool.enabled,
        created_at=tool.created_at,
        updated_at=tool.updated_at,
    )


@router.post("", response_model=ToolOut)
def create_tool(
    data: ToolCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    request: Request = None,
):
    """创建工具配置"""
    # 验证分类存在
    category = db.query(ToolCategory).filter_by(id=data.category_id).first()
    if not category:
        raise HTTPException(status_code=400, detail="分类不存在")

    tool = Tool(
        category_id=data.category_id,
        name=data.name,
        description=data.description,
        script_path=data.script_path,
        script_class=data.script_class,
        script_type=data.script_type,
        default_params=data.default_params,
        param_schema=data.param_schema,
        timeout=data.timeout,
        need_device=data.need_device,
        enabled=data.enabled,
    )
    db.add(tool)
    db.flush()
    record_audit(
        db,
        action="create",
        resource_type="tool",
        resource_id=tool.id,
        details={"name": tool.name, "category_id": tool.category_id,
                 "enabled": tool.enabled, "script_path": tool.script_path},
        user_id=current_user.id,
        username=current_user.username,
        request=request,
    )
    db.commit()
    db.refresh(tool)

    return ToolOut(
        id=tool.id,
        category_id=tool.category_id,
        category_name=category.name,
        name=tool.name,
        description=tool.description,
        script_path=tool.script_path,
        script_class=tool.script_class,
        script_type=tool.script_type,
        default_params=tool.default_params,
        param_schema=tool.param_schema,
        timeout=tool.timeout,
        need_device=tool.need_device,
        enabled=tool.enabled,
        created_at=tool.created_at,
        updated_at=tool.updated_at,
    )


@router.put("/{tool_id}", response_model=ToolOut)
def update_tool(
    tool_id: int,
    data: ToolCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    request: Request = None,
):
    """更新工具配置"""
    tool = db.query(Tool).filter_by(id=tool_id).first()
    if not tool:
        raise HTTPException(status_code=404, detail="工具不存在")

    # 验证分类存在
    category = db.query(ToolCategory).filter_by(id=data.category_id).first()
    if not category:
        raise HTTPException(status_code=400, detail="分类不存在")

    tool.category_id = data.category_id
    tool.name = data.name
    tool.description = data.description
    tool.script_path = data.script_path
    tool.script_class = data.script_class
    tool.script_type = data.script_type
    tool.default_params = data.default_params
    tool.param_schema = data.param_schema
    tool.timeout = data.timeout
    tool.need_device = data.need_device
    tool.enabled = data.enabled

    record_audit(
        db,
        action="update",
        resource_type="tool",
        resource_id=tool.id,
        details={"name": tool.name, "category_id": tool.category_id, "enabled": tool.enabled},
        user_id=current_user.id,
        username=current_user.username,
        request=request,
    )
    db.commit()
    db.refresh(tool)

    return ToolOut(
        id=tool.id,
        category_id=tool.category_id,
        category_name=category.name,
        name=tool.name,
        description=tool.description,
        script_path=tool.script_path,
        script_class=tool.script_class,
        script_type=tool.script_type,
        default_params=tool.default_params,
        param_schema=tool.param_schema,
        timeout=tool.timeout,
        need_device=tool.need_device,
        enabled=tool.enabled,
        created_at=tool.created_at,
        updated_at=tool.updated_at,
    )


@router.delete("/{tool_id}")
def delete_tool(
    tool_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    request: Request = None,
):
    """删除工具"""
    tool = db.query(Tool).filter_by(id=tool_id).first()
    if not tool:
        raise HTTPException(status_code=404, detail="工具不存在")

    tool_name = tool.name
    tool_category_id = tool.category_id
    db.delete(tool)
    record_audit(
        db,
        action="delete",
        resource_type="tool",
        resource_id=tool_id,
        details={"name": tool_name, "category_id": tool_category_id},
        user_id=current_user.id,
        username=current_user.username,
        request=request,
    )
    db.commit()

    return {"message": "删除成功"}


# ==================== 工具扫描与同步 ====================

@router.post("/scan")
def scan_tools(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """
    扫描工具目录并同步到数据库
    扫描路径: /home/android/sonic_agent/logs/ftp_log/sonic_tinno/Test_Tool
    """
    from backend.agent.tool_discovery import ToolDiscoveryService

    try:
        service = ToolDiscoveryService(db)
        result = service.sync()
        return {
            "message": "扫描完成",
            "result": result,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"扫描失败: {str(e)}")


@router.post("/bootstrap/monkey-aee", response_model=ToolOut)
def bootstrap_monkey_aee_tool(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """幂等创建/更新 MONKEY_AEE 工具。"""
    try:
        tool, _created = ensure_monkey_aee_tool(db)
        category = db.query(ToolCategory).filter_by(id=tool.category_id).first()
        return ToolOut(
            id=tool.id,
            category_id=tool.category_id,
            category_name=category.name if category else None,
            name=tool.name,
            description=tool.description,
            script_path=tool.script_path,
            script_class=tool.script_class,
            script_type=tool.script_type,
            default_params=tool.default_params,
            param_schema=tool.param_schema,
            timeout=tool.timeout,
            need_device=tool.need_device,
            enabled=tool.enabled,
            created_at=tool.created_at,
            updated_at=tool.updated_at,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"bootstrap failed: {str(e)}")


@router.get("/scan/preview")
def preview_scan():
    """
    预览扫描结果（不写入数据库）
    """
    from backend.agent.tool_discovery import ToolDiscovery

    try:
        discovery = ToolDiscovery()
        tools = discovery.scan()
        return {
            "tools": tools,
            "count": len(tools),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"扫描失败: {str(e)}")
