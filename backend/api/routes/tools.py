# -*- coding: utf-8 -*-
"""
工具管理 API 路由
"""

from typing import List
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from backend.core.database import get_db
from backend.models.schemas import ToolCategory, Tool
from backend.api.schemas import (
    ToolCategoryCreate,
    ToolCategoryOut,
    ToolCreate,
    ToolOut,
    ToolRunCreate,
    ToolRunOut,
)

router = APIRouter(prefix="/api/v1/tools", tags=["tools"])


# ==================== 专项分类管理 ====================

@router.get("/categories", response_model=List[ToolCategoryOut])
def list_categories(
    db: Session = Depends(get_db),
    enabled: bool = Query(None, description="过滤启用的分类"),
):
    """获取专项分类列表"""
    query = db.query(ToolCategory)
    if enabled is not None:
        query = query.filter(ToolCategory.enabled == enabled)
    query = query.order_by(ToolCategory.order, ToolCategory.name)

    categories = query.all()

    # 统计每个分类的工具数量
    result = []
    for cat in categories:
        tool_count = db.query(Tool).filter(
            Tool.category_id == cat.id,
            Tool.enabled == True
        ).count()

        result.append(ToolCategoryOut(
            id=cat.id,
            name=cat.name,
            description=cat.description,
            icon=cat.icon,
            order=cat.order,
            enabled=cat.enabled,
            created_at=cat.created_at,
            tools_count=tool_count,
        ))

    return result


@router.post("/categories", response_model=ToolCategoryOut)
def create_category(
    data: ToolCategoryCreate,
    db: Session = Depends(get_db),
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
):
    """删除专项分类（同时删除分类下的工具）"""
    category = db.query(ToolCategory).filter_by(id=category_id).first()
    if not category:
        raise HTTPException(status_code=404, detail="分类不存在")

    # 删除分类下的所有工具
    db.query(Tool).filter_by(category_id=category_id).delete()

    # 删除分类
    db.delete(category)
    db.commit()

    return {"message": "删除成功"}


# ==================== 工具管理 ====================

@router.get("", response_model=List[ToolOut])
def list_tools(
    category_id: int = Query(None, description="分类ID"),
    enabled: bool = Query(None, description="过滤启用的工具"),
    db: Session = Depends(get_db),
):
    """获取工具列表"""
    query = db.query(Tool)
    if category_id is not None:
        query = query.filter(Tool.category_id == category_id)
    if enabled is not None:
        query = query.filter(Tool.enabled == enabled)

    query = query.order_by(Tool.name)

    tools = query.all()
    result = []

    for tool in tools:
        category = db.query(ToolCategory).filter_by(id=tool.category_id).first()
        result.append(ToolOut(
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
        ))

    return result


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
):
    """删除工具"""
    tool = db.query(Tool).filter_by(id=tool_id).first()
    if not tool:
        raise HTTPException(status_code=404, detail="工具不存在")

    db.delete(tool)
    db.commit()

    return {"message": "删除成功"}


# ==================== 工具扫描与同步 ====================

@router.post("/scan")
def scan_tools(db: Session = Depends(get_db)):
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
