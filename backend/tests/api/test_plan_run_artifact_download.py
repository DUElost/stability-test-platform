"""ADR-0033 Phase A3 / #3013：PlanRunArtifact 下载 HTTP 面。"""

from __future__ import annotations

from backend.models.jira_run import JiraRun
from backend.models.plan_run_artifact import PlanRunArtifact


class TestPlanRunArtifactDownloadRoute:
    def test_download_ok(
        self, client, auth_headers, db_session, sample_plan_run, tmp_path, monkeypatch,
    ):
        monkeypatch.setenv("STP_AEE_NFS_ROOT", str(tmp_path))
        xls = tmp_path / "merge.xls"
        xls.write_bytes(b"fake-xls")
        art = PlanRunArtifact(
            plan_run_id=sample_plan_run.id,
            storage_uri=f"file://{xls}",
            artifact_type="merge_result_xls",
            size_bytes=8,
        )
        db_session.add(art)
        db_session.commit()

        resp = client.get(
            f"/api/v1/plan-runs/{sample_plan_run.id}/artifacts/{art.id}/download",
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        assert resp.content == b"fake-xls"

    def test_download_wrong_run_404(
        self, client, auth_headers, db_session, sample_plan_run, tmp_path, monkeypatch,
    ):
        from backend.models.plan_run import PlanRun
        from backend.models.enums import PlanRunStatus

        monkeypatch.setenv("STP_AEE_NFS_ROOT", str(tmp_path))
        xls = tmp_path / "scan.xls"
        xls.write_bytes(b"x")
        other = PlanRun(
            plan_id=sample_plan_run.plan_id,
            status=PlanRunStatus.SUCCESS.value,
            plan_snapshot=sample_plan_run.plan_snapshot,
            run_type=sample_plan_run.run_type,
        )
        db_session.add(other)
        db_session.flush()
        art = PlanRunArtifact(
            plan_run_id=sample_plan_run.id,
            storage_uri=f"file://{xls}",
            artifact_type="scan_result_xls",
        )
        db_session.add(art)
        db_session.commit()

        resp = client.get(
            f"/api/v1/plan-runs/{other.id}/artifacts/{art.id}/download",
            headers=auth_headers,
        )
        assert resp.status_code == 404


class TestJiraRunsPlanRunFilter:
    def test_filter_by_plan_run_id(
        self, client, auth_headers, db_session, sample_plan_run,
    ):
        db_session.add(
            JiraRun(
                console_run_id="con-a3-1",
                vendor="transsion",
                stage="create",
                status="SUCCESS",
                plan_run_id=sample_plan_run.id,
            )
        )
        db_session.add(
            JiraRun(
                console_run_id="con-a3-2",
                vendor="tinno",
                stage="create",
                status="SUCCESS",
                plan_run_id=None,
            )
        )
        db_session.commit()

        resp = client.get(
            "/api/v1/jira/runs",
            params={"plan_run_id": sample_plan_run.id},
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        rows = resp.json()["data"]
        assert len(rows) == 1
        assert rows[0]["console_run_id"] == "con-a3-1"
        assert rows[0]["plan_run_id"] == sample_plan_run.id
