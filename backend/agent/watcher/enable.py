"""Watcher enablement helpers — global env + per-plan defaults."""

from __future__ import annotations

import os
from typing import Any, Dict


def watcher_subsystem_enabled() -> bool:
    """Whether to configure LogWatcherManager / OutboxDrainer at agent startup."""
    global_on = os.getenv("STP_WATCHER_ENABLED", "false").lower() == "true"
    plan_default = os.getenv("STP_WATCHER_PLAN_DEFAULT", "true").lower() == "true"
    return global_on or plan_default


def job_wants_watcher(
    run: Dict[str, Any],
    *,
    globally_enabled: bool,
    plan_default: bool,
) -> bool:
    """Per-job watcher decision: global on, or Plan execution with default-on."""
    if globally_enabled:
        return True
    if not plan_default:
        return False

    watcher_policy = run.get("watcher_policy") or {}
    if watcher_policy.get("enabled") is False:
        return False

    pipeline_def = run.get("pipeline_def") or {}
    if run.get("plan_id") or pipeline_def.get("lifecycle"):
        return True
    return False
