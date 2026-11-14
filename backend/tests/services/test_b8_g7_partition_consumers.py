"""B8 G7：分区消费方适配（ADR-0062 D13/D16、#3886）。

覆盖本单验收的六组行为：

1. 混合 PlanRun 的 extract 只消费「现行」分区；纯先去重返回 -3；无模式存量不变；
2. 归档完成判据：先去重按交付状态（READY / READY_WITH_GAPS 完成，重试中未就绪），
   现行分区保留 merge + extract，两类独立；
3. 下载：DLE 代表证据按 G6 发布记录定位；交付统计导出按登记下载；跨 run / 越根拒绝；
4. 留存：I4 保护位让所属 run 推迟回收；单 run 清理不动链级证据；
5. 统计：发生数按 occurrence_key 去重（路径变化 / 上传 / 溢出不改计数，未选也计入）；
6. 状态与展示：分区视图、能力目录、上传汇总按平台。

隔离：测试库由 conftest 的 testcontainers 提供，文件系统用 tmp_path。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import sessionmaker

from backend.core.settings.scheduler import SchedulerSettings
from backend.models.device_log_event import DeviceLogEvent
from backend.models.host import Device, Host
from backend.models.job import JobInstance
from backend.models.plan import Plan
from backend.models.plan_run import PlanRun
from backend.models.plan_run_artifact import PlanRunArtifact
from backend.scheduler.cron_scheduler import purge_run_storage_dirs
from backend.services import dedup_extract
from backend.services import delivery_download
from backend.services import evidence_publication
from backend.services import issue_fact_store as facts
from backend.services import log_observation
from backend.services import partition_scope
from backend.services import shadow_delivery
from backend.services.device_log_event import summarize_upload_states
from backend.services.device_log_event_download import build_device_log_event_download_response
from backend.api.routes.dedup import (
    download_delivery_evidence,
    download_delivery_export,
    get_scan_status,
    trigger_extract,
)
from backend.services.delivery_download import DeliveryDownloadError
from backend.services.plan_run_result_views import build_plan_run_log_events
from backend.services.plan_run_watcher_summary import _load_deduped_aee_events
from backend.storage_families import EVIDENCE_FAMILY, RUN_FAMILIES

FOLDER = "MLD-NX3-P535AA-HONOR-NX3-16-1031V78_1031_MonkeyAEEinfo"
EVENT_A = "2026_1031_120000_123_db.74.ANR"
EVENT_B = "2026_1031_120001_456_db.74.JE"
SERIAL = "AZ6Q123456"


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    settings = SchedulerSettings(stp_scan_round_shadow_enabled=True)
    monkeypatch.setattr(shadow_delivery, "get_scheduler_settings", lambda: settings)
    monkeypatch.setattr(evidence_publication, "get_scheduler_settings", lambda: settings)


@pytest.fixture
def store(tmp_path, monkeypatch, db_session):
    factory = sessionmaker(bind=db_session.get_bind())
    monkeypatch.setattr(shadow_delivery, "SessionLocal", factory)
    monkeypatch.setattr(evidence_publication, "SessionLocal", factory)
    monkeypatch.setattr(shadow_delivery, "resolve_shared_storage_root", lambda: str(tmp_path))
    monkeypatch.setattr(delivery_download, "resolve_shared_storage_root", lambda: str(tmp_path))
    from backend.core import artifact_paths, storage_root
    for module in (artifact_paths, storage_root):
        monkeypatch.setattr(module, "resolve_shared_storage_root", lambda: str(tmp_path))
        monkeypatch.setattr(
            module, "resolve_control_plane_storage_root", lambda: str(tmp_path / "cp"),
        )
        monkeypatch.setattr(module, "resolve_legacy_log_storage_root", lambda: "")
    (tmp_path / "cp").mkdir(exist_ok=True)
    return tmp_path


def _plan_run(db, *, run_id=None, status="SUCCESS", root_plan_run_id=None):
    plan = Plan(name=f"g7-{uuid4()}", created_by="pytest")
    db.add(plan)
    db.flush()
    run = PlanRun(
        id=run_id, plan_id=plan.id, status=status, plan_snapshot={},
        run_type="MANUAL", triggered_by="pytest", root_plan_run_id=root_plan_run_id,
        started_at=datetime.now(timezone.utc) - timedelta(hours=2),
        ended_at=datetime.now(timezone.utc) - timedelta(hours=1),
    )
    db.add(run)
    db.flush()
    return run


def _host(db):
    host_id = f"g7-{uuid4().hex[:8]}"
    now = datetime.now(timezone.utc)
    db.add(Host(
        id=host_id, hostname=host_id, name=host_id, ip="192.0.2.31",
        ip_address="192.0.2.31", status="ONLINE", last_heartbeat=now,
    ))
    db.flush()
    return host_id


def _device(db, host_id, *, platform, serial=None):
    serial = serial or f"SN-{uuid4().hex[:8]}"
    device = Device(
        serial=serial, host_id=host_id, platform=platform, status="ONLINE",
        model="test", tags=[],
    )
    db.add(device)
    db.flush()
    return device


def _job(db, run, host_id, device):
    job = JobInstance(
        plan_run_id=run.id, plan_id=run.plan_id, device_id=device.id,
        host_id=host_id, status="COMPLETED", pipeline_def={},
    )
    db.add(job)
    db.flush()
    return job


def _mode(db, run_id, *, platform, mode):
    facts.record_partition_mode(db, plan_run_id=run_id, platform=platform, mode=mode)
    db.commit()


def _dle(
    db, *, run_id, host_id, platform="MTK", serial=SERIAL, state="LOCAL",
    remote_path=None, local_path=None, event=EVENT_A, job_id=None,
    occurrence_key=None, event_type="JE",
):
    dle = DeviceLogEvent(
        id=uuid4(), serial=serial, platform=platform, event_type=event_type,
        state=state,
        local_path=local_path or f"/data/aee/{FOLDER}/{serial}/aee_exp/{event}",
        remote_path=remote_path, host_id=host_id, plan_run_id=run_id,
        job_id=job_id, detected_at=datetime.now(timezone.utc),
        occurrence_key=occurrence_key,
    )
    db.add(dle)
    db.commit()
    return dle


def _event_dir(root: Path, *, run_id, name, platform="mtk"):
    path = root / "devices" / str(run_id) / name / "aee_exp" / name
    path.mkdir(parents=True)
    (path / "data.txt").write_text("payload", encoding="utf-8")
    return path


def _merge_artifact(db, root: Path, *, run_id, platform):
    path = root / "dedup" / str(run_id) / "merge" / platform / "Result_MergeFiles.xls"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("xls", encoding="utf-8")
    db.add(PlanRunArtifact(
        plan_run_id=run_id, host_id=None, storage_uri=str(path),
        artifact_type="merge_result_xls", size_bytes=3,
    ))
    db.commit()


def _closed_final(db, run_id, *, coverage=("RECEIVED",), merge="OK", platform="mtk"):
    expected = [
        {"host_id": f"host-{index}", "platform": "MTK", "expected": True, "coverage": value}
        for index, value in enumerate(coverage)
    ]
    row = facts.record_scan_round(
        db, plan_run_id=run_id, platform=platform, round_no=1,
        kind=facts.SCAN_ROUND_KIND_FINAL, expected_set=expected,
        input_cutoff_at=datetime.now(timezone.utc),
    )
    facts.set_scan_round_status(
        db, round_id=row.id, status=facts.SCAN_ROUND_STATUS_CLOSED,
    )
    facts.record_scan_round_merge(db, round_id=row.id, status=merge, summary={})
    db.commit()
    return row


def _selection(db, *, run_id, dle_id, platform="mtk"):
    signature = facts.record_issue_signature(
        db, signature_raw="db.fatal.00.JE", normalized_key=f"k-{uuid4()}",
        platform=platform, event_type="JE", seen_at=datetime.now(timezone.utc),
    )
    facts.record_representative_selection(
        db, plan_run_id=run_id, platform=platform,
        signature_id=signature.id, selection_status=facts.SELECTION_SELECTED,
        dle_id=dle_id,
    )
    db.commit()
    return signature


# ── 1. extract 只作用于现行分区 ────────────────────────────────────────────────


def test_mixed_run_extract_only_current_partition(db_session, store):
    run = _plan_run(db_session)
    host_id = _host(db_session)
    mtk = _device(db_session, host_id, platform="MTK")
    unisoc = _device(db_session, host_id, platform="UNISOC")
    _job(db_session, run, host_id, mtk)
    _job(db_session, run, host_id, unisoc)
    _mode(db_session, run.id, platform="mtk", mode=facts.PARTITION_MODE_DEDUP_FIRST)
    _mode(db_session, run.id, platform="unisoc", mode=facts.PARTITION_MODE_CURRENT)
    mtk_dir = _event_dir(store, run_id=run.id, name=EVENT_A)
    unisoc_dir = _event_dir(store, run_id=run.id, name=EVENT_B)
    _dle(db_session, run_id=run.id, host_id=host_id, platform="MTK", state="REMOTE",
         remote_path=str(mtk_dir), event=EVENT_A)
    _dle(db_session, run_id=run.id, host_id=host_id, platform="UNISOC", state="REMOTE",
         remote_path=str(unisoc_dir), event=EVENT_B, event_type="UNIVIEW")
    _merge_artifact(db_session, store, run_id=run.id, platform="mtk")
    _merge_artifact(db_session, store, run_id=run.id, platform="unisoc")

    copied = dedup_extract.run_extract_sync(run.id)

    # 1 个事件目录 + 1 份现行分区的 merge 报表；先去重侧两者都不进 extract。
    assert copied == 2
    jira = store / "jira" / str(run.id)
    assert (jira / EVENT_B).is_dir()
    assert not (jira / EVENT_A).exists()
    assert (jira / "merge" / "unisoc" / "Result_MergeFiles.xls").is_file()
    assert not (jira / "merge" / "mtk").exists()

    db_session.expire_all()
    refreshed = db_session.get(PlanRun, run.id)
    assert refreshed.run_context["extract"]["targets"] == 1
    assert refreshed.run_context["extract"]["copied"] == 1


def test_pure_dedup_first_extract_returns_no_current(db_session, store):
    run = _plan_run(db_session)
    host_id = _host(db_session)
    mtk = _device(db_session, host_id, platform="MTK")
    _job(db_session, run, host_id, mtk)
    _mode(db_session, run.id, platform="mtk", mode=facts.PARTITION_MODE_DEDUP_FIRST)
    _dle(db_session, run_id=run.id, host_id=host_id, platform="MTK", state="REMOTE",
         remote_path=str(_event_dir(store, run_id=run.id, name=EVENT_A)))

    assert dedup_extract.run_extract_sync(run.id) == -3
    assert not (store / "jira" / str(run.id)).exists()


def test_legacy_run_without_mode_keeps_both_platforms(db_session, store):
    run = _plan_run(db_session)
    host_id = _host(db_session)
    mtk_dir = _event_dir(store, run_id=run.id, name=EVENT_A)
    unisoc_dir = _event_dir(store, run_id=run.id, name=EVENT_B)
    _dle(db_session, run_id=run.id, host_id=host_id, platform="MTK", state="REMOTE",
         remote_path=str(mtk_dir))
    _dle(db_session, run_id=run.id, host_id=host_id, platform="UNISOC", state="REMOTE",
         remote_path=str(unisoc_dir), event=EVENT_B, event_type="UNIVIEW")
    _merge_artifact(db_session, store, run_id=run.id, platform="mtk")
    _merge_artifact(db_session, store, run_id=run.id, platform="unisoc")

    # 两个平台各 1 个事件目录 + 各 1 份 merge 报表。
    assert dedup_extract.run_extract_sync(run.id) == 4


# ── 2. 归档完成判据 ────────────────────────────────────────────────────────────


def test_dedup_first_ready_completes_without_extract(db_session, store):
    run = _plan_run(db_session)
    _mode(db_session, run.id, platform="mtk", mode=facts.PARTITION_MODE_DEDUP_FIRST)
    _closed_final(db_session, run.id)

    decision = shadow_delivery.current_delivery_decision(
        db_session, plan_run_id=run.id, platform="mtk",
    )
    assert decision.status == shadow_delivery.DELIVERY_STATUS_READY
    assert partition_scope.archive_complete(db_session, run.id) is True
    # 没有 merge 产物、没有 extract 记录 —— 判据确实不依赖它们。
    assert (db_session.get(PlanRun, run.id).run_context or {}) == {}


def test_dedup_first_ready_with_gaps_completes(db_session, store):
    run = _plan_run(db_session)
    _mode(db_session, run.id, platform="mtk", mode=facts.PARTITION_MODE_DEDUP_FIRST)
    _closed_final(db_session, run.id, coverage=("RECEIVED", "MISSING"))

    decision = shadow_delivery.current_delivery_decision(
        db_session, plan_run_id=run.id, platform="mtk",
    )
    assert decision.status == shadow_delivery.DELIVERY_STATUS_READY_WITH_GAPS
    assert partition_scope.archive_complete(db_session, run.id) is True


def test_dedup_first_retrying_representative_stays_not_ready(db_session, store):
    run = _plan_run(db_session)
    _mode(db_session, run.id, platform="mtk", mode=facts.PARTITION_MODE_DEDUP_FIRST)
    _closed_final(db_session, run.id)
    host_id = _host(db_session)
    dle = _dle(db_session, run_id=run.id, host_id=host_id, platform="MTK", state="REMOTE",
               remote_path="/tmp/x")
    _selection(db_session, run_id=run.id, dle_id=dle.id)

    decision = shadow_delivery.current_delivery_decision(
        db_session, plan_run_id=run.id, platform="mtk",
    )
    assert decision.status == shadow_delivery.DELIVERY_STATUS_NOT_READY
    assert decision.reason == shadow_delivery.REASON_REPRESENTATIVE_RETRYING
    assert partition_scope.archive_complete(db_session, run.id) is False


def test_mixed_run_requires_both_sides(db_session, store):
    run = _plan_run(db_session)
    host_id = _host(db_session)
    unisoc = _device(db_session, host_id, platform="UNISOC")
    _job(db_session, run, host_id, unisoc)
    _mode(db_session, run.id, platform="mtk", mode=facts.PARTITION_MODE_DEDUP_FIRST)
    _closed_final(db_session, run.id)

    # 先去重侧已就绪，但现行侧没有 merge 产物 / extract 记录。
    assert partition_scope.archive_complete(db_session, run.id) is False
    _merge_artifact(db_session, store, run_id=run.id, platform="unisoc")
    run.run_context = {"extract": {"targets": 0, "copied": 0}}
    db_session.commit()
    assert partition_scope.archive_complete(db_session, run.id) is True


def test_legacy_run_keeps_merge_and_extract_rule(db_session, store):
    run = _plan_run(db_session)
    assert partition_scope.archive_complete(db_session, run.id) is False
    _merge_artifact(db_session, store, run_id=run.id, platform="mtk")
    assert partition_scope.archive_complete(db_session, run.id) is False
    run.run_context = {"extract": {"targets": 1, "copied": 1}}
    db_session.commit()
    assert partition_scope.archive_complete(db_session, run.id) is True


# ── 3. 下载：按登记定位、跨 run 拒绝 ──────────────────────────────────────────


def _verified_publication(
    db_session, store, *, run_id, root_run_id, dle, folder=FOLDER, event=EVENT_A,
):
    rel = f"evidence/{root_run_id}_{folder}/{dle.serial}/{event}"
    target = store / rel
    target.mkdir(parents=True)
    (target / "data.txt").write_text("evidence", encoding="utf-8")
    row = facts.record_evidence_publication(
        db_session, plan_run_id=run_id, platform="mtk", dle_id=dle.id,
        root_plan_run_id=root_run_id, source_kind=facts.PUBLICATION_SOURCE_LOCAL,
        source_path=dle.local_path, collection_folder=folder,
        naming_rule_version="aee-folder-name/v1", destination_rel_path=rel,
    )
    facts.update_evidence_publication_state(
        db_session, row=row, state=facts.PUBLICATION_STATE_VERIFIED,
        checksum="a" * 64, size_bytes=8,
    )
    db_session.commit()
    return row


def test_dle_download_uses_verified_publication(db_session, store):
    run = _plan_run(db_session)
    host_id = _host(db_session)
    dle = _dle(db_session, run_id=run.id, host_id=host_id, platform="MTK", state="REMOTE",
               remote_path=None)
    publication = _verified_publication(
        db_session, store, run_id=run.id, root_run_id=run.id, dle=dle,
    )

    response = build_device_log_event_download_response(
        db_session, plan_run_id=run.id, event_id=dle.id,
    )
    assert isinstance(response, FileResponse)
    assert Path(response.path).is_file()

    with pytest.raises(HTTPException) as other_run:
        build_device_log_event_download_response(
            db_session, plan_run_id=run.id + 1, event_id=dle.id,
        )
    assert other_run.value.status_code == 404
    assert publication.id


def test_evidence_download_rejects_cross_root(db_session, store):
    run = _plan_run(db_session)
    host_id = _host(db_session)
    dle = _dle(db_session, run_id=run.id, host_id=host_id, platform="MTK", state="REMOTE")
    publication = _verified_publication(
        db_session, store, run_id=run.id, root_run_id=run.id, dle=dle,
    )
    # 登记行指向另一个链根：契约层拒绝，不按物理目录放行。
    publication.root_plan_run_id = run.id + 99
    db_session.commit()
    with pytest.raises(DeliveryDownloadError) as exc:
        delivery_download.resolve_evidence_directory(
            db_session, plan_run_id=run.id, publication_id=publication.id,
        )
    assert exc.value.status_code == 400


def test_evidence_download_route_returns_zip(db_session, store):
    run = _plan_run(db_session)
    host_id = _host(db_session)
    dle = _dle(db_session, run_id=run.id, host_id=host_id, platform="MTK", state="REMOTE")
    publication = _verified_publication(
        db_session, store, run_id=run.id, root_run_id=run.id, dle=dle,
    )

    response = download_delivery_evidence(run.id, publication.id, db_session, None)
    assert Path(response.path).is_file()


def test_log_event_flags_delivery_evidence(db_session, store):
    run = _plan_run(db_session)
    host_id = _host(db_session)
    dle = _dle(db_session, run_id=run.id, host_id=host_id, platform="MTK", state="PRUNED")
    plain = _dle(db_session, run_id=run.id, host_id=host_id, platform="MTK",
                 state="PRUNED", event=EVENT_B)
    publication = _verified_publication(
        db_session, store, run_id=run.id, root_run_id=run.id, dle=dle,
    )

    payload = build_plan_run_log_events(db_session, run.id, limit=10)
    flags = {row.id: row.delivery_evidence for row in payload.items}
    assert flags[str(dle.id)] is True
    assert flags[str(plain.id)] is False
    assert publication.state == facts.PUBLICATION_STATE_VERIFIED


def test_delivery_export_download_and_cross_run(db_session, store):
    run = _plan_run(db_session)
    _mode(db_session, run.id, platform="mtk", mode=facts.PARTITION_MODE_DEDUP_FIRST)
    _closed_final(db_session, run.id)
    version = shadow_delivery.register_delivery_version(
        db_session, plan_run_id=run.id, platform="mtk", root=str(store),
    )
    db_session.commit()
    assert version is not None

    path = delivery_download.resolve_delivery_export(
        db_session, plan_run_id=run.id, platform="mtk", version_no=version.version_no,
    )
    assert path.is_file()
    with pytest.raises(DeliveryDownloadError) as exc:
        delivery_download.resolve_delivery_export(
            db_session, plan_run_id=run.id + 1, platform="mtk",
            version_no=version.version_no,
        )
    assert exc.value.status_code == 404


def test_unverified_publication_does_not_serve_evidence(db_session, store):
    run = _plan_run(db_session)
    host_id = _host(db_session)
    dle = _dle(db_session, run_id=run.id, host_id=host_id, platform="MTK", state="LOCAL",
               remote_path=None)
    rel = f"evidence/{run.id}_{FOLDER}/{dle.serial}/{EVENT_A}"
    (store / rel).mkdir(parents=True)
    row = facts.record_evidence_publication(
        db_session, plan_run_id=run.id, platform="mtk", dle_id=dle.id,
        root_plan_run_id=run.id, source_kind=facts.PUBLICATION_SOURCE_LOCAL,
        source_path=dle.local_path, collection_folder=FOLDER,
        naming_rule_version="aee-folder-name/v1", destination_rel_path=rel,
    )
    db_session.commit()
    with pytest.raises(DeliveryDownloadError) as exc:
        delivery_download.resolve_evidence_directory(
            db_session, plan_run_id=run.id, publication_id=row.id,
        )
    assert exc.value.status_code == 409


# ── 4. 留存：I4 保护与链级证据 ────────────────────────────────────────────────


def test_retention_defers_run_with_retrying_publication(
    db_session, store, monkeypatch, scheduler_env,
):
    run = _plan_run(db_session)
    host_id = _host(db_session)
    dle = _dle(db_session, run_id=run.id, host_id=host_id, platform="MTK", state="LOCAL")
    facts.record_evidence_publication(
        db_session, plan_run_id=run.id, platform="mtk", dle_id=dle.id,
        root_plan_run_id=run.id, source_kind=facts.PUBLICATION_SOURCE_SPILL,
        source_path="/tmp/spill", collection_folder=FOLDER,
        naming_rule_version="aee-folder-name/v1",
        destination_rel_path=f"evidence/{run.id}_{FOLDER}/{SERIAL}/{EVENT_A}",
    )
    db_session.commit()
    spill_dir = store / "devices" / str(run.id) / EVENT_A
    spill_dir.mkdir(parents=True)
    (spill_dir / "f").write_text("spill", encoding="utf-8")

    from backend.scheduler import cron_scheduler

    monkeypatch.setattr(cron_scheduler, "SessionLocal", lambda: db_session)
    scheduler_env("PLAN_RUN_RETENTION_DAYS", "0")
    cron_scheduler.run_retention_cleanup()

    db_session.expire_all()
    assert db_session.get(PlanRun, run.id) is not None
    assert spill_dir.is_dir()


def test_single_run_purge_keeps_chain_evidence_and_other_run(db_session, store):
    root = _plan_run(db_session)
    child = _plan_run(db_session, root_plan_run_id=root.id)
    other = _plan_run(db_session)
    evidence = store / f"evidence/{root.id}_{FOLDER}/{SERIAL}/{EVENT_A}"
    evidence.mkdir(parents=True)
    (evidence / "e.txt").write_text("e", encoding="utf-8")
    other_evidence = store / f"evidence/{other.id}_{FOLDER}/{SERIAL}/{EVENT_A}"
    other_evidence.mkdir(parents=True)
    (other_evidence / "o.txt").write_text("o", encoding="utf-8")
    child_devices = store / "devices" / str(child.id) / EVENT_B
    child_devices.mkdir(parents=True)
    (child_devices / "f").write_text("x", encoding="utf-8")

    assert purge_run_storage_dirs([child.id]) == set()
    assert not child_devices.exists()
    assert (evidence / "e.txt").read_text(encoding="utf-8") == "e"
    assert (other_evidence / "o.txt").read_text(encoding="utf-8") == "o"
    assert EVIDENCE_FAMILY not in RUN_FAMILIES


# ── 5. 统计口径（#3886）──────────────────────────────────────────────────────


def test_occurrence_count_is_stable_across_path_changes(db_session, store):
    run = _plan_run(db_session)
    host_id = _host(db_session)
    device = _device(db_session, host_id, platform="MTK")
    job = _job(db_session, run, host_id, device)
    key = "occ-" + uuid4().hex
    first = _dle(db_session, run_id=run.id, host_id=host_id, job_id=job.id,
                 state="LOCAL", occurrence_key=key)
    _dle(db_session, run_id=run.id, host_id=host_id, job_id=job.id,
         state="REMOTE", occurrence_key=key, remote_path="/tmp/other-path")
    # 未选中的另一条发生（只有 local_path）也必须计入。
    _dle(db_session, run_id=run.id, host_id=host_id, job_id=job.id,
         state="LOCAL", occurrence_key="occ-" + uuid4().hex, event=EVENT_B)

    summary = log_observation.aggregate_risk_summary(db_session, [job.id])
    assert summary["counts"]["by_type"]["JE"] == 2

    first.remote_path = "/tmp/changed-after-upload"
    db_session.commit()
    summary = log_observation.aggregate_risk_summary(db_session, [job.id])
    assert summary["counts"]["by_type"]["JE"] == 2


def test_dashboard_includes_dle_without_signal(db_session, store):
    run = _plan_run(db_session)
    host_id = _host(db_session)
    device = _device(db_session, host_id, platform="MTK")
    job = _job(db_session, run, host_id, device)
    _dle(db_session, run_id=run.id, host_id=host_id, job_id=job.id,
         state="LOCAL", event_type="JE")

    events = _load_deduped_aee_events(
        db_session, job_ids=[job.id],
        cur_start=datetime.now(timezone.utc) - timedelta(hours=3),
        window_end=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    assert len(events) == 1
    assert events[0]["subtype"] == "JE"


def test_delivery_scope_spans_two_software_versions(db_session, store):
    run = _plan_run(db_session)
    _mode(db_session, run.id, platform="mtk", mode=facts.PARTITION_MODE_DEDUP_FIRST)
    _closed_final(db_session, run.id)
    host_id = _host(db_session)
    second_folder = "MLD-NX3-P535AA-HONOR-NX3-16-1103V80_1103_MonkeyAEEinfo"
    first = _dle(db_session, run_id=run.id, host_id=host_id, platform="MTK",
                 state="REMOTE", event=EVENT_A)
    second = _dle(db_session, run_id=run.id, host_id=host_id, platform="MTK",
                  state="REMOTE", event=EVENT_B, serial="AZ6Q654321")
    _selection(db_session, run_id=run.id, dle_id=first.id)
    _selection(db_session, run_id=run.id, dle_id=second.id)
    _verified_publication(db_session, store, run_id=run.id, root_run_id=run.id, dle=first)
    second_publication = _verified_publication(
        db_session, store, run_id=run.id, root_run_id=run.id, dle=second,
        folder=second_folder, event=EVENT_B,
    )
    shadow_delivery.register_delivery_version(
        db_session, plan_run_id=run.id, platform="mtk", root=str(store),
    )
    db_session.commit()

    view = partition_scope.delivery_view(db_session, plan_run_id=run.id, platform="mtk")
    assert set(view["primary_folders"]) == {
        f"{run.id}_{FOLDER}", f"{run.id}_{second_folder}",
    }
    path = delivery_download.resolve_evidence_directory(
        db_session, plan_run_id=run.id, publication_id=second_publication.id,
    )
    assert path.is_dir()


def test_upload_summary_and_partition_views_split_by_platform(db_session, store):
    run = _plan_run(db_session)
    host_id = _host(db_session)
    mtk = _device(db_session, host_id, platform="MTK")
    unisoc = _device(db_session, host_id, platform="UNISOC")
    _job(db_session, run, host_id, mtk)
    _job(db_session, run, host_id, unisoc)
    _mode(db_session, run.id, platform="mtk", mode=facts.PARTITION_MODE_DEDUP_FIRST)
    _mode(db_session, run.id, platform="unisoc", mode=facts.PARTITION_MODE_CURRENT)
    _dle(db_session, run_id=run.id, host_id=host_id, platform="MTK", state="LOCAL")
    _dle(db_session, run_id=run.id, host_id=host_id, platform="UNISOC", state="REMOTE",
         remote_path="/tmp/u", event=EVENT_B, event_type="UNIVIEW")

    summary = summarize_upload_states(db_session, run.id)
    assert summary["by_platform"]["mtk"]["local"] == 1
    assert summary["by_platform"]["unisoc"]["remote"] == 1

    views = partition_scope.partition_views(db_session, run.id)
    by_platform = {row["platform"]: row for row in views}
    assert by_platform["mtk"]["mode"] == facts.PARTITION_MODE_DEDUP_FIRST
    assert by_platform["mtk"]["capabilities"]["extract"] is False
    assert by_platform["unisoc"]["capabilities"]["extract"] is True


def test_status_route_exposes_partition_view(db_session, store):
    run = _plan_run(db_session, status="RUNNING")
    host_id = _host(db_session)
    mtk = _device(db_session, host_id, platform="MTK")
    unisoc = _device(db_session, host_id, platform="UNISOC")
    _job(db_session, run, host_id, mtk)
    _job(db_session, run, host_id, unisoc)
    _mode(db_session, run.id, platform="mtk", mode=facts.PARTITION_MODE_DEDUP_FIRST)
    _mode(db_session, run.id, platform="unisoc", mode=facts.PARTITION_MODE_CURRENT)

    payload = get_scan_status(run.id, db_session, None)
    partitions = {row.platform: row for row in payload.data.partitions}
    assert partitions["mtk"].mode == facts.PARTITION_MODE_DEDUP_FIRST
    assert partitions["unisoc"].capabilities["extract"] is True
    assert partitions["mtk"].capabilities["extract"] is False


def test_extract_route_maps_dedup_only_to_409(db_session, store):
    run = _plan_run(db_session)
    host_id = _host(db_session)
    mtk = _device(db_session, host_id, platform="MTK")
    _job(db_session, run, host_id, mtk)
    _mode(db_session, run.id, platform="mtk", mode=facts.PARTITION_MODE_DEDUP_FIRST)

    with pytest.raises(HTTPException) as exc:
        trigger_extract(run.id, None)
    assert exc.value.status_code == 409


def test_delivery_export_route_downloads_registered_version(db_session, store):
    run = _plan_run(db_session)
    _mode(db_session, run.id, platform="mtk", mode=facts.PARTITION_MODE_DEDUP_FIRST)
    _closed_final(db_session, run.id)
    version = shadow_delivery.register_delivery_version(
        db_session, plan_run_id=run.id, platform="mtk", root=str(store),
    )
    db_session.commit()

    response = download_delivery_export(
        run.id, "mtk", version.version_no, db_session, None,
    )
    assert Path(response.path).is_file()


def test_capabilities_and_partition_targets(db_session, store):
    run = _plan_run(db_session)
    host_id = _host(db_session)
    mtk = _device(db_session, host_id, platform="MTK")
    _job(db_session, run, host_id, mtk)
    _mode(db_session, run.id, platform="mtk", mode=facts.PARTITION_MODE_DEDUP_FIRST)

    assert partition_scope.run_capabilities(db_session, run.id)["extract"] is False
    from backend.services.dedup_scan import run_merge_for_partitions_sync

    # 纯先去重：自动 merge 不跑现行工具，也不当失败。
    assert run_merge_for_partitions_sync(run.id) == "ok"
