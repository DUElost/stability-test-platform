"""G1 事实底座服务层的行为测试（ADR-0056 D2，ADR-0062，B8 G1）。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select

from backend.models.issue_fact import (
    DeliveryVersion,
    IssueOccurrence,
    IssueOccurrenceEvent,
    IssueSignature,
    RepresentativeSelection,
    SpillLedger,
)
from backend.models.plan import Plan
from backend.models.plan_run import PlanRun
from backend.services import issue_fact_store as store


def _plan_run(db) -> PlanRun:
    plan = Plan(name=f"plan-fact-{uuid4().hex[:8]}", created_by="pytest")
    db.add(plan)
    db.flush()
    run = PlanRun(
        plan_id=plan.id,
        status="RUNNING",
        plan_snapshot={"plan": {"id": plan.id, "name": plan.name}, "steps": []},
        run_type="MANUAL",
        triggered_by="pytest",
    )
    db.add(run)
    db.flush()
    return run


def _signature(db, *, platform: str = "MTK", seen_at=None) -> IssueSignature:
    return store.record_issue_signature(
        db,
        signature_raw="Java (JE) at com.example.Foo",
        normalized_key=f"JE:com.example.Foo:{uuid4().hex[:8]}",
        platform=platform,
        event_type="JE",
        seen_at=seen_at or datetime.now(timezone.utc),
    )


def test_duplicate_occurrence_key_keeps_one_occurrence_and_two_observations(db_session):
    """#3800 §4 G1：同一发生身份重复写入，只保留一行；观察映射保留两行。"""
    run = _plan_run(db_session)
    signature = _signature(db_session, seen_at=datetime.now(timezone.utc))
    key = "a" * 64
    observed_at = datetime.now(timezone.utc)

    first = store.record_issue_occurrence(
        db_session,
        signature_id=signature.id,
        occurrence_key=key,
        serial="SN-001",
        observed_at=observed_at,
        first_seen_source=store.FIRST_SEEN_LOCAL,
        first_seen_plan_run_id=run.id,
    )
    store.link_occurrence_event(
        db_session,
        occurrence_id=first.id,
        dle_id=uuid4(),
        signature_id=signature.id,
    )

    # 不同 job 的 baseline / 溢出副本再次观察：新 DLE，同一发生。
    second = store.record_issue_occurrence(
        db_session,
        signature_id=signature.id,
        occurrence_key=key,
        serial="SN-001",
        observed_at=observed_at + timedelta(minutes=5),
        first_seen_source=store.FIRST_SEEN_SPILL,
        first_seen_plan_run_id=run.id,
    )
    store.link_occurrence_event(
        db_session,
        occurrence_id=second.id,
        dle_id=uuid4(),
        signature_id=signature.id,
    )

    assert first.id == second.id
    assert db_session.execute(
        select(func.count()).select_from(IssueOccurrence).where(
            IssueOccurrence.occurrence_key == key,
        )
    ).scalar_one() == 1
    assert db_session.execute(
        select(func.count()).select_from(IssueOccurrenceEvent).where(
            IssueOccurrenceEvent.occurrence_id == first.id,
        )
    ).scalar_one() == 2
    assert store.count_occurrences(
        db_session, signature_id=signature.id, plan_run_id=run.id,
    ) == 1
    assert store.count_machines(
        db_session, signature_id=signature.id, plan_run_id=run.id,
    ) == 1


def test_partition_mode_rewrite_is_rejected(db_session):
    """#3800 §4 G1：修改已写入的模式被拒绝（D13 第 2 条）。"""
    run = _plan_run(db_session)

    written = store.record_partition_mode(
        db_session,
        plan_run_id=run.id,
        platform="MTK",
        mode=store.PARTITION_MODE_CURRENT,
    )
    again = store.record_partition_mode(
        db_session,
        plan_run_id=run.id,
        platform="MTK",
        mode=store.PARTITION_MODE_CURRENT,
    )
    assert written.id == again.id

    with pytest.raises(store.ImmutableIssueFactError):
        store.record_partition_mode(
            db_session,
            plan_run_id=run.id,
            platform="MTK",
            mode=store.PARTITION_MODE_DEDUP_FIRST,
        )

    current = store.get_partition_mode(
        db_session, plan_run_id=run.id, platform="MTK",
    )
    assert current is not None
    assert current.mode == store.PARTITION_MODE_CURRENT


def test_selected_representative_cannot_be_reselected(db_session):
    """#3800 §4 G1：改选已经「已选」的代表被拒绝（D7 第 6 条）。"""
    run = _plan_run(db_session)
    signature = _signature(db_session)
    first_dle = uuid4()
    second_dle = uuid4()

    selected = store.record_representative_selection(
        db_session,
        plan_run_id=run.id,
        platform="MTK",
        signature_id=signature.id,
        selection_status=store.SELECTION_SELECTED,
        dle_id=first_dle,
        evidence_status=store.EVIDENCE_COMPLETE,
    )

    with pytest.raises(store.ImmutableIssueFactError):
        store.record_representative_selection(
            db_session,
            plan_run_id=run.id,
            platform="MTK",
            signature_id=signature.id,
            selection_status=store.SELECTION_SELECTED,
            dle_id=second_dle,
            evidence_status=store.EVIDENCE_COMPLETE,
        )

    # 也不得退回待选（已写入的选择不可改写）。
    with pytest.raises(store.ImmutableIssueFactError):
        store.record_representative_selection(
            db_session,
            plan_run_id=run.id,
            platform="MTK",
            signature_id=signature.id,
            selection_status=store.SELECTION_PENDING,
        )

    row = db_session.execute(
        select(RepresentativeSelection).where(
            RepresentativeSelection.plan_run_id == run.id,
            RepresentativeSelection.platform == "MTK",
            RepresentativeSelection.signature_id == signature.id,
        )
    ).scalars().one()
    assert row.id == selected.id
    assert row.dle_id == first_dle


def test_fact_rows_survive_plan_run_delete(db_session):
    """#3863 验收：删除 plan_run 行后，事实行仍然存在（不建级联 FK）。"""
    run = _plan_run(db_session)
    signature = _signature(db_session)
    occurrence = store.record_issue_occurrence(
        db_session,
        signature_id=signature.id,
        occurrence_key="b" * 64,
        serial="SN-002",
        observed_at=datetime.now(timezone.utc),
        first_seen_source=store.FIRST_SEEN_LOCAL,
        first_seen_plan_run_id=run.id,
    )
    occurrence_id = occurrence.id
    run_id = run.id

    db_session.execute(delete(PlanRun).where(PlanRun.id == run_id))
    db_session.commit()
    db_session.expire_all()

    assert db_session.get(PlanRun, run_id) is None
    survived = db_session.get(IssueOccurrence, occurrence_id)
    assert survived is not None
    assert survived.first_seen_plan_run_id == run_id


def test_counts_are_query_results_not_stored_counters(db_session):
    """I1：次数按 occurrence_key、机器数按 serial 去重；未证实身份逐行计数。"""
    run = _plan_run(db_session)
    signature = _signature(db_session)

    for key, serial in (("c" * 64, "SN-A"), ("d" * 64, "SN-A"), (None, "SN-B")):
        store.record_issue_occurrence(
            db_session,
            signature_id=signature.id,
            occurrence_key=key,
            serial=serial,
            observed_at=datetime.now(timezone.utc),
            first_seen_source=store.FIRST_SEEN_LOCAL,
            first_seen_plan_run_id=run.id,
        )

    assert store.count_occurrences(
        db_session, signature_id=signature.id, plan_run_id=run.id,
    ) == 3
    assert store.count_machines(
        db_session, signature_id=signature.id, plan_run_id=run.id,
    ) == 2


def test_same_occurrence_cannot_move_to_another_signature(db_session):
    run = _plan_run(db_session)
    signature = _signature(db_session)
    other = _signature(db_session)
    key = "e" * 64
    store.record_issue_occurrence(
        db_session,
        signature_id=signature.id,
        occurrence_key=key,
        serial="SN-003",
        observed_at=datetime.now(timezone.utc),
        first_seen_source=store.FIRST_SEEN_LOCAL,
        first_seen_plan_run_id=run.id,
    )

    with pytest.raises(store.ImmutableIssueFactError):
        store.record_issue_occurrence(
            db_session,
            signature_id=other.id,
            occurrence_key=key,
            serial="SN-003",
            observed_at=datetime.now(timezone.utc),
            first_seen_source=store.FIRST_SEEN_LOCAL,
            first_seen_plan_run_id=run.id,
        )


def test_scan_round_round_trip_and_close(db_session):
    run = _plan_run(db_session)
    cutoff = datetime.now(timezone.utc) + timedelta(hours=2)

    created = store.record_scan_round(
        db_session,
        plan_run_id=run.id,
        platform="MTK",
        round_no=1,
        kind=store.SCAN_ROUND_KIND_FINAL,
        expected_set=[{"host_id": "host-1", "platform": "MTK"}],
        input_cutoff_at=cutoff,
    )
    duplicate = store.record_scan_round(
        db_session,
        plan_run_id=run.id,
        platform="MTK",
        round_no=1,
        kind=store.SCAN_ROUND_KIND_FINAL,
        expected_set=[{"host_id": "host-1", "platform": "MTK"}],
        input_cutoff_at=cutoff,
    )
    assert created.id == duplicate.id
    assert created.status == store.SCAN_ROUND_STATUS_PENDING

    closed_at = datetime.now(timezone.utc)
    closed = store.set_scan_round_status(
        db_session,
        round_id=created.id,
        status=store.SCAN_ROUND_STATUS_CLOSED,
        closed_at=closed_at,
    )
    assert closed.status == store.SCAN_ROUND_STATUS_CLOSED
    assert closed.closed_at == closed_at


def test_delivery_version_and_spill_ledger_are_idempotent(db_session):
    run = _plan_run(db_session)
    version = store.record_delivery_version(
        db_session,
        plan_run_id=run.id,
        platform="MTK",
        version_no=1,
        software_versions=["V1"],
        round_ids=[1],
        effective_coverage=[{"host_id": "host-1", "platform": "MTK"}],
        issue_ids=[7],
        event_ids=["dle-1"],
        stats_snapshot_version="stats-v1",
        gaps=[],
        primary_folders=["root_MTK_V1_20260101_MonkeyAEEinfo"],
    )
    duplicate = store.record_delivery_version(
        db_session,
        plan_run_id=run.id,
        platform="MTK",
        version_no=1,
    )
    assert version.id == duplicate.id
    assert duplicate.stats_snapshot_version == "stats-v1"
    assert db_session.execute(
        select(func.count()).select_from(DeliveryVersion)
    ).scalar_one() == 1

    dle_id = uuid4()
    spilled_at = datetime.now(timezone.utc)
    spill = store.record_spill_ledger_entry(
        db_session,
        plan_run_id=run.id,
        platform="MTK",
        dle_id=dle_id,
        size_bytes=1024,
        spilled_at=spilled_at,
    )
    spill_again = store.record_spill_ledger_entry(
        db_session,
        plan_run_id=run.id,
        platform="MTK",
        dle_id=dle_id,
        size_bytes=1024,
        spilled_at=spilled_at,
    )
    assert spill.id == spill_again.id
    assert db_session.execute(
        select(func.count()).select_from(SpillLedger)
    ).scalar_one() == 1
