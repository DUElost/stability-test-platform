"""Tests for ADR-0025 selective dedup upload/extract helpers."""

from __future__ import annotations

from pathlib import Path

from datetime import datetime, timezone

import pytest

from backend.agent.aee.event_dirs import event_dir_basename_from_path
from backend.models.job import JobInstance, JobLogSignal
from backend.models.plan_run_artifact import PlanRunArtifact
from backend.services.dedup_extract import (
    collect_upload_event_dir_names,
    parse_event_dir_names_from_xls,
    run_extract_sync,
)


def test_event_dir_basename_from_path_compact():
    assert event_dir_basename_from_path(
        "/mnt/hdd/f/s/2026_0629_174940_206_db.74.ANR/__exp_main.txt"
    ) == "2026_0629_174940_206_db.74.ANR"


def test_parse_event_dir_names_from_xls_reads_path_column(tmp_path):
    xlwt = pytest.importorskip("xlwt")
    xls_path = tmp_path / "merge.xls"
    book = xlwt.Workbook()
    sheet = book.add_sheet("Sheet1")
    sheet.write(0, 0, "Path")
    sheet.write(0, 1, "ExpClass")
    sheet.write(
        1, 0,
        "/mnt/hdd/f/s/2026_0629_002306_121_db.71.JE/__exp_main.txt",
    )
    sheet.write(2, 0, "/mnt/hdd/f/s/2026_0629_004958_550_db.72.JE/main.dbg")
    sheet.write(3, 0, "/data/aee_exp/db.legacy")
    book.save(str(xls_path))

    names = parse_event_dir_names_from_xls(xls_path)
    assert names == {
        "2026_0629_002306_121_db.71.JE",
        "2026_0629_004958_550_db.72.JE",
    }


def test_collect_upload_event_dir_names_unions_signal_and_scan(
    db_session, sample_plan_run, sample_plan, sample_device, sample_host, tmp_path, monkeypatch,
):
    job = JobInstance(
        plan_run_id=sample_plan_run.id,
        plan_id=sample_plan.id,
        device_id=sample_device.id,
        host_id=sample_host.id,
        status="RUNNING",
        pipeline_def={"lifecycle": {"init": [], "teardown": []}},
    )
    db_session.add(job)
    db_session.flush()

    now = datetime.now(timezone.utc)
    db_session.add(JobLogSignal(
        job_id=job.id,
        host_id=sample_host.id,
        seq_no=1,
        category="AEE",
        source="reconciler",
        device_serial=sample_device.serial,
        path_on_device="/data/aee_exp/db.71.JE",
        detected_at=now,
        received_at=now,
        extra={
            "nfs_path": (
                "/mnt/hdd/aee/f/serial/2026_0629_002306_121_db.71.JE"
            ),
        },
    ))

    xlwt = pytest.importorskip("xlwt")
    scan_xls = tmp_path / "scan.xls"
    book = xlwt.Workbook()
    sheet = book.add_sheet("Sheet1")
    sheet.write(0, 0, "Path")
    sheet.write(
        1, 0,
        "/mnt/hdd/aee/f/serial/2026_0629_004958_550_db.72.JE/__exp_main.txt",
    )
    book.save(str(scan_xls))

    db_session.add(PlanRunArtifact(
        plan_run_id=sample_plan_run.id,
        host_id=sample_host.id,
        storage_uri=str(scan_xls),
        artifact_type="scan_result_xls",
        size_bytes=100,
    ))
    db_session.commit()

    names = collect_upload_event_dir_names(db_session, sample_plan_run.id)
    assert names == [
        "2026_0629_002306_121_db.71.JE",
        "2026_0629_004958_550_db.72.JE",
    ]


def test_run_extract_sync_copies_only_merge_referenced_dirs(
    db_session, sample_plan_run, tmp_path, monkeypatch,
):
    nfs = tmp_path / "nfs"
    devices = nfs / "devices" / str(sample_plan_run.id)
    jira = nfs / "jira" / str(sample_plan_run.id)
    keep = devices / "2026_0629_002306_121_db.71.JE"
    skip = devices / "2026_0603_030136_973_db.38.JE"
    keep.mkdir(parents=True)
    skip.mkdir(parents=True)
    (keep / "main.dbg").write_text("keep", encoding="utf-8")
    (skip / "main.dbg").write_text("skip", encoding="utf-8")

    xlwt = pytest.importorskip("xlwt")
    merge_xls = tmp_path / "Result_MergeFiles.xls"
    book = xlwt.Workbook()
    sheet = book.add_sheet("Sheet1")
    sheet.write(0, 0, "Path")
    sheet.write(1, 0, str(keep / "__exp_main.txt"))
    book.save(str(merge_xls))

    db_session.add(PlanRunArtifact(
        plan_run_id=sample_plan_run.id,
        host_id=None,
        storage_uri=str(merge_xls),
        artifact_type="merge_result_xls",
        size_bytes=200,
    ))
    db_session.commit()

    monkeypatch.setenv("STP_AEE_NFS_ROOT", str(nfs))
    extracted = run_extract_sync(sample_plan_run.id)

    assert extracted == 2
    assert (jira / "2026_0629_002306_121_db.71.JE" / "main.dbg").read_text(encoding="utf-8") == "keep"
    assert not (jira / "2026_0603_030136_973_db.38.JE").exists()
    assert (jira / "Result_MergeFiles.xls").is_file()
