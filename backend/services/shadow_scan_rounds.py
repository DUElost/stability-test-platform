"""B8 G2 影子轮：独立命名空间中的报表接收，不登记现行产物。

现行 scan/upload/merge/extract 的读取方不调用本模块。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select, text

from backend.core.database import SessionLocal
from backend.core.settings.scheduler import get_scheduler_settings
from backend.core.storage_root import resolve_shared_storage_root
from backend.core.task_queue import enqueue_sync
from backend.models.enums import PlanRunStatus
from backend.models.plan_run import PlanRun
from backend.models.host import Host
from backend.services import issue_fact_store as facts
from backend.services import shadow_delivery
from backend.services import partition_capability
from backend.services import partition_mode_admission as admission
from backend.services.agent_capabilities import (
    evaluate_host_capability,
    load_host_capability_evidence,
)
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
# 补偿发现只做增量重试，最长等锁 2s：远小于 auto_archive_poll_interval_seconds
# 默认 120s，delivery_version 上的强锁最多迟滞本轮现行归档 2s；超时按发现失败
# 处理，下一周期再试。
_DELIVERY_DISCOVERY_LOCK_TIMEOUT_MS = 2000


@dataclass(frozen=True)
class ShadowScanDispatch:
    host_id: str
    payload: dict
    reserved_at: str


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


def _late_inputs(rounds, root, *, strict=False):
    consumed = {
        _report_key(r) for row in rounds for u in row.expected_set for r in u.get("reports", [])
    }
    pending = {}
    for row in rounds:
        if row.status != facts.SCAN_ROUND_STATUS_CLOSED:
            continue
        for report in read_round_reports(root, row.plan_run_id, row.id, row.expected_set, strict=strict):
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


def _capability_recovered_units(db, latest_round, now):
    """Hosts whose latest closed round lost capability and whose current heartbeat proves it again."""
    recovered = []
    for unit in latest_round.expected_set:
        if not unit.get("expected", True) or unit.get("reason") != "capability_unverified":
            continue
        evidence = load_host_capability_evidence(db, unit["host_id"], now=now)
        if evidence.verified:
            recovered.append({
                "host_id": unit["host_id"],
                "platform": unit["platform"],
            })
            logger.info(
                "shadow_scan_capability_recovered plan_run=%d fact_round_id=%d host=%s platform=%s",
                latest_round.plan_run_id, latest_round.id, unit["host_id"], _PLATFORM,
            )
    return recovered


def _advance_run(db, run_id, root, now, settings):
    run = _locked_run(db, run_id)
    if run is None:
        return []
    partition = facts.get_partition_mode(db, plan_run_id=run_id, platform=_PLATFORM)
    if partition is not None and partition.shadow_settled_at is not None:
        return []
    rounds = facts.list_scan_rounds(db, plan_run_id=run_id, platform=_PLATFORM)
    is_dedup_first = (
        partition is not None and partition.mode == facts.PARTITION_MODE_DEDUP_FIRST
    )
    # 候选收集后可能已进入终态；未在 RUNNING 时建立影子轮的 CURRENT 分区不启动。
    # DEDUP_FIRST 分区（含 FAILED，ADR-0062 D15）即使尚无任何轮次，也必须补齐最后一轮。
    if run.status != PlanRunStatus.RUNNING.value and not rounds and not is_dedup_first:
        return []
    units = _expected_units(db, run_id)
    if not units and not rounds:
        return []
    if partition is None:
        # D3 第 1 条：轮次登记与随后的下发都是受模式影响的动作，模式必须先在同事务内固化。
        # 主干此前无条件写 CURRENT；现在由 #4043 的准入决定（白名单 + 能力证据 + 旧链副作用），
        # 已固化分区原样读回、不改写。影子入口保留「观察到的分区必有一行」的既有记账——
        # #3875 的 settled 出口把终态写在模式行上，取消它会让已观察分区每轮重复处理。
        sweep_action = admission.partition_action(
            db,
            run_id,
            platform=_PLATFORM,
            entrypoint=admission.ENTRY_SHADOW_SWEEP,
            settings=settings,
            record_current_fallback=True,
        )
        if sweep_action.held:
            # 规划裁决第 2 项：Owner 点名的候选在等能力证据期间，**登记轮次本身**也是受模式
            # 影响的扫描动作（下一跳就是 prepare_shadow_scan 的真实下发）。这里既不能建轮次、
            # 也不能入队，等能力证据到位后由下一次 sweep 正常固化并建轮。
            logger.info(
                "shadow_scan_round_hold plan_run=%d platform=%s reason=%s unverified_hosts=%s",
                run_id, _PLATFORM, sweep_action.reason,
                ",".join(sweep_action.detail.get("unverified_hosts", []) or []),
            )
            return []
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
            latest_delivery = next(
                r for r in reversed(rounds)
                if r.kind != facts.SCAN_ROUND_KIND_PERIODIC
            )
            recovered = (
                _capability_recovered_units(db, latest_delivery, now)
                if is_dedup_first else []
            )
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
            elif recovered:
                # DEDUP_FIRST 的终态最后一轮已关闭后能力才恢复：只补该轮
                # 因 capability_unverified 缺失的 host，模式保持不回溯。
                new = _new_round(
                    db,
                    run_id,
                    rounds,
                    recovered,
                    facts.SCAN_ROUND_KIND_SUPPLEMENT,
                    now,
                    settings.stp_scan_round_report_wait_seconds,
                )
            elif all(r.closed_at is not None for r in rounds) and now > max(
                r.closed_at for r in rounds
            ) + timedelta(seconds=settings.stp_scan_round_report_wait_seconds):
                # 6704918122：strict 仅用于准备 settled 时确认安静期。
                # 读失败只推迟 settled，不回滚本次轮次推进；普通迟到输入读取仍告警跳过。
                try:
                    quiet = not _late_inputs(rounds, root, strict=True)
                except (OSError, ValueError):
                    logger.warning(
                        "shadow_scan_settle_deferred plan_run=%d fact_round_id=%d "
                        "platform=%s reason=manifest_unreadable",
                        run_id, final.id, _PLATFORM, exc_info=True,
                    )
                else:
                    if quiet:
                        gaps = sorted(
                            unit["host_id"] for unit in latest_delivery.expected_set
                            if unit.get("reason") == "capability_unverified"
                            and unit["coverage"] not in _COVERED
                        )
                        if is_dedup_first and gaps:
                            # Owner 裁定 6975368043：能力失效不回退模式，只登记缺口。
                            # settled 后不再建立补充轮，因此缺口必须在终态留下可核验证据，
                            # 由生产验收/补跑工作面按 host 追认，不能被静默吞掉。
                            logger.warning(
                                "shadow_scan_settled_with_capability_gap plan_run=%d "
                                "fact_round_id=%d platform=%s hosts=%s",
                                run_id, latest_delivery.id, _PLATFORM, ",".join(gaps),
                            )
                        facts.settle_shadow_partition(
                            db, plan_run_id=run_id, platform=_PLATFORM, settled_at=now,
                            report_wait_seconds=settings.stp_scan_round_report_wait_seconds,
                        )
                        logger.info("shadow_scan_partition_settled plan_run=%d platform=%s", run_id, _PLATFORM)
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
        run_ids = set(facts.list_shadow_scan_run_ids(db, platform=_PLATFORM))
        merge_run_ids = facts.list_pending_shadow_merge_run_ids(db, now=now)
    delivery_run_ids: set[int] = set()
    try:
        with SessionLocal() as db:
            db.execute(
                text("SELECT set_config('lock_timeout', :value, true)"),
                {"value": f"{_DELIVERY_DISCOVERY_LOCK_TIMEOUT_MS}ms"},
            )
            discovered_run_ids = set(
                facts.list_shadow_delivery_due_run_ids(db, platform=_PLATFORM)
            )
    except Exception:
        # 补偿发现是增量重试：设置、SELECT、会话退出/回滚任一阶段失败都只跳过
        # 本轮补偿，不拖累本轮的影子 scan/merge 入队与随后的现行归档；候选
        # 只在会话正常退出后转正，失败时已取得的结果不得被消费。
        logger.exception("shadow_delivery_discovery_failed platform=%s", _PLATFORM)
    else:
        delivery_run_ids = discovered_run_ids
    for run_id in sorted(run_ids | set(merge_run_ids) | delivery_run_ids):
        try:
            with SessionLocal() as db:
                pending = _advance_run(db, run_id, root, now, settings) if run_id in run_ids else []
                merges = facts.pending_shadow_merge_ids(db, plan_run_id=run_id, now=now)
                db.commit()
            for round_id in merges:
                tick = int(now.timestamp()) // max(1, settings.auto_archive_poll_interval_seconds)
                enqueue_sync("shadow_merge_task", key=f"merge:shadow:{run_id}:{round_id}:{tick}",
                             timeout=_SCAN_TASK_SAQ_TIMEOUT, retries=2, required=True,
                             plan_run_id=run_id, fact_round_id=round_id)
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
            if run_id in delivery_run_ids:
                shadow_delivery.try_register_delivery_version(
                    run_id, platform=_PLATFORM,
                )
        except Exception:
            logger.exception("shadow_scan_sweep_failed plan_run=%d", run_id)


def _shadow_capability_verified(host, desired_digest=""):
    """下发守卫与准入共用的能力预筛接缝（Owner 裁定 6975368043）。

    判据住在 :mod:`backend.services.partition_capability`，该模块在 #4044/#4049
    落地后委托 :func:`backend.services.agent_capabilities.evaluate_host_capability`。
    ``desired_digest`` 保留为兼容形参，不再参与判定（摘要列只服务 ADR-0040 展示）。
    实际下发路径（``prepare_shadow_scan`` / ``verify_shadow_scan_dispatch``）直接调用
    同模块导入的 ``evaluate_host_capability`` / ``load_host_capability_evidence``，
    以便 #4044 探针能 monkeypatch 本模块命名空间；两侧证据来源仍是同一个。
    """
    return partition_capability.host_scan_round_capability(host, desired_digest).verified


def prepare_shadow_scan(plan_run_id, fact_round_id) -> list[ShadowScanDispatch]:
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
        partition = facts.get_partition_mode(db, plan_run_id=plan_run_id, platform=_PLATFORM)
        if partition is not None and partition.shadow_settled_at is not None:
            return []
        row = facts.get_scan_round(db, round_id=fact_round_id)
        if (
            row is None
            or row.plan_run_id != plan_run_id
            or row.platform != _PLATFORM
            or row.status == facts.SCAN_ROUND_STATUS_CLOSED
        ):
            return []
        if partition is None:
            # 同上：本函数提交后立刻返回下发载荷，模式必须在同一事务、同一次提交里固化。
            prepare_action = admission.partition_action(
                db,
                plan_run_id,
                platform=_PLATFORM,
                entrypoint=admission.ENTRY_SHADOW_PREPARE,
                record_current_fallback=True,
            )
            if prepare_action.held:
                # 候选在等能力证据：轮次已存在也不能下发扫描（裁决第 2 项）。轮次保持未执行，
                # 由下一次 sweep 在模式固化后继续；此处不覆盖记账，避免把「等待」写成「缺失」。
                logger.info(
                    "shadow_scan_dispatch_hold plan_run=%d fact_round_id=%d platform=%s "
                    "reason=%s",
                    plan_run_id, fact_round_id, _PLATFORM, prepare_action.reason,
                )
                db.commit()
                return []
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
        host_states = {host.id: host for host in db.execute(
            select(Host).where(Host.id.in_(targets)),
        ).scalars()}
        dispatches = []
        for host, status, retired in iter_plan_run_scan_hosts(db, plan_run_id):
            if host not in targets:
                continue
            unit = next(u for u in coverage if u["host_id"] == host)
            host_row = host_states.get(host)
            evidence = evaluate_host_capability(
                host_row, now=now,
                retired=retired or bool(host_row is not None and host_row.retired_at is not None),
            )
            if not evidence.verified:
                unit.update(coverage=COVERAGE_MISSING, reason="capability_unverified")
                logger.warning(
                    "shadow_scan_capability_unverified plan_run=%d fact_round_id=%d "
                    "host=%s platform=%s reason=%s",
                    plan_run_id, row.id, host, _PLATFORM, evidence.reason,
                )
                continue
            unit.pop("reason", None)
            if status == "ONLINE" and not retired:
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
                    reserved_at = now.isoformat()
                    unit["last_dispatched_at"] = reserved_at
                    dispatches.append(ShadowScanDispatch(host, payload, reserved_at))
                else:
                    logger.warning("shadow_scan_no_partition_serials round=%d host=%s", row.id, host)
        if coverage != row.expected_set:
            # 行锁内先保留下发时间，再由 scan_task 发出命令；并行任务不能重复下发。
            # 提交后若执行中断，超过现行 scan_task 超时后恢复。
            facts.record_scan_round_coverage(db, round_id=row.id, coverage=coverage)
        facts.set_scan_round_status(db, round_id=row.id, status=facts.SCAN_ROUND_STATUS_RUNNING)
        db.commit()
        return dispatches


def verify_shadow_scan_dispatch(plan_run_id, fact_round_id, dispatch: ShadowScanDispatch) -> str | None:
    """Re-check heartbeat evidence immediately before the actual send.

    Returns the capability-bearing Agent instance id only while the same
    reservation is still open and the current heartbeat still proves the
    capability. A late heartbeat that withdrew capability, or a reconnect to a
    different instance, makes this return ``None``.
    """
    with SessionLocal() as db:
        if _locked_run(db, plan_run_id) is None:
            return None
        row = facts.get_scan_round(db, round_id=fact_round_id)
        if (
            row is None
            or row.plan_run_id != plan_run_id
            or row.platform != _PLATFORM
            or row.status == facts.SCAN_ROUND_STATUS_CLOSED
        ):
            return None
        unit = next(
            (u for u in row.expected_set if u["host_id"] == dispatch.host_id),
            None,
        )
        if (
            unit is None
            or unit.get("last_dispatched_at") != dispatch.reserved_at
            or unit["coverage"] in _COVERED
        ):
            return None
        evidence = load_host_capability_evidence(
            db, dispatch.host_id, now=datetime.now(timezone.utc),
        )
        if not evidence.verified:
            logger.warning(
                "shadow_scan_capability_unverified_before_send plan_run=%d "
                "fact_round_id=%d host=%s platform=%s reason=%s",
                plan_run_id, fact_round_id, dispatch.host_id, _PLATFORM,
                evidence.reason,
            )
            return None
        return evidence.instance_id


def reject_shadow_scan_dispatch(plan_run_id, fact_round_id, dispatch: ShadowScanDispatch) -> None:
    """Record the rejection for this reservation and preserve its retry deadline."""
    logger.warning(
        "shadow_scan_capability_unverified_at_send plan_run=%d fact_round_id=%d host=%s platform=%s",
        plan_run_id, fact_round_id, dispatch.host_id, _PLATFORM,
    )
    with SessionLocal() as db:
        if _locked_run(db, plan_run_id) is None:
            return
        row = facts.get_scan_round(db, round_id=fact_round_id)
        if (
            row is None
            or row.plan_run_id != plan_run_id
            or row.platform != _PLATFORM
            or row.status == facts.SCAN_ROUND_STATUS_CLOSED
        ):
            return
        coverage = [dict(unit) for unit in row.expected_set]
        for unit in coverage:
            if (
                unit["host_id"] == dispatch.host_id
                and unit.get("last_dispatched_at") == dispatch.reserved_at
                and unit["coverage"] not in _COVERED
            ):
                unit.update(coverage=COVERAGE_MISSING, reason="capability_unverified")
                facts.record_scan_round_coverage(db, round_id=row.id, coverage=coverage)
                db.commit()
                return


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
    *, strict: bool = False,
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
            if strict:
                # 清单不可读不能当作「没有新迟到输入」而提前 settled。
                raise
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
            companions = {}
            for field, output in [("companion_file_key", "mapping_uri"), ("membership_file_key", "membership_uri")]:
                value = entry.get(field)
                companions[output] = None
                if isinstance(value, str):
                    rel = Path(value)
                    if not rel.is_absolute() and ".." not in rel.parts:
                        companion_base = report_dir.parents[2] if rel.parts[:1] == ("_rounds",) else report_dir.parent
                        candidate = companion_base / rel
                        if candidate.resolve().parent == report_dir.resolve() and candidate.is_file():
                            companions[output] = str(candidate)
            reports.append({**receipt, "empty_receipt": False, "storage_uri": str(path), **companions})
    return reports
