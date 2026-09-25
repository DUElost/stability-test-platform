"""Shared legacy AEE script guardrails.

契约模块（ADR-0054 D1）：控制面与 Agent 共用这一份常量表——Agent 侧经
``from ..contracts.legacy_aee import …`` 相对导入，两种包布局（``backend.agent`` /
顶层 ``agent``）下都不再需要 ``except ImportError`` 兜底或本地副本。
"""

from __future__ import annotations

LEGACY_AEE_SCRIPT_NAMES = frozenset({"scan_aee", "export_mobilelogs"})
LEGACY_AEE_TEMPLATE_NAMES = frozenset(
    {
        "aimonkey",
        "aimonkey_launcher_lifecycle",
        "monkey_aee",
        "monkey_aee_patrol",
        "monkey_aee_init",
        "monkey_aee_lifecycle",
        "monkey_aee_teardown",
    }
)
