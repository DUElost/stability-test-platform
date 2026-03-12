# -*- coding: utf-8 -*-
"""
内置工具引导：确保关键工具在数据库中存在。
"""

from __future__ import annotations

from pathlib import Path
from typing import Tuple

from sqlalchemy.orm import Session

from backend.agent.tools.config.monkey_aee_defaults import (
    MONKEY_AEE_PARAM_SCHEMA,
    build_monkey_aee_defaults,
)
from backend.models.schemas import Tool, ToolCategory


MONKEY_CATEGORY_NAME = "Monkey"
MONKEY_AEE_TOOL_NAME = "MONKEY_AEE Stability"


def _monkey_aee_script_path() -> str:
    backend_dir = Path(__file__).resolve().parents[1]
    return str((backend_dir / "agent" / "tools" / "monkey_aee_stability_test.py").resolve())


def ensure_monkey_aee_tool(db: Session) -> Tuple[Tool, bool]:
    """确保 MONKEY_AEE 工具存在，返回 (tool, created)。"""
    category = db.query(ToolCategory).filter(ToolCategory.name == MONKEY_CATEGORY_NAME).first()
    if not category:
        category = ToolCategory(
            name=MONKEY_CATEGORY_NAME,
            description="Monkey 稳定性测试工具",
            icon="activity",
            order=10,
            enabled=True,
        )
        db.add(category)
        db.flush()

    expected_script_path = _monkey_aee_script_path()
    tool = (
        db.query(Tool)
        .filter(
            Tool.script_class == "MonkeyAEEAction",
            Tool.script_path == expected_script_path,
        )
        .first()
    )
    if not tool:
        tool = (
            db.query(Tool)
            .filter(
                Tool.name == MONKEY_AEE_TOOL_NAME,
                Tool.category_id == category.id,
            )
            .first()
        )

    created = False
    if not tool:
        tool = Tool(
            category_id=category.id,
            name=MONKEY_AEE_TOOL_NAME,
            description="MONKEY_AEE 专项稳定性测试（兼容旧脚本）",
            script_path=expected_script_path,
            script_class="MonkeyAEEAction",
            script_type="python",
            default_params=build_monkey_aee_defaults(),
            param_schema=MONKEY_AEE_PARAM_SCHEMA,
            timeout=21600,
            need_device=True,
            enabled=True,
        )
        db.add(tool)
        created = True
    else:
        tool.category_id = category.id
        tool.description = "MONKEY_AEE 专项稳定性测试（兼容旧脚本）"
        tool.script_path = expected_script_path
        tool.script_class = "MonkeyAEEAction"
        tool.script_type = "python"
        tool.default_params = build_monkey_aee_defaults()
        tool.param_schema = MONKEY_AEE_PARAM_SCHEMA
        tool.timeout = 21600
        tool.need_device = True
        tool.enabled = True

    db.commit()
    db.refresh(tool)
    return tool, created

