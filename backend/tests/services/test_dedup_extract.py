"""Tests for ADR-0025 selective dedup upload/extract helpers."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from backend.agent.aee.event_dirs import event_dir_basename_from_path
from backend.models.device_log_event import DeviceLogEvent
from backend.models.enums import EventState
from backend.models.plan_run_artifact import PlanRunArtifact
from backend.services.dedup_extract import (
    parse_event_dir_names_from_xls,
    run_extract_sync,
)
from backend.services.device_log_event import associate_unassigned_events_to_plan_run


def test_event_dir_basename_from_path_compact():
    assert event_dir_basename_from_path(
        "/mnt/hdd/f/s/2026_0629_174940_206_db.74.ANR/__exp_main.txt"
    ) == "2026_0629_174940_206_db.74.ANR"


def test_parse_event_dir_names_from_xls_filters_foreign_serial(tmp_path):
    xlwt = pytest.importorskip("xlwt")
    xls_path = tmp_path / "org.xls"
    book = xlwt.Workbook()
    sheet = book.add_sheet("Sheet1")
    sheet.write(0, 0, "Path")
    sheet.write(0, 1, "Detail")
    sheet.write(
        1, 0,
        "/hdd/V551A_0808_MonkeyAEEinfo/0000NX2622000670/2026_0808_010203_001_db.00.ANR/__exp_main.txt",
    )
    sheet.write(1, 1, "Device_id: 0000NX2622000670\nver")
    sheet.write(
        2, 0,
        "/hdd/V551A_0808_MonkeyAEEinfo/0000NX2622000662/2026_0808_010203_002_db.00.ANR/__exp_main.txt",
    )
    sheet.write(2, 1, "Device_id: 0000NX2622000662\nver")
    book.save(str(xls_path))

    names = parse_event_dir_names_from_xls(
        xls_path, allowed_serials=["0000NX2622000670"],
    )
    assert names == {"2026_0808_010203_001_db.00.ANR"}


def test_parse_event_dir_names_from_xls_empty_serials_keeps_none(tmp_path):
    xlwt = pytest.importorskip("xlwt")
    xls_path = tmp_path / "org.xls"
    book = xlwt.Workbook()
    sheet = book.add_sheet("Sheet1")
    sheet.write(0, 0, "Path")
    sheet.write(
        1, 0,
        "/hdd/f/0000NX2622000670/2026_0808_010203_001_db.00.ANR/__exp_main.txt",
    )
    book.save(str(xls_path))

    assert parse_event_dir_names_from_xls(xls_path, allowed_serials=[]) == set()


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


def test_run_extract_sync_uses_dle_remote_paths_only(
    db_session, sample_plan_run, sample_host, sample_device, tmp_path, monkeypatch,
):
    """#213 B1/B4: extract copies only DLE remote_path dirs, not merge-xls Path names."""
    nfs = tmp_path / "nfs"
    devices = nfs / "devices" / str(sample_plan_run.id)
    jira = nfs / "jira" / str(sample_plan_run.id)
    keep = devices / "2026_0629_002306_121_db.71.JE"
    skip = devices / "2026_0603_030136_973_db.38.JE"
    keep.mkdir(parents=True)
    skip.mkdir(parents=True)
    (keep / "main.dbg").write_text("keep", encoding="utf-8")
    (skip / "main.dbg").write_text("skip", encoding="utf-8")

    # Merge artifact must exist; Path column is ignored for event discovery (B1).
    merge_xls = tmp_path / "Result_MergeFiles.xls"
    merge_xls.write_bytes(b"fake-merge-xls")

    db_session.add(PlanRunArtifact(
        plan_run_id=sample_plan_run.id,
        host_id=None,
        storage_uri=str(merge_xls),
        artifact_type="merge_result_xls",
        size_bytes=200,
    ))
    db_session.add(DeviceLogEvent(
        id=uuid4(),
        serial=sample_device.serial,
        platform="MTK",
        event_type="JE",
        detected_at=datetime.now(timezone.utc),
        state=EventState.REMOTE.value,
        local_path=str(keep),
        remote_path=str(keep),
        plan_run_id=sample_plan_run.id,
        host_id=sample_host.id,
    ))
    db_session.commit()

    monkeypatch.setenv("STP_AEE_NFS_ROOT", str(nfs))
    extracted = run_extract_sync(sample_plan_run.id)

    assert extracted == 2  # keep dir + merge xls
    assert (jira / "2026_0629_002306_121_db.71.JE" / "main.dbg").read_text(
        encoding="utf-8",
    ) == "keep"
    assert not (jira / "2026_0603_030136_973_db.38.JE").exists()
    assert (jira / "Result_MergeFiles.xls").is_file()


def test_run_extract_sync_writes_run_context_extract(
    db_session, sample_plan_run, sample_host, sample_device, tmp_path, monkeypatch,
):
    """#300 P3-4: extract 完成后 run_context.extract 记录 targets/copied/missing。"""
    from backend.models.plan_run import PlanRun

    nfs = tmp_path / "nfs"
    devices = nfs / "devices" / str(sample_plan_run.id)
    jira = nfs / "jira" / str(sample_plan_run.id)
    keep = devices / "2026_0629_002306_121_db.71.JE"
    keep.mkdir(parents=True)
    (keep / "main.dbg").write_text("keep", encoding="utf-8")

    merge_xls = tmp_path / "Result_MergeFiles.xls"
    merge_xls.write_bytes(b"fake-merge-xls")
    db_session.add(PlanRunArtifact(
        plan_run_id=sample_plan_run.id,
        host_id=None,
        storage_uri=str(merge_xls),
        artifact_type="merge_result_xls",
        size_bytes=200,
    ))
    db_session.add(DeviceLogEvent(
        id=uuid4(),
        serial=sample_device.serial,
        platform="MTK",
        event_type="JE",
        detected_at=datetime.now(timezone.utc),
        state=EventState.REMOTE.value,
        local_path=str(keep),
        remote_path=str(keep),
        plan_run_id=sample_plan_run.id,
        host_id=sample_host.id,
    ))
    # NFS 上缺失的 remote path —— 计入 missing 缺口。
    db_session.add(DeviceLogEvent(
        id=uuid4(),
        serial=sample_device.serial,
        platform="MTK",
        event_type="NE",
        detected_at=datetime.now(timezone.utc),
        state=EventState.REMOTE.value,
        local_path="/tmp/local",
        remote_path=str(devices / "missing_dir"),
        plan_run_id=sample_plan_run.id,
        host_id=sample_host.id,
    ))
    db_session.commit()

    monkeypatch.setenv("STP_AEE_NFS_ROOT", str(nfs))
    extracted = run_extract_sync(sample_plan_run.id)

    assert extracted == 2  # keep dir + merge xls
    assert (jira / "2026_0629_002306_121_db.71.JE" / "main.dbg").is_file()
    db_session.expire_all()
    pr = db_session.get(PlanRun, sample_plan_run.id)
    summary = pr.run_context["extract"]
    assert summary["targets"] == 2
    assert summary["copied"] == 1
    assert summary["missing"] == 1
    assert summary["merge_xls_copied"] == 1
    assert summary["archived"] == 1


def test_summarize_upload_states_groups_by_state(
    db_session, sample_plan_run, sample_host, sample_device,
):
    """#300 P3-2: upload_summary 按 state 分组，pending/remote 为聚合口径。"""
    from backend.services.device_log_event import summarize_upload_states

    now = datetime.now(timezone.utc)
    for state in (
        EventState.LOCAL,
        EventState.UPLOAD_PENDING,
        EventState.UPLOAD_FAILED,
        EventState.REMOTE,
        EventState.ARCHIVED,
    ):
        db_session.add(DeviceLogEvent(
            id=uuid4(),
            serial=sample_device.serial,
            platform="MTK",
            event_type="JE",
            detected_at=now,
            state=state.value,
            local_path="/tmp/x",
            plan_run_id=sample_plan_run.id,
            host_id=sample_host.id,
        ))
    db_session.commit()

    summary = summarize_upload_states(db_session, sample_plan_run.id)
    assert summary["total"] == 5
    assert summary["local"] == 1
    assert summary["pending"] == 2  # UPLOAD_PENDING + UPLOAD_FAILED
    assert summary["failed"] == 1
    assert summary["remote"] == 2   # REMOTE + ARCHIVED
    assert summary["archived"] == 1


def test_write_run_context_section_preserves_existing_keys(db_session, sample_plan_run):
    """#300: 分段写入只覆盖目标键，不动 run_context 既有内容。"""
    from backend.models.plan_run import PlanRun
    from backend.services.plan_run_context import write_run_context_section

    sample_plan_run.run_context = {"note": "keep-me"}
    db_session.commit()

    assert write_run_context_section(
        db_session, sample_plan_run.id, "upload_summary", {"total": 0},
    ) is True
    db_session.expire_all()
    pr = db_session.get(PlanRun, sample_plan_run.id)
    assert pr.run_context["note"] == "keep-me"
    assert pr.run_context["upload_summary"] == {"total": 0}


def test_run_extract_sync_rejects_non_integer_plan_run_id(db_session):
    """CodeQL #70: plan_run_id must normalize to an int before path construction."""
    from backend.core.artifact_paths import ArtifactPathError

    with pytest.raises(ArtifactPathError):
        run_extract_sync("1/../../outside")  # type: ignore[arg-type]


def test_associate_unassigned_by_job_then_extract(
    db_session,
    sample_plan_run,
    sample_job_instance,
    sample_host,
    sample_device,
    tmp_path,
    monkeypatch,
):
    """#213 B3: unassigned REMOTE event linked via job_id is extractable."""
    nfs = tmp_path / "nfs"
    event_id = uuid4()
    unassigned = nfs / "devices" / "unassigned" / str(event_id) / "2026_0803_db.02.NE"
    unassigned.mkdir(parents=True)
    (unassigned / "main.dbg").write_text("ne", encoding="utf-8")
    jira = nfs / "jira" / str(sample_plan_run.id)

    merge_xls = tmp_path / "Result_MergeFiles.xls"
    merge_xls.write_bytes(b"fake-merge-xls")
    db_session.add(PlanRunArtifact(
        plan_run_id=sample_plan_run.id,
        host_id=None,
        storage_uri=str(merge_xls),
        artifact_type="merge_result_xls",
        size_bytes=100,
    ))
    db_session.add(DeviceLogEvent(
        id=event_id,
        serial=sample_device.serial,
        platform="MTK",
        event_type="NE",
        detected_at=datetime.now(timezone.utc),
        state=EventState.REMOTE.value,
        local_path="/tmp/local",
        remote_path=str(unassigned),
        plan_run_id=None,
        host_id=sample_host.id,
        job_id=sample_job_instance.id,
    ))
    db_session.commit()

    n = associate_unassigned_events_to_plan_run(db_session, sample_plan_run.id)
    assert n == 1
    db_session.expire_all()
    row = db_session.get(DeviceLogEvent, event_id)
    assert row is not None
    assert row.plan_run_id == sample_plan_run.id

    monkeypatch.setenv("STP_AEE_NFS_ROOT", str(nfs))
    extracted = run_extract_sync(sample_plan_run.id)
    assert extracted >= 2
    assert (jira / "2026_0803_db.02.NE" / "main.dbg").read_text(encoding="utf-8") == "ne"


def test_associate_skips_unassigned_with_foreign_job_id(
    db_session,
    sample_plan_run,
    sample_plan,
    sample_host,
    sample_device,
):
    """Serial+time fallback must not steal events whose job_id is another PlanRun."""
    from backend.models.enums import JobStatus
    from backend.models.job import JobInstance
    from backend.models.plan_run import PlanRun

    now = datetime.now(timezone.utc)
    sample_plan_run.started_at = now
    # Serials come from this PlanRun's jobs; COMPLETED avoids uq_job_active_per_device.
    db_session.add(JobInstance(
        plan_run_id=sample_plan_run.id,
        plan_id=sample_plan.id,
        device_id=sample_device.id,
        host_id=sample_host.id,
        status=JobStatus.COMPLETED.value,
        pipeline_def={"lifecycle": {"init": [], "teardown": []}},
        started_at=now,
        ended_at=now,
    ))

    other_run = PlanRun(
        plan_id=sample_plan.id,
        status="RUNNING",
        failure_threshold=sample_plan.failure_threshold,
        plan_snapshot={"name": sample_plan.name, "plan_id": sample_plan.id},
        run_type="MANUAL",
        triggered_by="test-other",
        started_at=now,
    )
    db_session.add(other_run)
    db_session.flush()
    other_job = JobInstance(
        plan_run_id=other_run.id,
        plan_id=sample_plan.id,
        device_id=sample_device.id,
        host_id=sample_host.id,
        status=JobStatus.COMPLETED.value,
        pipeline_def={"lifecycle": {"init": [], "teardown": []}},
        started_at=now,
        ended_at=now,
    )
    db_session.add(other_job)
    db_session.flush()

    event_id = uuid4()
    db_session.add(DeviceLogEvent(
        id=event_id,
        serial=sample_device.serial,
        platform="MTK",
        event_type="ANR",
        detected_at=now,
        state=EventState.REMOTE.value,
        local_path="/tmp/local",
        remote_path="/tmp/remote",
        plan_run_id=None,
        host_id=sample_host.id,
        job_id=other_job.id,
    ))
    db_session.commit()

    n = associate_unassigned_events_to_plan_run(db_session, sample_plan_run.id)
    assert n == 0
    db_session.expire_all()
    row = db_session.get(DeviceLogEvent, event_id)
    assert row is not None
    assert row.plan_run_id is None


def test_associate_serial_fallback_when_job_id_null(
    db_session,
    sample_plan_run,
    sample_plan,
    sample_device,
    sample_host,
):
    """job_id IS NULL + serial in PlanRun window still associates."""
    from backend.models.enums import JobStatus
    from backend.models.job import JobInstance

    now = datetime.now(timezone.utc)
    sample_plan_run.started_at = now
    db_session.add(JobInstance(
        plan_run_id=sample_plan_run.id,
        plan_id=sample_plan.id,
        device_id=sample_device.id,
        host_id=sample_host.id,
        status=JobStatus.COMPLETED.value,
        pipeline_def={"lifecycle": {"init": [], "teardown": []}},
        started_at=now,
        ended_at=now,
    ))
    event_id = uuid4()
    db_session.add(DeviceLogEvent(
        id=event_id,
        serial=sample_device.serial,
        platform="MTK",
        event_type="ANR",
        detected_at=now,
        state=EventState.REMOTE.value,
        local_path="/tmp/local",
        remote_path="/tmp/remote",
        plan_run_id=None,
        host_id=sample_host.id,
        job_id=None,
    ))
    db_session.commit()

    n = associate_unassigned_events_to_plan_run(db_session, sample_plan_run.id)
    assert n == 1
    db_session.expire_all()
    row = db_session.get(DeviceLogEvent, event_id)
    assert row is not None
    assert row.plan_run_id == sample_plan_run.id


def test_resolve_extract_event_src_accepts_unassigned_absolute(tmp_path):
    from backend.core.artifact_paths import resolve_extract_event_src

    nfs = tmp_path / "nfs"
    event_dir = nfs / "devices" / "unassigned" / str(uuid4()) / "db.01.ANR"
    event_dir.mkdir(parents=True)
    (event_dir / "f.txt").write_text("x", encoding="utf-8")

    located = resolve_extract_event_src(
        str(event_dir),
        nfs_root=str(nfs),
        legacy_root="",
        plan_run_id=999,
    )
    assert located is not None
    src, scope = located
    assert src == event_dir.resolve()
    assert scope == (nfs / "devices").resolve()


def test_resolve_extract_event_src_rejects_traversal_without_raising(tmp_path) -> None:
    """Bad DLE remote_path must return None, not abort the whole extract."""
    from backend.core.artifact_paths import resolve_extract_event_src

    nfs = tmp_path / "nfs"
    nfs.mkdir()

    assert (
        resolve_extract_event_src(
            "../etc/passwd",
            nfs_root=str(nfs),
            legacy_root="",
            plan_run_id=1,
        )
        is None
    )
    assert (
        resolve_extract_event_src(
            "devices/1/../../etc/passwd",
            nfs_root=str(nfs),
            legacy_root="",
            plan_run_id=1,
        )
        is None
    )


def test_run_extract_sync_same_basename_only_first_row_archived(
    db_session, sample_plan_run, sample_host, sample_device, tmp_path, monkeypatch,
):
    """#386: 同 basename 多行只标「真正落进 jira 的那一行」ARCHIVED。

    其余行保持 REMOTE —— 盲目标 ARCHIVED 会让未拷贝目录永远进不了 jira
    且无法靠重跑恢复（ARCHIVED 行重跑命中 dest 已存在即跳过）。
    """
    from backend.models.plan_run import PlanRun

    nfs = tmp_path / "nfs"
    devices = nfs / "devices" / str(sample_plan_run.id)
    jira = nfs / "jira" / str(sample_plan_run.id)
    name = "2026_0629_002306_121_db.71.JE"
    primary = devices / name
    primary.mkdir(parents=True)
    (primary / "main.dbg").write_text("primary", encoding="utf-8")
    # 同 basename 的第二个来源（如 unassigned 上传后补关联）
    sibling_root = nfs / "devices" / "unassigned"
    sibling = sibling_root / name
    sibling.mkdir(parents=True)
    (sibling / "main.dbg").write_text("sibling", encoding="utf-8")

    merge_xls = tmp_path / "Result_MergeFiles.xls"
    merge_xls.write_bytes(b"fake-merge-xls")
    db_session.add(PlanRunArtifact(
        plan_run_id=sample_plan_run.id,
        host_id=None,
        storage_uri=str(merge_xls),
        artifact_type="merge_result_xls",
        size_bytes=200,
    ))
    db_session.add(DeviceLogEvent(
        id=uuid4(),
        serial=sample_device.serial,
        platform="MTK",
        event_type="JE",
        detected_at=datetime.now(timezone.utc),
        state=EventState.REMOTE.value,
        local_path=str(primary),
        remote_path=str(primary),
        plan_run_id=sample_plan_run.id,
        host_id=sample_host.id,
    ))
    db_session.add(DeviceLogEvent(
        id=uuid4(),
        serial=sample_device.serial,
        platform="MTK",
        event_type="JE",
        detected_at=datetime.now(timezone.utc),
        state=EventState.REMOTE.value,
        local_path=str(sibling),
        remote_path=str(sibling),
        plan_run_id=sample_plan_run.id,
        host_id=sample_host.id,
    ))
    db_session.commit()

    monkeypatch.setenv("STP_AEE_NFS_ROOT", str(nfs))
    extracted = run_extract_sync(sample_plan_run.id)

    assert extracted == 2  # primary dir + merge xls
    assert (jira / name / "main.dbg").read_text(encoding="utf-8") == "primary"
    db_session.expire_all()
    rows = db_session.query(DeviceLogEvent).filter(
        DeviceLogEvent.plan_run_id == sample_plan_run.id,
    ).all()
    by_content = {row.remote_path: row.state for row in rows}
    assert by_content[str(primary)] == EventState.ARCHIVED.value
    assert by_content[str(sibling)] == EventState.REMOTE.value
    pr = db_session.get(PlanRun, sample_plan_run.id)
    assert pr.run_context["extract"]["same_basename_left_remote"] == 1
