"""GET /api/v1/jobs/active-by-device — B1b occupancy bulk endpoint."""

from backend.models.enums import JobStatus
from backend.models.job import JobInstance


class TestActiveJobsByDevice:
    def test_returns_active_jobs_aligned_with_host_active_job(
        self, client, auth_headers, sample_running_job
    ):
        response = client.get("/api/v1/jobs/active-by-device", headers=auth_headers)
        assert response.status_code == 200
        rows = response.json()
        assert isinstance(rows, list)
        assert len(rows) >= 1
        match = next(row for row in rows if row["id"] == sample_running_job.id)
        assert match["device_id"] == sample_running_job.device_id
        assert match["plan_run_id"] == sample_running_job.plan_run_id
        assert match["status"] == "RUNNING"
        assert "abort_pending" in match

    def test_includes_pending_jobs(
        self, client, auth_headers, db_session, sample_plan_run, sample_plan, sample_device, sample_host
    ):
        job = JobInstance(
            plan_run_id=sample_plan_run.id,
            plan_id=sample_plan.id,
            device_id=sample_device.id,
            host_id=sample_host.id,
            status=JobStatus.PENDING.value,
            pipeline_def={"lifecycle": {"init": [], "teardown": []}},
        )
        db_session.add(job)
        db_session.commit()

        response = client.get("/api/v1/jobs/active-by-device", headers=auth_headers)
        assert response.status_code == 200
        by_id = {row["id"]: row for row in response.json()}
        assert job.id in by_id
        assert by_id[job.id]["status"] == "PENDING"

    def test_empty_when_no_active_jobs(self, client, auth_headers):
        response = client.get("/api/v1/jobs/active-by-device", headers=auth_headers)
        assert response.status_code == 200
        assert response.json() == []

    def test_requires_auth(self, client):
        response = client.get("/api/v1/jobs/active-by-device")
        assert response.status_code in (401, 403)
