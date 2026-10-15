"""#3723 §3.3.1：三真实 API 入口墙钟拒路径 + 显式值入库/下发一致。"""
from __future__ import annotations

from uuid import uuid4

import pytest

import backend.api.routes.plans as plans_mod
from backend.models.plan import Plan, PlanStep
from backend.models.script import Script
from backend.services.plan_dispatcher_core import build_lifecycle_from_steps


def _uniq(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:8]}"


ONBOARDED = {
    "power-cycle": {
        "min_wall_clock_seconds": {
            "powercycle_finish": lambda params: int(
                params.get("wait_device_online_seconds", 600)
            )
            + 60,
        }
    }
}


def _init_step() -> dict:
    return {
        "step_key": "init_0",
        "script_name": "check_device",
        "script_version": "1.0.0",
        "stage": "init",
        "sort_order": 0,
        "timeout_seconds": 30,
    }


def _teardown_step(**kwargs) -> dict:
    base = {
        "step_key": "teardown_pc",
        "script_name": "powercycle_finish",
        "script_version": "1.0.4",
        "stage": "teardown",
        "sort_order": 0,
        "params": {"wait_device_online_seconds": 600},
    }
    base.update(kwargs)
    return base


@pytest.fixture
def pc_script(db_session):
    row = db_session.query(Script).filter_by(
        name="powercycle_finish", version="1.0.4",
    ).first()
    if row is None:
        row = Script(
            name="powercycle_finish",
            script_type="python",
            version="1.0.4",
            nfs_path="/nfs/scripts/powercycle_finish/1.0.4",
            content_sha256="a" * 64,
            is_active=True,
            default_params={"wait_device_online_seconds": 600},
            param_schema={},
        )
        db_session.add(row)
    else:
        row.is_active = True
        row.default_params = {"wait_device_online_seconds": 600}
    db_session.commit()
    return row


@pytest.fixture
def onboarded_registry(monkeypatch):
    monkeypatch.setattr(plans_mod, "_ONBOARDED_REGISTRY", ONBOARDED)
    return ONBOARDED


@pytest.fixture(autouse=True)
def _seed_plan_defaults(db_session):
    from backend.models.project import Specialty, TestProject

    if not db_session.query(TestProject).filter_by(project_key="GENERIC").first():
        db_session.add(TestProject(
            project_key="GENERIC", display_name="通用（不限项目）",
        ))
    if not db_session.query(Specialty).filter_by(key="ops").first():
        db_session.add(Specialty(key="ops", display_name="运维", sort_order=10))
    db_session.commit()
    yield


def _create_ok_plan(client, auth_headers, timeout=660):
    name = _uniq("wc_ok")
    resp = client.post("/api/v1/plans", json={
        "name": name,
        "steps": [_init_step(), _teardown_step(timeout_seconds=timeout)],
        "project_key": "GENERIC",
        "specialty_key": "ops",
    }, headers=auth_headers)
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]


class TestWallClockCreateReject:
    def test_omit_rejected_no_partial_write(
        self, client, auth_headers, sample_script, pc_script, onboarded_registry, db_session,
    ):
        name = _uniq("wc_omit")
        before_steps = db_session.query(PlanStep).count()
        before_plans = db_session.query(Plan).count()
        resp = client.post("/api/v1/plans", json={
            "name": name,
            "steps": [_init_step(), _teardown_step()],  # omit timeout on registered
            "project_key": "GENERIC",
            "specialty_key": "ops",
        }, headers=auth_headers)
        assert resp.status_code == 422, resp.text
        detail = resp.json()["detail"]
        assert detail["code"] == "WALL_CLOCK_BELOW_ONBOARDED_BUDGET"
        assert detail["violations"][0]["timeout_seconds"] is None
        assert detail["violations"][0]["min_wall_clock_seconds"] == 660
        assert db_session.query(PlanStep).count() == before_steps
        assert db_session.query(Plan).filter(Plan.name == name).count() == 0
        assert db_session.query(Plan).count() == before_plans


class TestWallClockUpdateReject:
    def test_update_steps_omit_rejected_keeps_old_steps(
        self, client, auth_headers, sample_script, pc_script, onboarded_registry, db_session,
    ):
        plan = _create_ok_plan(client, auth_headers, timeout=660)
        resp = client.put(f"/api/v1/plans/{plan['id']}", json={
            "expected_updated_at": plan["updated_at"],
            "steps": [_init_step(), _teardown_step()],  # omit
            "project_key": "GENERIC",
            "specialty_key": "ops",
        }, headers=auth_headers)
        assert resp.status_code == 422, resp.text
        assert resp.json()["detail"]["code"] == "WALL_CLOCK_BELOW_ONBOARDED_BUDGET"
        steps = (
            db_session.query(PlanStep)
            .filter(PlanStep.plan_id == plan["id"])
            .order_by(PlanStep.stage, PlanStep.sort_order)
            .all()
        )
        assert len(steps) == 2
        teardown = [s for s in steps if s.stage == "teardown"][0]
        assert teardown.timeout_seconds == 660


class TestWallClockAppendReject:
    def test_append_chain_tail_omit_rejected(
        self, client, auth_headers, sample_script, pc_script, onboarded_registry, db_session,
    ):
        head = _create_ok_plan(client, auth_headers, timeout=660)
        before_plans = db_session.query(Plan).count()
        resp = client.post(
            f"/api/v1/plans/{head['id']}/append-chain-tail",
            json={
                "name": _uniq("wc_tail"),
                "steps": [_init_step(), _teardown_step()],  # omit
                "project_key": "GENERIC",
                "specialty_key": "ops",
                "expected_updated_at": head["updated_at"],
            },
            headers=auth_headers,
        )
        assert resp.status_code == 422, resp.text
        assert resp.json()["detail"]["code"] == "WALL_CLOCK_BELOW_ONBOARDED_BUDGET"
        assert db_session.query(Plan).count() == before_plans
        fresh = client.get(
            f"/api/v1/plans/{head['id']}", headers=auth_headers,
        ).json()["data"]
        assert fresh["next_plan_id"] is None


class TestWallClockPersistAndDispatch:
    def test_create_persists_explicit_and_dispatch_matches(
        self, client, auth_headers, sample_script, pc_script, onboarded_registry, db_session,
    ):
        plan = _create_ok_plan(client, auth_headers, timeout=660)
        row = (
            db_session.query(PlanStep)
            .filter(PlanStep.plan_id == plan["id"], PlanStep.stage == "teardown")
            .one()
        )
        assert row.timeout_seconds == 660
        plan_row = db_session.get(Plan, plan["id"])
        steps = (
            db_session.query(PlanStep)
            .filter(PlanStep.plan_id == plan["id"])
            .all()
        )
        defaults = {
            ("check_device", "1.0.0"): {},
            ("powercycle_finish", "1.0.4"): dict(pc_script.default_params or {}),
        }
        lifecycle = build_lifecycle_from_steps(plan_row, steps, defaults)
        assert lifecycle["teardown"][0]["timeout_seconds"] == 660
