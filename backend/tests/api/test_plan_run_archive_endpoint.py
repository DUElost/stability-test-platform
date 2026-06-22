from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from backend.models.enums import HostStatus, JobStatus
from backend.models.host import Host
from backend.models.job import JobInstance


class TestArchivePlanRunLogsEndpoint:
    """POST /api/v1/plan-runs/{run_id}/archive (ADR-0025 S2)"""

    def test_unauthenticated_returns_401(self, client):
        resp = client.post("/api/v1/plan-runs/1/archive")
        assert resp.status_code == 401

    def test_unknown_run_returns_404(self, client, auth_headers):
        resp = client.post("/api/v1/plan-runs/999999/archive", headers=auth_headers)
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"].lower()

    def test_run_with_no_jobs_returns_400(
        self, client, auth_headers, db_session, sample_plan_run
    ):
        # sample_plan_run has no JobInstance associated
        resp = client.post(
            f"/api/v1/plan-runs/{sample_plan_run.id}/archive", headers=auth_headers
        )
        assert resp.status_code == 400
        assert "no jobs" in resp.json()["detail"].lower()

    def test_online_host_triggered(
        self, client, auth_headers, db_session, sample_plan_run, sample_plan, sample_device, sample_host
    ):
        # sample_host is ONLINE (id=101); link a job to it
        job = JobInstance(
            plan_run_id=sample_plan_run.id,
            plan_id=sample_plan.id,
            device_id=sample_device.id,
            host_id=sample_host.id,
            status=JobStatus.RUNNING.value,
            pipeline_def={"lifecycle": {"init": [], "teardown": []}},
        )
        db_session.add(job)
        db_session.commit()

        with patch(
            "backend.realtime.socketio_server.emit_agent_control",
            new=AsyncMock(),
        ) as mock_emit:
            resp = client.post(
                f"/api/v1/plan-runs/{sample_plan_run.id}/archive", headers=auth_headers
            )

        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["archived_now"] is True
        assert str(sample_host.id) in data["triggered_hosts"]
        assert data["skipped_offline"] == []
        assert mock_emit.await_count == 2
        commands = [c[0][1] for c in mock_emit.call_args_list]
        assert "archive_now" in commands
        assert "scan_now" in commands

    def test_offline_host_skipped(
        self, client, auth_headers, db_session, sample_plan_run, sample_plan, sample_device, sample_offline_host
    ):
        # sample_offline_host is OFFLINE (id=102)
        job = JobInstance(
            plan_run_id=sample_plan_run.id,
            plan_id=sample_plan.id,
            device_id=sample_device.id,
            host_id=sample_offline_host.id,
            status=JobStatus.PENDING.value,
            pipeline_def={"lifecycle": {"init": [], "teardown": []}},
        )
        db_session.add(job)
        db_session.commit()

        with patch(
            "backend.realtime.socketio_server.emit_agent_control",
            new=AsyncMock(),
        ) as mock_emit:
            resp = client.post(
                f"/api/v1/plan-runs/{sample_plan_run.id}/archive", headers=auth_headers
            )

        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["triggered_hosts"] == []
        assert len(data["skipped_offline"]) == 1
        assert data["skipped_offline"][0]["host_id"] == sample_offline_host.id
        mock_emit.assert_not_awaited()

    def test_mixed_hosts_online_and_offline(
        self, client, auth_headers, db_session, sample_plan_run, sample_plan, sample_device
    ):
        online_host = Host(id="h-online", hostname="h-online", name="h-online", status=HostStatus.ONLINE.value)
        offline_host = Host(id="h-offline", hostname="h-offline", name="h-offline", status=HostStatus.OFFLINE.value)
        db_session.add_all([online_host, offline_host])
        db_session.commit()

        for h in (online_host, offline_host):
            db_session.add(JobInstance(
                plan_run_id=sample_plan_run.id,
                plan_id=sample_plan.id,
                device_id=sample_device.id,
                host_id=h.id,
                status=JobStatus.PENDING.value,
                pipeline_def={"lifecycle": {"init": [], "teardown": []}},
            ))
        db_session.commit()

        with patch(
            "backend.realtime.socketio_server.emit_agent_control",
            new=AsyncMock(),
        ) as mock_emit:
            resp = client.post(
                f"/api/v1/plan-runs/{sample_plan_run.id}/archive", headers=auth_headers
            )

        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert str(online_host.id) in data["triggered_hosts"]
        assert len(data["skipped_offline"]) == 1
        assert mock_emit.await_count == 2
