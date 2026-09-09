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
    assert summary["pass_rate"] == round(500 / 501, 4)

    assert len(data["devices"]) == 500
    assert all(d["status"] == JobStatus.COMPLETED.value for d in data["devices"])
    assert JobStatus.FAILED.value not in {d["status"] for d in data["devices"]}
