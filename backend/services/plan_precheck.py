"""ADR-0021 C3 — Dispatch gate public facade.

Implementation is split under :mod:`backend.services.precheck`; this module
re-exports the stable surface used by routes, SAQ tasks, and tests.
"""

from backend.services.precheck import (
    DISPATCH_SYNC_MAX_ATTEMPTS,
    MIXED_WATCHER_ACTIVITY_CODE,
    MIXED_WATCHER_ACTIVITY_MESSAGE,
    SYNC_SETTLE_SECONDS,
    VERIFY_TIMEOUT_SECONDS,
)
from backend.services.precheck.runner import (
    PlanRunDispatchRetryError,
    _drive_dispatch_gate,
    _expected_scripts_for_run,
    _gather_verify,
    _mark_precheck_failed,
    _persist_precheck,
    _push_mismatched_scripts,
    _sync_host_via_hot_update,
    _utc_iso,
    drive_dispatch_gate,
    initialise_precheck_state,
    precheck_and_dispatch_task,
    retry_plan_run_dispatch,
)
from backend.realtime.socketio_server import call_agent_rpc

__all__ = [
    "DISPATCH_SYNC_MAX_ATTEMPTS",
    "MIXED_WATCHER_ACTIVITY_CODE",
    "MIXED_WATCHER_ACTIVITY_MESSAGE",
    "SYNC_SETTLE_SECONDS",
    "VERIFY_TIMEOUT_SECONDS",
    "PlanRunDispatchRetryError",
    "_drive_dispatch_gate",
    "_expected_scripts_for_run",
    "_gather_verify",
    "_mark_precheck_failed",
    "_persist_precheck",
    "_push_mismatched_scripts",
    "_sync_host_via_hot_update",
    "_utc_iso",
    "call_agent_rpc",
    "drive_dispatch_gate",
    "initialise_precheck_state",
    "precheck_and_dispatch_task",
    "retry_plan_run_dispatch",
]
