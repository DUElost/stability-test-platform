"""PlanRun watcher-summary / AEE dashboard vertical slice (#1520 God-module).

Owns:
- ``GET /plan-runs/{id}/watcher-summary`` aggregation (AEE dashboard sections,
  aee_breakdown, platform buckets, capability, run-log archive status);
- ``GET /plan-runs/{id}/crash-details`` event list (shares the dedup logic);
- the watcher time-window resolution and the dedup-key helpers
  (``_aee_event_dedup_key`` / ``_uniview_dedup_key``, contract-pinned by #2285).

Routes in ``backend/api/routes/plan_runs.py`` stay thin:
``_require_plan_run`` + ``ok(build_*(...))``; the moved private helpers remain
re-exported from that module for existing test imports (#2285) — see the
``noqa: F401`` block there.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import case, func, select, text
from sqlalchemy.orm import Session

from backend.api.schemas.plan_run import (
    AeeBreakdownOut,
    AeeDashboardSectionOut,
    PackageRankingOut,
    PackageStatOut,
    PackageSubtypeCountOut,
    SubtypeDistributionOut,
    WatcherAgentOpsMetrics,
    WatcherArchiveOut,
    WatcherCategoryOut,
    WatcherPlatformBucketOut,
    WatcherSignalLinkStatsOut,
    WatcherSummaryOut,
)
from backend.core.aee_metadata import (
    infer_aee_subtype_from_paths,
    normalize_aee_subtype,
    normalize_package_name,
    parse_exp_main_summary,
)
from backend.core.artifact_paths import (
    ArtifactPathError,
    resolve_local_artifact_path,
)
from backend.models.enums import JobStatus
from backend.models.host import Device, Host
from backend.models.job import JobInstance, JobLogSignal
from backend.models.plan_run import PlanRun
from backend.services.device_log_event import LATE_EVENT_GRACE
from backend.services.log_observation import (
    ANOMALY_SIGNAL_CATEGORIES,
    aggregate_signal_link_stats,
)


def _iso(v) -> str | None:
    return v.isoformat() if v else None


def _iso(v) -> str | None:
    return v.isoformat() if v else None


_WATCHER_TIME_SCOPE_TO_MINUTES: dict[str, int] = {
    "15m": 15,
    "1h": 60,
    "6h": 360,
    "24h": 1440,
}
_SUBTYPE_FIXED_ORDER = [
    "ANR",
    "JE",
    "NE",
    "SWT",
    "Fatal NE",
    "Fatal JE",
    "Combo EE",
    "Kernel API Dump",
    "System API Dump",
    "HWT",
    "HANG",
    "KE",
    "HW Reboot",
    "Modem EE",
    "OCP Reboot",
    "其他",
]

_DEFAULT_WATCHER_WINDOW_MIN   = 60
_MAX_WATCHER_WINDOW_MIN       = 1440  # 1 天

# M0/PR #2: AEE 细分聚合(crash / vendor_crash / anr 互斥 + by_package)
# 数据来源:JobLogSignal.extra (JSONB);仅当 source='reconciler' 的 signal
# 携带完整 extra 字段(event_type/package_name/aee_ts/nfs_path/pull_source);
# 旧 inotifyd 路径 signal 没有 extra,自动落入 unknown 桶。


def _aggregate_watcher_platform_buckets(
    db: Session,
    *,
    job_ids: list[int],
    cur_start: datetime,
    window_end: datetime,
) -> list[WatcherPlatformBucketOut]:
    if not job_ids:
        return []
    from backend.core.dedup_platform import has_collection_impl
    rows = db.execute(
        select(
            func.coalesce(Device.platform, "UNKNOWN").label("platform"),
            JobLogSignal.category,
            func.count(JobLogSignal.id),
            func.count(func.distinct(JobLogSignal.device_serial)),
        )
        .select_from(JobLogSignal)
        .join(JobInstance, JobLogSignal.job_id == JobInstance.id)
        .join(Device, JobInstance.device_id == Device.id)
        .where(
            JobLogSignal.job_id.in_(job_ids),
            JobLogSignal.detected_at >= cur_start,
            JobLogSignal.detected_at <= window_end,
        )
        .group_by(Device.platform, JobLogSignal.category)
    ).all()
    by_platform: dict[str, list[tuple[str, int, int]]] = defaultdict(list)
    for platform, category, count, affected in rows:
        by_platform[str(platform or "UNKNOWN")].append(
            (str(category), int(count or 0), int(affected or 0)),
        )
    # #749：原「RUNNING 一次 + 全量一次」两次全量聚合合并为一次条件聚合——
    # ``count(distinct case ...)`` 只计非 NULL，等价于原来带 status 过滤的那次查询。
    participation_rows = db.execute(
        select(
            func.coalesce(Device.platform, "UNKNOWN").label("platform"),
            func.count(func.distinct(JobInstance.device_id)).label("participating"),
            func.count(
                func.distinct(
                    case(
                        (JobInstance.status == JobStatus.RUNNING.value,
                         JobInstance.device_id),
                        else_=None,
                    )
                )
            ).label("running"),
        )
        .select_from(JobInstance)
        .join(Device, JobInstance.device_id == Device.id)
        .where(JobInstance.id.in_(job_ids))
        .group_by(Device.platform)
    ).all()
    participating_by_platform = {
        str(platform or "UNKNOWN"): int(participating or 0)
        for platform, participating, _running in participation_rows
    }
    running_by_platform = {
        str(platform or "UNKNOWN"): int(running or 0)
        for platform, _participating, running in participation_rows
    }
    # #749：受影响设备数（每平台去重 device_serial）由「平台循环内 N 次查询」改为
    # 一次分组查询。分组键用原始 Device.platform（与 by_platform 同源，故平台集合一致），
    # Python 侧再归一成 "UNKNOWN"。
    affected_rows = db.execute(
        select(
            func.coalesce(Device.platform, "UNKNOWN").label("platform"),
            func.count(func.distinct(JobLogSignal.device_serial)),
        )
        .select_from(JobLogSignal)
        .join(JobInstance, JobLogSignal.job_id == JobInstance.id)
        .join(Device, JobInstance.device_id == Device.id)
        .where(
            JobLogSignal.job_id.in_(job_ids),
            JobLogSignal.detected_at >= cur_start,
            JobLogSignal.detected_at <= window_end,
        )
        .group_by(Device.platform)
    ).all()
    affected_by_platform = {
        str(platform or "UNKNOWN"): int(count or 0)
        for platform, count in affected_rows
    }
    all_platforms = (
        set(by_platform) | set(running_by_platform) | set(participating_by_platform)
    )

    buckets: list[WatcherPlatformBucketOut] = []
    for platform in sorted(all_platforms):
        platform_rows = by_platform.get(platform, [])
        categories_out = [
            WatcherCategoryOut(
                category=cat, count=count, affected_device_count=affected, trend_change=0,
            )
            for cat, count, affected in sorted(platform_rows, key=lambda r: -r[1])
        ]
        # 该平台无信号行时字典无键 → 取 0（等价于原 `else: affected_total = 0`）
        affected_total = affected_by_platform.get(platform, 0)
        buckets.append(
            WatcherPlatformBucketOut(
                platform=platform,
                categories=categories_out,
                total=sum(c.count for c in categories_out),
                affected_device_count=int(affected_total),
                running_device_count=running_by_platform.get(platform, 0),
                participating_device_count=participating_by_platform.get(platform, 0),
                # R4-b b1：让「平台未支持」在 UI 上可与「没有异常」区分。
                reconciler_supported=has_collection_impl(platform),
            )
        )
    return buckets

_CAPABILITY_SEVERITY: dict[str, int] = {
    "unavailable":       40,   # 探测全失败 → reconciler 单通道(目标徽章场景)
    "polling":           30,   # inotifyd 不可用,reconciler 承担拉+emit
    "inotifyd_shell":    20,
    "inotifyd_root":     10,
    "inotifyd_realtime": 10,
    "stub":               5,
    "skipped":           -10,  # watcher 未启动(非降级,前端不徽章)
}


def _aggregate_run_log_archive(
    db: Session,
    *,
    job_rows: list,
    total_jobs: int,
    plan_run_id: int,
    link_stats: Optional[WatcherSignalLinkStatsOut] = None,
) -> WatcherArchiveOut:
    host_ids = {r.host_id for r in job_rows if r.host_id}
    if not host_ids:
        return WatcherArchiveOut()

    hosts = db.execute(
        select(Host).where(Host.id.in_(host_ids))
    ).scalars().all()

    ops = WatcherAgentOpsMetrics()
    for host in hosts:
        extra = host.extra if isinstance(host.extra, dict) else {}
        archive = extra.get("archive")
        if not isinstance(archive, dict):
            continue
        ops.pruned_total += int(archive.get("pruned_total") or 0)
        ops.spill_cycles += int(archive.get("spill_cycles") or 0)
        ops.spilled_total += int(archive.get("spilled_total") or 0)
        host_pct = archive.get("local_disk_usage_pct")
        if host_pct is not None:
            try:
                host_pct_float = float(host_pct)
                if ops.local_disk_usage_pct is None or host_pct_float > ops.local_disk_usage_pct:
                    ops.local_disk_usage_pct = host_pct_float
            except (TypeError, ValueError):
                pass

    scan_status: Optional[str] = None
    scan_triggered_at: Optional[str] = None
    signaled_jobs = 0
    pending_jobs = 0
    failed_jobs = 0

    _TERMINAL_JOB = {"COMPLETED", "SUCCESS", "PARTIAL_SUCCESS", "FAILED", "ABORTED"}
    if job_rows:
        job_ids = [r.id for r in job_rows]
        job_statuses = {r.id: r.status for r in job_rows}

        from backend.models.plan_run_artifact import PlanRunArtifact
        from backend.models.job import JobLogSignal
        from sqlalchemy import func as sa_func

        signal_job_ids = set(
            row[0] for row in db.execute(
                select(func.distinct(JobLogSignal.job_id)).where(
                    JobLogSignal.job_id.in_(job_ids),
                )
            ).all()
        )

        for jid in job_ids:
            status = job_statuses.get(jid, "")
            if jid in signal_job_ids:
                signaled_jobs += 1
            elif status in _TERMINAL_JOB:
                failed_jobs += 1
            else:
                pending_jobs += 1

        merge_count = db.execute(
            select(sa_func.count(PlanRunArtifact.id)).where(
                PlanRunArtifact.plan_run_id == plan_run_id,
                PlanRunArtifact.artifact_type == "merge_result_xls",
            )
        ).scalar_one()
        scan_count = db.execute(
            select(sa_func.count(PlanRunArtifact.id)).where(
                PlanRunArtifact.plan_run_id == plan_run_id,
                PlanRunArtifact.artifact_type == "scan_result_xls",
            )
        ).scalar_one()

        if merge_count > 0:
            scan_status = "merged"
            first = db.execute(
                select(PlanRunArtifact.created_at).where(
                    PlanRunArtifact.plan_run_id == plan_run_id,
                    PlanRunArtifact.artifact_type == "merge_result_xls",
                ).order_by(PlanRunArtifact.created_at.asc()).limit(1)
            ).scalar_one_or_none()
        elif scan_count > 0:
            scan_status = "scanned"
            first = db.execute(
                select(PlanRunArtifact.created_at).where(
                    PlanRunArtifact.plan_run_id == plan_run_id,
                    PlanRunArtifact.artifact_type == "scan_result_xls",
                ).order_by(PlanRunArtifact.created_at.asc()).limit(1)
            ).scalar_one_or_none()
        else:
            scan_status = "pending"
            first = None

        if first is not None:
            scan_triggered_at = first.isoformat() if hasattr(first, "isoformat") else str(first)

    return WatcherArchiveOut(
        ops_metrics=ops,
        scan_status=scan_status,
        scan_triggered_at=scan_triggered_at,
        signaled_jobs=signaled_jobs,
        pending_jobs=pending_jobs,
        failed_jobs=failed_jobs,
        link_stats=link_stats,
    )


def _aggregate_watcher_capability(db: Session, *, job_ids: list[int]) -> Optional[str]:
    """C-6 (§2.4 #5): 汇总该 PlanRun 下 Job 的 watcher 能力快照。

    取 JobInstance.watcher_capability 列中"最降级"的一档(按 _CAPABILITY_SEVERITY);
    全部为 NULL(Agent 未回填)时返回 None。该值仅用于前端提示,不参与聚合计数,
    因此跨方言(PG / SQLite)均可工作。
    """
    if not job_ids:
        return None
    rows = db.execute(
        select(JobInstance.watcher_capability)
        .where(JobInstance.id.in_(job_ids))
        .where(JobInstance.watcher_capability.isnot(None))
    ).all()
    caps = [str(r[0]) for r in rows if r[0]]
    if not caps:
        return None
    return max(caps, key=lambda c: _CAPABILITY_SEVERITY.get(c, 0))

def _resolve_watcher_summary_window(
    pr: PlanRun,
    *,
    now: datetime,
    time_scope: Optional[str],
    window_minutes: Optional[int],
) -> tuple[str, Optional[int], datetime, datetime, datetime]:
    window_end = pr.ended_at if pr.ended_at else now
    if window_end.tzinfo is None:
        window_end = window_end.replace(tzinfo=timezone.utc)
    run_start = pr.started_at or window_end
    if run_start.tzinfo is None:
        run_start = run_start.replace(tzinfo=timezone.utc)

    # #1962：只有「窗口本身就是 run 窗口」的场景才加末端宽限。相对范围
    # （15m/1h/… 与 legacy window_minutes）是**相对切片**，其末端不随 run 结束
    # 延展——否则「15m」会静默变成 45m，标签说谎。
    covers_run_window = False

    if time_scope:
        if time_scope == "all":
            cur_start = run_start
            resolved_minutes = None
            covers_run_window = True
        else:
            resolved_minutes = _WATCHER_TIME_SCOPE_TO_MINUTES[time_scope]
            cur_start = max(run_start, window_end - timedelta(minutes=resolved_minutes))
        resolved_scope = time_scope
    elif window_minutes is not None:
        # Legacy window_minutes keeps the historical rolling-window semantics and
        # may include signals emitted before this PlanRun started.
        cur_start = window_end - timedelta(minutes=window_minutes)
        resolved_minutes = window_minutes
        resolved_scope = _window_minutes_to_scope_label(window_minutes)
    else:
        cur_start = run_start
        resolved_minutes = None
        resolved_scope = "all"
        covers_run_window = True

    delta = max(window_end - cur_start, timedelta(minutes=1))
    # 趋势基线按**未加宽限**的窗口推算，避免放宽末端后基线漂移。
    prev_start = cur_start - delta

    # #1962：窗口即 run 窗口时，末端加宽限。reconciler 的首个 tick 需要 ls + pull
    # 若干事件目录，可能**慢于 job 本身的生命周期**（实测 run 393 的 job 只跑了
    # 11s，事件在其后 70s 才落库），而 DLE 视图按 plan_run_id 取数不受窗口限制
    # ——于是出现「DLE 有行、仪表盘 0」。宽限值与 device_log_event 的
    # 「PlanRun 窗口附近的迟到落库」语义同一来源（LATE_EVENT_GRACE）。
    # 相对范围不加宽限（见上）；RUNNING run（无 ended_at）亦不受影响。
    if covers_run_window and pr.ended_at is not None:
        window_end = window_end + LATE_EVENT_GRACE

    return resolved_scope, resolved_minutes, cur_start, window_end, prev_start


def _window_minutes_to_scope_label(window_minutes: int) -> str:
    for scope, minutes in _WATCHER_TIME_SCOPE_TO_MINUTES.items():
        if minutes == window_minutes:
            return scope
    return f"{window_minutes}m"


def _empty_dashboard_section() -> AeeDashboardSectionOut:
    return AeeDashboardSectionOut(
        total_events=0,
        affected_device_count=0,
        top_package_name=None,
        top_subtype=None,
        subtype_distribution=[],
        package_ranking=[],
    )


def _aggregate_aee_dashboard_sections(
    db: Session,
    *,
    job_ids: list[int],
    cur_start: datetime,
    window_end: datetime,
) -> tuple[bool, AeeDashboardSectionOut, AeeDashboardSectionOut]:
    events = _load_deduped_aee_events(
        db,
        job_ids=job_ids,
        cur_start=cur_start,
        window_end=window_end,
    )
    supports_origin_split = all(
        event["entry_origin"] in {"baseline", "runtime"} for event in events
    )
    if not supports_origin_split:
        return (
            False,
            _build_dashboard_section(events),
            _empty_dashboard_section(),
        )
    runtime_events = [event for event in events if event["entry_origin"] == "runtime"]
    baseline_events = [event for event in events if event["entry_origin"] == "baseline"]
    return (
        True,
        _build_dashboard_section(runtime_events),
        _build_dashboard_section(baseline_events),
    )


def _load_deduped_aee_events(
    db: Session,
    *,
    job_ids: list[int],
    cur_start: datetime,
    window_end: datetime,
) -> list[dict[str, Any]]:
    if not job_ids:
        return []

    rows = db.execute(
        select(
            JobLogSignal.id,
            JobLogSignal.category,
            JobLogSignal.device_serial,
            JobLogSignal.path_on_device,
            JobLogSignal.artifact_uri,
            JobLogSignal.detected_at,
            JobLogSignal.extra,
        )
        .where(JobLogSignal.job_id.in_(job_ids))
        .where(JobLogSignal.detected_at >= cur_start)
        .where(JobLogSignal.detected_at <= window_end)
        # #1956：类别口径与风险汇总共用真源。此前这里硬编码三元组，
        # 导致 UNIVIEW（展锐）事件虽已入库却不进仪表盘。
        .where(JobLogSignal.category.in_(ANOMALY_SIGNAL_CATEGORIES))
    ).all()

    deduped: dict[str, dict[str, Any]] = {}
    for row in rows:
        extra = row.extra if isinstance(row.extra, dict) else {}
        package_name = _infer_dashboard_package_name(
            extra,
            artifact_uri=row.artifact_uri,
        )
        group, subtype = _infer_dashboard_event_group_and_subtype(
            row.category,
            extra,
            path_on_device=row.path_on_device,
            artifact_uri=row.artifact_uri,
        )
        entry_origin = _normalize_entry_origin(extra.get("entry_origin"))
        key = _aee_event_dedup_key(
            row.id, row.category, row.path_on_device, extra,
            device_serial=row.device_serial or "",
        )
        candidate = {
            "key": key,
            "group": group,
            "subtype": subtype,
            "package_name": package_name,
            "device_serial": row.device_serial,
            "detected_at": row.detected_at,
            "entry_origin": entry_origin,
        }
        existing = deduped.get(key)
        if existing is None or _prefer_deduped_event(candidate, existing):
            deduped[key] = candidate
    return list(deduped.values())


def _prefer_deduped_event(candidate: dict[str, Any], existing: dict[str, Any]) -> bool:
    if bool(candidate["entry_origin"]) != bool(existing["entry_origin"]):
        return bool(candidate["entry_origin"])
    if candidate["package_name"] != "unknown" and existing["package_name"] == "unknown":
        return True
    return candidate["detected_at"] > existing["detected_at"]


def _normalize_entry_origin(value: Any) -> Optional[str]:
    normalized = str(value or "").strip().lower()
    if normalized in {"baseline", "runtime"}:
        return normalized
    return None


def _aee_event_dedup_key(
    signal_id: int,
    category: str,
    path_on_device: str,
    extra: dict[str, Any],
    device_serial: str = "",
) -> str:
    nfs_path = str(extra.get("nfs_path") or "").strip()
    # #1956：UNIVIEW 与 AEE 同用 nfs_path 去重——同一物理事件被多次 run 拉取时只算一次。
    #
    # #2080：UNIVIEW 的 `nfs_path` 是**事件目录**（一个目录可容纳多个异常），不是单条
    # 异常的物理路径；对 AEE/MTK 而言目录键等价于事件键，对 UNIVIEW 则不唯一。
    # Agent 侧 #2010 已改为「签名变化就再发射一条」（同一目录可产出多条信号），若消费侧
    # 仍按目录去重，会把它们**并回一条**——即 #2010 修好的症状在消费侧残留。
    # 故 UNIVIEW 键补上事件身份；AEE / VENDOR_AEE 保持目录键不动（其 nfs_path 指向
    # 单事件产物，目录键本已唯一）。
    if category == "UNIVIEW" and nfs_path:
        return _uniview_dedup_key(nfs_path, extra, device_serial=device_serial)
    if category in {"AEE", "VENDOR_AEE"} and nfs_path:
        return f"nfs:{nfs_path}"
    path = str(path_on_device or "").strip()
    if path:
        return f"path:{path}"
    return f"id:{signal_id}"


def _uniview_dedup_key(
    nfs_path: str, extra: dict[str, Any], *, device_serial: str = "",
) -> str:
    """UNIVIEW 去重键：**serial + 事件目录 + 事件身份**，形态恒定四段（#2080/#2285/#2394）。

    #2394-②：身份段**不含日期根**。``nfs_path`` 的存储布局
    ``…/uniview_watcher/{MMDD}/{serial}/{event_dir}`` 里的 ``{MMDD}`` 随 run 日期漂移——
    同一条物理事件若跨天被重放（如 agent 状态丢失后重新拉取），旧键会因日期段不同而
    裂成两行（C9 双计）。serial 取自行列（非路径反解），目录名取路径末段，日期根整体
    不再参与键。读侧派生、非持久列 → 新旧行同函数同规则，一次性切换，无存量迁移。

    前缀 ``uniview:`` 与 AEE 家族键（``nfs:…``）天然不同形，保住 #2285
    「UNIVIEW 行与 AEE 行永不互并」的不变量；字段缺失时仍以空占位保形（宁多勿并）。
    serial 或目录名不可得时**退回旧式全路径键**（防御异常数据，不抛错不并错）。

    事件身份取 ``event_subtype`` + ``aee_ts``——二者由 Agent 侧
    ``unisoc_reconciler._emit_event`` 一并写入 ``extra``（``aee_ts`` 为设备时钟原文，
    #785）；同目录内不同异常至少有一项不同。同一条异常被多次 run 拉取时二者不变，
    故仍能正确去重。

    #2285：两项皆缺时**不再**退化为两段键 ``nfs:{dir}`` —— 那与 AEE / VENDOR_AEE
    家族的键（``_aee_event_dedup_key`` 的 ``f"nfs:{nfs_path}"``）**同形**，同目录的
    AEE 行与 UNIVIEW 行会被并成一条（#2010「签名变化就再发射一条」在消费侧的反向
    残留）。恒带两个占位后，UNIVIEW 键与 AEE 键不可能相等。

    有字段 / 无字段仍是两个身份：字段缺失时无法安全归并——把同目录的未知身份行并成
    一条会把**不同异常**算成一次（欠计数），与 #2080「宁多勿并」的取向一致。
    """
    subtype = str(extra.get("event_subtype") or "").strip()
    aee_ts = str(extra.get("aee_ts") or "").strip()
    serial = str(device_serial or "").strip()
    event_dir = nfs_path.rstrip("/").rsplit("/", 1)[-1].strip()
    if not serial or not event_dir:
        return f"nfs:{nfs_path}#{subtype}#{aee_ts}"
    return f"uniview:{serial}#{event_dir}#{subtype}#{aee_ts}"


def _infer_dashboard_event_group_and_subtype(
    category: str,
    extra: dict[str, Any],
    *,
    path_on_device: str = "",
    artifact_uri: Optional[str] = None,
) -> tuple[str, str]:
    event_subtype = str(extra.get("event_subtype") or "").strip()
    if event_subtype:
        subtype = event_subtype
    else:
        raw_event_type = str(extra.get("raw_event_type") or "").strip()
        event_type = str(extra.get("event_type") or "").strip().upper()
        subtype = _normalize_dashboard_subtype(
            raw_event_type,
            event_type,
            category,
            path_on_device=path_on_device,
            artifact_uri=artifact_uri,
            nfs_path=str(extra.get("nfs_path") or "").strip(),
        )

    # #1956：展锐（UNIVIEW）自成一组，且必须**先**于下面的 ANR / VENDOR 判定，
    # 否则 UNIVIEW 的 ANR 会被并进 MTK 的 AEE/ANR 桶，混平台后分不清来源。
    if category == "UNIVIEW":
        return "UNIVIEW", subtype
    if subtype == "ANR":
        return "AEE", "ANR"
    if category == "VENDOR_AEE":
        return "VENDOR_AEE", subtype
    return "AEE", subtype


def _normalize_dashboard_subtype(
    raw_event_type: str,
    event_type: str,
    category: str,
    *,
    path_on_device: str = "",
    artifact_uri: Optional[str] = None,
    nfs_path: str = "",
) -> str:
    normalized = normalize_aee_subtype(raw_event_type, event_type, category=category)
    if normalized != "其他":
        return normalized

    entry_dir = _resolve_dashboard_local_aee_dir(nfs_path=nfs_path, artifact_uri=artifact_uri)
    if entry_dir is not None:
        exp_main_summary = parse_exp_main_summary(entry_dir)
        exp_main_subtype = str(exp_main_summary.get("event_subtype") or "").strip()
        if exp_main_subtype:
            return exp_main_subtype

    return infer_aee_subtype_from_paths(path_on_device, nfs_path, artifact_uri or "") or normalized


def _infer_dashboard_package_name(
    extra: dict[str, Any],
    *,
    artifact_uri: Optional[str] = None,
) -> str:
    package_name = normalize_package_name(str(extra.get("package_name") or ""))
    if package_name:
        return package_name

    entry_dir = _resolve_dashboard_local_aee_dir(
        nfs_path=str(extra.get("nfs_path") or "").strip(),
        artifact_uri=artifact_uri,
    )
    if entry_dir is not None:
        exp_main_summary = parse_exp_main_summary(entry_dir)
        for key in ("package_name", "current_process"):
            candidate = normalize_package_name(exp_main_summary.get(key, ""))
            if candidate:
                return candidate

    return "unknown"


def _resolve_dashboard_local_aee_dir(
    *,
    nfs_path: str = "",
    artifact_uri: Optional[str] = None,
) -> Optional[Path]:
    for raw_path in (nfs_path, artifact_uri or ""):
        candidate = (raw_path or "").strip()
        if not candidate:
            continue
        try:
            resolved = resolve_local_artifact_path(candidate, must_exist=False)
        except ArtifactPathError:
            continue
        if resolved.exists():
            return resolved if resolved.is_dir() else resolved.parent
        if resolved.suffix:
            return resolved.parent
        return resolved
    return None


def _build_dashboard_section(events: list[dict[str, Any]]) -> AeeDashboardSectionOut:
    if not events:
        return _empty_dashboard_section()

    subtype_counts: dict[tuple[str, str], int] = defaultdict(int)
    package_stats: dict[str, dict[str, Any]] = {}
    for event in events:
        subtype_counts[(event["group"], event["subtype"])] += 1
        pkg = package_stats.setdefault(
            event["package_name"],
            {
                "total_count": 0,
                "devices": set(),
                "latest_detected_at": None,
                "subtype_breakdown": defaultdict(int),
            },
        )
        pkg["total_count"] += 1
        pkg["devices"].add(event["device_serial"])
        # #1956：按 (group, subtype) 计数——同名 subtype 可能分属不同平台分组（如 ANR）。
        pkg["subtype_breakdown"][(event["group"], event["subtype"])] += 1
        latest_ts = pkg["latest_detected_at"]
        if latest_ts is None or event["detected_at"] > latest_ts:
            pkg["latest_detected_at"] = event["detected_at"]

    subtype_distribution = [
        SubtypeDistributionOut(
            subtype=subtype,
            group=group,
            count=count,
            share=round(count / len(events), 4),
        )
        for (group, subtype), count in sorted(
            subtype_counts.items(),
            key=lambda item: (
                -item[1],
                _subtype_order_index(item[0][1]),
                item[0][0],
                item[0][1],
            ),
        )
    ]

    package_ranking = [
        PackageRankingOut(
            package_name=package_name,
            total_count=stats["total_count"],
            affected_device_count=len(stats["devices"]),
            latest_detected_at=_iso(stats["latest_detected_at"]),
            subtype_breakdown=[
                PackageSubtypeCountOut(subtype=subtype, group=group, count=count)
                for (group, subtype), count in sorted(
                    stats["subtype_breakdown"].items(),
                    key=lambda item: (
                        -item[1],
                        _subtype_order_index(item[0][1]),
                        item[0][0],
                        item[0][1],
                    ),
                )
            ],
        )
        for package_name, stats in sorted(
            package_stats.items(),
            key=lambda item: (
                -item[1]["total_count"],
                item[0] == "unknown",
                -(item[1]["latest_detected_at"] or datetime.min.replace(tzinfo=timezone.utc)).timestamp(),
                item[0],
            ),
        )
    ]

    return AeeDashboardSectionOut(
        total_events=len(events),
        affected_device_count=len({event["device_serial"] for event in events}),
        top_package_name=package_ranking[0].package_name if package_ranking else None,
        top_subtype=subtype_distribution[0].subtype if subtype_distribution else None,
        subtype_distribution=subtype_distribution,
        package_ranking=package_ranking,
    )


def _subtype_order_index(subtype: str) -> int:
    try:
        return _SUBTYPE_FIXED_ORDER.index(subtype)
    except ValueError:
        return len(_SUBTYPE_FIXED_ORDER)


def _aggregate_aee_breakdown(
    db: Session,
    *,
    job_ids: list[int],
    cur_start: datetime,
    now: datetime,
) -> AeeBreakdownOut:
    """按 package_name 聚合 AEE/VENDOR_AEE 崩溃与 ANR;reconciler signal
    携带 extra.nfs_path 时按 nfs_path 去重(同目录视为同 crash),ANR 用
    path_on_device 兜底以兼容旧 inotifyd 路径无 extra 的 signal。

    返回零值 AeeBreakdownOut 而非 None — 调用方决定是否上抛 None(早返回路径)。
    """
    sql = text("""
        SELECT
            COALESCE(NULLIF(extra->>'package_name', ''), 'unknown') AS pkg,
            COUNT(DISTINCT extra->>'nfs_path') FILTER (
                WHERE category = 'AEE'
                  AND COALESCE(NULLIF(extra->>'event_type', ''), 'CRASH') <> 'ANR'
            ) AS crash_count,
            COUNT(DISTINCT extra->>'nfs_path') FILTER (
                WHERE category = 'VENDOR_AEE'
                  AND COALESCE(NULLIF(extra->>'event_type', ''), 'CRASH') <> 'ANR'
            ) AS vendor_crash_count,
            COUNT(DISTINCT path_on_device) FILTER (
                WHERE category = 'ANR'
                   OR extra->>'event_type' = 'ANR'
            ) AS anr_count,
            MAX(detected_at) AS latest_detected_at
        FROM job_log_signal
        WHERE job_id = ANY(:job_ids)
          AND detected_at >= :cur_start
          AND detected_at <= :now
          AND (
              category IN ('AEE', 'VENDOR_AEE', 'ANR')
              OR extra->>'event_type' = 'ANR'
          )
        GROUP BY pkg
        ORDER BY (
            COUNT(DISTINCT extra->>'nfs_path') FILTER (
                WHERE category = 'AEE'
                  AND COALESCE(NULLIF(extra->>'event_type', ''), 'CRASH') <> 'ANR'
            )
            + COUNT(DISTINCT extra->>'nfs_path') FILTER (
                WHERE category = 'VENDOR_AEE'
                  AND COALESCE(NULLIF(extra->>'event_type', ''), 'CRASH') <> 'ANR'
            )
            + COUNT(DISTINCT path_on_device) FILTER (
                WHERE category = 'ANR'
                   OR extra->>'event_type' = 'ANR'
            )
        ) DESC, pkg ASC
    """)

    rows = db.execute(
        sql, {"job_ids": list(job_ids), "cur_start": cur_start, "now": now},
    ).all()

    by_package: list[PackageStatOut] = []
    crash_total = 0
    vendor_crash_total = 0
    anr_total = 0
    for pkg, crash, vendor_crash, anr, latest_ts in rows:
        # 排除三类计数全 0 的行(理论上 WHERE 已过滤,防御性兜底)
        if not (crash or vendor_crash or anr):
            continue
        by_package.append(PackageStatOut(
            package_name=pkg,
            crash_count=int(crash or 0),
            vendor_crash_count=int(vendor_crash or 0),
            anr_count=int(anr or 0),
            latest_detected_at=_iso(latest_ts),
        ))
        crash_total += int(crash or 0)
        vendor_crash_total += int(vendor_crash or 0)
        anr_total += int(anr or 0)

    return AeeBreakdownOut(
        crash_count=crash_total,
        vendor_crash_count=vendor_crash_total,
        anr_count=anr_total,
        packages=[p.package_name for p in by_package],
        by_package=by_package,
    )

def build_plan_run_crash_details(
    db: Session,
    pr: PlanRun,
    *,
    package_name: Optional[str],
    time_scope: Optional[str],
) -> list[dict[str, Any]]:
    """ADR-0025 Sprint 3 步骤 4: 按 package_name 返回 crash 事件详情列表。

    复用 watcher-summary 的去重逻辑 (_load_deduped_aee_events)，
    按 package_name 过滤后返回事件级详情（含 nfs_path / event_type / device_serial / job_id）。
    """

    run_id = pr.id

    job_rows = db.execute(
        select(JobInstance.id).where(JobInstance.plan_run_id == run_id)
    ).all()
    job_ids = [r.id for r in job_rows]
    if not job_ids:
        return ([])

    now = datetime.now(timezone.utc)
    resolved_scope, _resolved_minutes, cur_start, window_end, _prev_start = (
        _resolve_watcher_summary_window(
            pr,
            now=now,
            time_scope=time_scope,
            window_minutes=None,
        )
    )

    events = _load_deduped_aee_events(
        db,
        job_ids=job_ids,
        cur_start=cur_start,
        window_end=window_end,
    )

    result = []
    for event in events:
        pkg = event.get("package_name") or "unknown"
        if package_name and pkg != package_name:
            continue
        result.append({
            "package_name": pkg,
            "subtype": event.get("subtype"),
            "group": event.get("group"),
            "device_serial": event.get("device_serial"),
            "detected_at": _iso(event.get("detected_at")),
            "entry_origin": event.get("entry_origin"),
        })

    result.sort(key=lambda e: e.get("detected_at") or "", reverse=True)
    return (result)

def build_plan_run_watcher_summary(
    db: Session,
    pr: PlanRun,
    *,
    window_minutes: Optional[int],
    time_scope: Optional[str],
) -> WatcherSummaryOut:
    """ADR-0018 / ADR-0021 C5a₂: 最近 N 分钟内 watcher log_signal 按 category
    聚合,带 trend(对比上一相同长度窗口的差值)。

    abnormal_rate = 当前窗口受影响设备数 / PlanRun 总设备数;
    与 PlanRun.failure_threshold 比较给出 exceeded 标志。
    """

    run_id = pr.id

    job_rows = db.execute(
        select(JobInstance.id, JobInstance.device_id, JobInstance.host_id, JobInstance.status).where(JobInstance.plan_run_id == run_id)
    ).all()
    job_ids   = [r.id for r in job_rows]
    total_dev = len({r.device_id for r in job_rows})

    now = datetime.now(timezone.utc)
    resolved_scope, resolved_window_minutes, cur_start, window_end, prev_start = (
        _resolve_watcher_summary_window(
            pr,
            now=now,
            time_scope=time_scope,
            window_minutes=window_minutes,
        )
    )

    if not job_ids:
        return (WatcherSummaryOut(
            plan_run_id=pr.id,
            window_minutes=resolved_window_minutes,
            time_scope=resolved_scope,
            window_start_at=_iso(cur_start) or "",
            window_end_at=_iso(window_end) or "",
            categories=[], total=0, affected_device_count=0,
            total_devices=0, abnormal_rate=0.0,
            threshold=pr.failure_threshold, exceeded=False,
            supports_origin_split=False,
            current_run=_empty_dashboard_section(),
            preexisting=_empty_dashboard_section(),
            watcher_capability=None,
            archive=WatcherArchiveOut(
                link_stats=WatcherSignalLinkStatsOut(),
            ),
        ))

    # #556: read-only. Link repair is owned by the signal_link_reconcile sweep —
    # doing it here ran inside a get_db() session that is never committed, so it
    # never persisted while still locking rows on every poll.
    link_stats = WatcherSignalLinkStatsOut(
        **aggregate_signal_link_stats(db, job_ids),
    )

    # 当前窗口聚合(按 category 分组)
    cur_rows = db.execute(
        select(
            JobLogSignal.category,
            func.count(JobLogSignal.id),
            func.count(func.distinct(JobLogSignal.device_serial)),
            func.max(JobLogSignal.detected_at),
        )
        .where(
            (JobLogSignal.job_id.in_(job_ids))
            & (JobLogSignal.detected_at >= cur_start)
            & (JobLogSignal.detected_at <= window_end)
        )
        .group_by(JobLogSignal.category)
    ).all()

    # 上一窗口仅计 count(用于 trend)
    prev_rows = db.execute(
        select(JobLogSignal.category, func.count(JobLogSignal.id))
        .where(
            (JobLogSignal.job_id.in_(job_ids))
            & (JobLogSignal.detected_at >= prev_start)
            & (JobLogSignal.detected_at < cur_start)
        )
        .group_by(JobLogSignal.category)
    ).all()
    prev_counts = {row[0]: row[1] for row in prev_rows}

    # 找当前窗口 latest_device_serial
    latest_serial_by_cat: dict[str, str] = {}
    if cur_rows:
        latest_rows = db.execute(
            select(JobLogSignal.category, JobLogSignal.device_serial, JobLogSignal.detected_at)
            .where(
                (JobLogSignal.job_id.in_(job_ids))
                & (JobLogSignal.detected_at >= cur_start)
                & (JobLogSignal.detected_at <= window_end)
            )
            .order_by(JobLogSignal.detected_at.desc())
        ).all()
        for cat, serial, _ts in latest_rows:
            latest_serial_by_cat.setdefault(cat, serial)

    # 受影响设备数(去重所有 category)
    affected_total = db.execute(
        select(func.count(func.distinct(JobLogSignal.device_serial))).where(
            (JobLogSignal.job_id.in_(job_ids))
            & (JobLogSignal.detected_at >= cur_start)
            & (JobLogSignal.detected_at <= window_end)
        )
    ).scalar() or 0

    categories_out: list[WatcherCategoryOut] = []
    total = 0
    for cat, count, affected, latest_ts in cur_rows:
        total += count
        categories_out.append(WatcherCategoryOut(
            category=cat,
            count=count,
            affected_device_count=affected,
            trend_change=count - prev_counts.get(cat, 0),
            latest_device_serial=latest_serial_by_cat.get(cat),
            latest_detected_at=_iso(latest_ts),
        ))
    categories_out.sort(key=lambda c: c.count, reverse=True)

    abnormal_rate = (affected_total / total_dev) if total_dev else 0.0

    supports_origin_split, current_run, preexisting = _aggregate_aee_dashboard_sections(
        db,
        job_ids=job_ids,
        cur_start=cur_start,
        window_end=window_end,
    )

    # M0/PR #2: AEE 细分(crash / vendor_crash / anr 互斥 + by_package)
    # PG-only(JSONB):未含 extra 的 legacy signal 自动落入 unknown package +
    # NULL nfs_path,通过 path_on_device 兜底为 ANR 去重键。
    aee_breakdown = _aggregate_aee_breakdown(
        db, job_ids=job_ids, cur_start=cur_start, now=window_end,
    )
    platform_buckets = _aggregate_watcher_platform_buckets(
        db, job_ids=job_ids, cur_start=cur_start, window_end=window_end,
    )
    watcher_capability = _aggregate_watcher_capability(db, job_ids=job_ids)

    return (WatcherSummaryOut(
        plan_run_id=pr.id,
        window_minutes=resolved_window_minutes,
        time_scope=resolved_scope,
        window_start_at=_iso(cur_start) or "",
        window_end_at=_iso(window_end) or "",
        categories=categories_out,
        total=total,
        affected_device_count=affected_total,
        total_devices=total_dev,
        abnormal_rate=round(abnormal_rate, 4),
        threshold=pr.failure_threshold,
        exceeded=abnormal_rate > pr.failure_threshold,
        supports_origin_split=supports_origin_split,
        current_run=current_run,
        preexisting=preexisting,
        aee_breakdown=aee_breakdown,
        platform_buckets=platform_buckets,
        watcher_capability=watcher_capability,
        archive=_aggregate_run_log_archive(
            db,
            job_rows=job_rows,
            total_jobs=len(job_ids),
            plan_run_id=run_id,
            link_stats=link_stats,
        ),
    ))
