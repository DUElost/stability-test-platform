"""Regression: read / sensitive GET APIs require authentication.

Covers endpoints that were previously anonymous and now use
``get_current_active_user``.  Unauthenticated requests must receive 401;
authenticated requests must not be rejected as unauthenticated (401).

``/metrics`` stays public for Prometheus — see ``TestPublicMetricsEndpoints``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

import pytest
from fastapi.testclient import TestClient

from backend.core.security import create_refresh_token


@dataclass(frozen=True)
class ReadEndpointCase:
    """Single HTTP call shape for auth regression."""

    id: str
    method: str
    path: str
    json: Optional[dict[str, Any]] = None
    params: Optional[dict[str, Any]] = None


def _invoke(
    client: TestClient,
    case: ReadEndpointCase,
    *,
    headers: Optional[dict[str, str]] = None,
) -> Any:
    kwargs: dict[str, Any] = {}
    if headers is not None:
        kwargs["headers"] = headers
    if case.params is not None:
        kwargs["params"] = case.params
    if case.json is not None:
        kwargs["json"] = case.json

    method = case.method.upper()
    if method == "GET":
        return client.get(case.path, **kwargs)
    if method == "POST":
        return client.post(case.path, **kwargs)
    raise ValueError(f"unsupported method: {case.method}")


# ---------------------------------------------------------------------------
# Registry — keep in sync when adding new read routes with get_current_active_user
# ---------------------------------------------------------------------------

PROTECTED_READ_ENDPOINTS: tuple[ReadEndpointCase, ...] = (
    # plans
    ReadEndpointCase("plans_list", "GET", "/api/v1/plans"),
    ReadEndpointCase("plans_get", "GET", "/api/v1/plans/1"),
    ReadEndpointCase(
        "plans_run_preview",
        "POST",
        "/api/v1/plans/1/run/preview",
        json={"device_ids": [1]},
    ),
    # plan-runs (aggregation + detail)
    ReadEndpointCase("plan_runs_list", "GET", "/api/v1/plan-runs"),
    ReadEndpointCase("plan_runs_get", "GET", "/api/v1/plan-runs/1"),
    ReadEndpointCase("plan_runs_jobs", "GET", "/api/v1/plan-runs/1/jobs"),
    ReadEndpointCase("plan_runs_chain", "GET", "/api/v1/plan-runs/1/chain"),
    ReadEndpointCase("plan_runs_timeline", "GET", "/api/v1/plan-runs/1/timeline"),
    ReadEndpointCase("plan_runs_events", "GET", "/api/v1/plan-runs/1/events"),
    ReadEndpointCase("plan_runs_devices", "GET", "/api/v1/plan-runs/1/devices"),
    ReadEndpointCase(
        "plan_runs_watcher_summary",
        "GET",
        "/api/v1/plan-runs/1/watcher-summary",
    ),
    ReadEndpointCase("plan_runs_summary", "GET", "/api/v1/plan-runs/1/summary"),
    ReadEndpointCase(
        "plan_runs_job_artifacts",
        "GET",
        "/api/v1/plan-runs/1/jobs/1/artifacts",
    ),
    ReadEndpointCase(
        "plan_runs_artifact_download",
        "GET",
        "/api/v1/plan-runs/1/jobs/1/artifacts/1/download",
    ),
    # job runs (legacy report/steps)
    ReadEndpointCase("runs_report", "GET", "/api/v1/runs/1/report"),
    ReadEndpointCase(
        "runs_report_export",
        "GET",
        "/api/v1/runs/1/report/export",
        params={"format": "markdown"},
    ),
    ReadEndpointCase("runs_report_cached", "GET", "/api/v1/runs/1/report/cached"),
    ReadEndpointCase(
        "runs_jira_draft_cached",
        "GET",
        "/api/v1/runs/1/jira-draft/cached",
    ),
    ReadEndpointCase("runs_steps_list", "GET", "/api/v1/runs/1/steps"),
    ReadEndpointCase("runs_step_get", "GET", "/api/v1/runs/1/steps/1"),
    ReadEndpointCase(
        "runs_artifact_download",
        "GET",
        "/api/v1/runs/1/artifacts/1/download",
    ),
    # stats
    ReadEndpointCase("stats_activity", "GET", "/api/v1/stats/activity"),
    ReadEndpointCase(
        "stats_device_metrics",
        "GET",
        "/api/v1/stats/device/1/metrics",
    ),
    ReadEndpointCase(
        "stats_dashboard_summary",
        "GET",
        "/api/v1/stats/dashboard-summary",
    ),
    ReadEndpointCase(
        "stats_completion_trend",
        "GET",
        "/api/v1/stats/completion-trend",
    ),
    # schedules
    ReadEndpointCase("schedules_list", "GET", "/api/v1/schedules"),
    ReadEndpointCase("schedules_get", "GET", "/api/v1/schedules/1"),
    # results
    ReadEndpointCase(
        "results_summary",
        "GET",
        "/api/v1/results/summary",
        params={"limit": 5},
    ),
    # pipeline templates
    ReadEndpointCase("pipeline_templates_list", "GET", "/api/v1/pipeline/templates"),
    ReadEndpointCase(
        "pipeline_template_get",
        "GET",
        "/api/v1/pipeline/templates/smoke",
    ),
)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestPublicMetricsEndpoints:
    """Prometheus scrape endpoints intentionally stay unauthenticated."""

    def test_metrics_exposition_public(self, client):
        resp = client.get("/metrics")
        assert resp.status_code == 200

    def test_metrics_health_public(self, client):
        resp = client.get("/metrics/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "healthy"


class TestReadApiRequiresAuth:
    @pytest.mark.parametrize(
        "case",
        PROTECTED_READ_ENDPOINTS,
        ids=[c.id for c in PROTECTED_READ_ENDPOINTS],
    )
    def test_unauthenticated_returns_401(self, client, case: ReadEndpointCase):
        resp = _invoke(client, case)
        assert resp.status_code == 401, (
            f"{case.method} {case.path} expected 401, got {resp.status_code}: {resp.text[:200]}"
        )

    @pytest.mark.parametrize(
        "case",
        PROTECTED_READ_ENDPOINTS,
        ids=[c.id for c in PROTECTED_READ_ENDPOINTS],
    )
    def test_authenticated_not_rejected_as_unauthorized(
        self, client, auth_headers, case: ReadEndpointCase,
    ):
        resp = _invoke(client, case, headers=auth_headers)
        assert resp.status_code != 401, (
            f"{case.method} {case.path} must not 401 when authenticated "
            f"(got {resp.status_code})"
        )

    def test_invalid_bearer_token_returns_401(self, client):
        headers = {"Authorization": "Bearer not-a-valid-jwt"}
        resp = client.get("/api/v1/plans", headers=headers)
        assert resp.status_code == 401

    def test_refresh_token_cannot_access_read_api(self, client):
        """ADR-0024: refresh token must not satisfy get_current_active_user."""
        refresh = create_refresh_token(data={"sub": "testuser"})
        headers = {"Authorization": f"Bearer {refresh}"}
        resp = client.get("/api/v1/plan-runs", headers=headers)
        assert resp.status_code == 401


class TestReadApiAuthWithSeededData:
    """Smoke: authenticated reads against real rows return success (not 401)."""

    @pytest.fixture
    def seeded_plan_run(self, db_session, sample_plan):
        from datetime import datetime, timezone

        from backend.models.plan_run import PlanRun

        pr = PlanRun(
            plan_id=sample_plan.id,
            status="RUNNING",
            failure_threshold=sample_plan.failure_threshold,
            plan_snapshot={"plan_id": sample_plan.id, "name": sample_plan.name},
            run_type="MANUAL",
            triggered_by="testuser",
            started_at=datetime.now(timezone.utc),
        )
        db_session.add(pr)
        db_session.commit()
        db_session.refresh(pr)
        return pr

    def test_list_plans_with_auth(self, client, auth_headers, sample_plan):
        resp = client.get("/api/v1/plans", headers=auth_headers)
        assert resp.status_code == 200
        assert any(p["id"] == sample_plan.id for p in resp.json()["data"])

    def test_get_plan_run_with_auth(self, client, auth_headers, seeded_plan_run):
        resp = client.get(
            f"/api/v1/plan-runs/{seeded_plan_run.id}",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["id"] == seeded_plan_run.id

    def test_stats_dashboard_with_auth(self, client, auth_headers):
        resp = client.get("/api/v1/stats/dashboard-summary", headers=auth_headers)
        assert resp.status_code == 200
        assert "hosts" in resp.json()

    def test_results_summary_with_auth(self, client, auth_headers):
        resp = client.get("/api/v1/results/summary", headers=auth_headers)
        assert resp.status_code == 200
        assert "runs_by_status" in resp.json()

    def test_pipeline_templates_with_auth(self, client, auth_headers):
        resp = client.get("/api/v1/pipeline/templates", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)

        names = {item["name"] for item in data}
        assert "monkey_aee_patrol" in names
        assert "aimonkey" not in names
        assert "monkey_aee" not in names
        assert "monkey_aee_lifecycle" not in names
        assert "monkey_aee_init" not in names
        assert "monkey_aee_teardown" not in names

    def test_hidden_pipeline_template_alias_returns_404(self, client, auth_headers):
        resp = client.get("/api/v1/pipeline/templates/monkey_aee", headers=auth_headers)
        assert resp.status_code == 404
