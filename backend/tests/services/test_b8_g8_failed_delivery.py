"""B8 G8：FAILED 分区日志交付（ADR-0062 D15，§11 场景 11）。

覆盖 #4031 第 1 项：

- MTK DEDUP_FIRST 分区在没有任何轮次时进入终态（含 FAILED），调度仍能建立
  最终轮；
- 最终轮无输入/部分覆盖/代表待校验/代表已校验按 D9 顺序判定；
- 补充轮、代表发布补偿与中断后的重新入队继续工作；
- 同一实际 PlanRun 的 CURRENT 分区保持既有行为。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest
from sqlalchemy.orm import sessionmaker

from backend.agent.contracts.round_report_layout import (
    round_manifest_path,
    round_report_dir,
)
from backend.core.settings.scheduler import SchedulerSettings
from backend.models.device_log_event import DeviceLogEvent
from backend.models.host import Host
from backend.models.plan import Plan
from backend.models.plan_run import PlanRun
from backend.services import evidence_publication as ep
from backend.services import issue_fact_store as facts
from backend.services import shadow_delivery as delivery
from backend.services import shadow_merge
from backend.services import shadow_scan_rounds as shadow

HOST_A = "host-a"
HOST_B = "host-b"


@pytest.fixture
def g8_env(db_session, tmp_path, monkeypatch):
    factory = sessionmaker(bind=db_session.get_bind(), expire_on_commit=False)
    settings = SchedulerSettings(stp_scan_round_shadow_enabled=True)
    queued = Mock()

    def make_run(db, *, status="FAILED"):
        plan = Plan(name=f"g8-{status.lower()}", created_by="pytest")
        db.add(plan)
        db.flush()
        run = PlanRun(
            plan_id=plan.id, status=status, plan_snapshot={},
            run_type="MANUAL", triggered_by="pytest",
        )
        db.add(run)
        db.flush()
        return run

    for module in (shadow, delivery, ep, shadow_merge):
        monkeypatch.setattr(module, "get_scheduler_settings", lambda: settings)
    monkeypatch.setattr(shadow, "SessionLocal", factory)
    monkeypatch.setattr(delivery, "SessionLocal", factory)
    monkeypatch.setattr(ep, "SessionLocal", factory)
    monkeypatch.setattr(shadow_merge, "SessionLocal", factory)
    monkeypatch.setattr(shadow, "resolve_shared_storage_root", lambda: str(tmp_path))
    monkeypatch.setattr(delivery, "resolve_shared_storage_root", lambda: str(tmp_path))
    monkeypatch.setattr(ep, "resolve_shared_storage_root", lambda: str(tmp_path))
    monkeypatch.setattr(shadow, "enqueue_sync", queued)
    monkeypatch.setattr(ep, "enqueue_sync", Mock())
    monkeypatch.setattr(
        shadow, "_expected_units",
        lambda *_args: [
            {"host_id": host, "platform": "mtk"}
            for host in (HOST_A, HOST_B)
            if host in _expected_hosts[0]
        ],
    )
    _expected_hosts[0] = {HOST_A}

    def set_hosts(*hosts):
        _expected_hosts[0] = set(hosts)

    return factory, settings, queued, tmp_path, make_run, set_hosts


_expected_hosts = [{HOST_A}]


def _mode(db, run_id, mode=facts.PARTITION_MODE_DEDUP_FIRST):
    facts.record_partition_mode(
        db, plan_run_id=run_id, platform="mtk", mode=mode,
    )
    db.flush()


def _dle(db, run_id, host_id=HOST_A, serial="SN-A"):
    if db.get(Host, host_id) is None:
        db.add(Host(id=host_id, hostname=host_id, status="ONLINE"))
        db.flush()
    row = DeviceLogEvent(
        serial=serial, platform="MTK", event_type="JE", state="LOCAL",
        local_path=f"/data/aee/{serial}/aee_exp/evt-{serial}",
        host_id=host_id, plan_run_id=run_id,
        occurrence_key=f"{serial}-occ", detected_at=datetime.now(timezone.utc),
    )
    db.add(row)
    db.flush()
    return row


def _selection(db, run_id, dle):
    signature = facts.record_issue_signature(
        db, signature_raw=json.dumps({"exp_class": "JE"}),
        normalized_key=f"key-{dle.serial}", platform="mtk", event_type="JE",
        seen_at=datetime.now(timezone.utc),
    )
    occurrence = facts.record_issue_occurrence(
        db, signature_id=signature.id, occurrence_key=dle.occurrence_key,
        serial=dle.serial, observed_at=dle.detected_at,
        first_seen_source="local", first_seen_plan_run_id=run_id,
    )
    facts.link_occurrence_event(
        db, occurrence_id=occurrence.id, dle_id=dle.id,
        signature_id=signature.id,
    )
    facts.record_representative_selection(
        db, plan_run_id=run_id, platform="mtk", signature_id=signature.id,
        selection_status=facts.SELECTION_SELECTED, dle_id=dle.id,
        evidence_status=facts.EVIDENCE_COMPLETE,
    )
    db.flush()
    return signature


def _final_round(
    db, run_id, *, coverage=("RECEIVED",), merge_status="OK",
    kind=facts.SCAN_ROUND_KIND_FINAL,
):
    now = datetime.now(timezone.utc)
    units = []
    for index, state in enumerate(coverage):
        host = HOST_A if index == 0 else HOST_B
        units.append({
            "host_id": host, "platform": "mtk",
            "coverage": state, "expected": True,
        })
    row = facts.record_scan_round(
        db, plan_run_id=run_id, platform="mtk", round_no=1, kind=kind,
        expected_set=units, input_cutoff_at=now,
    )
    facts.set_scan_round_status(db, round_id=row.id, status="CLOSED", closed_at=now)
    if merge_status is not None:
        facts.record_scan_round_merge(
            db, round_id=row.id, status=merge_status, summary={},
        )
    db.flush()
    return row


def _receipt(root, run_id, round_id, host, *, empty=True):
    entry = {"fact_round_id": round_id, "platform": "mtk", "empty_receipt": empty}
    if not empty:
        folder = round_report_dir(str(root), run_id, round_id, "mtk")
        folder.mkdir(parents=True, exist_ok=True)
        file = folder / f"{host}_Result_late_org.xls"
        file.write_bytes(b"report")
        entry.update(file_key=f"mtk/{file.name}", registerable=True)
    manifest = round_manifest_path(str(root), run_id, round_id, host)
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps({
        "schema_version": 1,
        "plan_run_id": run_id,
        "host_id": host,
        "artifacts": [entry],
    }), encoding="utf-8")


def test_failed_dedup_first_without_rounds_starts_final_round(g8_env):
    factory, _settings, queued, _root, make_run, set_hosts = g8_env
    with factory() as db:
        run = make_run(db, status="FAILED")
        _mode(db, run.id)
        db.commit()
        run_id = run.id

    shadow.sweep_shadow_scan_rounds(now=datetime.now(timezone.utc))

    with factory() as db:
        rounds = facts.list_scan_rounds(db, plan_run_id=run_id, platform="mtk")
    assert [row.kind for row in rounds] == [facts.SCAN_ROUND_KIND_FINAL]
    assert rounds[0].status == facts.SCAN_ROUND_STATUS_PENDING
    assert any(
        call.kwargs.get("is_final") is True
        for call in queued.call_args_list
        if call.args and call.args[0] == "scan_task"
    )


def test_failed_dedup_first_no_input_registers_delivery(g8_env):
    factory, _settings, _queued, _root, make_run, set_hosts = g8_env
    with factory() as db:
        run = make_run(db, status="FAILED")
        _mode(db, run.id)
        final = _final_round(db, run.id, coverage=("MISSING",), merge_status="NO_INPUT")
        db.commit()
        run_id, final_id = run.id, final.id

    ep.sweep_evidence_publications(now=datetime.now(timezone.utc))

    with factory() as db:
        versions = facts.list_delivery_versions(db, plan_run_id=run_id, platform="mtk")
        decision = delivery.current_delivery_decision(
            db, plan_run_id=run_id, platform="mtk",
        )
    assert [row.version_no for row in versions] == [1]
    assert versions[0].basis_rounds == [{"round_id": final_id, "merge_generation": 1}]
    assert decision.status == delivery.DELIVERY_STATUS_NOT_READY
    assert decision.reason == delivery.REASON_LATEST_MERGE_NO_INPUT


def test_d9_states_after_failed_final(g8_env):
    factory, _settings, _queued, _root, make_run, _set_hosts = g8_env
    cases = {
        "partial": {
            "coverage": ("RECEIVED", "MISSING"), "merge_status": "OK",
            "publication": "VERIFIED", "selected": True,
            "status": delivery.DELIVERY_STATUS_READY_WITH_GAPS,
            "reason": delivery.REASON_GAPS_PRESENT,
        },
        "pending": {
            "coverage": ("RECEIVED",), "merge_status": "OK",
            "publication": "PENDING", "selected": True,
            "status": delivery.DELIVERY_STATUS_NOT_READY,
            "reason": delivery.REASON_REPRESENTATIVE_RETRYING,
        },
        "verified": {
            "coverage": ("RECEIVED",), "merge_status": "OK",
            "publication": "VERIFIED", "selected": True,
            "status": delivery.DELIVERY_STATUS_READY,
            "reason": delivery.REASON_ALL_CHECKS_PASSED,
        },
        "no_input": {
            "coverage": ("MISSING",), "merge_status": "NO_INPUT",
            "publication": None, "selected": False,
            "status": delivery.DELIVERY_STATUS_NOT_READY,
            "reason": delivery.REASON_LATEST_MERGE_NO_INPUT,
        },
    }
    for name, case in cases.items():
        with factory() as db:
            run = make_run(db, status="FAILED")
            _mode(db, run.id)
            _final_round(
                db, run.id, coverage=case["coverage"],
                merge_status=case["merge_status"],
            )
            if case["selected"]:
                dle = _dle(db, run.id)
                _selection(db, run.id, dle)
                if case["publication"]:
                    publication = facts.record_evidence_publication(
                        db, plan_run_id=run.id, platform="mtk", dle_id=dle.id,
                        root_plan_run_id=run.id,
                        source_kind=facts.PUBLICATION_SOURCE_LOCAL,
                        source_path="/src", collection_folder="folder",
                        naming_rule_version="v1",
                        destination_rel_path=(
                            f"evidence/{run.id}_compat/{dle.serial}/evt-{dle.serial}"
                        ),
                        state=facts.PUBLICATION_STATE_PENDING,
                    )
                    if case["publication"] == facts.PUBLICATION_STATE_VERIFIED:
                        facts.update_evidence_publication_state(
                            db, row=publication,
                            state=facts.PUBLICATION_STATE_VERIFIED,
                            checksum="a" * 64, size_bytes=1,
                        )
            db.commit()
            decision = delivery.current_delivery_decision(
                db, plan_run_id=run.id, platform="mtk",
            )
            assert (decision.status, decision.reason) == (
                case["status"], case["reason"],
            ), name


def test_interrupted_dispatch_retries_same_final_round(g8_env):
    factory, _settings, queued, _root, make_run, _set_hosts = g8_env
    with factory() as db:
        run = make_run(db, status="FAILED")
        _mode(db, run.id)
        db.commit()
        run_id = run.id

    queued.side_effect = RuntimeError("queue down")
    shadow.sweep_shadow_scan_rounds(now=datetime.now(timezone.utc))
    with factory() as db:
        rounds = facts.list_scan_rounds(db, plan_run_id=run_id, platform="mtk")
    assert len(rounds) == 1 and rounds[0].status == facts.SCAN_ROUND_STATUS_PENDING

    queued.reset_mock()
    queued.side_effect = None
    shadow.sweep_shadow_scan_rounds(now=datetime.now(timezone.utc) + timedelta(seconds=1))
    with factory() as db:
        rounds = facts.list_scan_rounds(db, plan_run_id=run_id, platform="mtk")
    assert len(rounds) == 1
    assert any(
        call.kwargs.get("is_final") is True
        for call in queued.call_args_list
        if call.args and call.args[0] == "scan_task"
    )


def test_supplement_round_still_created_after_late_report(g8_env):
    factory, _settings, _queued, root, make_run, set_hosts = g8_env
    set_hosts(HOST_A, HOST_B)
    with factory() as db:
        run = make_run(db, status="FAILED")
        _mode(db, run.id)
        final = _final_round(
            db, run.id, coverage=("RECEIVED", "MISSING"), merge_status="OK",
        )
        db.commit()
        run_id, final_id = run.id, final.id

    # 终态关闭之后到达的 B 主机报表属于补充轮，不得并入最终轮。
    _receipt(root, run_id, final_id, HOST_B, empty=False)
    shadow.sweep_shadow_scan_rounds(now=datetime.now(timezone.utc))

    with factory() as db:
        rounds = facts.list_scan_rounds(db, plan_run_id=run_id, platform="mtk")
    assert [row.kind for row in rounds] == [
        facts.SCAN_ROUND_KIND_FINAL,
        facts.SCAN_ROUND_KIND_SUPPLEMENT,
    ]
    supplement = rounds[1]
    assert [unit["host_id"] for unit in supplement.expected_set] == [HOST_B]
    assert supplement.expected_set[0]["coverage"] == "RECEIVED"


def test_current_partition_unobserved_terminal_run_still_skipped(g8_env):
    factory, _settings, queued, _root, make_run, set_hosts = g8_env
    with factory() as db:
        run = make_run(db, status="FAILED")
        _mode(db, run.id, facts.PARTITION_MODE_CURRENT)
        db.commit()
        run_id = run.id

    shadow.sweep_shadow_scan_rounds(now=datetime.now(timezone.utc))
    with factory() as db:
        assert facts.list_scan_rounds(db, plan_run_id=run_id, platform="mtk") == []
    queued.assert_not_called()
