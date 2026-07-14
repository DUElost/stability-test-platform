"""Agent protocol + code revision helpers for host display and hot-update audit."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from sqlalchemy.orm.attributes import flag_modified

from backend.services.host_updater import get_agent_code_version

AgentCodeSyncStatus = Literal["unknown", "matched", "drift", "pending"]


def resolve_agent_code_sync_status(
    *,
    agent_code_revision: str | None,
    expected_code_revision: str | None,
    agent_code_deployed: str | None = None,
) -> AgentCodeSyncStatus:
    """Compare heartbeat-reported revision against control-plane expectation."""
    if not expected_code_revision:
        return "unknown"
    if agent_code_revision:
        if agent_code_revision == expected_code_revision:
            return "matched"
        return "drift"
    if agent_code_deployed and agent_code_deployed == expected_code_revision:
        return "pending"
    return "unknown"


def build_host_version_view(extra: dict | None) -> dict:
    """Derive top-level HostOut version fields from host.extra."""
    data = extra if isinstance(extra, dict) else {}
    expected = get_agent_code_version() or None

    def _clean(value: object) -> str | None:
        if not isinstance(value, str):
            return None
        text = value.strip()
        return text or None

    reported = _clean(data.get("agent_code_revision"))
    deployed = _clean(data.get("agent_code_deployed"))
    protocol = _clean(data.get("agent_version"))
    deployed_at = _clean(data.get("agent_code_deployed_at"))

    return {
        "agent_protocol_version": protocol,
        "agent_code_revision": reported,
        "expected_code_revision": expected,
        "agent_code_deployed": deployed,
        "agent_code_deployed_at": deployed_at,
        "agent_code_sync_status": resolve_agent_code_sync_status(
            agent_code_revision=reported,
            expected_code_revision=expected,
            agent_code_deployed=deployed,
        ),
    }


def record_agent_code_deployed(host, code_version: str) -> None:
    """Persist last successful hot-update revision on host.extra."""
    revision = (code_version or "").strip()
    if not revision:
        return
    extra = dict(host.extra or {})
    extra["agent_code_deployed"] = revision
    extra["agent_code_deployed_at"] = datetime.now(timezone.utc).isoformat()
    host.extra = extra
    flag_modified(host, "extra")
