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

from backend.core.dedup_platform import dedup_platform_for_device_platform
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


def iter_plan_run_scan_hosts(db: Session, plan_run_id: int) -> list[tuple[str, str, bool]]:
    """``(host_id, status, is_retired)`` for each scan target host.

    Missing host → OFFLINE。退役标记来自 ADR-0038 的 ``Host.retired_at``：
    历史证据集合必须可见（182d4e-R02：控制拒绝与历史收口不得共用一个
    「隐藏主机」过滤器），是否下发由调用方按 D5 策略裁决。
    """
    host_ids = load_plan_run_scan_host_ids(db, plan_run_id)
    if not host_ids:
        return []
    rows = db.execute(
        select(Host.id, Host.status, Host.retired_at).where(Host.id.in_(host_ids))
    ).all()
    by_id = {row[0]: (row[1], row[2] is not None) for row in rows}
    return [
        (hid, *(by_id.get(hid, ("OFFLINE", False))))
        for hid in host_ids
    ]


def load_expected_scan_platforms(
    db: Session, plan_run_id: int, host_ids: Iterable[str],
) -> dict[str, set[str]]:
    """每个**目标 host** 在本 PlanRun 中预期产出的归档平台分区集合。

    期望按「host 持有的设备平台构成」派生，**不是**「每个 host 都要所有平台」：
    Agent 侧两个 runner 都会跑、各自按 serial 过滤
    （``scan_runner._execute_job``），纯 MTK host 的 UNISOC 工具扫不到 uniview
    目录 → 永远产不出 unisoc 产物。若对每个 host 要求全部平台，纯平台 host 每轮
    都判不齐、烧满轮询预算——ADR-0032 B1 的「MTK/UNISOC **分区各自**完备性判定」
    被收紧成「每 host 双平台齐」的回归。

    来源与 :func:`load_plan_run_scan_host_ids` 同源：``JobInstance``（实际执行）
    + ``PlanRunTargetDevice.host_id_snapshot``（prepare 快照）。两处都取不到设备的
    host 不出现在返回值中——它没有可预期的产物，不计入完备性。设备平台到分区键的
    映射见 :func:`backend.core.dedup_platform.dedup_platform_for_device_platform`；
    无采集实现的平台（如 QCOM）映射为 ``None``，同样不计入。
    """
    wanted = {str(h) for h in host_ids if h}
    if not wanted:
        return {}
    host_list = sorted(wanted)
    rows = db.execute(
        select(JobInstance.host_id, Device.platform)
        .join(Device, Device.id == JobInstance.device_id)
        .where(
            JobInstance.plan_run_id == plan_run_id,
            JobInstance.host_id.in_(host_list),
        )
    ).all()
    snapshot_rows = db.execute(
        select(PlanRunTargetDevice.host_id_snapshot, Device.platform)
        .join(Device, Device.id == PlanRunTargetDevice.device_id)
        .where(
            PlanRunTargetDevice.plan_run_id == plan_run_id,
            PlanRunTargetDevice.host_id_snapshot.in_(host_list),
        )
    ).all()

    expected: dict[str, set[str]] = {}
    for host_id, platform in (*rows, *snapshot_rows):
        if not host_id or str(host_id) not in wanted:
            continue
        partition = dedup_platform_for_device_platform(platform)
        if partition is None:
            continue
        expected.setdefault(str(host_id), set()).add(partition)
    return expected


def classify_recycle_targets(
    host_rows: Iterable[tuple[str, str, bool]],
    *,
    allow_retired: bool,
) -> tuple[list[str], list[dict], list[dict]]:
    """ADR-0038 D5：数据回收类（scan/archive）目标分类。

    - 退役主机**允许**成为回收目标，但仅显式 admin 触发（``allow_retired=True``）；
    - 未获准的退役主机计入 ``skipped_retired``（不虚报完整）；
    - 其余非 ONLINE 主机计入 ``skipped_offline``。

    返回 ``(targets, skipped_offline, skipped_retired)``；skipped 条目为
    ``{"host_id", "status"}``。
    """
    targets: list[str] = []
    skipped_offline: list[dict] = []
    skipped_retired: list[dict] = []
    for host_id, status, is_retired in host_rows:
        if is_retired and not (allow_retired and status == "ONLINE"):
            # 含「获准但非 ONLINE」：退役主机死机同样不可达，如实计 retired
            skipped_retired.append({"host_id": host_id, "status": status})
            continue
        if status == "ONLINE":
            targets.append(host_id)
        else:
            skipped_offline.append({"host_id": host_id, "status": status})
    return targets, skipped_offline, skipped_retired


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
