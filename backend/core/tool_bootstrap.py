# -*- coding: utf-8 -*-
"""
内置工具引导：确保关键工具在数据库中存在。

使用新 Tool 模型（table: tool）——分类通过 Tool.category 字符串字段管理，
不再依赖 ToolCategory 表。
"""

from __future__ import annotations

from pathlib import Path
from typing import Tuple

from sqlalchemy.orm import Session

from backend.agent.tools.config.monkey_aee_defaults import (
    MONKEY_AEE_PARAM_SCHEMA,
    build_monkey_aee_defaults,
)
from backend.models.tool import Tool


MONKEY_CATEGORY = "Monkey"
MONKEY_AEE_TOOL_NAME = "MONKEY_AEE Stability"
MONKEY_AEE_VERSION = "1.0.0"


def _monkey_aee_script_path() -> str:
    backend_dir = Path(__file__).resolve().parents[1]
    return str((backend_dir / "agent" / "tools" / "monkey_aee_stability_test.py").resolve())


def ensure_monkey_aee_tool(db: Session) -> Tuple[Tool, bool]:
    """确保 MONKEY_AEE 工具存在，返回 (tool, created)。"""
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
            .filter(Tool.name == MONKEY_AEE_TOOL_NAME)
            .first()
        )

    param_schema = {
        **MONKEY_AEE_PARAM_SCHEMA,
        "_defaults": build_monkey_aee_defaults(),
    }

    created = False
    if not tool:
        tool = Tool(
            name=MONKEY_AEE_TOOL_NAME,
            version=MONKEY_AEE_VERSION,
            description="MONKEY_AEE 专项稳定性测试（兼容旧脚本）",
            script_path=expected_script_path,
            script_class="MonkeyAEEAction",
            param_schema=param_schema,
            category=MONKEY_CATEGORY,
            is_active=True,
        )
        db.add(tool)
        created = True
    else:
        tool.description = "MONKEY_AEE 专项稳定性测试（兼容旧脚本）"
        tool.script_path = expected_script_path
        tool.script_class = "MonkeyAEEAction"
        tool.param_schema = param_schema
        tool.category = MONKEY_CATEGORY
        tool.is_active = True

    db.commit()
    db.refresh(tool)
    return tool, created
