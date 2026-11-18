"""#4044: current-heartbeat capability evidence for scan dispatch and admission.

The evidence is intentionally read-only. ``partition_capability_evidence`` is
the interface #4043 consumes before the first DEDUP_FIRST write: it reports
every expected host of one actual PlanRun x platform partition, with a stable
``capability_unverified`` reason for each host whose current Agent heartbeat
does not prove the required capability.

Verification rules (all must hold):

- host exists, is ONLINE, and is not retired;
- ``last_heartbeat`` is within ``HOST_HEARTBEAT_TIMEOUT_SECONDS`` (the same
  freshness window the session watchdog uses);
- capability evidence was written by that exact heartbeat
  (``agent_capabilities_reported_at == last_heartbeat``);
- the required capability is present in the heartbeat's explicit list;
- the reporting ``agent_instance_id`` is present, so the send path can bind
  capability evidence to the local Socket.IO connection.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.agent.contracts.capabilities import SCAN_ROUND_V1, normalize_capabilities
from backend.core.job_timeout_config import HOST_HEARTBEAT_TIMEOUT_SECONDS
from backend.models.host import Host
from backend.services.plan_run_scan_scope import (
    iter_plan_run_scan_hosts,
    load_expected_scan_platforms,
)

REASON_HOST_MISSING = "host_missing"
REASON_HOST_OFFLINE = "host_offline"
REASON_HOST_RETIRED = "host_retired"
REASON_HEARTBEAT_MISSING = "heartbeat_missing"
REASON_HEARTBEAT_STALE = "heartbeat_stale"
REASON_CAPABILITY_NEVER_REPORTED = "capability_never_reported"
REASON_CAPABILITY_NOT_CURRENT = "capability_not_current_heartbeat"
REASON_CAPABILITY_MISSING = "capability_missing"
REASON_INSTANCE_UNBOUND = "instance_unbound"


@dataclass(frozen=True)
class HostCapabilityEvidence:
    host_id: str
    verified: bool
    reason: str | None
    status: str | None
    capabilities: tuple[str, ...]
    instance_id: str | None
    heartbeat_at: datetime | None
    capability_reported_at: datetime | None
    retired: bool = False

    def as_dict(self) -> dict:
        return {
            "host_id": self.host_id,
            "verified": self.verified,
            "reason": self.reason,
            "status": self.status,
            "capabilities": list(self.capabilities),
            "instance_id": self.instance_id,
            "heartbeat_at": self.heartbeat_at.isoformat() if self.heartbeat_at else None,
            "capability_reported_at": (
                self.capability_reported_at.isoformat()
                if self.capability_reported_at
                else None
            ),
            "retired": self.retired,
        }


@dataclass(frozen=True)
class PartitionCapabilityEvidence:
    plan_run_id: int
    platform: str
    hosts: tuple[HostCapabilityEvidence, ...]

    @property
    def admitted(self) -> bool:
        return all(host.verified for host in self.hosts)

    @property
    def unverified(self) -> tuple[HostCapabilityEvidence, ...]:
        return tuple(host for host in self.hosts if not host.verified)

    def as_dict(self) -> dict:
        return {
            "plan_run_id": self.plan_run_id,
            "platform": self.platform,
            "admitted": self.admitted,
            "hosts": [host.as_dict() for host in self.hosts],
        }


def evaluate_host_capability(
    host: Host | None,
    *,
    now: datetime,
    required: str = SCAN_ROUND_V1,
    retired: bool = False,
) -> HostCapabilityEvidence:
    if host is None:
        return HostCapabilityEvidence(
            host_id="", verified=False, reason=REASON_HOST_MISSING,
            status=None, capabilities=(), instance_id=None,
            heartbeat_at=None, capability_reported_at=None, retired=retired,
        )
    capabilities = normalize_capabilities(host.agent_capabilities)
    evidence = HostCapabilityEvidence(
        host_id=str(host.id),
        verified=False,
        reason=None,
        status=host.status,
        capabilities=capabilities,
        instance_id=host.agent_capabilities_instance_id or None,
        heartbeat_at=host.last_heartbeat,
        capability_reported_at=host.agent_capabilities_reported_at,
        retired=retired,
    )
    if host.status != "ONLINE":
        evidence = _with_reason(evidence, REASON_HOST_OFFLINE)
    elif retired:
        evidence = _with_reason(evidence, REASON_HOST_RETIRED)
    elif host.last_heartbeat is None:
        evidence = _with_reason(evidence, REASON_HEARTBEAT_MISSING)
    elif host.last_heartbeat < now - timedelta(seconds=HOST_HEARTBEAT_TIMEOUT_SECONDS):
        evidence = _with_reason(evidence, REASON_HEARTBEAT_STALE)
    elif host.agent_capabilities_reported_at is None:
        evidence = _with_reason(evidence, REASON_CAPABILITY_NEVER_REPORTED)
    elif host.agent_capabilities_reported_at != host.last_heartbeat:
        evidence = _with_reason(evidence, REASON_CAPABILITY_NOT_CURRENT)
    elif required not in capabilities:
        evidence = _with_reason(evidence, REASON_CAPABILITY_MISSING)
    elif not evidence.instance_id:
        evidence = _with_reason(evidence, REASON_INSTANCE_UNBOUND)
    else:
        evidence = replace(evidence, verified=True)
    return evidence


def _with_reason(evidence: HostCapabilityEvidence, reason: str) -> HostCapabilityEvidence:
    return replace(evidence, reason=reason)


def load_host_capability_evidence(
    db: Session,
    host_id: str,
    *,
    now: datetime | None = None,
    required: str = SCAN_ROUND_V1,
) -> HostCapabilityEvidence:
    host = db.get(Host, str(host_id))
    evidence = evaluate_host_capability(
        host,
        now=now or datetime.now(timezone.utc),
        required=required,
        retired=bool(host is not None and host.retired_at is not None),
    )
    if host is None:
        evidence = replace(evidence, host_id=str(host_id))
    return evidence


def partition_capability_evidence(
    db: Session,
    *,
    plan_run_id: int,
    platform: str,
    now: datetime | None = None,
    required: str = SCAN_ROUND_V1,
) -> PartitionCapabilityEvidence:
    """Return capability evidence for every expected host of this partition.

    The expected-host source matches ``shadow_scan_rounds._expected_units``:
    actual jobs plus the prepare-time target snapshot, filtered by the
    platform each host is expected to produce. Retired hosts stay visible and
    are reported unverified (fail closed) rather than silently dropped.
    """
    now = now or datetime.now(timezone.utc)
    scope = {
        host_id: (status, retired)
        for host_id, status, retired in iter_plan_run_scan_hosts(db, plan_run_id)
    }
    expected = load_expected_scan_platforms(db, plan_run_id, list(scope))
    expected_ids = sorted(
        host_id for host_id, platforms in expected.items() if platform in platforms
    )
    hosts = {}
    if expected_ids:
        hosts = {
            str(host.id): host
            for host in db.execute(
                select(Host).where(Host.id.in_(expected_ids))
            ).scalars()
        }
    evidence = []
    for host_id in expected_ids:
        status, retired = scope.get(host_id, ("OFFLINE", False))
        host = hosts.get(host_id)
        if host is None:
            evidence.append(HostCapabilityEvidence(
                host_id=host_id, verified=False, reason=REASON_HOST_MISSING,
                status=status, capabilities=(), instance_id=None,
                heartbeat_at=None, capability_reported_at=None, retired=retired,
            ))
            continue
        evidence.append(evaluate_host_capability(
            host, now=now, required=required,
            retired=retired or host.retired_at is not None,
        ))
    return PartitionCapabilityEvidence(
        plan_run_id=plan_run_id, platform=platform, hosts=tuple(evidence),
    )
