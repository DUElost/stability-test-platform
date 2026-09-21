"""控制面 Jira 厂商工具防腐层（ADR-0033 Phase A 样板）。

从 ``api/routes/dedup`` 下沉 argv / env 接缝；编排与 HTTP 仍在路由。
"""
from __future__ import annotations

from backend.services.jira_vendor.base import JiraVendorEngine
from backend.services.jira_vendor.stability_jira import (
    StabilityJiraAutomationEngine,
    build_jira_argv,
    get_jira_vendor_engine,
    load_vendor_tool_env,
    reset_jira_vendor_engine_for_tests,
    resolve_vendor_tool,
)

__all__ = [
    "JiraVendorEngine",
    "StabilityJiraAutomationEngine",
    "build_jira_argv",
    "get_jira_vendor_engine",
    "load_vendor_tool_env",
    "reset_jira_vendor_engine_for_tests",
    "resolve_vendor_tool",
]
