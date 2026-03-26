# -*- coding: utf-8 -*-
"""
DEPRECATED — 旧工具管理 API，基于 schemas.py 的 Tool/ToolCategory 模型。

该路由已被 tool_catalog.py 取代（使用 tool.py 的新 Tool 模型）。
自 Wave 1 (ADR-0008) 起不再挂载。禁止恢复挂载。
"""

_DEPRECATED = True

raise ImportError(
    "backend.api.routes.tools is DEPRECATED. "
    "Use backend.api.routes.tool_catalog instead. "
    "See ADR-0008 Wave 1 and dual-track-merger-v3."
)
