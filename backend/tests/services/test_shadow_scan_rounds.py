"""B8 G2 影子轮报表隔离与归属反例（#3800 v1.9 / #3865）。"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import xlwt
import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker
from unittest.mock import Mock

from backend.agent.contracts.round_report_layout import round_manifest_path, round_report_dir
from backend.services import dedup_scan
from backend.services.shadow_scan_rounds import classify_round_coverage, read_round_reports
from backend.models.plan_run_artifact import PlanRunArtifact
from backend.services.dedup_extract import collect_upload_event_dir_names
from backend.services import issue_fact_store as facts, shadow_scan_rounds as shadow
from backend.core.settings.scheduler import SchedulerSettings


def _manifest(root, *, round_id=7, host="host-a", entries=None):
    manifest = round_manifest_path(str(root), 42, round_id, host)
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "plan_run_id": 42,
                "host_id": host,
                "artifacts": entries or [],
            }
        ),
        encoding="utf-8",
    )
    return manifest


def test_shadow_report_namespace_is_invisible_to_current_readers(tmp_path):
    report_dir = round_report_dir(str(tmp_path), 42, 7, "mtk")
    report_dir.mkdir(parents=True)
    (report_dir / "host-a_Result_1_org.xls").write_bytes(b"report")
    _manifest(
        tmp_path,
        entries=[
            {
                "file_key": "mtk/host-a_Result_1_org.xls",
                "platform": "mtk",
                "fact_round_id": 7,
                "registerable": True,
            }
        ],
    )
    assert read_round_reports(str(tmp_path), 42, 7, [{"host_id": "host-a", "platform": "mtk"}])
    assert dedup_scan._read_manifest_shards(tmp_path / "_meta" / "42", 42) == []

    # 使用现行注册函数自身的 glob；影子目录不能变成 plan_run_artifact。
    class NoWrites:
        def execute(self, *_args, **_kwargs):
            raise AssertionError("shadow report entered current registration")

    assert (
        dedup_scan._register_scan_artifacts_from_nfs(
            NoWrites(),
            42,
            tmp_path / "dedup" / "42" / "mtk",
        )
        == 0
    )


def test_missing_or_wrong_fact_round_cannot_satisfy_coverage(tmp_path):
    report_dir = round_report_dir(str(tmp_path), 42, 7, "mtk")
    report_dir.mkdir(parents=True)
    (report_dir / "host-a_Result_1_org.xls").write_bytes(b"report")
    base = {"file_key": "mtk/host-a_Result_1_org.xls", "platform": "mtk", "registerable": True}
    _manifest(
        tmp_path,
        entries=[
            base,
            {**base, "fact_round_id": 6},
            {
                **base,
                "fact_round_id": 7,
                "platform": "unisoc",
            },
        ],
    )
    expected = [{"host_id": "host-a", "platform": "mtk"}]
    assert read_round_reports(str(tmp_path), 42, 7, expected) == []
    # 当前轮目录中绝不接收其他轮；上一轮自己的报表只能从上一轮目录读取。
    _manifest(tmp_path, round_id=6, entries=[{**base, "fact_round_id": 6}])
    assert read_round_reports(str(tmp_path), 42, 7, expected) == []


def test_empty_receipt_is_explicit_and_host_bound(tmp_path):
    _manifest(tmp_path, entries=[{"fact_round_id": 7, "platform": "mtk", "empty_receipt": True}])
    expected = [{"host_id": "host-a", "platform": "mtk"}, {"host_id": "offline", "platform": "mtk"}]
    reports = read_round_reports(str(tmp_path), 42, 7, expected)
    assert reports == [
        {"host_id": "host-a", "platform": "mtk", "fact_round_id": 7, "empty_receipt": True}
    ]


def test_explicit_empty_offline_and_report_remain_distinct():
    now = datetime.now(timezone.utc)
    units = [{"host_id": host, "platform": "mtk"} for host in ["empty", "offline", "reported"]]
    reports = [
        {"host_id": "empty", "platform": "mtk", "empty_receipt": True},
        {
            "host_id": "reported",
            "platform": "mtk",
            "empty_receipt": False,
            "storage_uri": "report.xls",
        },
    ]
    coverage = classify_round_coverage(units, reports, observed_at=now, input_cutoff_at=now)
    assert [u["coverage"] for u in coverage] == ["EMPTY", "MISSING", "RECEIVED"]
    # merge 失败没有写入覆盖函数的入口：有报表始终不等于零问题或交付完成。
    assert coverage[2]["reports"][0]["storage_uri"] == "report.xls"


def test_late_input_never_becomes_a_closed_round_valid_report():
    now = datetime.now(timezone.utc)
    expected = [{"host_id": "a", "platform": "mtk", "coverage": "MISSING"}]
    reports = [{"host_id": "a", "platform": "mtk", "empty_receipt": False}]
    for closed, cutoff in [(True, now + timedelta(hours=1)), (False, now - timedelta(seconds=1))]:
        result = classify_round_coverage(
            expected, reports, observed_at=now, input_cutoff_at=cutoff, closed=closed
        )
        assert result[0]["coverage"] == "LATE"
        assert "reports" not in result[0]
        assert result[0]["late_reports"] == reports
    assert expected[0]["coverage"] == "MISSING"


def test_shadow_files_do_not_change_current_registration_upload_or_merge(
    db_session,
    sample_plan_run,
    tmp_path,
    monkeypatch,
):
    run_id = sample_plan_run.id
    current_dir = tmp_path / "dedup" / str(run_id) / "mtk"
    current_dir.mkdir(parents=True)
    current_file = current_dir / "host-a_Result_current_org.xls"
    shadow_dir = round_report_dir(str(tmp_path), run_id, 7, "mtk")
    shadow_dir.mkdir(parents=True)
    shadow_file = shadow_dir / "host-a_Result_shadow_org.xls"
    current_event = "2026_1104_010000_001_db.00.JE"
    shadow_event = "2026_1104_020000_001_db.00.JE"
    for path, event in [(current_file, current_event), (shadow_file, shadow_event)]:
        book = xlwt.Workbook()
        sheet = book.add_sheet("report")
        sheet.write(0, 0, "Path")
        sheet.write(1, 0, f"/local/serial-a/{event}/db.00.JE")
        book.save(str(path))
    manifest = round_manifest_path(str(tmp_path), run_id, 7, "host-a")
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "plan_run_id": run_id,
                "host_id": "host-a",
                "artifacts": [
                    {
                        "file_key": "mtk/" + shadow_file.name,
                        "platform": "mtk",
                        "fact_round_id": 7,
                        "registerable": True,
                    }
                ],
            }
        )
    )
    current_round = datetime.now(timezone.utc).isoformat()
    assert (
        dedup_scan._sync_scan_artifacts(
            db_session,
            run_id,
            str(tmp_path),
            expected_hosts=["host-a"],
            scan_round_id=current_round,
        )
        == "1"
    )
    assert db_session.execute(
        select(PlanRunArtifact.storage_uri).where(
            PlanRunArtifact.plan_run_id == run_id,
        )
    ).scalars().all() == [str(current_file)]
    monkeypatch.setattr(
        "backend.services.plan_run_scan_scope.load_plan_run_device_serials",
        lambda *_args: ["serial-a"],
    )
    assert collect_upload_event_dir_names(db_session, run_id) == [current_event]
    assert dedup_scan._load_org_files_for_merge(
        run_id, scan_round_id=current_round, platform="mtk"
    ) == [str(current_file)]


@pytest.fixture
def shadow_env(db_session, sample_plan_run, tmp_path, monkeypatch):
    factory = sessionmaker(bind=db_session.get_bind(), expire_on_commit=False)
    settings = SchedulerSettings(stp_scan_round_shadow_enabled=True)
    queued = Mock()
    monkeypatch.setattr(shadow, "SessionLocal", factory)
    monkeypatch.setattr(shadow, "get_scheduler_settings", lambda: settings)
    monkeypatch.setattr(shadow, "resolve_shared_storage_root", lambda: str(tmp_path))
    monkeypatch.setattr(shadow, "enqueue_sync", queued)
    units = [{"host_id": h, "platform": "mtk"} for h in ["host-a", "offline"]]
    monkeypatch.setattr(shadow, "_expected_units", lambda *_args: units)
    return factory, sample_plan_run.id, tmp_path, queued, settings


def _rows(factory, run_id):
    with factory() as db:
        return facts.list_scan_rounds(db, plan_run_id=run_id, platform="mtk")


def _delivery_rounds(factory, run_id):
    return [r for r in _rows(factory, run_id) if r.kind != facts.SCAN_ROUND_KIND_PERIODIC]


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
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "plan_run_id": run_id,
                "host_id": host,
                "artifacts": [entry],
            }
        )
    )


def _observe_running_run(shadow_env):
    factory, run_id, root, queued, _settings = shadow_env
    now = datetime.now(timezone.utc)
    shadow.sweep_shadow_scan_rounds(now=now)
    periodic = _rows(factory, run_id)[0]
    for unit in periodic.expected_set:
        _receipt(root, run_id, periodic.id, unit["host_id"])
    shadow.sweep_shadow_scan_rounds(now=now + timedelta(seconds=1))
    assert _rows(factory, run_id)[0].status == facts.SCAN_ROUND_STATUS_CLOSED
    queued.reset_mock()


@pytest.mark.parametrize("status", ["SUCCESS", "PARTIAL_SUCCESS", "FAILED"])
def test_first_shadow_sweep_skips_unobserved_terminal_runs(shadow_env, monkeypatch, status):
    factory, run_id, _root, queued, _settings = shadow_env
    with factory() as db:
        db.get(shadow.PlanRun, run_id).status = status
        db.commit()
    advance = Mock(wraps=shadow._advance_run)
    monkeypatch.setattr(shadow, "_advance_run", advance)
    shadow.sweep_shadow_scan_rounds()
    assert _rows(factory, run_id) == []
    with factory() as db:
        assert facts.get_partition_mode(db, plan_run_id=run_id, platform="mtk") is None
    queued.assert_not_called()
    advance.assert_not_called()


def test_run_ending_before_first_locked_observation_is_skipped(shadow_env, monkeypatch):
    factory, run_id, _root, queued, _settings = shadow_env
    locked_run = shadow._locked_run

    def end_before_observation(db, candidate_id):
        row = locked_run(db, candidate_id)
        row.status = "SUCCESS"
        db.flush()
        return row

    monkeypatch.setattr(shadow, "_locked_run", end_before_observation)
    shadow.sweep_shadow_scan_rounds()
    assert _rows(factory, run_id) == []
    with factory() as db:
        assert facts.get_partition_mode(db, plan_run_id=run_id, platform="mtk") is None
    queued.assert_not_called()


def test_another_platforms_round_does_not_admit_unobserved_mtk_terminal(shadow_env):
    factory, run_id, _root, queued, _settings = shadow_env
    with factory() as db:
        db.get(shadow.PlanRun, run_id).status = "SUCCESS"
        facts.record_scan_round(
            db, plan_run_id=run_id, platform="unisoc", round_no=1,
            kind=facts.SCAN_ROUND_KIND_PERIODIC, expected_set=[],
            input_cutoff_at=datetime.now(timezone.utc) + timedelta(hours=2),
        )
        db.commit()
    shadow.sweep_shadow_scan_rounds()
    assert _rows(factory, run_id) == []
    with factory() as db:
        assert facts.get_partition_mode(db, plan_run_id=run_id, platform="mtk") is None
    queued.assert_not_called()


def _single_host_round(shadow_env, monkeypatch):
    factory, run_id, _root, _queued, _settings = shadow_env
    monkeypatch.setattr(shadow, "_expected_units", lambda *_args: [{"host_id": "host-a", "platform": "mtk"}])
    monkeypatch.setattr(shadow, "iter_plan_run_scan_hosts", lambda *_args: [("host-a", "ONLINE", False)])
    monkeypatch.setattr(shadow, "_shadow_capability_verified", lambda *_args: True)
    monkeypatch.setattr(shadow, "build_scan_now_payload", lambda _db, run_id, _host, **kwargs: {
        "plan_run_id": run_id, "device_serials": ["mtk-serial"], **kwargs,
    })
    now = datetime.now(timezone.utc)
    shadow.sweep_shadow_scan_rounds(now=now)
    return _rows(factory, run_id)[0], now


def test_same_round_host_is_dispatched_once_until_scan_timeout(shadow_env, monkeypatch):
    factory, run_id, root, _queued, _settings = shadow_env
    row, now = _single_host_round(shadow_env, monkeypatch)
    clock = Mock(wraps=datetime)
    clock.now.return_value = now
    monkeypatch.setattr(shadow, "datetime", clock)
    assert len(shadow.prepare_shadow_scan(run_id, row.id)) == 1
    clock.now.return_value = now + timedelta(seconds=120)
    assert shadow.prepare_shadow_scan(run_id, row.id) == []
    clock.now.return_value = now + timedelta(seconds=dedup_scan._SCAN_TASK_SAQ_TIMEOUT + 1)
    assert len(shadow.prepare_shadow_scan(run_id, row.id)) == 1
    assert _rows(factory, run_id)[0].expected_set[0]["last_dispatched_at"] == clock.now.return_value.isoformat()
    _receipt(root, run_id, row.id, "host-a")
    clock.now.return_value = now + timedelta(seconds=2 * dedup_scan._SCAN_TASK_SAQ_TIMEOUT + 2)
    assert shadow.prepare_shadow_scan(run_id, row.id) == []


def test_inflight_round_is_not_enqueued_at_every_check(shadow_env, monkeypatch):
    _factory, run_id, _root, queued, _settings = shadow_env
    row, now = _single_host_round(shadow_env, monkeypatch)
    assert len(shadow.prepare_shadow_scan(run_id, row.id)) == 1
    queued.reset_mock()
    shadow.sweep_shadow_scan_rounds(now=now + timedelta(seconds=120))
    queued.assert_not_called()
    shadow.sweep_shadow_scan_rounds(now=now + timedelta(seconds=dedup_scan._SCAN_TASK_SAQ_TIMEOUT + 1))
    queued.assert_called_once()
    assert queued.call_args.kwargs["fact_round_id"] == row.id


def test_parallel_tasks_reserve_one_dispatch_per_round_host(shadow_env, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    _factory, run_id, _root, _queued, _settings = shadow_env
    row, _now = _single_host_round(shadow_env, monkeypatch)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(shadow.prepare_shadow_scan, run_id, row.id) for _ in range(2)]
        payloads = [future.result(timeout=10) for future in futures]
    assert sum(len(batch) for batch in payloads) == 1


def test_periodic_round_uses_independent_period_and_records_mode(shadow_env):
    factory, run_id, _root, queued, settings = shadow_env
    now = datetime.now(timezone.utc)
    shadow.sweep_shadow_scan_rounds(now=now)
    first = _rows(factory, run_id)[0]
    assert first.kind == facts.SCAN_ROUND_KIND_PERIODIC
    assert [u["host_id"] for u in first.expected_set] == ["host-a", "offline"]
    assert queued.call_args.kwargs["fact_round_id"] == first.id
    with factory() as db:
        assert facts.get_partition_mode(db, plan_run_id=run_id, platform="mtk").mode == "CURRENT"
    shadow.sweep_shadow_scan_rounds(now=now + timedelta(hours=3))
    assert len(_rows(factory, run_id)) == 1
    shadow.sweep_shadow_scan_rounds(
        now=first.created_at + timedelta(seconds=settings.stp_scan_round_interval_seconds)
    )
    assert len(_rows(factory, run_id)) == 2


def test_final_round_persists_after_queue_failure_and_requeues_each_check(shadow_env):
    factory, run_id, _root, queued, _settings = shadow_env
    _observe_running_run(shadow_env)
    with factory() as db:
        run = db.get(shadow.PlanRun, run_id)
        run.status = "FAILED"
        db.commit()
    queued.side_effect = RuntimeError("queue unavailable")
    now = datetime.now(timezone.utc)
    shadow.sweep_shadow_scan_rounds(now=now)
    row = _delivery_rounds(factory, run_id)[0]
    assert row.kind == facts.SCAN_ROUND_KIND_FINAL
    assert row.status == facts.SCAN_ROUND_STATUS_PENDING
    assert all("last_dispatched_at" not in unit for unit in row.expected_set)
    queued.side_effect = None
    shadow.sweep_shadow_scan_rounds(now=now + timedelta(seconds=120))
    assert [r.id for r in _delivery_rounds(factory, run_id)] == [row.id]
    # Closed periodic rounds now enqueue G4 independently of the FINAL scan.
    assert queued.call_count == 3
    assert any(call.args[0] == "shadow_merge_task" for call in queued.call_args_list)
    assert queued.call_args.kwargs["fact_round_id"] == row.id


def test_final_partial_close_then_late_report_goes_to_persisted_supplement(shadow_env):
    factory, run_id, root, queued, _settings = shadow_env
    _observe_running_run(shadow_env)
    with factory() as db:
        db.get(shadow.PlanRun, run_id).status = "SUCCESS"
        db.commit()
    now = datetime.now(timezone.utc)
    shadow.sweep_shadow_scan_rounds(now=now)
    final = _delivery_rounds(factory, run_id)[0]
    _receipt(root, run_id, final.id, "host-a")
    shadow.sweep_shadow_scan_rounds(now=now + timedelta(minutes=1))
    shadow.sweep_shadow_scan_rounds(now=final.input_cutoff_at)
    final = _delivery_rounds(factory, run_id)[0]
    assert final.status == "CLOSED"
    assert [u["coverage"] for u in final.expected_set] == ["EMPTY", "MISSING"]
    _receipt(root, run_id, final.id, "offline", empty=False)
    shadow.sweep_shadow_scan_rounds(now=final.input_cutoff_at + timedelta(seconds=120))
    final_again, supplement = _delivery_rounds(factory, run_id)
    assert final_again.expected_set == final.expected_set
    assert supplement.kind == facts.SCAN_ROUND_KIND_SUPPLEMENT
    assert [u["host_id"] for u in supplement.expected_set if u.get("expected", True)] == ["offline"]
    assert supplement.expected_set[0]["coverage"] == "RECEIVED"
    assert supplement.expected_set[0]["reports"][0]["fact_round_id"] == final.id
    assert supplement.status == "CLOSED"
    assert round_report_dir(str(root), run_id, final.id, "mtk").is_dir()
    shadow.sweep_shadow_scan_rounds(now=final.input_cutoff_at + timedelta(seconds=240))
    assert len(_delivery_rounds(factory, run_id)) == 2


def test_disabled_shadow_has_no_database_or_queue_actions(monkeypatch):
    monkeypatch.setattr(shadow, "get_scheduler_settings", lambda: SchedulerSettings())
    monkeypatch.setattr(
        shadow, "SessionLocal", Mock(side_effect=AssertionError("DB action while disabled"))
    )
    shadow.sweep_shadow_scan_rounds()
    assert shadow.prepare_shadow_scan(1, 7) == []


def test_concurrent_sweeps_create_only_one_final_round(shadow_env):
    from concurrent.futures import ThreadPoolExecutor

    factory, run_id, _root, _queued, _settings = shadow_env
    _observe_running_run(shadow_env)
    with factory() as db:
        db.get(shadow.PlanRun, run_id).status = "SUCCESS"
        db.commit()
    now = datetime.now(timezone.utc)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(shadow.sweep_shadow_scan_rounds, now=now) for _ in range(2)]
        for future in futures:
            future.result(timeout=10)
    rows = _delivery_rounds(factory, run_id)
    assert len(rows) == 1
    assert rows[0].kind == "FINAL"


def test_supplement_retries_and_never_claims_another_closed_round_as_its_own(
    shadow_env, monkeypatch
):
    factory, run_id, root, queued, _settings = shadow_env
    units = [{"host_id": h, "platform": "mtk"} for h in ["host-a", "host-b", "host-c"]]
    monkeypatch.setattr(shadow, "_expected_units", lambda *_args: units)
    _observe_running_run(shadow_env)
    with factory() as db:
        db.get(shadow.PlanRun, run_id).status = "PARTIAL_SUCCESS"
        db.commit()
    now = datetime.now(timezone.utc)
    shadow.sweep_shadow_scan_rounds(now=now)
    final = _delivery_rounds(factory, run_id)[0]
    _receipt(root, run_id, final.id, "host-a")
    shadow.sweep_shadow_scan_rounds(now=now + timedelta(seconds=120))
    shadow.sweep_shadow_scan_rounds(now=final.input_cutoff_at)
    _receipt(root, run_id, final.id, "host-b")
    queued.side_effect = RuntimeError("queue unavailable")
    later = final.input_cutoff_at + timedelta(seconds=120)
    shadow.sweep_shadow_scan_rounds(now=later)
    final_again, supplement = _delivery_rounds(factory, run_id)
    assert supplement.status == "PENDING"
    assert [u["coverage"] for u in supplement.expected_set] == ["EMPTY", "MISSING"]
    _receipt(root, run_id, final.id, "host-c", empty=False)
    queued.side_effect = None
    shadow.sweep_shadow_scan_rounds(now=later + timedelta(seconds=120))
    assert queued.call_args.kwargs["fact_round_id"] == supplement.id
    current = _delivery_rounds(factory, run_id)[1]
    assert current.expected_set[1]["coverage"] == "MISSING"
    assert all("reports" not in u for u in final_again.expected_set if u["host_id"] != "host-a")
    # 在补充轮截止前到达旧轮命名空间，仍不能冒充当前轮；关闭后另开补充轮。
    shadow.sweep_shadow_scan_rounds(now=current.input_cutoff_at)
    shadow.sweep_shadow_scan_rounds(now=current.input_cutoff_at + timedelta(seconds=120))
    rows = _delivery_rounds(factory, run_id)
    assert len(rows) == 3
    assert rows[2].expected_set[0]["host_id"] == "host-c"
    assert rows[2].expected_set[0]["reports"][0]["fact_round_id"] == final.id
