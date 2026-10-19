"""#3730 U5：真实 ONBOARDED 接入后，Plan API 三入口墙钟按有效参数生效。"""
from __future__ import annotations

from uuid import uuid4

import pytest

from backend.agent.contracts import device_return
from backend.models.script import Script


def _uniq(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:8]}"


@pytest.fixture(autouse=True)
def _seed(db_session):
    from backend.models.project import Specialty, TestProject

    if not db_session.query(TestProject).filter_by(project_key="GENERIC").first():
        db_session.add(TestProject(
            project_key="GENERIC", display_name="通用（不限项目）",
        ))
    if not db_session.query(Specialty).filter_by(key="ops").first():
        db_session.add(Specialty(key="ops", display_name="运维", sort_order=10))
    db_session.commit()


@pytest.fixture
def scripts(db_session):
    rows = []
    for name, ver, defaults in (
        ("check_device", "1.0.0", {}),
        ("powercycle_disarm", "1.0.5", {
            "mode": "disarm", "wait_device_online_seconds": 600,
        }),
        ("powercycle_finish", "1.1.0", {
            "collect_attempts": 3, "collect_retry_wait_seconds": 120,
        }),
    ):
        row = db_session.query(Script).filter_by(name=name, version=ver).first()
        if row is None:
            row = Script(
                name=name,
                script_type="python",
                version=ver,
                nfs_path=f"/nfs/scripts/{name}/{ver}",
                content_sha256="a" * 64,
                is_active=True,
                default_params=defaults,
                param_schema={},
            )
            db_session.add(row)
        else:
            row.is_active = True
            row.default_params = defaults
        rows.append(row)
    db_session.commit()
    return rows


def _steps(*, disarm_timeout, finish_timeout, disarm_params=None, finish_params=None):
    return [
        {
            "step_key": "init_0",
            "script_name": "check_device",
            "script_version": "1.0.0",
            "stage": "init",
            "sort_order": 0,
            "timeout_seconds": 30,
        },
        {
            "step_key": "teardown_disarm",
            "script_name": "powercycle_disarm",
            "script_version": "1.0.5",
            "stage": "teardown",
            "sort_order": 0,
            "timeout_seconds": disarm_timeout,
            "params": disarm_params or {"mode": "disarm", "wait_device_online_seconds": 600},
        },
        {
            "step_key": "teardown_finish",
            "script_name": "powercycle_finish",
            "script_version": "1.1.0",
            "stage": "teardown",
            "sort_order": 1,
            "timeout_seconds": finish_timeout,
            "params": finish_params or {
                "collect_attempts": 3, "collect_retry_wait_seconds": 120,
            },
        },
    ]


@pytest.mark.usefixtures("scripts")
class TestRealOnboardedWallClock:
    def test_registry_is_live_power_cycle(self):
        assert "power-cycle" in device_return.ONBOARDED

    def test_create_rejects_below_budget(self, client, auth_headers, scripts):
        payload = {
            "name": _uniq("u5-wall"),
            "project_key": "GENERIC",
            "specialty_key": "ops",
            "steps": _steps(disarm_timeout=100, finish_timeout=420),
        }
        resp = client.post("/api/v1/plans", headers=auth_headers, json=payload)
        assert resp.status_code == 422, resp.text
        detail = resp.json()["detail"]
        assert detail["code"] == "WALL_CLOCK_BELOW_ONBOARDED_BUDGET"
        assert detail["violations"][0]["script_name"] == "powercycle_disarm"
        assert detail["violations"][0]["min_wall_clock_seconds"] == 660

    def test_create_accepts_explicit_budgets(self, client, auth_headers, scripts):
        payload = {
            "name": _uniq("u5-wall-ok"),
            "project_key": "GENERIC",
            "specialty_key": "ops",
            "steps": _steps(disarm_timeout=660, finish_timeout=420),
        }
        resp = client.post("/api/v1/plans", headers=auth_headers, json=payload)
        assert resp.status_code in (200, 201), resp.text

    def test_finish_budget_scales_with_effective_params(self, client, auth_headers, scripts):
        # collect_attempts=5 * 120 + 60 = 660；timeout 420 应拒
        payload = {
            "name": _uniq("u5-finish-scale"),
            "project_key": "GENERIC",
            "specialty_key": "ops",
            "steps": _steps(
                disarm_timeout=660,
                finish_timeout=420,
                finish_params={"collect_attempts": 5, "collect_retry_wait_seconds": 120},
            ),
        }
        resp = client.post("/api/v1/plans", headers=auth_headers, json=payload)
        assert resp.status_code == 422, resp.text
        detail = resp.json()["detail"]
        assert detail["code"] == "WALL_CLOCK_BELOW_ONBOARDED_BUDGET"
        viol = [v for v in detail["violations"] if v["script_name"] == "powercycle_finish"]
        assert viol and viol[0]["min_wall_clock_seconds"] == 660
