"""ADR-0021 C3 — Dispatch gate public facade.

Implementation lives under :mod:`backend.services.precheck`; this module
re-exports the stable surface used by routes, SAQ tasks, and tests.
"""

from backend.realtime.socketio_server import call_agent_rpc
from backend.services.plan_dispatcher_sync import complete_plan_run_dispatch

from backend.services.precheck import (
    DISPATCH_SYNC_MAX_ATTEMPTS,
    MIXED_WATCHER_ACTIVITY_CODE,
    MIXED_WATCHER_ACTIVITY_MESSAGE,
    SYNC_SETTLE_SECONDS,
    VERIFY_TIMEOUT_SECONDS,
    utc_iso,
)
from backend.services.precheck.notify import NotifyPayload
from backend.services.precheck.runner import (
    PlanRunDispatchRetryError,
    _drive_dispatch_gate,
    drive_dispatch_gate,
    precheck_and_dispatch_task,
    retry_plan_run_dispatch,
)
from backend.services.precheck.scripts import (
    _expected_scripts_for_run,
    expected_scripts_for_run,
)
from backend.services.precheck.state import (
    _initial_precheck_state,
    _mark_precheck_failed,
    _persist_precheck,
    _update_dispatch_state,
    initial_precheck_state,
    initialise_precheck_state,
    mark_precheck_failed,
    persist_precheck,
)
from backend.services.precheck.sync import (
    _push_mismatched_scripts,
    _sync_host_via_hot_update,
    push_mismatched_scripts,
    sync_host_via_hot_update,
)
from backend.services.precheck.verify import _gather_verify, _verify_one_host, gather_verify

_utc_iso = utc_iso

__all__ = [
    "DISPATCH_SYNC_MAX_ATTEMPTS",
    "MIXED_WATCHER_ACTIVITY_CODE",
    "MIXED_WATCHER_ACTIVITY_MESSAGE",
    "NotifyPayload",
    "SYNC_SETTLE_SECONDS",
    "VERIFY_TIMEOUT_SECONDS",
    "PlanRunDispatchRetryError",
    "_drive_dispatch_gate",
    "_expected_scripts_for_run",
    "_gather_verify",
    "_initial_precheck_state",
    "_mark_precheck_failed",
    "_persist_precheck",
    "_push_mismatched_scripts",
    "_sync_host_via_hot_update",
    "_update_dispatch_state",
    "_utc_iso",
    "_verify_one_host",
    "call_agent_rpc",
    "complete_plan_run_dispatch",
    "drive_dispatch_gate",
    "expected_scripts_for_run",
    "gather_verify",
    "initial_precheck_state",
    "initialise_precheck_state",
    "mark_precheck_failed",
    "persist_precheck",
    "precheck_and_dispatch_task",
    "push_mismatched_scripts",
    "retry_plan_run_dispatch",
    "sync_host_via_hot_update",
    "utc_iso",
]
