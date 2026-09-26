"""ADR-0021 dispatch gate (precheck) package."""

from __future__ import annotations

import os
from datetime import datetime, timezone

from .notify import NotifyPayload, emit_dispatch_gate_invalidation

VERIFY_TIMEOUT_SECONDS = 10.0
# #3422：单次 gather_verify 的主机并发上限。2026-09-26 生产实测：同一次
# gather 内 ≤5 主机恒成功（579/580），≥30 主机时 ack 大面积超时（581：单轮
# 甚至 30/30；578 全机队：每轮 7–20/48），而 48 个**独立**单机 RPC 全 200。
# admission 全有全无 ⇒ 不设界则大 run 恒不准入。加界后按单机粒度（已证实
# 健康）分波执行；每台仍持有独立的 VERIFY_TIMEOUT 预算，语义不变。
VERIFY_CONCURRENCY = max(1, int(os.getenv("STP_PRECHECK_VERIFY_CONCURRENCY", "5")))
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
    "VERIFY_CONCURRENCY",
    "VERIFY_TIMEOUT_SECONDS",
    "emit_dispatch_gate_invalidation",
    "utc_iso",
]
