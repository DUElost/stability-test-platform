"""B8 #4043：首次分区模式写入的准入判据（ADR-0062 v1.2 D3 第 1 条、D13）。

覆盖本单验收的判定矩阵：

1. 白名单 + 能力全部证实 + 无旧链副作用 → 在首个动作前写入 ``DEDUP_FIRST``；
2. 同一 Plan 的两个实际 run 只有一个获批；未获批 run 不被「开关回读」改写；
3. 有白名单但任一预期 MTK host 能力未证实 → 硬阻断（既不写 DEDUP_FIRST，也不锁死 CURRENT）；
4. 在途旧 run（已有旧链痕迹）→ 固化 CURRENT；展锐产物不算本分区副作用；
5. 历史无模式分区按现行解释，非影子入口不补写记录（D13 第 2 条）；
6. 只有 MTK 可以先去重（D13 第 3 条）；关开关只影响后续入场（D13 第 5 条）；
7. 并发首写只留一条记录，任务重试与进程重建读到同一决定。

能力证据走 :mod:`partition_capability` 的真实判据（#4044 心跳显式能力），期望集走
``plan_run_scan_scope`` 的真实派生（JobInstance × Device.platform）；判据本身不被 mock。
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from backend.agent.contracts.capabilities import SCAN_ROUND_V1
from backend.core.database import SessionLocal
from backend.core.settings.scheduler import SchedulerSettings
from backend.models.host import Device, Host
from backend.models.issue_fact import PartitionMode
from backend.models.job import JobInstance
from backend.models.plan import Plan
from backend.models.plan_run import PlanRun
from backend.models.plan_run_artifact import PlanRunArtifact
from backend.services import issue_fact_store as facts
from backend.services import partition_capability as capability
from backend.services import partition_mode_admission as admission
from backend.services import partition_scope
from backend.services import shadow_scan_rounds


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _settings(**kwargs) -> SchedulerSettings:
    base = dict(
        stp_scan_round_shadow_enabled=True,
        stp_dedup_first_enabled=True,
        stp_dedup_first_plan_run_ids="",
    )
    base.update(kwargs)
    return SchedulerSettings(**base)


def _whitelist(*run_ids) -> str:
    return ",".join(str(rid) for rid in run_ids)


def _plan_run(db, plan=None, *, status="RUNNING"):
    plan = plan or Plan(name=f"p4043-{uuid4()}", created_by="pytest")
    if plan.id is None:
        db.add(plan)
        db.commit()
    run = PlanRun(
        plan_id=plan.id, status=status, plan_snapshot={}, run_type="MANUAL",
        triggered_by="pytest", started_at=_now(), ended_at=None,
    )
    db.add(run)
    db.commit()
    return run


def _ip() -> str:
    """``host.ip`` 有唯一约束（``host_ip_key``），每台测试主机用可辨识的独立地址。"""
    _ip.counter += 1
    return f"192.0.{_ip.counter // 254}.{_ip.counter % 254}"


_ip.counter = 0


def _host(
    db,
    *,
    status="ONLINE",
    reported_at=None,
    host_id=None,
    capable=True,
    capabilities=None,
    instance="agent-a",
):
    """构造「能力可证明 / 不可证明」的 host 事实（不 mock 判据；#4044 心跳能力）。"""
    host_id = host_id or f"h-{uuid4().hex[:8]}"
    now = _now()
    address = _ip()
    if capabilities is None and capable:
        capabilities = [SCAN_ROUND_V1]
    db.add(Host(
        id=host_id, hostname=host_id, name=host_id,
        ip_address=address, ip=address, status=status, last_heartbeat=now,
        agent_capabilities=list(capabilities) if capabilities is not None else None,
        agent_capabilities_reported_at=(
            reported_at if reported_at is not None else (now if capable else None)
        ),
        agent_capabilities_instance_id=instance if capable else None,
    ))
    db.commit()
    return host_id


def _device_job(db, run, host_id, *, platform="MTK"):
    device = Device(
        serial=f"SN-{uuid4().hex[:8]}", host_id=host_id, platform=platform,
        status="ONLINE", model="test", tags=[],
    )
    db.add(device)
    db.flush()
    db.add(JobInstance(
        plan_run_id=run.id, plan_id=run.plan_id, device_id=device.id,
        host_id=host_id, status="RUNNING", pipeline_def={},
    ))
    db.commit()
    return device


def _mode_rows(db, run_id):
    return list(db.execute(
        select(PartitionMode).where(PartitionMode.plan_run_id == run_id)
    ).scalars())


def _decide(db, run_id, *, settings, platform="mtk"):
    return admission.decide_partition_mode(db, run_id, platform=platform, settings=settings)


def test_admitted_run_fixates_dedup_first_before_any_action(db_session):
    run = _plan_run(db_session)
    _device_job(db_session, run, _host(db_session))
    _device_job(db_session, run, _host(db_session))
    settings = _settings(stp_dedup_first_plan_run_ids=_whitelist(run.id))

    decision = _decide(db_session, run.id, settings=settings)
    assert (decision.mode, decision.reason) == (
        facts.PARTITION_MODE_DEDUP_FIRST, admission.REASON_OWNER_ADMITTED,
    )

    mode = admission.fixate_partition_mode(
        db_session, run.id, entrypoint=admission.ENTRY_SCAN_TASK, settings=settings,
    )
    db_session.commit()
    assert mode == facts.PARTITION_MODE_DEDUP_FIRST
    assert [(row.platform, row.mode) for row in _mode_rows(db_session, run.id)] == [
        ("mtk", "DEDUP_FIRST"),
    ]


@pytest.mark.parametrize("reason_case", ["offline_host", "stale_capability_report"])
def test_unverified_expected_host_hard_blocks_dedup_first(db_session, reason_case):
    """白名单命中，但有一个预期 MTK host 能力未证实 → 不写 DEDUP_FIRST，也不写 CURRENT。"""
    run = _plan_run(db_session)
    good = _host(db_session)
    _device_job(db_session, run, good)
    bad = _host(
        db_session,
        status="OFFLINE" if reason_case == "offline_host" else "ONLINE",
        capable=False if reason_case == "offline_host" else True,
        reported_at=(
            None if reason_case == "offline_host" else _now() - timedelta(hours=1)
        ),
    )
    _device_job(db_session, run, bad)
    settings = _settings(stp_dedup_first_plan_run_ids=_whitelist(run.id))

    assert capability.expected_mtk_host_ids(db_session, run.id) == sorted([bad, good])
    decision = _decide(db_session, run.id, settings=settings)
    assert decision.mode is None
    assert decision.reason == admission.REASON_CAPABILITY_UNVERIFIED
    assert decision.detail["unverified_hosts"] == [bad]
    assert decision.detail["reasons"][bad] == (
        "host_offline" if reason_case == "offline_host"
        else capability.REASON_CAPABILITY_NOT_CURRENT
    )

    assert admission.fixate_partition_mode(
        db_session, run.id, entrypoint=admission.ENTRY_UPLOAD_TASK, settings=settings,
    ) is None
    db_session.commit()
    assert _mode_rows(db_session, run.id) == []


def test_admission_and_send_guard_share_one_capability_rule(db_session):
    """Owner 裁定 6975368043：模式准入与轮次下发守卫必须读同一份可核验能力证据。"""
    run = _plan_run(db_session)
    good = _host(db_session)
    stale = _host(db_session, reported_at=_now() - timedelta(hours=1))
    _device_job(db_session, run, good)
    _device_job(db_session, run, stale)

    verdicts = {item.host_id: item.verified for item in
                capability.verify_expected_mtk_hosts(db_session, run.id)}
    assert verdicts == {good: True, stale: False}

    rows = {row.id: row for row in db_session.query(Host).filter(
        Host.id.in_([good, stale])).all()}
    assert shadow_scan_rounds._shadow_capability_verified(rows[good]) is True
    assert shadow_scan_rounds._shadow_capability_verified(rows[stale]) is False


def test_in_flight_old_run_with_old_chain_report_fixates_current(db_session):
    run = _plan_run(db_session, status="SUCCESS")
    host_id = _host(db_session)
    _device_job(db_session, run, host_id)
    db_session.add(PlanRunArtifact(
        plan_run_id=run.id, artifact_type="scan_result_xls", host_id=host_id,
        storage_uri=f"/mnt/stp-log/dedup/{run.id}/mtk/{host_id}_Result_l_org.xls",
    ))
    db_session.commit()
    settings = _settings(stp_dedup_first_plan_run_ids=_whitelist(run.id))

    decision = _decide(db_session, run.id, settings=settings)
    assert (decision.mode, decision.reason) == (
        facts.PARTITION_MODE_CURRENT, admission.REASON_OLD_CHAIN_EFFECTS,
    )
    assert decision.detail["mtk_report_artifacts"] == 1

    assert admission.fixate_partition_mode(
        db_session, run.id, entrypoint=admission.ENTRY_MERGE, settings=settings,
    ) == facts.PARTITION_MODE_CURRENT
    db_session.commit()


def test_unisoc_artifacts_are_not_this_partition_side_effects(db_session):
    run = _plan_run(db_session)
    host_id = _host(db_session)
    _device_job(db_session, run, host_id)
    db_session.add(PlanRunArtifact(
        plan_run_id=run.id, artifact_type="merge_result_xls", host_id=host_id,
        storage_uri=f"/mnt/stp-log/dedup/{run.id}/unisoc/Result_Merge.xls",
    ))
    db_session.commit()
    settings = _settings(stp_dedup_first_plan_run_ids=_whitelist(run.id))

    assert _decide(db_session, run.id, settings=settings).mode == "DEDUP_FIRST"


def test_merge_platforms_record_with_mtk_blocks_entry(db_session):
    """可归因到 MTK 的旧 merge 记录 = 已进入现行链 → 固化 CURRENT。"""
    run = _plan_run(db_session, status="SUCCESS")
    _device_job(db_session, run, _host(db_session))
    run.run_context = {"merge_platforms": {"platforms": {"mtk": "ok", "unisoc": "ok"}}}
    db_session.commit()
    settings = _settings(stp_dedup_first_plan_run_ids=_whitelist(run.id))

    decision = _decide(db_session, run.id, settings=settings)
    assert decision.reason == admission.REASON_OLD_CHAIN_EFFECTS
    assert decision.detail["run_context_sections"] == ["merge_platforms"]


def test_unisoc_only_current_work_does_not_forfeit_mtk_candidate(db_session):
    """展锐现行侧的正常旧链动作不得把 MTK 候选打回旧链（裁决第 2 项）。

    混合运行里 upload/merge/extract 会为展锐写 run 级段；只有能归因到 MTK 的痕迹才算失格证据，
    否则每个混合试点都会在第一次展锐上传后被永久排除在新链之外。
    """
    run = _plan_run(db_session, status="SUCCESS")
    host_id = _host(db_session)
    _device_job(db_session, run, host_id)
    _device_job(db_session, run, host_id, platform="UNISOC")
    run.run_context = {
        "upload_mark": {"marked": 12},
        "archive": {"artifacts": 1},
        "merge_platforms": {"platforms": {"unisoc": "ok"}},
        "extract": {"extracted": 3},
    }
    db_session.add(PlanRunArtifact(
        plan_run_id=run.id, artifact_type="merge_result_xls", host_id=host_id,
        storage_uri=f"/mnt/stp-log/dedup/{run.id}/unisoc/Result_Merge.xls",
    ))
    db_session.commit()
    settings = _settings(stp_dedup_first_plan_run_ids=_whitelist(run.id))

    decision = _decide(db_session, run.id, settings=settings)
    assert decision.mode == facts.PARTITION_MODE_DEDUP_FIRST
    assert decision.reason == admission.REASON_OWNER_ADMITTED


def test_sibling_run_of_same_plan_is_not_admitted(db_session):
    """白名单按实际 PlanRun，不按 Plan 扩展：同一 Plan 的兄弟 run 各判各的。"""
    plan = Plan(name=f"p4043-siblings-{uuid4()}", created_by="pytest")
    db_session.add(plan)
    db_session.commit()
    approved = _plan_run(db_session, plan)
    sibling = _plan_run(db_session, plan)
    _device_job(db_session, approved, _host(db_session))
    _device_job(db_session, sibling, _host(db_session))
    settings = _settings(stp_dedup_first_plan_run_ids=_whitelist(approved.id))

    assert _decide(db_session, approved.id, settings=settings).mode == "DEDUP_FIRST"
    denied = _decide(db_session, sibling.id, settings=settings)
    assert (denied.mode, denied.reason) == (None, admission.REASON_NOT_WHITELISTED)

    assert admission.fixate_partition_mode(
        db_session, sibling.id, entrypoint=admission.ENTRY_SCAN_TASK, settings=settings,
    ) is None
    db_session.commit()
    assert _mode_rows(db_session, sibling.id) == []
    # 影子入口保留主干既有记账（#3875 settled 出口写在模式行上）：仍落 CURRENT。
    assert admission.fixate_partition_mode(
        db_session, sibling.id, entrypoint=admission.ENTRY_SHADOW_SWEEP, settings=settings,
        record_current_fallback=True,
    ) == facts.PARTITION_MODE_CURRENT
    db_session.commit()
    assert [row.mode for row in _mode_rows(db_session, sibling.id)] == ["CURRENT"]


def test_historical_partition_without_mode_is_read_as_current(db_session):
    """历史无模式：准入不写记录，消费方按现行解释（D13 第 2 条）。"""
    run = _plan_run(db_session, status="SUCCESS")
    _device_job(db_session, run, _host(db_session))
    settings = _settings()  # 阶段二开、白名单为空

    assert admission.fixate_partition_mode(
        db_session, run.id, entrypoint=admission.ENTRY_EXTRACT, settings=settings,
    ) is None
    db_session.commit()
    assert _mode_rows(db_session, run.id) == []
    assert partition_scope.partition_rows(db_session, run.id) == [
        {"platform": "mtk", "mode": "CURRENT", "mode_recorded": False},
    ]


def test_switch_disabled_after_admission_keeps_fixed_mode(db_session):
    """关开关只影响后续入场；已固定 DEDUP_FIRST 的分区仍按既有模式返回（D13 第 5 条）。"""
    run = _plan_run(db_session)
    _device_job(db_session, run, _host(db_session))
    admitted = _settings(stp_dedup_first_plan_run_ids=_whitelist(run.id))
    assert admission.fixate_partition_mode(
        db_session, run.id, entrypoint=admission.ENTRY_SHADOW_SWEEP, settings=admitted,
    ) == "DEDUP_FIRST"
    db_session.commit()

    closed = _settings(stp_dedup_first_enabled=False)
    decision = _decide(db_session, run.id, settings=closed)
    assert (decision.mode, decision.reason) == ("DEDUP_FIRST", "already_recorded")
    assert admission.fixate_partition_mode(
        db_session, run.id, entrypoint=admission.ENTRY_UPLOAD_TASK, settings=closed,
    ) == "DEDUP_FIRST"


def test_only_mtk_partition_can_be_fixated(db_session):
    """D13 第 3 条：展锐分区不写模式记录，即使该 run 在白名单里。"""
    run = _plan_run(db_session)
    _device_job(db_session, run, _host(db_session), platform="UNISOC")
    settings = _settings(stp_dedup_first_plan_run_ids=_whitelist(run.id))

    decision = _decide(db_session, run.id, settings=settings, platform="unisoc")
    assert (decision.mode, decision.reason) == (None, admission.REASON_NON_MTK_PARTITION)
    assert admission.fixate_partition_mode(
        db_session, run.id, platform="unisoc", entrypoint=admission.ENTRY_MERGE,
        settings=settings, record_current_fallback=True,
    ) is None
    db_session.commit()
    assert _mode_rows(db_session, run.id) == []


def test_mixed_platform_run_admits_mtk_only(db_session):
    """混合平台：MTK 走去重链，展锐仍按现行分区处理（D13 第 3 条）。"""
    run = _plan_run(db_session)
    mtk_host = _host(db_session)
    unisoc_host = _host(db_session)
    _device_job(db_session, run, mtk_host, platform="MTK")
    _device_job(db_session, run, unisoc_host, platform="UNISOC")
    settings = _settings(stp_dedup_first_plan_run_ids=_whitelist(run.id))

    assert admission.fixate_partition_mode(
        db_session, run.id, entrypoint=admission.ENTRY_SCAN_TASK, settings=settings,
    ) == "DEDUP_FIRST"
    db_session.commit()
    rows = partition_scope.partition_rows(db_session, run.id)
    current = sorted(r["platform"] for r in rows if r["mode"] == "CURRENT")
    dedup = sorted(r["platform"] for r in rows if r["mode"] == "DEDUP_FIRST")
    assert dedup == ["mtk"]
    assert "unisoc" in current


def test_concurrent_first_write_keeps_one_mode_and_one_decision(db_session):
    """并发首写：多入口同时入场，只留一条记录，所有调用方读到同一模式。"""
    run_id = _plan_run(db_session).id
    with SessionLocal() as setup:
        run = setup.get(PlanRun, run_id)
        _device_job(setup, run, _host(setup))
        setup.commit()
    settings = _settings(stp_dedup_first_plan_run_ids=_whitelist(run_id))
    entries = [
        admission.ENTRY_SCAN_TASK, admission.ENTRY_UPLOAD_TASK, admission.ENTRY_MERGE,
        admission.ENTRY_EXTRACT, admission.ENTRY_SHADOW_SWEEP,
        admission.ENTRY_SHADOW_PREPARE, admission.ENTRY_SCAN_TASK, admission.ENTRY_MERGE,
    ]

    with ThreadPoolExecutor(max_workers=8) as pool:
        modes = list(pool.map(
            lambda entry: admission.fixate_partition_mode_at_entry(
                run_id, entrypoint=entry, settings=settings,
            ),
            entries,
        ))

    assert set(modes) == {"DEDUP_FIRST"}
    with SessionLocal() as check:
        count = check.execute(
            select(func.count()).select_from(PartitionMode).where(
                PartitionMode.plan_run_id == run_id,
            )
        ).scalar_one()
    assert count == 1


def test_retry_and_process_rebuild_read_the_same_mode(db_session):
    """任务重试 / 进程重建读到同一次提交的决定；运行中失去能力也不回退、不改写。"""
    run_id = _plan_run(db_session).id
    host_id = _host(db_session)
    with SessionLocal() as setup:
        _device_job(setup, setup.get(PlanRun, run_id), host_id)
        setup.commit()
    settings = _settings(stp_dedup_first_plan_run_ids=_whitelist(run_id))

    first = admission.fixate_partition_mode_at_entry(
        run_id, entrypoint=admission.ENTRY_SCAN_TASK, settings=settings,
    )
    rebuilt = admission.fixate_partition_mode_at_entry(
        run_id, entrypoint=admission.ENTRY_UPLOAD_TASK, settings=settings,
    )
    assert (first, rebuilt) == ("DEDUP_FIRST", "DEDUP_FIRST")

    with SessionLocal() as db:
        assert facts.get_partition_mode(
            db, plan_run_id=run_id, platform="mtk",
        ).mode == "DEDUP_FIRST"
        db.get(Host, host_id).status = "OFFLINE"
        db.commit()
        assert admission.fixate_partition_mode_at_entry(
            run_id, entrypoint=admission.ENTRY_MERGE, settings=settings,
        ) == "DEDUP_FIRST"
        with pytest.raises(facts.ImmutableIssueFactError):
            facts.record_partition_mode(
                db, plan_run_id=run_id, platform="mtk", mode=facts.PARTITION_MODE_CURRENT,
            )
        db.rollback()
