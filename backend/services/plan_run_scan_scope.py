"""PlanRun scan/merge device scope — full device set across all hosts.

A PlanRun's devices commonly sit on many hosts. Scan/merge must use that
whole serial list, not one host's local subset. Each Agent still only
reads its own HDD, but the payload it receives is the PlanRun-wide set;
Merge then unions every host's org xls.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Iterable
from zoneinfo import ZoneInfo

from sqlalchemy import select

from backend.models.host import Device, Host
from backend.models.job import JobInstance
from backend.models.plan_run import PlanRunHost, PlanRunTargetDevice

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

_SHANGHAI = ZoneInfo("Asia/Shanghai")


def run_date_stamp_from_started_at(started_at: datetime | None) -> str | None:
    """Agent folder stamp is Asia/Shanghai MMDD (`get_or_create_run_date_stamp`)."""
    if started_at is None:
        return None
    dt = started_at if started_at.tzinfo else started_at.replace(tzinfo=timezone.utc)
    return dt.astimezone(_SHANGHAI).strftime("%m%d")


def _is_safe_serial(serial: str) -> bool:
    if not serial or serial.strip() != serial:
        return False
    if serial in (".", "..") or ".." in serial:
        return False
    if "/" in serial or "\\" in serial:
        return False
    return True


def _dedupe(items: Iterable[str | None]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out


def load_plan_run_device_serials(db: Session, plan_run_id: int) -> list[str]:
    """All PlanRun device serials, across every host.

    Union of JobInstance devices and PlanRunTargetDevice snapshot — the
    PlanRun's device set, not a single host's slice.
    """
    job_serials = db.execute(
        select(Device.serial)
        .join(JobInstance, JobInstance.device_id == Device.id)
        .where(JobInstance.plan_run_id == plan_run_id)
    ).scalars().all()
    target_serials = db.execute(
        select(Device.serial)
        .join(PlanRunTargetDevice, PlanRunTargetDevice.device_id == Device.id)
        .where(PlanRunTargetDevice.plan_run_id == plan_run_id)
    ).scalars().all()
    return [s for s in _dedupe([*job_serials, *target_serials]) if _is_safe_serial(s)]


def load_plan_run_scan_scope(db: Session, plan_run_id: int) -> tuple[list[str], list[str]]:
    """PlanRun-wide serials + union of all job-start MMDD stamps.

    Empty stamps with a non-empty serial list would scan every date folder
    for those devices. Fall back to today's Shanghai MMDD only.
    """
    serials = load_plan_run_device_serials(db, plan_run_id)
    started_rows = db.execute(
        select(JobInstance.started_at).where(JobInstance.plan_run_id == plan_run_id)
    ).scalars().all()
    stamps = _dedupe(run_date_stamp_from_started_at(ts) for ts in started_rows)
    if serials and not stamps:
        stamps = [datetime.now(_SHANGHAI).strftime("%m%d")]
    return serials, stamps


def load_plan_run_scan_host_ids(db: Session, plan_run_id: int) -> list[str]:
    """Hosts that execute this PlanRun: jobs + prepare snapshot.

    Device list is PlanRun-wide (cross-host); scan fan-out follows where
    those jobs actually ran, not the device's current ``host_id``.
    """
    job_hosts = db.execute(
        select(JobInstance.host_id).where(
            JobInstance.plan_run_id == plan_run_id,
            JobInstance.host_id.isnot(None),
        )
    ).scalars().all()
    prh_hosts = db.execute(
        select(PlanRunHost.host_id).where(PlanRunHost.plan_run_id == plan_run_id)
    ).scalars().all()
    target_hosts = db.execute(
        select(PlanRunTargetDevice.host_id_snapshot).where(
            PlanRunTargetDevice.plan_run_id == plan_run_id,
        )
    ).scalars().all()
    return _dedupe([*job_hosts, *prh_hosts, *target_hosts])


def iter_plan_run_scan_hosts(db: Session, plan_run_id: int) -> list[tuple[str, str]]:
    """``(host_id, status)`` for each scan target host. Missing host → OFFLINE."""
    host_ids = load_plan_run_scan_host_ids(db, plan_run_id)
    if not host_ids:
        return []
    rows = db.execute(
        select(Host.id, Host.status).where(Host.id.in_(host_ids))
    ).all()
    status_by_id = {row[0]: row[1] for row in rows}
    return [(hid, status_by_id.get(hid, "OFFLINE")) for hid in host_ids]


def build_scan_now_payload(
    db: Session,
    plan_run_id: int,
    host_id: str,  # noqa: ARG001 — Agent receiver; serials are PlanRun-wide
    *,
    is_final: bool = False,
) -> dict:
    """Same PlanRun-wide serial/stamp lists for every host.

    ``host_id`` identifies the Agent receiving this command; it does not
    shrink the device list. Each Agent searches only its local HDD.
    """
    serials, stamps = load_plan_run_scan_scope(db, plan_run_id)
    return {
        "plan_run_id": plan_run_id,
        "is_final": is_final,
        "device_serials": serials,
        "run_date_stamps": stamps,
    }


def xls_row_matches_serials(
    path: str,
    detail: str,
    serials: Iterable[str],
) -> bool:
    """Keep a scan/merge xls row if Path or Detail Device_id hits a serial."""
    serial_list = [s for s in serials if s]
    if not serial_list:
        return False
    parts = {p for p in (path or "").replace("\\", "/").split("/") if p}
    if any(serial in parts for serial in serial_list):
        return True
    detail_text = detail or ""
    for serial in serial_list:
        if f"Device_id: {serial}" in detail_text or f"Device_id:{serial}" in detail_text:
            return True
    return False
