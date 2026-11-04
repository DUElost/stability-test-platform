"""B8 G2 影子轮：独立命名空间中的报表接收，不登记现行产物。

现行 scan/upload/merge/extract 的读取方不调用本模块。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select

from backend.core.database import SessionLocal
from backend.core.settings.scheduler import get_scheduler_settings
from backend.core.storage_root import resolve_shared_storage_root
from backend.core.task_queue import enqueue_sync
from backend.models.enums import PlanRunStatus
from backend.models.plan_run import PlanRun
from backend.services import issue_fact_store as facts
from backend.services.dedup_scan import _SCAN_TASK_SAQ_TIMEOUT
from backend.services.plan_run_scan_scope import (
    build_scan_now_payload,
    iter_plan_run_scan_hosts,
    load_expected_scan_platforms,
)

from backend.agent.contracts.round_report_layout import (
    round_manifest_path,
    round_report_dir,
)

logger = logging.getLogger(__name__)

COVERAGE_RECEIVED = "RECEIVED"
COVERAGE_EMPTY = "EMPTY"
COVERAGE_MISSING = "MISSING"
COVERAGE_LATE = "LATE"
_COVERED = {COVERAGE_RECEIVED, COVERAGE_EMPTY}
_TERMINAL = {
    PlanRunStatus.SUCCESS.value,
    PlanRunStatus.PARTIAL_SUCCESS.value,
    PlanRunStatus.FAILED.value,
}
_PLATFORM = "mtk"


def _dispatch_due(unit, now):
    if not unit.get("expected", True) or unit["coverage"] in _COVERED:
        return False
    last_sent = unit.get("last_dispatched_at")
    return last_sent is None or now >= datetime.fromisoformat(last_sent) + timedelta(
        seconds=_SCAN_TASK_SAQ_TIMEOUT
    )


def _locked_run(db, plan_run_id):
    # 本路径不锁 job/device/lease；统一先 PlanRun，再按 round_no 锁 scan_round。
    return db.execute(
        select(PlanRun).where(PlanRun.id == plan_run_id).with_for_update()
    ).scalar_one_or_none()


def _expected_units(db, plan_run_id):
    # 与 scan_completeness 同源，不把离线 host 从期望集中删掉。
    hosts = iter_plan_run_scan_hosts(db, plan_run_id)
    expected = load_expected_scan_platforms(db, plan_run_id, [host for host, _, _ in hosts])
    return [
        {"host_id": host, "platform": _PLATFORM}
        for host, platforms in sorted(expected.items())
        if _PLATFORM in platforms
    ]


def _refresh_round(db, row, root, now):
    if row.status == facts.SCAN_ROUND_STATUS_CLOSED:
        return
    reports = read_round_reports(root, row.plan_run_id, row.id, row.expected_set)
    coverage = classify_round_coverage(
        row.expected_set, reports, observed_at=now, input_cutoff_at=row.input_cutoff_at
    )
    facts.record_scan_round_coverage(db, round_id=row.id, coverage=coverage)
    expected = [u for u in coverage if u.get("expected", True)]
    if all(u["coverage"] in _COVERED for u in expected) or now >= row.input_cutoff_at:
        facts.set_scan_round_status(
            db, round_id=row.id, status=facts.SCAN_ROUND_STATUS_CLOSED, closed_at=now
        )


def _report_key(report):
    return (
        report["fact_round_id"],
        report["host_id"],
        report["platform"],
        report.get("storage_uri"),
        report["empty_receipt"],
    )


def _late_inputs(rounds, root):
    consumed = {
        _report_key(r) for row in rounds for u in row.expected_set for r in u.get("reports", [])
    }
    pending = {}
    for row in rounds:
        if row.status != facts.SCAN_ROUND_STATUS_CLOSED:
            continue
        for report in read_round_reports(root, row.plan_run_id, row.id, row.expected_set):
            key = _report_key(report)
            if key not in consumed:
                pending[key] = report
    return list(pending.values())


def _new_round(db, run_id, rounds, units, kind, now, wait, *, carried=()):
    coverage = [{"host_id": u["host_id"], "platform": u["platform"], "coverage": COVERAGE_MISSING} for u in units]
    by_host = {u["host_id"]: u for u in coverage}
    for report in carried:
        host = report["host_id"]
        if host not in by_host:
            # 仅存输入引用的项不进入补充轮期望集，保留已覆盖 host 的迟到发生。
            unit = {
                "host_id": host,
                "platform": _PLATFORM,
                "expected": False,
                "coverage": COVERAGE_MISSING,
            }
            coverage.append(unit)
            by_host[host] = unit
        by_host[host].setdefault("reports", []).append(report)
    for unit in coverage:
        if unit.get("reports"):
            unit["coverage"] = (
                COVERAGE_EMPTY
                if all(r["empty_receipt"] for r in unit["reports"])
                else COVERAGE_RECEIVED
            )
    return facts.record_scan_round(
        db,
        plan_run_id=run_id,
        platform=_PLATFORM,
        round_no=max((r.round_no for r in rounds), default=0) + 1,
        kind=kind,
        expected_set=coverage,
        input_cutoff_at=now + timedelta(seconds=wait),
    )


def _advance_run(db, run_id, root, now, settings):
    run = _locked_run(db, run_id)
    if run is None:
        return []
    rounds = facts.list_scan_rounds(db, plan_run_id=run_id, platform=_PLATFORM)
    # 候选收集后可能已进入终态；未在 RUNNING 时建立影子轮的分区不启动。
    if run.status != PlanRunStatus.RUNNING.value and not rounds:
        return []
    units = _expected_units(db, run_id)
    if not units and not rounds:
        return []
    facts.record_partition_mode(
        db, plan_run_id=run_id, platform=_PLATFORM, mode=facts.PARTITION_MODE_CURRENT
    )
    for row in rounds:
        _refresh_round(db, row, root, now)
    new = None
    if run.status == PlanRunStatus.RUNNING.value:
        if not rounds or now >= rounds[-1].created_at + timedelta(
            seconds=settings.stp_scan_round_interval_seconds
        ):
            new = _new_round(
                db,
                run_id,
                rounds,
                units,
                facts.SCAN_ROUND_KIND_PERIODIC,
                now,
                settings.stp_scan_round_report_wait_seconds,
                carried=_late_inputs(rounds, root),
            )
    elif run.status in _TERMINAL:
        final = next((r for r in rounds if r.kind == facts.SCAN_ROUND_KIND_FINAL), None)
        if final is None:
            new = _new_round(
                db,
                run_id,
                rounds,
                units,
                facts.SCAN_ROUND_KIND_FINAL,
                now,
                settings.stp_scan_round_report_wait_seconds,
                carried=_late_inputs(rounds, root),
            )
        elif final.status == facts.SCAN_ROUND_STATUS_CLOSED and all(
            r.status == facts.SCAN_ROUND_STATUS_CLOSED for r in rounds
        ):
            late = _late_inputs(rounds, root)
            if late:
                covered = {
                    u["host_id"]
                    for r in rounds
                    if r.kind != facts.SCAN_ROUND_KIND_PERIODIC
                    for u in r.expected_set
                    if u["coverage"] in _COVERED
                }
                missing = [
                    u
                    for u in final.expected_set
                    if u["host_id"] not in covered and u.get("expected", True)
                ]
                new = _new_round(
                    db,
                    run_id,
                    rounds,
                    missing,
                    facts.SCAN_ROUND_KIND_SUPPLEMENT,
                    now,
                    settings.stp_scan_round_report_wait_seconds,
                    carried=late,
                )
    if new is not None:
        rounds.append(new)
        _refresh_round(db, new, root, now)
    return [
        (r.id, r.kind != facts.SCAN_ROUND_KIND_PERIODIC)
        for r in rounds
        if r.status != facts.SCAN_ROUND_STATUS_CLOSED
        and any(_dispatch_due(unit, now) for unit in r.expected_set)
    ]


def sweep_shadow_scan_rounds(*, now=None):
    """借现有检查周期推进影子轮；先提交轮次，再尝试入队，失败下周期恢复。"""
    settings = get_scheduler_settings()
    if not settings.stp_scan_round_shadow_enabled:
        return
    root = resolve_shared_storage_root()
    if not root:
        logger.warning("shadow_scan_no_storage_root")
        return
    now = now or datetime.now(timezone.utc)
    with SessionLocal() as db:
        run_ids = facts.list_shadow_scan_run_ids(db, platform=_PLATFORM)
    for run_id in sorted(run_ids):
        try:
            with SessionLocal() as db:
                pending = _advance_run(db, run_id, root, now, settings)
                db.commit()
            for round_id, is_final in pending:
                # 每个检查周期独立尝试，已完成 SAQ key 的 TTL 不阻塞未关闭轮重试。
                tick = int(now.timestamp()) // max(1, settings.auto_archive_poll_interval_seconds)
                enqueue_sync(
                    "scan_task",
                    key=f"scan:shadow:{run_id}:{round_id}:{tick}",
                    timeout=_SCAN_TASK_SAQ_TIMEOUT,
                    retries=2,
                    required=True,
                    plan_run_id=run_id,
                    fact_round_id=round_id,
                    is_final=is_final,
                )
        except Exception:
            logger.exception("shadow_scan_sweep_failed plan_run=%d", run_id)


def prepare_shadow_scan(plan_run_id, fact_round_id):
    """工作线程短事务：先锁 run 与 round，再生成仅影子调用的载荷。"""
    if not get_scheduler_settings().stp_scan_round_shadow_enabled:
        return []
    if type(fact_round_id) is not int or fact_round_id <= 0:
        raise ValueError("fact_round_id must be a positive int")
    root = resolve_shared_storage_root()
    if not root:
        return []
    with SessionLocal() as db:
        if _locked_run(db, plan_run_id) is None:
            return []
        row = facts.get_scan_round(db, round_id=fact_round_id)
        if (
            row is None
            or row.plan_run_id != plan_run_id
            or row.platform != _PLATFORM
            or row.status == facts.SCAN_ROUND_STATUS_CLOSED
        ):
            return []
        facts.record_partition_mode(
            db, plan_run_id=plan_run_id, platform=_PLATFORM, mode=facts.PARTITION_MODE_CURRENT
        )
        now = datetime.now(timezone.utc)
        _refresh_round(db, row, root, now)
        if row.status == facts.SCAN_ROUND_STATUS_CLOSED:
            db.commit()
            return []
        targets = {
            u["host_id"]
            for u in row.expected_set
            if _dispatch_due(u, now)
        }
        coverage = [dict(unit) for unit in row.expected_set]
        payloads = []
        for host, status, retired in iter_plan_run_scan_hosts(db, plan_run_id):
            if host in targets and status == "ONLINE" and not retired:
                spill = facts.list_partition_spill_inputs(
                    db, plan_run_id=plan_run_id, platform=_PLATFORM, host_id=host
                )
                payload = build_scan_now_payload(
                    db,
                    plan_run_id,
                    host,
                    is_final=row.kind != facts.SCAN_ROUND_KIND_PERIODIC,
                    fact_round_id=row.id,
                    spill_inputs=spill,
                )
                if payload["device_serials"]:
                    payloads.append((host, payload))
                    for unit in coverage:
                        if unit["host_id"] == host:
                            unit["last_dispatched_at"] = now.isoformat()
                else:
                    logger.warning("shadow_scan_no_partition_serials round=%d host=%s", row.id, host)
        if payloads:
            # 行锁内先保留下发时间，再由 scan_task 发出命令；并行任务不能重复下发。
            # 提交后若执行中断，超过现行 scan_task 超时后恢复。
            facts.record_scan_round_coverage(db, round_id=row.id, coverage=coverage)
        facts.set_scan_round_status(db, round_id=row.id, status=facts.SCAN_ROUND_STATUS_RUNNING)
        db.commit()
        return payloads


def classify_round_coverage(
    expected_set: list[dict],
    reports: list[dict],
    *,
    observed_at: datetime,
    input_cutoff_at: datetime,
    closed: bool = False,
) -> list[dict]:
    """按期望项区分有报表、明确空回执、无回执、截止后到达。

    已确认的有效输入保持不变；迟到条目单独记录，不转成有效报表。merge
    的成功或失败不参与本函数，因而不会把 merge 失败解释为空回执。
    """
    coverage = []
    for unit in expected_set:
        current = dict(unit)
        identity = (unit["host_id"], unit["platform"])
        matching = [r for r in reports if (r["host_id"], r["platform"]) == identity]
        if current.get("coverage") in {COVERAGE_RECEIVED, COVERAGE_EMPTY}:
            previous = current.get("reports", [])
            additional = [r for r in matching if r not in previous]
            if not closed and observed_at <= input_cutoff_at:
                current["reports"] = [*previous, *additional]
                if any(not r["empty_receipt"] for r in current["reports"]):
                    current["coverage"] = COVERAGE_RECEIVED
            elif additional:
                current["late_reports"] = additional
            coverage.append(current)
            continue
        if not matching:
            current.setdefault("coverage", COVERAGE_MISSING)
        elif closed or observed_at > input_cutoff_at:
            current.update(coverage=COVERAGE_LATE, late_reports=matching)
        else:
            current.update(
                coverage=COVERAGE_EMPTY
                if all(r["empty_receipt"] for r in matching)
                else COVERAGE_RECEIVED,
                reports=matching,
                observed_at=observed_at.isoformat(),
            )
        coverage.append(current)
    return coverage


def read_round_reports(
    nfs_root: str,
    plan_run_id: int,
    fact_round_id: int,
    expected_set: list[dict],
) -> list[dict]:
    """仅接受本轮命名空间、绑定相同 run/host/platform/轮次的分片条目。

    空回执使用同一 artifacts 列表中的 ``empty_receipt: true`` 条目；缺少
    fact_round_id 的旧分片不推断归属。返回值只供影子覆盖/G4 使用。
    """
    reports: list[dict] = []
    for unit in expected_set:
        host_id, platform = unit["host_id"], unit["platform"]
        manifest = round_manifest_path(nfs_root, plan_run_id, fact_round_id, host_id)
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except FileNotFoundError:
            continue
        except (OSError, ValueError):
            logger.warning(
                "shadow_scan_manifest_unreadable round=%d host=%s", fact_round_id, host_id
            )
            continue
        if (
            not isinstance(data, dict)
            or data.get("schema_version") != 1
            or type(data.get("plan_run_id")) is not int
            or data.get("plan_run_id") != plan_run_id
            or data.get("host_id") != host_id
            or not isinstance(data.get("artifacts"), list)
        ):
            continue
        report_dir = round_report_dir(nfs_root, plan_run_id, fact_round_id, platform)
        for entry in data["artifacts"]:
            if (
                not isinstance(entry, dict)
                or type(entry.get("fact_round_id")) is not int
                or entry["fact_round_id"] != fact_round_id
                or entry.get("platform") != platform
            ):
                continue
            receipt = {"host_id": host_id, "platform": platform, "fact_round_id": fact_round_id}
            if entry.get("empty_receipt") is True:
                reports.append({**receipt, "empty_receipt": True})
                continue
            if not entry.get("registerable"):
                continue
            file_key = entry.get("file_key")
            if not isinstance(file_key, str):
                continue
            # file_key 可相对 run 的 dedup 根或轮次根；最终必须落在契约目录。
            relative = Path(file_key)
            if relative.is_absolute() or ".." in relative.parts:
                continue
            base = (
                report_dir.parents[2] if relative.parts[:1] == ("_rounds",) else report_dir.parent
            )
            path = base / relative
            if path.parent.resolve() != report_dir.resolve() or not path.is_file():
                continue
            reports.append({**receipt, "empty_receipt": False, "storage_uri": str(path)})
    return reports
