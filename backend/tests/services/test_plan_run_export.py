"""#1080: plan_run export summary must aggregate all jobs, truncate devices only."""

from __future__ import annotations

from backend.models.enums import DeviceStatus, JobStatus
from backend.models.host import Device
from backend.models.job import JobInstance
from backend.services import plan_run_export as export_mod


def test_export_summary_uses_all_jobs_when_devices_truncated(
    db_session, sample_plan_run, sample_plan, sample_host, monkeypatch,
):
    """>500 jobs with a FAILED tail: summary reflects full run; devices truncated."""
    monkeypatch.setattr(export_mod, "_EXPORT_MAX_JOBS", 500)

    devices = [
        Device(
            serial=f"export-dev-{i:04d}",
            host_id=sample_host.id,
            status=DeviceStatus.ONLINE.value,
        )
        for i in range(501)
    ]
    db_session.add_all(devices)
    db_session.flush()

    jobs = [
        JobInstance(
            plan_run_id=sample_plan_run.id,
            plan_id=sample_plan.id,
            device_id=devices[i].id,
            host_id=sample_host.id,
            status=(
                JobStatus.FAILED.value if i == 500 else JobStatus.COMPLETED.value
            ),
            pipeline_def={"lifecycle": {"init": [], "teardown": []}},
        )
        for i in range(501)
    ]
    db_session.add_all(jobs)
    db_session.commit()

    data = export_mod.build_plan_run_export(db_session, sample_plan_run)
    summary = data["summary"]

    assert summary["truncated"] is True
    assert summary["max_jobs"] == 500
    assert summary["total_jobs"] == 501
    assert summary["status_counts"]["COMPLETED"] == 500
    assert summary["status_counts"]["FAILED"] == 1
    assert summary["status_counts"]["FAILED"] == 1  # ADR-0048：失败台数即事实（pass_rate 已删）

    assert len(data["devices"]) == 500
    assert all(d["status"] == JobStatus.COMPLETED.value for d in data["devices"])
    assert JobStatus.FAILED.value not in {d["status"] for d in data["devices"]}


def test_export_summary_reports_failed_devices_2847(
    db_session, sample_plan_run, sample_plan, sample_host,
):
    """#2847: summary 必须带 failed 键，markdown 的 "Failed devices" 才不是恒 0。

    口径 = FAILED + ABORTED（未成功的设备，与 ``report_service`` 的运行报告一致）；
    逐状态计数仍逐行可见。
    """
    statuses = [
        JobStatus.COMPLETED.value,
        JobStatus.FAILED.value,
        JobStatus.FAILED.value,
        JobStatus.ABORTED.value,
    ]
    devices = [
        Device(
            serial=f"export-2847-dev-{i}",
            host_id=sample_host.id,
            status=DeviceStatus.ONLINE.value,
        )
        for i in range(len(statuses))
    ]
    db_session.add_all(devices)
    db_session.flush()
    db_session.add_all([
        JobInstance(
            plan_run_id=sample_plan_run.id,
            plan_id=sample_plan.id,
            device_id=devices[i].id,
            host_id=sample_host.id,
            status=status,
            pipeline_def={"lifecycle": {"init": [], "teardown": []}},
        )
        for i, status in enumerate(statuses)
    ])
    db_session.commit()

    data = export_mod.build_plan_run_export(db_session, sample_plan_run)
    assert data["summary"]["failed"] == 3

    markdown = export_mod.plan_run_export_to_markdown(data)
    assert "- Failed devices: 3" in markdown
    assert "- ABORTED: 1" in markdown  # 合计之外仍逐状态可见，不藏数
