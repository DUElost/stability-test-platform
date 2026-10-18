"""#3730 U5 v1.7 §3.6.5 / §4.1：三入口接入形态校验（真 API）。"""
from __future__ import annotations

from uuid import uuid4

import pytest

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
        ("powercycle_setup", "1.2.9", {}),
        ("powercycle_disarm", "1.0.3", {
            "mode": "disarm", "wait_device_online_seconds": 600,
        }),
        ("powercycle_disarm", "1.0.1", {
            "mode": "disarm", "wait_device_online_seconds": 600,
        }),
        ("powercycle_finish", "1.1.0", {
            "collect_attempts": 3, "collect_retry_wait_seconds": 120,
        }),
        ("powercycle_finish", "1.0.8", {}),
        ("check_device", "1.0.0", {}),
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


def _finish_only_steps(*, finish_ver="1.1.0", finish_timeout=420):
    return [
        {
            "step_key": "init_setup",
            "script_name": "powercycle_setup",
            "script_version": "1.2.9",
            "stage": "init",
            "sort_order": 0,
            "timeout_seconds": 900,
            "enabled": True,
        },
        {
            "step_key": "td_finish",
            "script_name": "powercycle_finish",
            "script_version": finish_ver,
            "stage": "teardown",
            "sort_order": 0,
            "timeout_seconds": finish_timeout,
            "enabled": True,
            "params": {"collect_attempts": 3, "collect_retry_wait_seconds": 120},
        },
    ]


def _good_steps():
    return [
        {
            "step_key": "init_setup",
            "script_name": "powercycle_setup",
            "script_version": "1.2.9",
            "stage": "init",
            "sort_order": 0,
            "timeout_seconds": 900,
            "enabled": True,
        },
        {
            "step_key": "td_disarm",
            "script_name": "powercycle_disarm",
            "script_version": "1.0.3",
            "stage": "teardown",
            "sort_order": 0,
            "timeout_seconds": 660,
            "enabled": True,
            "params": {"mode": "disarm", "wait_device_online_seconds": 600},
        },
        {
            "step_key": "td_finish",
            "script_name": "powercycle_finish",
            "script_version": "1.1.0",
            "stage": "teardown",
            "sort_order": 1,
            "timeout_seconds": 420,
            "enabled": True,
            "params": {"collect_attempts": 3, "collect_retry_wait_seconds": 120},
        },
    ]


def _assert_teardown_invalid(resp, reason: str):
    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    assert detail["code"] == "ONBOARDED_TEARDOWN_INVALID"
    reasons = [v["reason"] for v in detail["violations"]]
    assert reason in reasons, detail
    for v in detail["violations"]:
        assert "family" in v and "teardown_steps" in v
        assert "footprint_step_keys" in v
        assert "required_disarm_script" in v


@pytest.mark.usefixtures("scripts")
class TestOnboardedTeardownShapeAPI:
    def test_post_plans_finish_only_rejected_no_partial_write(
        self, client, auth_headers, db_session,
    ):
        from backend.models.plan import Plan, PlanStep

        name = _uniq("u5-td-post")
        before = db_session.query(Plan).filter(Plan.name == name).count()
        resp = client.post(
            "/api/v1/plans",
            headers=auth_headers,
            json={
                "name": name,
                "project_key": "GENERIC",
                "specialty_key": "ops",
                "steps": _finish_only_steps(),
            },
        )
        _assert_teardown_invalid(resp, "disarm_missing")
        assert db_session.query(Plan).filter(Plan.name == name).count() == before
        assert db_session.query(PlanStep).join(Plan).filter(Plan.name == name).count() == 0

    def test_put_plans_finish_only_keeps_original_steps(
        self, client, auth_headers, db_session,
    ):
        from backend.models.plan import PlanStep

        create = client.post(
            "/api/v1/plans",
            headers=auth_headers,
            json={
                "name": _uniq("u5-td-put"),
                "project_key": "GENERIC",
                "specialty_key": "ops",
                "steps": _good_steps(),
            },
        )
        assert create.status_code in (200, 201), create.text
        body = create.json()["data"]
        plan_id = body["id"]
        original = [
            (s.script_name, s.script_version)
            for s in db_session.query(PlanStep).filter_by(plan_id=plan_id).order_by(PlanStep.sort_order)
        ]
        resp = client.put(
            f"/api/v1/plans/{plan_id}",
            headers=auth_headers,
            json={
                "steps": _finish_only_steps(),
                "expected_updated_at": body["updated_at"],
            },
        )
        _assert_teardown_invalid(resp, "disarm_missing")
        db_session.expire_all()
        after = [
            (s.script_name, s.script_version)
            for s in db_session.query(PlanStep).filter_by(plan_id=plan_id).order_by(PlanStep.sort_order)
        ]
        assert after == original

    def test_append_chain_tail_finish_only_no_new_tail(
        self, client, auth_headers, db_session,
    ):
        from backend.models.plan import Plan

        create = client.post(
            "/api/v1/plans",
            headers=auth_headers,
            json={
                "name": _uniq("u5-td-chain-head"),
                "project_key": "GENERIC",
                "specialty_key": "ops",
                "steps": _good_steps(),
            },
        )
        assert create.status_code in (200, 201), create.text
        plan_id = create.json()["data"]["id"]
        before = db_session.query(Plan).count()
        resp = client.post(
            f"/api/v1/plans/{plan_id}/append-chain-tail",
            headers=auth_headers,
            json={
                "name": _uniq("u5-td-chain-tail"),
                "steps": _finish_only_steps(),
            },
        )
        _assert_teardown_invalid(resp, "disarm_missing")
        assert db_session.query(Plan).count() == before

    def test_reject_disarm_disabled(self, client, auth_headers):
        steps = _good_steps()
        for s in steps:
            if s["script_name"] == "powercycle_disarm":
                s["enabled"] = False
        resp = client.post(
            "/api/v1/plans",
            headers=auth_headers,
            json={
                "name": _uniq("u5-td-disabled"),
                "project_key": "GENERIC",
                "specialty_key": "ops",
                "steps": steps,
            },
        )
        _assert_teardown_invalid(resp, "disarm_disabled")

    def test_reject_disarm_below_min(self, client, auth_headers):
        steps = _good_steps()
        for s in steps:
            if s["script_name"] == "powercycle_disarm":
                s["script_version"] = "1.0.1"
        resp = client.post(
            "/api/v1/plans",
            headers=auth_headers,
            json={
                "name": _uniq("u5-td-below"),
                "project_key": "GENERIC",
                "specialty_key": "ops",
                "steps": steps,
            },
        )
        _assert_teardown_invalid(resp, "disarm_below_min_version")

    def test_reject_finish_below_min(self, client, auth_headers):
        steps = _good_steps()
        for s in steps:
            if s["script_name"] == "powercycle_finish":
                s["script_version"] = "1.0.8"
                s["timeout_seconds"] = 420
        resp = client.post(
            "/api/v1/plans",
            headers=auth_headers,
            json={
                "name": _uniq("u5-td-old-finish"),
                "project_key": "GENERIC",
                "specialty_key": "ops",
                "steps": steps,
            },
        )
        _assert_teardown_invalid(resp, "finish_below_min_version")

    def test_reject_no_powercycle_teardown_at_all(self, client, auth_headers):
        steps = [
            {
                "step_key": "init_setup",
                "script_name": "powercycle_setup",
                "script_version": "1.2.9",
                "stage": "init",
                "sort_order": 0,
                "timeout_seconds": 900,
                "enabled": True,
            },
            {
                "step_key": "td_check",
                "script_name": "check_device",
                "script_version": "1.0.0",
                "stage": "teardown",
                "sort_order": 0,
                "timeout_seconds": 30,
                "enabled": True,
            },
        ]
        resp = client.post(
            "/api/v1/plans",
            headers=auth_headers,
            json={
                "name": _uniq("u5-td-empty"),
                "project_key": "GENERIC",
                "specialty_key": "ops",
                "steps": steps,
            },
        )
        _assert_teardown_invalid(resp, "disarm_missing")

    def test_positive_split_ok(self, client, auth_headers):
        resp = client.post(
            "/api/v1/plans",
            headers=auth_headers,
            json={
                "name": _uniq("u5-td-ok"),
                "project_key": "GENERIC",
                "specialty_key": "ops",
                "steps": _good_steps(),
            },
        )
        assert resp.status_code in (200, 201), resp.text

    def test_collect_only_no_footprint_ok(self, client, auth_headers):
        # 无 powercycle_setup 足迹：只收取计划不受 §3.6.5 约束（仍需合法 lifecycle.init）
        resp = client.post(
            "/api/v1/plans",
            headers=auth_headers,
            json={
                "name": _uniq("u5-td-collect"),
                "project_key": "GENERIC",
                "specialty_key": "ops",
                "steps": [
                    {
                        "step_key": "init_check",
                        "script_name": "check_device",
                        "script_version": "1.0.0",
                        "stage": "init",
                        "sort_order": 0,
                        "timeout_seconds": 30,
                        "enabled": True,
                    },
                    {
                        "step_key": "td_finish",
                        "script_name": "powercycle_finish",
                        "script_version": "1.1.0",
                        "stage": "teardown",
                        "sort_order": 0,
                        "timeout_seconds": 420,
                        "enabled": True,
                        "params": {
                            "collect_attempts": 3,
                            "collect_retry_wait_seconds": 120,
                        },
                    },
                ],
            },
        )
        assert resp.status_code in (200, 201), resp.text

    def test_co_violation_returns_teardown_code_not_wall_clock(
        self, client, auth_headers,
    ):
        # finish-only（disarm_missing）且 finish 墙钟不足 → 必须返回本节码
        resp = client.post(
            "/api/v1/plans",
            headers=auth_headers,
            json={
                "name": _uniq("u5-td-co"),
                "project_key": "GENERIC",
                "specialty_key": "ops",
                "steps": _finish_only_steps(finish_timeout=1),
            },
        )
        _assert_teardown_invalid(resp, "disarm_missing")
        detail = resp.json()["detail"]
        assert detail["code"] != "WALL_CLOCK_BELOW_ONBOARDED_BUDGET"
