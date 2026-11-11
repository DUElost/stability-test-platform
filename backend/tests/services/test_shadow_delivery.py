"""B8 G5 交付物版本登记与交付状态判定（#3929 / ADR-0062 D9）。

反例先行：本文件在实现落地前应在 origin/main 上失败。
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from backend.core.settings.scheduler import SchedulerSettings
from backend.models.device_log_event import DeviceLogEvent
from backend.models.issue_fact import DeliveryVersion
from backend.models.plan import Plan
from backend.models.plan_run import PlanRun
from backend.models.plan_run_artifact import PlanRunArtifact
from backend.services import issue_fact_store as facts


def _delivery():
    from backend.services import shadow_delivery
    return shadow_delivery


@pytest.fixture(autouse=True)
def _shadow_enabled(monkeypatch):
    from backend.services import shadow_delivery
    monkeypatch.setattr(
        shadow_delivery, "get_scheduler_settings",
        lambda: SimpleNamespace(stp_scan_round_shadow_enabled=True),
    )


def _plan_run(db, run_id=42, status="SUCCESS"):
    plan = Plan(name=f"g5-{uuid4()}", created_by="pytest")
    db.add(plan)
    db.flush()
    db.add(PlanRun(id=run_id, plan_id=plan.id, status=status, plan_snapshot={},
                   run_type="MANUAL", triggered_by="pytest"))
    db.flush()


def _unit(host_id, coverage, *, expected=True):
    return {"host_id": host_id, "platform": "mtk", "coverage": coverage, "expected": expected}


def _round(db, run_id, round_no, kind, units, *, merge="OK", platform="mtk", closed=True):
    now = datetime.now(timezone.utc)
    row = facts.record_scan_round(
        db, plan_run_id=run_id, platform=platform, round_no=round_no, kind=kind,
        expected_set=units, input_cutoff_at=now,
    )
    if closed:
        facts.set_scan_round_status(db, round_id=row.id, status="CLOSED", closed_at=now)
    if merge is not None:
        facts.record_scan_round_merge(db, round_id=row.id, status=merge, summary={})
    db.flush()
    return row


def _issue(db, run_id, host_id, *, serial="SN1", build="V1", evidence="COMPLETE", key=None):
    now = datetime.now(timezone.utc)
    key = key or uuid4().hex
    signature = facts.record_issue_signature(
        db, signature_raw=json.dumps({"exp_class": "JE", "package": "pkg"}),
        normalized_key=f"key-{uuid4()}", platform="mtk", event_type="JE", seen_at=now,
    )
    dle = DeviceLogEvent(
        id=uuid4(), serial=serial, platform="MTK", event_type="JE", state="REMOTE",
        local_path=f"/event/{uuid4()}", host_id=host_id, plan_run_id=run_id,
        occurrence_key=key, detected_at=now,
    )
    db.add(dle)
    db.flush()
    occurrence = facts.record_issue_occurrence(
        db, signature_id=signature.id, occurrence_key=key, serial=serial,
        build_version=build, observed_at=now, first_seen_source="local",
        first_seen_plan_run_id=run_id,
    )
    facts.link_occurrence_event(db, occurrence_id=occurrence.id, dle_id=dle.id,
                                signature_id=signature.id)
    facts.record_representative_selection(
        db, plan_run_id=run_id, platform="mtk", signature_id=signature.id,
        selection_status="SELECTED", dle_id=dle.id, evidence_status=evidence,
    )
    db.flush()
    return signature, dle


def _versions(db, run_id=42):
    return list(db.scalars(select(DeliveryVersion).where(
        DeliveryVersion.plan_run_id == run_id).order_by(DeliveryVersion.version_no)).all())


def _register(db, tmp_path, *, run_id=42, platform="mtk", now=None):
    return _delivery().register_delivery_version(
        db, plan_run_id=run_id, platform=platform, root=str(tmp_path), now=now)


def _patch_sweep_env(monkeypatch, tmp_path, factory, settings, module):
    from backend.services import shadow_scan_rounds as shadow
    monkeypatch.setattr(shadow, "SessionLocal", factory)
    monkeypatch.setattr(shadow, "get_scheduler_settings", lambda: settings)
    monkeypatch.setattr(shadow, "resolve_shared_storage_root", lambda: str(tmp_path))
    monkeypatch.setattr(shadow, "enqueue_sync", Mock())
    monkeypatch.setattr(facts, "list_shadow_scan_run_ids", lambda *_a, **_kw: [])
    monkeypatch.setattr(shadow, "_advance_run",
                        lambda *_a, **_kw: (_ for _ in ()).throw(AssertionError("advanced")))
    monkeypatch.setattr(module, "SessionLocal", factory)
    monkeypatch.setattr(module, "get_scheduler_settings", lambda: settings)
    monkeypatch.setattr(module, "resolve_shared_storage_root", lambda: str(tmp_path))


def test_final_merge_registers_v1_export_and_scope_only(db_session, sample_host, tmp_path):
    _plan_run(db_session)
    final = _round(db_session, 42, 1, "FINAL",
                   [_unit(sample_host.id, "RECEIVED"), _unit("102", "EMPTY")])
    signature, dle = _issue(db_session, 42, sample_host.id)
    db_session.commit()

    row = _register(db_session, tmp_path)
    db_session.commit()

    assert row is not None and row.version_no == 1
    assert row.round_ids == [final.id]
    assert row.basis_rounds == [{"round_id": final.id, "merge_generation": 1}]
    assert row.effective_coverage == [
        {"host_id": sample_host.id, "platform": "mtk", "covered": True, "round_ids": [final.id]},
        {"host_id": "102", "platform": "mtk", "covered": True, "round_ids": [final.id]},
    ]
    assert row.issue_ids == [signature.id]
    assert row.event_ids == [str(dle.id)]
    assert row.software_versions == ["V1"]
    assert row.pending_items == [] and row.primary_folders == [] and row.gaps == []

    path = tmp_path / "_meta" / "42" / "_delivery" / "mtk" / "v1.json"
    assert path.is_file()
    assert hashlib.sha256(path.read_bytes()).hexdigest() == row.stats_snapshot_version
    payload = json.loads(path.read_text())
    assert payload["issues"][0]["signature_id"] == signature.id
    assert payload["issues"][0]["occurrences"] == 1
    assert payload["issues"][0]["machines"] == 1
    assert payload["issues"][0]["representative_dle_id"] == str(dle.id)
    assert payload["issues"][0]["evidence_status"] == "COMPLETE"

    decision = _delivery().evaluate_version_status(
        row,
        rounds=[{"round_no": 1, "kind": "FINAL", "status": "CLOSED", "merge_status": "OK"}],
        representatives=[{"signature_id": signature.id, "selection_status": "SELECTED",
                          "evidence_status": "COMPLETE"}],
        publish_status=None,
    )
    assert decision.status == "SCOPE_ONLY"
    assert decision.reason == "publish_status_not_applicable"


def test_repeat_registration_is_idempotent(db_session, sample_host, tmp_path):
    _plan_run(db_session)
    _round(db_session, 42, 1, "FINAL", [_unit(sample_host.id, "EMPTY")])
    db_session.commit()

    first = _register(db_session, tmp_path)
    db_session.commit()
    second = _register(db_session, tmp_path)
    db_session.commit()

    assert first.version_no == second.version_no == 1
    assert len(_versions(db_session)) == 1


def test_supplement_registers_v2_and_preserves_v1(db_session, sample_host, tmp_path):
    _plan_run(db_session)
    final = _round(db_session, 42, 1, "FINAL",
                   [_unit(sample_host.id, "RECEIVED"), _unit("102", "MISSING")])
    db_session.commit()
    v1 = _register(db_session, tmp_path)
    db_session.commit()
    assert v1.gaps == [{"kind": "coverage_missing", "host_id": "102", "platform": "mtk"}]

    supplement = _round(db_session, 42, 2, "SUPPLEMENT",
                        [_unit(sample_host.id, "MISSING", expected=False), _unit("102", "RECEIVED")])
    db_session.commit()
    v2 = _register(db_session, tmp_path)
    db_session.commit()

    assert v2.version_no == 2
    assert v2.round_ids == [final.id, supplement.id]
    assert [u["covered"] for u in v2.effective_coverage] == [True, True]
    assert v2.gaps == []
    db_session.refresh(v1)
    assert v1.gaps == [{"kind": "coverage_missing", "host_id": "102", "platform": "mtk"}]
    assert v1.stats_snapshot_version != v2.stats_snapshot_version


def test_all_missing_is_not_ready_because_coverage_is_empty():
    decision = _delivery().evaluate_delivery_status(
        rounds=[{"round_no": 1, "kind": "FINAL", "status": "CLOSED", "merge_status": "OK"}],
        effective_coverage=[{"host_id": "101", "platform": "mtk", "covered": False}],
        representatives=[],
        publish_status=None,
    )
    assert decision.status == "NOT_READY"
    assert decision.reason == "effective_coverage_empty"


@pytest.mark.parametrize(("merge_status", "reason"), [
    ("NO_INPUT", "latest_merge_no_input"),
    ("FAILED_FINAL", "latest_merge_failed"),
    ("FAILED", "latest_merge_failed"),
])
def test_terminal_merge_failure_is_not_ready(merge_status, reason):
    decision = _delivery().evaluate_delivery_status(
        rounds=[{"round_no": 1, "kind": "FINAL", "status": "CLOSED", "merge_status": merge_status}],
        effective_coverage=[{"host_id": "101", "platform": "mtk", "covered": True}],
        representatives=[],
        publish_status={"1": "VERIFIED"},
    )
    assert decision.status == "NOT_READY"
    assert decision.reason == reason


def test_incomplete_representative_is_a_gap(db_session, sample_host, tmp_path):
    _plan_run(db_session)
    _round(db_session, 42, 1, "FINAL", [_unit(sample_host.id, "RECEIVED")])
    signature, _ = _issue(db_session, 42, sample_host.id, evidence="INCOMPLETE")
    db_session.commit()

    row = _register(db_session, tmp_path)
    db_session.commit()
    assert row.gaps == [{"kind": "evidence_incomplete", "signature_id": signature.id}]

    decision = _delivery().evaluate_delivery_status(
        rounds=[{"round_no": 1, "kind": "FINAL", "status": "CLOSED", "merge_status": "OK"}],
        effective_coverage=row.effective_coverage,
        representatives=[{"signature_id": signature.id, "selection_status": "SELECTED",
                          "evidence_status": "INCOMPLETE"}],
        publish_status={signature.id: "VERIFIED"},
    )
    assert decision.status == "READY_WITH_GAPS"
    assert {"kind": "evidence_incomplete", "signature_id": signature.id} in decision.gaps


def _ready_inputs(**overrides):
    values = {
        "rounds": [{"round_no": 1, "kind": "FINAL", "status": "CLOSED", "merge_status": "OK"}],
        "effective_coverage": [{"host_id": "101", "platform": "mtk", "covered": True}],
        "representatives": [{"signature_id": 7, "selection_status": "SELECTED",
                            "evidence_status": "COMPLETE"}],
        "publish_status": {7: "VERIFIED"},
    }
    values.update(overrides)
    return values


@pytest.mark.parametrize(("publish_status", "status", "reason"), [
    ({7: "VERIFIED"}, "READY", "all_checks_passed"),
    ({7: "RETRYING"}, "NOT_READY", "representative_retrying"),
    ({7: "FAILED"}, "READY_WITH_GAPS", "gaps_present"),
])
def test_publish_status_matrix(publish_status, status, reason):
    decision = _delivery().evaluate_delivery_status(**_ready_inputs(publish_status=publish_status))
    assert decision.status == status
    assert decision.reason == reason
    if publish_status[7] == "RETRYING":
        assert decision.gaps == ()
    if publish_status[7] == "FAILED":
        assert decision.gaps == ({"kind": "publish_failed", "signature_id": 7},)


def test_retrying_never_counts_as_gap_even_with_failures():
    decision = _delivery().evaluate_delivery_status(**_ready_inputs(
        representatives=[
            {"signature_id": 7, "selection_status": "SELECTED", "evidence_status": "COMPLETE"},
            {"signature_id": 8, "selection_status": "SELECTED", "evidence_status": "COMPLETE"},
        ],
        publish_status={7: "RETRYING", 8: "FAILED"},
    ))
    assert decision.status == "NOT_READY"
    assert decision.reason == "representative_retrying"
    assert all(gap.get("signature_id") != 7 for gap in decision.gaps)


def test_open_final_is_not_ready_before_any_coverage_check():
    decision = _delivery().evaluate_delivery_status(**_ready_inputs(
        rounds=[{"round_no": 1, "kind": "FINAL", "status": "RUNNING", "merge_status": None}],
        effective_coverage=[],
    ))
    assert decision.status == "NOT_READY"
    assert decision.reason == "final_round_not_closed"


def test_write_failure_leaves_no_registration_and_retry_is_consistent(
    db_session, sample_host, tmp_path, monkeypatch,
):
    _plan_run(db_session)
    _round(db_session, 42, 1, "FINAL", [_unit(sample_host.id, "EMPTY")])
    db_session.commit()
    module = _delivery()
    original = module._write_export
    monkeypatch.setattr(module, "_write_export", Mock(side_effect=OSError("disk full")))
    with pytest.raises(OSError):
        _register(db_session, tmp_path)
    db_session.rollback()
    assert _versions(db_session) == []

    monkeypatch.setattr(module, "_write_export", original)
    row = _register(db_session, tmp_path)
    db_session.commit()
    path = tmp_path / "_meta" / "42" / "_delivery" / "mtk" / "v1.json"
    assert row.version_no == 1
    assert hashlib.sha256(path.read_bytes()).hexdigest() == row.stats_snapshot_version


def test_db_write_failure_leaves_no_registration_and_retry_is_consistent(
    db_session, sample_host, tmp_path, monkeypatch,
):
    _plan_run(db_session)
    _round(db_session, 42, 1, "FINAL", [_unit(sample_host.id, "EMPTY")])
    db_session.commit()
    original = facts.record_delivery_version
    monkeypatch.setattr(facts, "record_delivery_version", Mock(side_effect=RuntimeError("db down")))
    with pytest.raises(RuntimeError):
        _register(db_session, tmp_path)
    db_session.rollback()
    assert _versions(db_session) == []
    path = tmp_path / "_meta" / "42" / "_delivery" / "mtk" / "v1.json"
    assert path.is_file()  # 文件先行：未登记的孤儿文件可以下次覆盖，不能出现有登记无文件

    monkeypatch.setattr(facts, "record_delivery_version", original)
    row = _register(db_session, tmp_path)
    db_session.commit()
    assert row.version_no == 1
    assert hashlib.sha256(path.read_bytes()).hexdigest() == row.stats_snapshot_version


def test_switch_off_periodic_and_non_mtk_do_not_register(
    db_session, sample_host, tmp_path, monkeypatch,
):
    _plan_run(db_session)
    periodic = _round(db_session, 42, 1, "PERIODIC", [_unit(sample_host.id, "RECEIVED")])
    db_session.commit()
    module = _delivery()

    monkeypatch.setattr(module, "get_scheduler_settings",
                        lambda: SimpleNamespace(stp_scan_round_shadow_enabled=False))
    monkeypatch.setattr(module, "SessionLocal",
                        lambda: (_ for _ in ()).throw(AssertionError("database touched")))
    assert module.try_register_delivery_version(42) is None
    assert _register(db_session, tmp_path) is None
    assert _versions(db_session) == []

    monkeypatch.setattr(module, "get_scheduler_settings",
                        lambda: SimpleNamespace(stp_scan_round_shadow_enabled=True))
    assert _register(db_session, tmp_path) is None
    assert _versions(db_session) == []

    unisoc = _round(db_session, 42, 2, "FINAL", [
        {"host_id": sample_host.id, "platform": "unisoc", "coverage": "RECEIVED"},
    ], platform="unisoc")
    assert unisoc.kind == "FINAL"
    assert _register(db_session, tmp_path, platform="unisoc") is None
    assert _versions(db_session) == []
    assert periodic.merge_status == "OK"

    _plan_run(db_session, 43)
    facts.record_partition_mode(
        db_session, plan_run_id=43, platform="mtk", mode=facts.PARTITION_MODE_DEDUP_FIRST,
    )
    _round(db_session, 43, 1, "FINAL", [_unit(sample_host.id, "RECEIVED")])
    assert _register(db_session, tmp_path, run_id=43) is None
    assert _versions(db_session, 43) == []


def test_current_chain_artifacts_are_untouched(db_session, sample_host, tmp_path):
    _plan_run(db_session)
    _round(db_session, 42, 1, "FINAL", [_unit(sample_host.id, "RECEIVED")])
    _, dle = _issue(db_session, 42, sample_host.id)
    db_session.add(PlanRunArtifact(plan_run_id=42, storage_uri="/center/dedup/42/current.xls",
                                   artifact_type="scan_result_xls", scan_round_id="7"))
    db_session.flush()
    dedup = tmp_path / "dedup" / "42"
    dedup.mkdir(parents=True)
    current = dedup / "current.xls"
    current.write_bytes(b"current")
    db_session.commit()

    _register(db_session, tmp_path)
    db_session.commit()

    assert [row.storage_uri for row in db_session.scalars(select(PlanRunArtifact)).all()] == [
        "/center/dedup/42/current.xls"]
    assert db_session.get(DeviceLogEvent, dle.id).state == "REMOTE"
    assert current.read_bytes() == b"current"


def test_terminal_shadow_merge_triggers_registration(
    db_session, sample_host, tmp_path, monkeypatch,
):
    from backend.services import shadow_merge
    _plan_run(db_session)
    final = _round(db_session, 42, 1, "FINAL", [_unit(sample_host.id, "EMPTY")])
    db_session.commit()
    factory = sessionmaker(bind=db_session.get_bind())
    settings = SchedulerSettings(stp_scan_round_shadow_enabled=True)
    module = _delivery()
    monkeypatch.setattr(shadow_merge, "SessionLocal", factory)
    monkeypatch.setattr(shadow_merge, "get_scheduler_settings", lambda: settings)
    monkeypatch.setattr(module, "SessionLocal", factory)
    monkeypatch.setattr(module, "get_scheduler_settings", lambda: settings)
    monkeypatch.setattr(module, "resolve_shared_storage_root", lambda: str(tmp_path))

    shadow_merge.run_shadow_merge(42, final.id)

    with factory() as db:
        rows = _versions(db)
        assert [row.version_no for row in rows] == [1]
        path = tmp_path / "_meta" / "42" / "_delivery" / "mtk" / "v1.json"
        assert hashlib.sha256(path.read_bytes()).hexdigest() == rows[0].stats_snapshot_version


def test_registration_failure_does_not_break_shadow_merge(
    db_session, sample_host, tmp_path, monkeypatch, caplog,
):
    from backend.services import shadow_merge
    _plan_run(db_session)
    final = _round(db_session, 42, 1, "FINAL", [_unit(sample_host.id, "EMPTY")])
    db_session.commit()
    factory = sessionmaker(bind=db_session.get_bind())
    settings = SchedulerSettings(stp_scan_round_shadow_enabled=True)
    module = _delivery()
    monkeypatch.setattr(shadow_merge, "SessionLocal", factory)
    monkeypatch.setattr(shadow_merge, "get_scheduler_settings", lambda: settings)
    monkeypatch.setattr(module, "SessionLocal", factory)
    monkeypatch.setattr(module, "get_scheduler_settings", lambda: settings)
    monkeypatch.setattr(module, "register_delivery_version",
                        Mock(side_effect=RuntimeError("registration down")))

    shadow_merge.run_shadow_merge(42, final.id)

    with factory() as db:
        assert db.get(type(final), final.id).merge_status == "OK"
        assert _versions(db) == []
    assert any("shadow_delivery_register_failed" in record.getMessage()
               for record in caplog.records)


def test_sweep_compensates_once_and_does_not_repeat_write(
    db_session, sample_host, tmp_path, monkeypatch,
):
    from backend.services import shadow_scan_rounds as shadow
    _plan_run(db_session)
    final = _round(db_session, 42, 1, "FINAL", [_unit(sample_host.id, "EMPTY")])
    db_session.commit()
    factory = sessionmaker(bind=db_session.get_bind())
    settings = SchedulerSettings(stp_scan_round_shadow_enabled=True)
    module = _delivery()
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(shadow, "SessionLocal", factory)
    monkeypatch.setattr(shadow, "get_scheduler_settings", lambda: settings)
    monkeypatch.setattr(shadow, "resolve_shared_storage_root", lambda: str(tmp_path))
    monkeypatch.setattr(facts, "list_shadow_scan_run_ids", lambda *_a, **_kw: [])
    monkeypatch.setattr(shadow, "_advance_run",
                        lambda *_a, **_kw: (_ for _ in ()).throw(AssertionError("advanced")))
    queued = Mock()
    monkeypatch.setattr(shadow, "enqueue_sync", queued)
    monkeypatch.setattr(module, "SessionLocal", factory)
    monkeypatch.setattr(module, "get_scheduler_settings", lambda: settings)
    monkeypatch.setattr(module, "resolve_shared_storage_root", lambda: str(tmp_path))

    with factory() as db:
        assert facts.list_shadow_delivery_due_run_ids(db, platform="mtk") == [42]
    shadow.sweep_shadow_scan_rounds(now=now)
    with factory() as db:
        assert facts.list_shadow_delivery_due_run_ids(db, platform="mtk") == []
    shadow.sweep_shadow_scan_rounds(now=now + timedelta(seconds=120))

    with factory() as db:
        assert [row.version_no for row in _versions(db)] == [1]
    queued.assert_not_called()
    assert final.merge_status == "OK"

@pytest.mark.parametrize(("merge_status", "reason"), [
    ("NO_INPUT", "latest_merge_no_input"),
    ("FAILED_FINAL", "latest_merge_failed"),
])
def test_terminal_failure_still_registers_and_is_not_ready(
    db_session, sample_host, tmp_path, merge_status, reason,
):
    _plan_run(db_session)
    _round(db_session, 42, 1, "FINAL", [_unit(sample_host.id, "MISSING")], merge=merge_status)
    db_session.commit()

    row = _register(db_session, tmp_path)
    db_session.commit()
    assert row.version_no == 1
    decision = _delivery().evaluate_version_status(
        row,
        rounds=[{"round_no": 1, "kind": "FINAL", "status": "CLOSED",
                 "merge_status": merge_status}],
        representatives=[],
        publish_status={},
    )
    assert decision.status == "NOT_READY"
    assert decision.reason == reason


def test_registration_failure_retries_on_next_sweep(
    db_session, sample_host, tmp_path, monkeypatch, caplog,
):
    from backend.services import shadow_scan_rounds as shadow
    _plan_run(db_session)
    final = _round(db_session, 42, 1, "FINAL", [_unit(sample_host.id, "EMPTY")])
    db_session.commit()
    factory = sessionmaker(bind=db_session.get_bind())
    settings = SchedulerSettings(stp_scan_round_shadow_enabled=True)
    module = _delivery()
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(shadow, "SessionLocal", factory)
    monkeypatch.setattr(shadow, "get_scheduler_settings", lambda: settings)
    monkeypatch.setattr(shadow, "resolve_shared_storage_root", lambda: str(tmp_path))
    monkeypatch.setattr(facts, "list_shadow_scan_run_ids", lambda *_a, **_kw: [])
    monkeypatch.setattr(shadow, "_advance_run",
                        lambda *_a, **_kw: (_ for _ in ()).throw(AssertionError("advanced")))
    monkeypatch.setattr(shadow, "enqueue_sync", Mock())
    monkeypatch.setattr(module, "SessionLocal", factory)
    monkeypatch.setattr(module, "get_scheduler_settings", lambda: settings)
    monkeypatch.setattr(module, "resolve_shared_storage_root", lambda: str(tmp_path))
    real_register = module.register_delivery_version
    calls = []

    def flaky_register(*args, **kwargs):
        if not calls:
            calls.append(1)
            raise RuntimeError("transient registration failure")
        return real_register(*args, **kwargs)

    monkeypatch.setattr(module, "register_delivery_version", flaky_register)

    shadow.sweep_shadow_scan_rounds(now=now)
    with factory() as db:
        assert _versions(db) == []
        assert facts.list_shadow_delivery_due_run_ids(db, platform="mtk") == [42]
    assert any("shadow_delivery_register_failed" in record.getMessage()
               for record in caplog.records)

    shadow.sweep_shadow_scan_rounds(now=now + timedelta(seconds=120))
    with factory() as db:
        assert [row.version_no for row in _versions(db)] == [1]
        assert facts.list_shadow_delivery_due_run_ids(db, platform="mtk") == []
    assert final.merge_status == "OK"

def test_merge_later_than_sweep_start_uses_generation_not_clock(
    db_session, sample_host, tmp_path,
):
    _plan_run(db_session)
    final = _round(db_session, 42, 1, "FINAL", [_unit(sample_host.id, "EMPTY")])
    db_session.commit()
    sweep_started = datetime.now(timezone.utc)
    final.merged_at = sweep_started + timedelta(minutes=5)
    db_session.commit()

    # merge 在 sweep 起点之后提交：登记只记实时时间，是否重复由代次决定。
    version = _register(db_session, tmp_path, now=sweep_started)
    db_session.commit()
    assert version.round_ids == [final.id]
    assert version.registered_at == sweep_started
    assert version.basis_rounds == [{"round_id": final.id, "merge_generation": 1}]
    assert facts.list_shadow_delivery_due_run_ids(db_session, platform="mtk") == []


def test_due_list_includes_new_terminal_round_not_in_latest(db_session, sample_host, tmp_path):
    _plan_run(db_session)
    final = _round(db_session, 42, 1, "FINAL", [_unit(sample_host.id, "EMPTY")])
    db_session.commit()
    version = _register(db_session, tmp_path)
    db_session.commit()
    assert version.round_ids == [final.id]

    supplement = _round(db_session, 42, 2, "SUPPLEMENT", [_unit(sample_host.id, "EMPTY")])
    db_session.commit()
    assert supplement.id not in version.round_ids
    assert facts.list_shadow_delivery_due_run_ids(db_session, platform="mtk") == [42]

    v2 = _register(db_session, tmp_path)
    db_session.commit()
    assert v2.version_no == 2
    assert facts.list_shadow_delivery_due_run_ids(db_session, platform="mtk") == []


def test_due_list_excludes_non_current_and_unfinished_final(db_session, sample_host):
    _plan_run(db_session, 43)
    facts.record_partition_mode(
        db_session, plan_run_id=43, platform="mtk", mode=facts.PARTITION_MODE_DEDUP_FIRST,
    )
    _round(db_session, 43, 1, "FINAL", [_unit(sample_host.id, "EMPTY")])

    # FINAL 尚未终态，即使补充轮已终态也不入选。
    _plan_run(db_session, 44)
    _round(db_session, 44, 1, "FINAL", [_unit(sample_host.id, "EMPTY")], merge=None)
    _round(db_session, 44, 2, "SUPPLEMENT", [_unit(sample_host.id, "EMPTY")])
    db_session.commit()

    assert facts.list_shadow_delivery_due_run_ids(db_session, platform="mtk") == []

def test_remerge_after_reset_registers_v2_on_clock_rollback_with_content_change(
    db_session, sample_host, tmp_path, monkeypatch, caplog,
):
    from backend.services import shadow_scan_rounds as shadow
    _plan_run(db_session)
    final = _round(db_session, 42, 1, "FINAL", [_unit(sample_host.id, "EMPTY")],
                   merge="FAILED_FINAL")
    db_session.commit()
    v1 = _register(db_session, tmp_path)
    db_session.commit()
    assert v1.version_no == 1
    assert v1.basis_rounds == [{"round_id": final.id, "merge_generation": 1}]
    assert v1.issue_ids == []

    signature, _ = _issue(db_session, 42, sample_host.id)  # 内容变化
    db_session.commit()
    facts.reset_failed_final_shadow_merge(db_session, round_id=final.id)
    db_session.commit()
    facts.record_scan_round_merge(db_session, round_id=final.id, status="OK", summary={})
    db_session.commit()
    final.merged_at = v1.registered_at - timedelta(seconds=5)  # 墙钟回拨
    db_session.commit()

    factory = sessionmaker(bind=db_session.get_bind())
    settings = SchedulerSettings(stp_scan_round_shadow_enabled=True)
    module = _delivery()
    _patch_sweep_env(monkeypatch, tmp_path, factory, settings, module)
    original = module.register_delivery_version
    monkeypatch.setattr(module, "register_delivery_version",
                        Mock(side_effect=RuntimeError("merge path registration failure")))

    # merge 路径的登记失败，但轮次代次已加 1，补偿清单必须仍能发现。
    assert module.try_register_delivery_version(42) is None
    with factory() as db:
        assert [row.version_no for row in _versions(db)] == [1]
        assert facts.list_shadow_delivery_due_run_ids(db, platform="mtk") == [42]
    assert any("shadow_delivery_register_failed" in record.getMessage()
               for record in caplog.records)

    monkeypatch.setattr(module, "register_delivery_version", original)
    shadow.sweep_shadow_scan_rounds(now=v1.registered_at)
    with factory() as db:
        versions = _versions(db)
        assert [row.version_no for row in versions] == [1, 2]
        assert versions[1].basis_rounds == [{"round_id": final.id, "merge_generation": 2}]
        assert versions[1].issue_ids == [signature.id]
        assert facts.list_shadow_delivery_due_run_ids(db, platform="mtk") == []
    shadow.sweep_shadow_scan_rounds(now=v1.registered_at)
    with factory() as db:
        assert [row.version_no for row in _versions(db)] == [1, 2]


def test_remerge_with_identical_content_registers_v2_on_clock_rollback(
    db_session, sample_host, tmp_path, monkeypatch,
):
    from backend.services import shadow_scan_rounds as shadow
    _plan_run(db_session)
    final = _round(db_session, 42, 1, "FINAL", [_unit(sample_host.id, "EMPTY")],
                   merge="FAILED_FINAL")
    db_session.commit()
    v1 = _register(db_session, tmp_path)
    db_session.commit()
    assert v1.basis_rounds == [{"round_id": final.id, "merge_generation": 1}]
    before = (
        v1.round_ids, v1.effective_coverage, v1.issue_ids, v1.event_ids,
        v1.software_versions, v1.gaps,
    )

    facts.reset_failed_final_shadow_merge(db_session, round_id=final.id)
    db_session.commit()
    facts.record_scan_round_merge(db_session, round_id=final.id, status="OK", summary={})
    db_session.commit()
    final.merged_at = v1.registered_at - timedelta(seconds=5)  # 墙钟回拨
    db_session.commit()

    factory = sessionmaker(bind=db_session.get_bind())
    settings = SchedulerSettings(stp_scan_round_shadow_enabled=True)
    module = _delivery()
    _patch_sweep_env(monkeypatch, tmp_path, factory, settings, module)

    # 内容不变、登记无故障：下一次真实 sweep 由补偿清单发现并写 v2。
    with factory() as db:
        assert facts.list_shadow_delivery_due_run_ids(db, platform="mtk") == [42]
    shadow.sweep_shadow_scan_rounds(now=v1.registered_at)

    with factory() as db:
        versions = _versions(db)
        assert [row.version_no for row in versions] == [1, 2]
        v2 = versions[1]
        assert (
            v2.round_ids, v2.effective_coverage, v2.issue_ids, v2.event_ids,
            v2.software_versions, v2.gaps,
        ) == before
        assert versions[0].basis_rounds == [{"round_id": final.id, "merge_generation": 1}]
        assert v2.basis_rounds == [{"round_id": final.id, "merge_generation": 2}]
        assert v2.stats_snapshot_version != versions[0].stats_snapshot_version
        assert facts.list_shadow_delivery_due_run_ids(db, platform="mtk") == []

    # 登记后不再入选，后续 sweep 不产生 v3。
    shadow.sweep_shadow_scan_rounds(now=v1.registered_at)
    with factory() as db:
        assert [row.version_no for row in _versions(db)] == [1, 2]


def test_delivery_discovery_lock_timeout_keeps_pending_merge_enqueued(
    db_session, sample_host, tmp_path, monkeypatch, caplog,
):
    from backend.services import shadow_scan_rounds as shadow
    _plan_run(db_session)
    final = _round(db_session, 42, 1, "FINAL", [_unit(sample_host.id, "EMPTY")])
    db_session.commit()
    v1 = _register(db_session, tmp_path)
    db_session.commit()
    assert v1.version_no == 1
    assert v1.basis_rounds == [{"round_id": final.id, "merge_generation": 1}]

    # 新补充轮已终态 → 补偿清单应发现 42；另一补充轮未 merge → 本轮仍要入队。
    supplement = _round(db_session, 42, 2, "SUPPLEMENT", [_unit(sample_host.id, "EMPTY")])
    pending = _round(db_session, 42, 3, "SUPPLEMENT", [_unit(sample_host.id, "EMPTY")],
                     merge=None)
    db_session.commit()

    factory = sessionmaker(bind=db_session.get_bind())
    settings = SchedulerSettings(stp_scan_round_shadow_enabled=True)
    module = _delivery()
    _patch_sweep_env(monkeypatch, tmp_path, factory, settings, module)
    monkeypatch.setattr(shadow, "_DELIVERY_DISCOVERY_LOCK_TIMEOUT_MS", 100)
    with factory() as db:
        assert facts.list_shadow_delivery_due_run_ids(db, platform="mtk") == [42]

    # 复核反例：delivery_version 上持有 ACCESS EXCLUSIVE，补偿发现只能等到
    # lock_timeout 失败；失败不得吞掉本轮的影子 merge 入队。
    blocker = db_session.get_bind().connect()
    try:
        blocker.execute(text("LOCK TABLE delivery_version IN ACCESS EXCLUSIVE MODE"))
        shadow.sweep_shadow_scan_rounds(now=v1.registered_at)
    finally:
        blocker.rollback()
        blocker.close()

    assert any("shadow_delivery_discovery_failed" in record.getMessage()
               for record in caplog.records)
    assert any(
        call.args and call.args[0] == "shadow_merge_task"
        and call.kwargs.get("plan_run_id") == 42
        and call.kwargs.get("fact_round_id") == pending.id
        for call in shadow.enqueue_sync.call_args_list
    )
    with factory() as db:
        assert [row.version_no for row in _versions(db)] == [1]

    # 下一周期锁已释放：补偿重试成功，v2 覆盖新增补充轮，之后不再入选。
    shadow.sweep_shadow_scan_rounds(now=v1.registered_at)
    with factory() as db:
        versions = _versions(db)
        assert [row.version_no for row in versions] == [1, 2]
        assert versions[1].round_ids == [final.id, supplement.id]
        assert versions[1].basis_rounds == [
            {"round_id": final.id, "merge_generation": 1},
            {"round_id": supplement.id, "merge_generation": 1},
        ]
        assert facts.list_shadow_delivery_due_run_ids(db, platform="mtk") == []


def _one_shot_exit_failure_factory(real_factory):
    """第二次取会话时，SELECT 成功后让退出回滚失败（模拟连接被终止）。"""
    state = {"calls": 0, "exit_failures": 0}

    class _ExitFailingSession:
        def __init__(self, session):
            self._session = session

        def __enter__(self):
            self._session.__enter__()
            return self._session

        def __exit__(self, exc_type, exc, tb):
            self._session.close()
            if exc_type is None:
                state["exit_failures"] += 1
                raise OperationalError(
                    "ROLLBACK", {},
                    RuntimeError("server closed the connection unexpectedly"),
                )
            return None

    def factory():
        state["calls"] += 1
        session = real_factory()
        if state["calls"] == 2:
            return _ExitFailingSession(session)
        return session

    return factory, state


def test_delivery_discovery_session_exit_failure_defers_registration(
    db_session, sample_host, tmp_path, monkeypatch, caplog,
):
    from backend.services import shadow_scan_rounds as shadow
    _plan_run(db_session)
    final = _round(db_session, 42, 1, "FINAL", [_unit(sample_host.id, "EMPTY")])
    db_session.commit()
    v1 = _register(db_session, tmp_path)
    db_session.commit()
    assert v1.version_no == 1

    # 新补充轮已终态 → 补偿清单应发现 42；另一补充轮未 merge → 本轮仍要入队。
    supplement = _round(db_session, 42, 2, "SUPPLEMENT", [_unit(sample_host.id, "EMPTY")])
    pending = _round(db_session, 42, 3, "SUPPLEMENT", [_unit(sample_host.id, "EMPTY")],
                     merge=None)
    db_session.commit()

    real_factory = sessionmaker(bind=db_session.get_bind())
    factory, state = _one_shot_exit_failure_factory(real_factory)
    settings = SchedulerSettings(stp_scan_round_shadow_enabled=True)
    module = _delivery()
    _patch_sweep_env(monkeypatch, tmp_path, factory, settings, module)

    with real_factory() as db:
        assert facts.list_shadow_delivery_due_run_ids(db, platform="mtk") == [42]

    # 复核反例：补偿 SELECT 已返回候选，会话退出回滚时连接断开。
    shadow.sweep_shadow_scan_rounds(now=v1.registered_at)
    assert state["exit_failures"] == 1
    assert any("shadow_delivery_discovery_failed" in record.getMessage()
               for record in caplog.records)
    assert any(
        call.args and call.args[0] == "shadow_merge_task"
        and call.kwargs.get("plan_run_id") == 42
        and call.kwargs.get("fact_round_id") == pending.id
        for call in shadow.enqueue_sync.call_args_list
    )
    with real_factory() as db:
        # 当轮不得消费已获得的候选；清单保留该 run 供下周期重试。
        assert [row.version_no for row in _versions(db)] == [1]
        assert facts.list_shadow_delivery_due_run_ids(db, platform="mtk") == [42]

    # 下一周期发现正常：补登记 v2，清单收敛。
    shadow.sweep_shadow_scan_rounds(now=v1.registered_at)
    with real_factory() as db:
        versions = _versions(db)
        assert [row.version_no for row in versions] == [1, 2]
        assert versions[1].round_ids == [final.id, supplement.id]
        assert facts.list_shadow_delivery_due_run_ids(db, platform="mtk") == []


def test_merge_generation_is_monotonic_and_reset_does_not_decrease(
    db_session, sample_host,
):
    _plan_run(db_session)
    retry = _round(db_session, 42, 1, "FINAL", [_unit(sample_host.id, "EMPTY")],
                   merge="FAILED")
    db_session.commit()
    assert retry.merge_generation == 0

    facts.record_scan_round_merge(db_session, round_id=retry.id, status="OK", summary={})
    db_session.commit()
    db_session.refresh(retry)
    assert retry.merge_generation == 1

    # 已是终态时重复写入直接返回，不加代次。
    facts.record_scan_round_merge(db_session, round_id=retry.id, status="OK", summary={})
    db_session.commit()
    db_session.refresh(retry)
    assert retry.merge_generation == 1

    failed_final = _round(db_session, 42, 2, "SUPPLEMENT",
                          [_unit(sample_host.id, "EMPTY")], merge="FAILED_FINAL")
    db_session.commit()
    assert failed_final.merge_generation == 1
    facts.reset_failed_final_shadow_merge(db_session, round_id=failed_final.id)
    db_session.commit()
    db_session.refresh(failed_final)
    assert failed_final.merge_generation == 1

    facts.record_scan_round_merge(
        db_session, round_id=failed_final.id, status="OK", summary={},
    )
    db_session.commit()
    db_session.refresh(failed_final)
    assert failed_final.merge_generation == 2


def test_due_list_excludes_deleted_plan_run(db_session, sample_host):
    _plan_run(db_session, 45)
    _round(db_session, 45, 1, "FINAL", [_unit(sample_host.id, "EMPTY")])
    db_session.commit()
    assert facts.list_shadow_delivery_due_run_ids(db_session, platform="mtk") == [45]

    db_session.delete(db_session.get(PlanRun, 45))
    db_session.commit()
    assert facts.list_shadow_delivery_due_run_ids(db_session, platform="mtk") == []