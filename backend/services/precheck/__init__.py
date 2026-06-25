"""ADR-0021 dispatch gate (precheck) package."""

from __future__ import annotations

import os
from datetime import datetime, timezone

from .notify import NotifyPayload, emit_dispatch_gate_invalidation
from .state import initial_precheck_state, initialise_precheck_state

VERIFY_TIMEOUT_SECONDS = 10.0
SYNC_SETTLE_SECONDS = 8.0
DISPATCH_SYNC_MAX_ATTEMPTS = max(1, int(os.getenv("DISPATCH_SYNC_MAX_ATTEMPTS", "1")))
MIXED_WATCHER_ACTIVITY_CODE = "MIXED_WATCHER_ACTIVITY"
MIXED_WATCHER_ACTIVITY_MESSAGE = "watch激活与不激活的节点不能同时在一个计划中"

_REMOTE_AGENT_PREFIX = "/opt/stability-test-agent/agent/"


def utc_iso() -> str:
    """Return UTC now as ISO-8601 string with Z suffix."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


# Back-compat alias used across submodules during migration.
_utc_iso = utc_iso

__all__ = [
    "DISPATCH_SYNC_MAX_ATTEMPTS",
    "MIXED_WATCHER_ACTIVITY_CODE",
    "MIXED_WATCHER_ACTIVITY_MESSAGE",
    "NotifyPayload",
    "SYNC_SETTLE_SECONDS",
    "VERIFY_TIMEOUT_SECONDS",
    "emit_dispatch_gate_invalidation",
    "initial_precheck_state",
    "initialise_precheck_state",
    "utc_iso",
]
