"""B8 G6：代表证据发布、校验回执与交付接线（ADR-0062 D8/D9）。

覆盖 #3957 复核清单：本地代表、共享 DLE、HddSpill PRUNED、落点冲突、
命名来源缺失、回执幂等/哈希不符、登记失败不归档、两 run 同链根、
多软件版本、全空/全缺回执。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from backend.core.settings.scheduler import SchedulerSettings
from backend.models.device_log_event import DeviceLogEvent
from backend.models.issue_fact import EvidencePublication
from backend.models.plan import Plan
from backend.models.plan_run import PlanRun
from backend.services import evidence_publication as ep
from backend.services import issue_fact_store as facts
from backend.services import shadow_delivery as delivery

FOLDER = "MLD-NX3-P535AA-HONOR-NX3-16-1031V78_1031_MonkeyAEEinfo"
EVENT = "2026_1031_120000_123_db.74.ANR"
SERIAL = "AZ6Q123456"


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    settings = SchedulerSettings(stp_scan_round_shadow_enabled=True)
    monkeypatch.setattr(delivery, "get_scheduler_settings", lambda: settings)
    monkeypatch.setattr(ep, "get_scheduler_settings", lambda: settings)


@pytest.fixture
def store(tmp_path, monkeypatch, db_session):
    factory = sessionmaker(bind=db_session.get_bind())
    monkeypatch.setattr(delivery, "SessionLocal", factory)
    monkeypatch.setattr(ep, "SessionLocal", factory)
    monkeypatch.setattr(delivery, "resolve_shared_storage_root", lambda: str(tmp_path))
    monkeypatch.setattr(ep, "resolve_shared_storage_root", lambda: str(tmp_path))
    from backend.core import artifact_paths, storage_root
    for module in (artifact_paths, storage_root):
        monkeypatch.setattr(module, "resolve_shared_storage_root", lambda: str(tmp_path))
        monkeypatch.setattr(
            module, "resolve_control_plane_storage_root", lambda: str(tmp_path / "cp"),
        )
        monkeypatch.setattr(module, "resolve_legacy_log_storage_root", lambda: "")
    (tmp_path / "cp").mkdir(exist_ok=True)
    return tmp_path


def _plan_run(db, *, run_id=42, root_plan_run_id=None, status="SUCCESS"):
    plan = Plan(name=f"g6-{uuid4()}", created_by="pytest")
    db.add(plan)
    db.flush()
    db.add(PlanRun(
        id=run_id, plan_id=plan.id, status=status, plan_snapshot={},
        run_type="MANUAL", triggered_by="pytest", root_plan_run_id=root_plan_run_id,
    ))
    db.flush()


def _dedup_first(db, *, run_id=42):
    facts.record_partition_mode(
        db, plan_run_id=run_id, platform="mtk", mode=facts.PARTITION_MODE_DEDUP_FIRST,
    )
    db.flush()


def _dle(
    db, host_id, *, run_id=42, serial=SERIAL, folder=FOLDER, event=EVENT,
    state="LOCAL", remote_path=None, local_path=None,
):
    dle = DeviceLogEvent(
        id=uuid4(), serial=serial, platform="MTK", event_type="JE", state=state,
        local_path=local_path or f"/data/aee/{folder}/{serial}/aee_exp/{event}",
        remote_path=remote_path, host_id=host_id, plan_run_id=run_id,
        occurrence_key=uuid4().hex, detected_at=datetime.now(timezone.utc),
    )
    db.add(dle)
    db.flush()
    return dle


def _selected(db, dle, *, occurrence_key=None, evidence="COMPLETE"):
    signature = facts.record_issue_signature(
        db, signature_raw=json.dumps({"exp_class": "JE"}),
        normalized_key=f"key-{uuid4()}", platform="mtk", event_type="JE",
        seen_at=datetime.now(timezone.utc),
    )
    key = occurrence_key or uuid4().hex
    occurrence = facts.record_issue_occurrence(
        db, signature_id=signature.id, occurrence_key=key, serial=dle.serial,
        observed_at=dle.detected_at, first_seen_source="local",
        first_seen_plan_run_id=dle.plan_run_id,
    )
    facts.link_occurrence_event(
        db, occurrence_id=occurrence.id, dle_id=dle.id, signature_id=signature.id,
    )
    facts.record_representative_selection(
        db, plan_run_id=dle.plan_run_id, platform="mtk", signature_id=signature.id,
        selection_status=facts.SELECTION_SELECTED, dle_id=dle.id, evidence_status=evidence,
    )
    db.flush()
    return signature


def _final_round(db, host_id, *, run_id=42, coverage="RECEIVED"):
    now = datetime.now(timezone.utc)
    row = facts.record_scan_round(
        db, plan_run_id=run_id, platform="mtk", round_no=1, kind="FINAL",
        expected_set=[{
            "host_id": host_id, "platform": "mtk", "coverage": coverage, "expected": True,
        }],
        input_cutoff_at=now,
    )
    facts.set_scan_round_status(db, round_id=row.id, status="CLOSED", closed_at=now)
    facts.record_scan_round_merge(db, round_id=row.id, status="OK", summary={})
    db.flush()
    return row


def _write_destination(root: Path, rel: str, files: dict[str, bytes] | None = None) -> Path:
    dest = root / rel
    dest.mkdir(parents=True)
    for name, data in (files or {"main.dbg": b"dbg"}).items():
        target = dest / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    return dest


def _publication(db, run_id=42):
    stmt = select(EvidencePublication)
    if run_id is not None:
        stmt = stmt.where(EvidencePublication.plan_run_id == run_id)
    return db.execute(stmt.order_by(EvidencePublication.id)).scalars().all()


def test_shared_representative_creates_one_publication(db_session, sample_host, store):
    _plan_run(db_session)
    _dedup_first(db_session)
    dle = _dle(db_session, sample_host.id)
    _selected(db_session, dle, occurrence_key="occ-1")
    _selected(db_session, dle, occurrence_key="occ-2")

    created = ep.ensure_partition_publications(
        db_session, plan_run_id=42, platform="mtk",
    )
    db_session.commit()

    assert created == 1
    rows = _publication(db_session)
    assert len(rows) == 1
    row = rows[0]
    assert row.dle_id == dle.id
    assert row.source_kind == facts.PUBLICATION_SOURCE_LOCAL
    assert row.destination_rel_path == f"evidence/42_{FOLDER}/{SERIAL}/{EVENT}"
    assert row.state == facts.PUBLICATION_STATE_PENDING
    assert row.executor_host_id == sample_host.id
    assert row.naming_rule_version == ep.NAMING_RULE_VERSION


def test_current_missing_mode_and_unisoc_do_not_publish(db_session, sample_host, store):
    _plan_run(db_session)
    dle = _dle(db_session, sample_host.id)
    _selected(db_session, dle)
    # 无模式记录 = CURRENT
    assert ep.ensure_partition_publications(db_session, plan_run_id=42, platform="mtk") == 0

    facts.record_partition_mode(
        db_session, plan_run_id=42, platform="mtk", mode=facts.PARTITION_MODE_CURRENT,
    )
    assert ep.ensure_partition_publications(db_session, plan_run_id=42, platform="mtk") == 0
    assert ep.ensure_partition_publications(db_session, plan_run_id=42, platform="unisoc") == 0


def test_naming_source_missing_is_explicit_terminal_gap(db_session, sample_host, store):
    _plan_run(db_session)
    _dedup_first(db_session)
    dle = _dle(
        db_session, sample_host.id,
        local_path=f"/data/aee/jobs/42/AEE/{EVENT}",
    )
    _selected(db_session, dle)
    ep.ensure_partition_publications(db_session, plan_run_id=42, platform="mtk")
    db_session.commit()

    row = _publication(db_session)[0]
    assert row.state == facts.PUBLICATION_STATE_FAILED
    assert row.failure_reason == "naming_source_missing"
    assert row.destination_rel_path is None

    _final_round(db_session, sample_host.id)
    db_session.commit()
    assert delivery.register_delivery_version(
        db_session, plan_run_id=42, root=str(store),
    ) is not None
    issues = facts.list_partition_delivery_issues(db_session, plan_run_id=42, platform="mtk")
    status, _pending, _folders = delivery._publication_view(
        facts.list_partition_evidence_publications(db_session, plan_run_id=42, platform="mtk"),
        issues,
    )
    assert status == {issues[0]["signature_id"]: delivery.PUBLISH_FAILED}
    decision = delivery.evaluate_version_status(
        facts.get_latest_delivery_version(db_session, plan_run_id=42, platform="mtk"),
        rounds=[
            {"round_no": row.round_no, "kind": row.kind, "status": row.status,
             "merge_status": row.merge_status}
            for row in facts.list_scan_rounds(db_session, plan_run_id=42, platform="mtk")
        ],
        representatives=issues,
        publish_status=status,
    )
    assert decision.status == delivery.DELIVERY_STATUS_READY_WITH_GAPS
    assert any(gap["kind"] == "publish_failed" for gap in decision.gaps)


def test_same_destination_from_another_identity_is_refused(db_session, sample_host, store):
    _plan_run(db_session)
    _dedup_first(db_session)
    first = _dle(db_session, sample_host.id)
    second = _dle(db_session, sample_host.id)  # 同一 SN / 同一事件目录名、不同 DLE
    _selected(db_session, first)
    _selected(db_session, second)

    ep.ensure_partition_publications(db_session, plan_run_id=42, platform="mtk")
    db_session.commit()

    rows = {row.dle_id: row for row in _publication(db_session)}
    assert rows[first.id].state == facts.PUBLICATION_STATE_PENDING
    assert rows[second.id].state == facts.PUBLICATION_STATE_FAILED
    assert rows[second.id].failure_reason == "destination_conflict"
    assert rows[second.id].destination_rel_path == rows[first.id].destination_rel_path


def test_two_runs_share_chain_parent_but_not_delivery(db_session, sample_host, store):
    _plan_run(db_session, run_id=100)
    _plan_run(db_session, run_id=101, root_plan_run_id=100)
    _dedup_first(db_session, run_id=100)
    _dedup_first(db_session, run_id=101)
    dle_a = _dle(db_session, sample_host.id, run_id=100)
    dle_b = _dle(
        db_session, sample_host.id, run_id=101,
        event="2026_1031_130000_456_db.01.JE",
    )
    _selected(db_session, dle_a)
    _selected(db_session, dle_b)
    ep.ensure_partition_publications(db_session, plan_run_id=100, platform="mtk")
    ep.ensure_partition_publications(db_session, plan_run_id=101, platform="mtk")
    db_session.commit()

    rows = {row.plan_run_id: row for row in _publication(db_session, run_id=None)}
    assert rows[100].destination_rel_path.startswith(f"evidence/100_{FOLDER}/")
    assert rows[101].destination_rel_path.startswith(f"evidence/100_{FOLDER}/")
    assert rows[100].destination_rel_path != rows[101].destination_rel_path


def test_multiple_software_versions_make_multiple_primary_folders(db_session, sample_host, store):
    _plan_run(db_session)
    _dedup_first(db_session)
    other_folder = "MLD-NX3-P535AA-HONOR-NX3-16-1101V80_1101_MonkeyAEEinfo"
    dle_a = _dle(db_session, sample_host.id)
    dle_b = _dle(db_session, sample_host.id, event="2026_1101_090000_123_db.01.JE", folder=other_folder)
    _selected(db_session, dle_a)
    _selected(db_session, dle_b)
    ep.ensure_partition_publications(db_session, plan_run_id=42, platform="mtk")
    _final_round(db_session, sample_host.id)
    db_session.commit()

    version = delivery.register_delivery_version(db_session, plan_run_id=42, root=str(store))
    assert version is not None
    rows = _publication(db_session)
    for row in rows:
        dest = _write_destination(store, row.destination_rel_path)
        checksum, size = ep.dir_sha256_and_size(dest)
        facts.update_evidence_publication_state(
            db_session, row=row, state=facts.PUBLICATION_STATE_VERIFIED,
            attempts=1, checksum=checksum, size_bytes=size,
        )
    db_session.commit()
    assert delivery.register_delivery_version(
        db_session, plan_run_id=42, root=str(store),
    ) is not None
    latest = facts.get_latest_delivery_version(db_session, plan_run_id=42, platform="mtk")
    assert len(latest.primary_folders) == 2
    assert f"42_{other_folder}" in latest.primary_folders


def _prepare_local_verified(db, sample_host, store, *, attempts=1):
    _plan_run(db)
    _dedup_first(db)
    dle = _dle(db, sample_host.id)
    _selected(db, dle)
    ep.ensure_partition_publications(db, plan_run_id=42, platform="mtk")
    db.commit()
    row = _publication(db)[0]
    dest = _write_destination(store, row.destination_rel_path)
    checksum, size = ep.dir_sha256_and_size(dest)
    return dle, row, dest, checksum, size


def test_local_receipt_verifies_then_archives_after_delivery(
    db_session, sample_host, store,
):
    dle, row, _dest, checksum, size = _prepare_local_verified(
        db_session, sample_host, store,
    )
    result = ep.record_agent_receipt(
        db_session,
        publication_id=row.id,
        host_id=sample_host.id,
        dle_id=str(dle.id),
        destination_rel_path=row.destination_rel_path,
        state=facts.PUBLICATION_STATE_VERIFIED,
        checksum=checksum,
        size_bytes=size,
        attempts=1,
    )
    db_session.commit()
    assert result["state"] == facts.PUBLICATION_STATE_VERIFIED

    _final_round(db_session, sample_host.id)
    db_session.commit()
    version_no = ep.refresh_delivery_and_archive(42)
    assert version_no is not None

    db_session.expire_all()
    assert db_session.get(DeviceLogEvent, dle.id).state == "ARCHIVED"
    latest = facts.get_latest_delivery_version(db_session, plan_run_id=42, platform="mtk")
    assert latest.version_no == 1
    assert str(dle.id) in latest.event_ids
    decision = delivery.evaluate_version_status(
        latest,
        rounds=[
            {"round_no": r.round_no, "kind": r.kind, "status": r.status,
             "merge_status": r.merge_status}
            for r in facts.list_scan_rounds(db_session, plan_run_id=42, platform="mtk")
        ],
        representatives=facts.list_partition_delivery_issues(
            db_session, plan_run_id=42, platform="mtk",
        ),
        publish_status=delivery._publication_view(
            facts.list_partition_evidence_publications(db_session, plan_run_id=42, platform="mtk"),
            facts.list_partition_delivery_issues(db_session, plan_run_id=42, platform="mtk"),
        )[0],
    )
    assert decision.status == delivery.DELIVERY_STATUS_READY


def test_receipt_hash_mismatch_retries_then_fails_without_archive(
    db_session, sample_host, store,
):
    dle, row, dest, _checksum, size = _prepare_local_verified(
        db_session, sample_host, store,
    )
    dest.joinpath("main.dbg").write_bytes(b"tampered")
    for attempt in range(1, 6):
        result = ep.record_agent_receipt(
            db_session,
            publication_id=row.id,
            host_id=sample_host.id,
            dle_id=str(dle.id),
            destination_rel_path=row.destination_rel_path,
            state=facts.PUBLICATION_STATE_VERIFIED,
            checksum="0" * 64,
            size_bytes=size,
            attempts=attempt,
        )
        db_session.commit()
        assert result["error"] == "EVIDENCE_HASH_MISMATCH"
    db_session.expire_all()
    stored = facts.get_evidence_publication(db_session, publication_id=row.id)
    assert stored.state == facts.PUBLICATION_STATE_FAILED
    assert stored.failure_reason == "hash_mismatch"
    assert db_session.get(DeviceLogEvent, dle.id).state == "LOCAL"


def test_receipt_idempotent_on_lost_ack_and_wrong_identity_rejected(
    db_session, sample_host, store,
):
    dle, row, _dest, checksum, size = _prepare_local_verified(
        db_session, sample_host, store,
    )
    params = dict(
        publication_id=row.id,
        host_id=sample_host.id,
        dle_id=str(dle.id),
        destination_rel_path=row.destination_rel_path,
        state=facts.PUBLICATION_STATE_VERIFIED,
        checksum=checksum,
        size_bytes=size,
        attempts=1,
    )
    assert ep.record_agent_receipt(db_session, **params)["state"] == "VERIFIED"
    db_session.commit()
    assert ep.record_agent_receipt(db_session, **params)["state"] == "VERIFIED"

    for override in (
        {"host_id": "999"},
        {"dle_id": str(uuid4())},
        {"destination_rel_path": row.destination_rel_path + "-x"},
    ):
        with pytest.raises(ep.EvidencePublicationError):
            ep.record_agent_receipt(db_session, **{**params, **override})


def test_registration_failure_blocks_archive(db_session, sample_host, store, monkeypatch):
    dle, row, _dest, checksum, size = _prepare_local_verified(
        db_session, sample_host, store,
    )
    ep.record_agent_receipt(
        db_session, publication_id=row.id, host_id=sample_host.id,
        dle_id=str(dle.id), destination_rel_path=row.destination_rel_path,
        state=facts.PUBLICATION_STATE_VERIFIED, checksum=checksum,
        size_bytes=size, attempts=1,
    )
    _final_round(db_session, sample_host.id)
    db_session.commit()

    original = delivery.register_delivery_version
    monkeypatch.setattr(
        delivery, "register_delivery_version",
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("registration down")),
    )
    assert ep.refresh_delivery_and_archive(42) is None
    db_session.expire_all()
    assert db_session.get(DeviceLogEvent, dle.id).state == "LOCAL"
    assert facts.get_latest_delivery_version(db_session, plan_run_id=42, platform="mtk") is None

    monkeypatch.setattr(delivery, "register_delivery_version", original)
    assert ep.refresh_delivery_and_archive(42) == 1
    db_session.expire_all()
    assert db_session.get(DeviceLogEvent, dle.id).state == "ARCHIVED"


def test_spill_copy_uses_center_copy_and_keeps_remote_path(
    db_session, sample_host, store,
):
    _plan_run(db_session)
    _dedup_first(db_session)
    source = store / "devices" / "42" / str(uuid4()) / EVENT
    source.mkdir(parents=True)
    (source / "main.dbg").write_bytes(b"spill")
    dle = _dle(
        db_session, sample_host.id, state="PRUNED", remote_path=str(source),
    )
    facts.record_spill_ledger_entry(
        db_session, plan_run_id=42, platform="mtk", dle_id=dle.id,
        size_bytes=5, spilled_at=datetime.now(timezone.utc),
    )
    _selected(db_session, dle)
    ep.ensure_partition_publications(db_session, plan_run_id=42, platform="mtk")
    db_session.commit()
    row = _publication(db_session)[0]
    assert row.source_kind == facts.PUBLICATION_SOURCE_SPILL

    ep.copy_spill_publication(row.id)
    db_session.expire_all()
    stored = facts.get_evidence_publication(db_session, publication_id=row.id)
    assert stored.state == facts.PUBLICATION_STATE_VERIFIED, (stored.failure_reason, stored.attempts, stored.source_path)
    dest = store / row.destination_rel_path
    assert (dest / "main.dbg").read_bytes() == b"spill"
    assert source.is_dir() and (source / "main.dbg").read_bytes() == b"spill"
    assert db_session.get(DeviceLogEvent, dle.id).remote_path == str(source)


def test_spill_copy_refuses_existing_different_destination(
    db_session, sample_host, store,
):
    _plan_run(db_session)
    _dedup_first(db_session)
    source = store / "devices" / "42" / str(uuid4()) / EVENT
    source.mkdir(parents=True)
    (source / "main.dbg").write_bytes(b"spill")
    dle = _dle(db_session, sample_host.id, state="PRUNED", remote_path=str(source))
    facts.record_spill_ledger_entry(
        db_session, plan_run_id=42, platform="mtk", dle_id=dle.id,
        size_bytes=5, spilled_at=datetime.now(timezone.utc),
    )
    _selected(db_session, dle)
    ep.ensure_partition_publications(db_session, plan_run_id=42, platform="mtk")
    db_session.commit()
    row = _publication(db_session)[0]
    dest = _write_destination(store, row.destination_rel_path, {"main.dbg": b"other"})

    ep.copy_spill_publication(row.id)
    db_session.expire_all()
    stored = facts.get_evidence_publication(db_session, publication_id=row.id)
    assert stored.state == facts.PUBLICATION_STATE_FAILED
    assert stored.failure_reason == "destination_conflict"
    assert (dest / "main.dbg").read_bytes() == b"other"


def test_spill_copy_missing_source_exhausts_five_attempts(
    db_session, sample_host, store,
):
    _plan_run(db_session)
    _dedup_first(db_session)
    source = store / "devices" / "42" / str(uuid4()) / EVENT
    source.mkdir(parents=True)
    (source / "main.dbg").write_bytes(b"spill")
    dle = _dle(db_session, sample_host.id, state="PRUNED", remote_path=str(source))
    facts.record_spill_ledger_entry(
        db_session, plan_run_id=42, platform="mtk", dle_id=dle.id,
        size_bytes=5, spilled_at=datetime.now(timezone.utc),
    )
    _selected(db_session, dle)
    ep.ensure_partition_publications(db_session, plan_run_id=42, platform="mtk")
    db_session.commit()
    row = _publication(db_session)[0]
    import shutil
    shutil.rmtree(source)

    for _ in range(5):
        ep.copy_spill_publication(row.id)
        db_session.expire_all()
        stored = facts.get_evidence_publication(db_session, publication_id=row.id)
        stored.next_attempt_at = None
        db_session.commit()
    db_session.expire_all()
    stored = facts.get_evidence_publication(db_session, publication_id=row.id)
    assert stored.state == facts.PUBLICATION_STATE_FAILED
    assert stored.attempts == 5


@pytest.mark.parametrize(
    "coverage,publish,expected",
    [
        ([{"host_id": "101", "platform": "mtk", "covered": True}], {}, "READY"),
        ([], {}, "NOT_READY"),
        ([{"host_id": "101", "platform": "mtk", "covered": False}], {}, "NOT_READY"),
        (
            [{"host_id": "101", "platform": "mtk", "covered": True}],
            {1: "RETRYING"},
            "NOT_READY",
        ),
        (
            [{"host_id": "101", "platform": "mtk", "covered": True}],
            {1: "FAILED"},
            "READY_WITH_GAPS",
        ),
    ],
)
def test_delivery_matrix(coverage, publish, expected):
    reps = (
        [{"selection_status": "SELECTED", "signature_id": 1, "evidence_status": "COMPLETE"}]
        if publish else []
    )
    decision = delivery.evaluate_delivery_status(
        rounds=[{"round_no": 1, "kind": "FINAL", "status": "CLOSED", "merge_status": "OK"}],
        effective_coverage=coverage,
        representatives=reps,
        publish_status=publish or {},
    )
    assert decision.status == expected


def test_agent_task_list_only_local_active_for_host(db_session, sample_host, store):
    from backend.models.host import Host
    _plan_run(db_session)
    _dedup_first(db_session)
    db_session.add(Host(
        id="999", hostname="other-host", name="other", ip="192.0.2.199",
        ip_address="192.0.2.199", status="ONLINE",
        last_heartbeat=datetime.now(timezone.utc),
    ))
    db_session.flush()
    local = _dle(db_session, sample_host.id)
    other = _dle(
        db_session, "999", serial="AZ6Q999999",
        local_path=f"/data/aee/{FOLDER}/AZ6Q999999/aee_exp/{EVENT}",
    )
    for dle in (local, other):
        _selected(db_session, dle)
    ep.ensure_partition_publications(db_session, plan_run_id=42, platform="mtk")
    db_session.commit()
    rows = facts.list_agent_evidence_publications(db_session, host_id=sample_host.id)
    assert [str(row.dle_id) for row in rows] == [str(local.id)]


def test_route_lists_tasks_and_rejects_wrong_executor(db_session, sample_host, store):
    from fastapi import HTTPException
    from backend.api.routes.agent_evidence import (
        list_evidence_publications,
        record_evidence_receipt,
    )
    from backend.services.evidence_publication import EvidenceReceiptIn

    _plan_run(db_session)
    _dedup_first(db_session)
    dle = _dle(db_session, sample_host.id)
    _selected(db_session, dle)
    ep.ensure_partition_publications(db_session, plan_run_id=42, platform="mtk")
    db_session.commit()
    row = _publication(db_session)[0]

    response = list_evidence_publications(
        host_id=sample_host.id, limit=50, db=db_session, _=None,
    )
    assert response.data["publications"][0]["publication_id"] == row.id
    assert response.data["publications"][0]["event_dir"] == EVENT

    with pytest.raises(HTTPException) as excinfo:
        record_evidence_receipt(
            publication_id=row.id,
            payload=EvidenceReceiptIn(
                host_id="999",
                dle_id=str(dle.id),
                destination_rel_path=row.destination_rel_path,
                state="VERIFIED",
                checksum="0" * 64,
                size_bytes=1,
                attempts=1,
            ),
            db=db_session,
            _=None,
        )
    assert excinfo.value.status_code == 409
    assert excinfo.value.detail["code"] == "EVIDENCE_EXECUTOR_MISMATCH"


def test_ensure_is_idempotent_under_repeat_dispatch(db_session, sample_host, store):
    _plan_run(db_session)
    _dedup_first(db_session)
    dle = _dle(db_session, sample_host.id)
    _selected(db_session, dle)
    first = ep.ensure_partition_publications(db_session, plan_run_id=42, platform="mtk")
    second = ep.ensure_partition_publications(db_session, plan_run_id=42, platform="mtk")
    db_session.commit()
    assert (first, second) == (1, 0)
    assert len(_publication(db_session)) == 1
    assert _publication(db_session)[0].state == facts.PUBLICATION_STATE_PENDING


def test_pending_publication_keeps_delivery_not_ready_and_no_archive(
    db_session, sample_host, store,
):
    _plan_run(db_session)
    _dedup_first(db_session)
    dle = _dle(db_session, sample_host.id)
    _selected(db_session, dle)
    ep.ensure_partition_publications(db_session, plan_run_id=42, platform="mtk")
    _final_round(db_session, sample_host.id)
    db_session.commit()

    version = delivery.register_delivery_version(db_session, plan_run_id=42, root=str(store))
    assert version is not None
    db_session.commit()  # 释放 PlanRun 行锁，refresh 才能在另一会话登记
    issues = facts.list_partition_delivery_issues(db_session, plan_run_id=42, platform="mtk")
    status, pending, _folders = delivery._publication_view(
        facts.list_partition_evidence_publications(db_session, plan_run_id=42, platform="mtk"),
        issues,
    )
    assert status == {issues[0]["signature_id"]: delivery.PUBLISH_RETRYING}
    assert pending[0]["signature_id"] == issues[0]["signature_id"]
    decision = delivery.evaluate_version_status(
        version,
        rounds=[{"round_no": 1, "kind": "FINAL", "status": "CLOSED", "merge_status": "OK"}],
        representatives=issues,
        publish_status=status,
    )
    assert decision.status == delivery.DELIVERY_STATUS_NOT_READY
    assert ep.refresh_delivery_and_archive(42) is not None
    db_session.expire_all()
    assert db_session.get(DeviceLogEvent, dle.id).state == "LOCAL"


def test_device_version_change_does_not_move_destination(db_session, sample_host, store):
    _plan_run(db_session)
    _dedup_first(db_session)
    dle = _dle(db_session, sample_host.id)
    _selected(db_session, dle)
    db_session.get(PlanRun, 42).build_version = "V80-REFLASHED"
    ep.ensure_partition_publications(db_session, plan_run_id=42, platform="mtk")
    db_session.commit()
    row = _publication(db_session)[0]
    # 目的路径使用 DLE.local_path 里采集时固化的 V78 目录，不读当前设备版本。
    assert row.destination_rel_path == f"evidence/42_{FOLDER}/{SERIAL}/{EVENT}"
    assert "V80-REFLASHED" not in row.destination_rel_path


def test_planner_audit_expired_copy_failure_does_not_poison_next_success(
    db_session, sample_host, store, monkeypatch,
):
    from concurrent.futures import ThreadPoolExecutor
    from datetime import timedelta
    from threading import Event, current_thread

    _plan_run(db_session)
    _dedup_first(db_session)
    source = store / "devices" / "42" / str(uuid4()) / EVENT
    source.mkdir(parents=True)
    (source / "main.dbg").write_bytes(b"spill")
    dle = _dle(db_session, sample_host.id, state="PRUNED", remote_path=str(source))
    now = datetime.now(timezone.utc)
    facts.record_spill_ledger_entry(
        db_session, plan_run_id=42, platform="mtk", dle_id=dle.id,
        size_bytes=5, spilled_at=now,
    )
    _selected(db_session, dle)
    ep.ensure_partition_publications(db_session, plan_run_id=42, platform="mtk")
    row = _publication(db_session)[0]
    row.attempts = 3
    publication_id = row.id
    destination = store / row.destination_rel_path
    db_session.commit()

    clock = [now]
    monkeypatch.setattr(ep, "_utcnow", lambda: clock[0])
    stale_copy_entered = Event()
    allow_stale_failure = Event()
    stale_copy_finished = Event()
    main_thread = current_thread()
    real_copy = ep.copytree_no_symlinks
    real_finish_verified = ep._finish_copy_verified

    def copy_tree(*args, **kwargs):
        if current_thread() is not main_thread:
            stale_copy_entered.set()
            assert allow_stale_failure.wait(8)
            raise OSError("injected interruption after the old lease expired")
        return real_copy(*args, **kwargs)

    def finish_verified(*args, **kwargs):
        assert (destination / "main.dbg").read_bytes() == b"spill"
        allow_stale_failure.set()
        assert stale_copy_finished.wait(8)
        return real_finish_verified(*args, **kwargs)

    def run_stale_copy():
        try:
            ep.copy_spill_publication(publication_id)
        finally:
            stale_copy_finished.set()

    monkeypatch.setattr(ep, "copytree_no_symlinks", copy_tree)
    monkeypatch.setattr(ep, "_finish_copy_verified", finish_verified)
    with ThreadPoolExecutor(max_workers=1) as pool:
        stale = pool.submit(run_stale_copy)
        try:
            assert stale_copy_entered.wait(8)
            clock[0] += timedelta(seconds=ep._COPY_LEASE_SECONDS + 1)
            ep.copy_spill_publication(publication_id)
        finally:
            allow_stale_failure.set()
        stale.result(timeout=8)

    db_session.expire_all()
    stored = facts.get_evidence_publication(db_session, publication_id=publication_id)
    assert stored.attempts == 5
    assert ep.dir_sha256_and_size(destination) == ep.dir_sha256_and_size(source)
    assert stored.state == facts.PUBLICATION_STATE_VERIFIED, (
        stored.state, stored.attempts, stored.failure_reason,
    )

def test_planner_audit_expired_copy_success_cannot_register_for_next_attempt(
    db_session, sample_host, store, monkeypatch,
):
    from concurrent.futures import ThreadPoolExecutor
    from datetime import timedelta
    from threading import Event, current_thread

    _plan_run(db_session)
    _dedup_first(db_session)
    source = store / "devices" / "42" / str(uuid4()) / EVENT
    source.mkdir(parents=True)
    (source / "main.dbg").write_bytes(b"spill")
    dle = _dle(db_session, sample_host.id, state="PRUNED", remote_path=str(source))
    now = datetime.now(timezone.utc)
    facts.record_spill_ledger_entry(
        db_session, plan_run_id=42, platform="mtk", dle_id=dle.id,
        size_bytes=5, spilled_at=now,
    )
    _selected(db_session, dle)
    ep.ensure_partition_publications(db_session, plan_run_id=42, platform="mtk")
    row = _publication(db_session)[0]
    row.attempts = 3
    publication_id = row.id
    db_session.commit()

    clock = [now]
    monkeypatch.setattr(ep, "_utcnow", lambda: clock[0])
    stale_ready = Event()
    allow_stale_success = Event()
    main_thread = current_thread()
    real_finish_verified = ep._finish_copy_verified
    refreshes = []

    def finish_verified(*args, **kwargs):
        if current_thread() is not main_thread:
            stale_ready.set()
            assert allow_stale_success.wait(8)
        return real_finish_verified(*args, **kwargs)

    def run_stale_copy():
        ep.copy_spill_publication(publication_id)

    monkeypatch.setattr(ep, "_finish_copy_verified", finish_verified)
    monkeypatch.setattr(
        ep, "refresh_delivery_and_archive",
        lambda *args, **kwargs: refreshes.append((args, kwargs)),
    )
    with ThreadPoolExecutor(max_workers=1) as pool:
        stale = pool.submit(run_stale_copy)
        try:
            assert stale_ready.wait(8)
            clock[0] += timedelta(seconds=ep._COPY_LEASE_SECONDS + 1)
            ep.copy_spill_publication(publication_id)
        finally:
            allow_stale_success.set()
        stale.result(timeout=8)

    db_session.expire_all()
    stored = facts.get_evidence_publication(db_session, publication_id=publication_id)
    assert stored.state == facts.PUBLICATION_STATE_VERIFIED
    assert stored.attempts == 5
    assert len(refreshes) == 1, "stale success must not trigger another delivery refresh"
