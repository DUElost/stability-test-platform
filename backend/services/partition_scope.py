"""B8 G7：分区（实际 PlanRun × 平台）的只读消费视图与归档判据。

ADR-0062 D13 规定运行模式的单位是「实际 PlanRun × 平台」；D16 要求 extract、
归档完成判据、下载、能力目录、状态 API 与页面按分区工作。本模块把这些消费方
需要的同一份事实收在一处，避免 routes / scheduler / catalog 各写一套判断：

- 分区清单：已登记模式 ∪ 运行内出现过的平台（设备、DLE），缺模式按「现行」；
- 交付视图：仅「先去重」分区有 D9 交付登记与代表发布状态；
- 归档完成：先去重分区按交付状态（就绪 / 就绪但有缺口），现行分区保留
  「merge 产物 + extract 记录」的既有判据；两类分区独立判断。

本模块只读；写入口仍在 G1 的 issue_fact_store 与 G5 的 shadow_delivery。
"""

from __future__ import annotations

from typing import Any, Optional

from sqlalchemy import Text, cast, distinct, func, select
from sqlalchemy.orm import Session

from backend.models.device_log_event import DeviceLogEvent
from backend.models.host import Device
from backend.models.job import JobInstance
from backend.models.plan_run import PlanRun
from backend.models.plan_run_artifact import PlanRunArtifact
from backend.services import issue_fact_store as facts
from backend.services import shadow_delivery

PLATFORM_MTK = "mtk"
DEDUP_FIRST_PLATFORMS = frozenset({PLATFORM_MTK})


def normalize_platform(value: Any) -> str:
    return str(value or "").strip().lower()


def run_platforms(db: Session, plan_run_id: int) -> list[str]:
    """运行涉及的全部平台：模式记录 ∪ 设备平台 ∪ DLE 平台（去重升序）。

    设备平台是期望集的一部分：即使某个平台还没有任何 DLE，也要按现行分区
    保留它的归档判据，不能被另一侧的先去重分区「顺带完成」。
    """
    platforms: set[str] = {
        normalize_platform(row.platform)
        for row in facts.list_partition_modes(db, plan_run_id=plan_run_id)
    }
    device_platforms = db.execute(
        select(distinct(Device.platform))
        .select_from(Device)
        .join(JobInstance, JobInstance.device_id == Device.id)
        .where(JobInstance.plan_run_id == plan_run_id)
    ).scalars()
    platforms.update(normalize_platform(value) for value in device_platforms)
    dle_platforms = db.execute(
        select(distinct(DeviceLogEvent.platform)).where(
            DeviceLogEvent.plan_run_id == plan_run_id,
        )
    ).scalars()
    platforms.update(normalize_platform(value) for value in dle_platforms)
    return sorted(value for value in platforms if value)


def partition_rows(db: Session, plan_run_id: int) -> list[dict]:
    """分区清单：[{platform, mode, mode_recorded}]，缺记录按「现行」。"""
    recorded = {
        normalize_platform(row.platform): row.mode
        for row in facts.list_partition_modes(db, plan_run_id=plan_run_id)
    }
    rows: list[dict] = []
    for platform in run_platforms(db, plan_run_id):
        mode = recorded.get(platform, facts.PARTITION_MODE_CURRENT)
        rows.append({
            "platform": platform,
            "mode": mode,
            "mode_recorded": platform in recorded,
        })
    return rows


def _observation_identity():
    """DLE 观察身份：发生身份优先，缺失时按 DLE 行（路径变化不改变计数）。"""
    return func.coalesce(
        DeviceLogEvent.occurrence_key,
        func.concat("dle:", cast(DeviceLogEvent.id, Text)),
    )


def observation_counts(db: Session, *, plan_run_id: int, platform: str) -> dict[str, int]:
    """本分区 DLE 观察数与设备数（不区分上传状态，#3886：未选也要计入）。"""
    identity = _observation_identity()
    events = int(db.execute(
        select(func.count(distinct(identity))).where(
            DeviceLogEvent.plan_run_id == plan_run_id,
            func.lower(DeviceLogEvent.platform) == platform,
        )
    ).scalar_one())
    machines = int(db.execute(
        select(func.count(distinct(DeviceLogEvent.serial))).where(
            DeviceLogEvent.plan_run_id == plan_run_id,
            func.lower(DeviceLogEvent.platform) == platform,
        )
    ).scalar_one())
    return {"events": events, "machines": machines}


def _rounds_view(db: Session, *, plan_run_id: int, platform: str) -> list[dict]:
    return [
        {
            "round_no": row.round_no,
            "kind": row.kind,
            "status": row.status,
            "merge_status": row.merge_status,
            "merge_generation": row.merge_generation,
            "closed_at": row.closed_at.isoformat() if row.closed_at else None,
        }
        for row in facts.list_scan_rounds_for_read(
            db, plan_run_id=plan_run_id, platform=platform,
        )
    ]


def _representative_counts(
    db: Session, *, plan_run_id: int, platform: str,
) -> dict[str, int]:
    selected = [
        issue for issue in facts.list_partition_delivery_issues(
            db, plan_run_id=plan_run_id, platform=platform,
        )
        if issue.get("selection_status") == facts.SELECTION_SELECTED
    ]
    publications = {
        str(row.dle_id): row.state
        for row in facts.list_partition_evidence_publications(
            db, plan_run_id=plan_run_id, platform=platform,
        )
    }
    published = pending = failed = 0
    for issue in selected:
        state = publications.get(str(issue.get("representative_dle_id")))
        if state == facts.PUBLICATION_STATE_VERIFIED:
            published += 1
        elif state == facts.PUBLICATION_STATE_FAILED:
            failed += 1
        else:
            pending += 1
    return {
        "selected": len(selected),
        "published": published,
        "pending": pending,
        "failed": failed,
    }


def delivery_view(
    db: Session, *, plan_run_id: int, platform: str,
) -> Optional[dict]:
    """先去重分区的交付视图；现行分区没有 D9 交付登记，返回 None。"""
    partition = facts.get_partition_mode(
        db, plan_run_id=plan_run_id, platform=platform,
    )
    mode = partition.mode if partition is not None else facts.PARTITION_MODE_CURRENT
    if mode != facts.PARTITION_MODE_DEDUP_FIRST:
        return None
    latest = facts.get_latest_delivery_version(
        db, plan_run_id=plan_run_id, platform=platform,
    )
    decision = shadow_delivery.current_delivery_decision(
        db, plan_run_id=plan_run_id, platform=platform,
    )
    issues = facts.list_partition_delivery_issues(
        db, plan_run_id=plan_run_id, platform=platform,
    )
    coverage = list(latest.effective_coverage or []) if latest is not None else []
    covered = sum(1 for unit in coverage if unit.get("covered"))
    occurrences = facts.count_partition_occurrences(
        db, plan_run_id=plan_run_id, platform=platform,
    )
    machines = facts.count_partition_machines(
        db, plan_run_id=plan_run_id, platform=platform,
    )
    observations = observation_counts(
        db, plan_run_id=plan_run_id, platform=platform,
    )
    return {
        "status": decision.status,
        "reason": decision.reason,
        "archive_complete": shadow_delivery.delivery_is_archive_complete(decision),
        "version_no": latest.version_no if latest is not None else None,
        "software_versions": list(latest.software_versions or []) if latest else [],
        "round_ids": list(latest.round_ids or []) if latest else [],
        "coverage": {
            "expected": len(coverage),
            "covered": covered,
            "missing": [unit for unit in coverage if not unit.get("covered")],
        },
        "issues": len(issues),
        # 口径（#3886）：occurrences = 发生数（issue_occurrence 去重，含未选）；
        # machines = 设备数（serial 去重）；observed_events = DLE 观察数（供对账）。
        "occurrences": occurrences,
        "machines": machines,
        "observed_events": observations["events"],
        "representatives": _representative_counts(
            db, plan_run_id=plan_run_id, platform=platform,
        ),
        "gaps": [dict(gap) for gap in decision.gaps],
        "pending_items": list(latest.pending_items or []) if latest else [],
        "primary_folders": list(latest.primary_folders or []) if latest else [],
    }


def partition_views(db: Session, plan_run_id: int) -> list[dict]:
    """状态 API / 页面用的分区列表（模式、覆盖、交付与缺口）。"""
    rows: list[dict] = []
    for item in partition_rows(db, plan_run_id=plan_run_id):
        platform = item["platform"]
        delivery = delivery_view(db, plan_run_id=plan_run_id, platform=platform)
        observations = (
            {"events": None, "machines": None}
            if delivery is not None
            else observation_counts(db, plan_run_id=plan_run_id, platform=platform)
        )
        rows.append({
            **item,
            "delivery": delivery,
            "observed_events": observations["events"],
            "machines": observations["machines"],
            "rounds": _rounds_view(db, plan_run_id=plan_run_id, platform=platform),
            "capabilities": {
                "extract": item["mode"] == facts.PARTITION_MODE_CURRENT,
                "current_merge": item["mode"] == facts.PARTITION_MODE_CURRENT,
                "delivery_merge": item["mode"] == facts.PARTITION_MODE_DEDUP_FIRST,
                "delivery_download": delivery is not None,
            },
        })
    return rows


def current_platforms(db: Session, plan_run_id: int) -> list[str]:
    """本次运行中按「现行」处理的分区（含缺模式分区的兼容解释）。"""
    return [
        row["platform"] for row in partition_rows(db, plan_run_id=plan_run_id)
        if row["mode"] == facts.PARTITION_MODE_CURRENT
    ]


def dedup_first_platforms(db: Session, plan_run_id: int) -> list[str]:
    return [
        row["platform"] for row in partition_rows(db, plan_run_id=plan_run_id)
        if row["mode"] == facts.PARTITION_MODE_DEDUP_FIRST
    ]


def _legacy_archive_complete(db: Session, plan_run_id: int) -> bool:
    """现行分区的既有判据（#1110）：merge 产物存在且已记录 extract。"""
    merge_count = int(db.execute(
        select(func.count()).select_from(PlanRunArtifact).where(
            PlanRunArtifact.plan_run_id == plan_run_id,
            PlanRunArtifact.artifact_type == "merge_result_xls",
        )
    ).scalar_one())
    if merge_count == 0:
        return False
    run = db.get(PlanRun, plan_run_id)
    if run is None:
        return False
    return isinstance((run.run_context or {}).get("extract"), dict)


def archive_complete(db: Session, plan_run_id: int) -> bool:
    """归档完成判据（D16，B8 G7）。

    - 先去重分区：交付状态为就绪或就绪但有缺口（不要求 extract 产物）；
    - 现行分区（含无模式存量）：沿用 merge + extract，一侧失败不伪装成另一侧完成；
    - 没有任何先去重分区时，行为与改动前逐字一致。
    """
    rows = partition_rows(db, plan_run_id=plan_run_id)
    dedup = [row["platform"] for row in rows
             if row["mode"] == facts.PARTITION_MODE_DEDUP_FIRST]
    if not dedup:
        return _legacy_archive_complete(db, plan_run_id)
    for platform in dedup:
        decision = shadow_delivery.current_delivery_decision(
            db, plan_run_id=plan_run_id, platform=platform,
        )
        if not shadow_delivery.delivery_is_archive_complete(decision):
            return False
    if any(row["mode"] == facts.PARTITION_MODE_CURRENT for row in rows):
        return _legacy_archive_complete(db, plan_run_id)
    return True


def run_capabilities(db: Session, plan_run_id: int) -> dict:
    """能力目录的分区补充：现有扁平 capabilities 保持兼容，新增分区键。"""
    rows = partition_rows(db, plan_run_id=plan_run_id)
    modes = {row["platform"]: row["mode"] for row in rows}
    has_current = any(
        row["mode"] == facts.PARTITION_MODE_CURRENT for row in rows
    ) or not rows
    return {
        "partition_modes": modes,
        # D13 第 4 条：只有「先去重」分区时 extract 必须拒绝；混合运行仍可提取现行侧。
        "extract": has_current,
    }


__all__ = [
    "archive_complete",
    "current_platforms",
    "dedup_first_platforms",
    "delivery_view",
    "normalize_platform",
    "observation_counts",
    "partition_rows",
    "partition_views",
    "run_capabilities",
    "run_platforms",
]
