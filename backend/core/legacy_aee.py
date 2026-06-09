"""Shared legacy AEE script guardrails."""

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
