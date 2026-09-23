"""Unit tests for expected_scripts_for_run."""

from __future__ import annotations

from backend.models.plan import Plan, PlanStep
from backend.models.plan_run import PlanRun
from backend.models.script import Script
from backend.services.precheck.scripts import expected_scripts_for_run


def test_expected_scripts_intersects_snapshot_with_active_scripts(db_session):
    plan = Plan(name="script-plan", patrol_interval_seconds=60)
    active = Script(
        name="check_device",
        script_type="python",
        version="1.0.0",
        nfs_path="/scripts/check_device/v1.0.0/check_device.py",
        content_sha256="aa" * 32,
        is_active=True,
        default_params={},
    )
    inactive = Script(
        name="monkey_launch",
        script_type="python",
        version="2.0.0",
        nfs_path="/scripts/monkey_launch/v2.0.0/monkey_launch.py",
        content_sha256="bb" * 32,
        is_active=False,
        default_params={},
    )
    db_session.add_all([plan, active, inactive])
    db_session.commit()
    db_session.add(
        PlanStep(
            plan_id=plan.id,
            step_key="init_check",
            script_name="check_device",
            script_version="1.0.0",
            stage="init",
            sort_order=0,
            timeout_seconds=30,
            retry=0,
        )
    )
    db_session.commit()

    pr = PlanRun(
        plan_id=plan.id,
        status="RUNNING",
        plan_snapshot={
            "plan_id": plan.id,
            "steps": [
                {
                    "script_name": "check_device",
                    "script_version": "1.0.0",
                },
                {
                    "script_name": "monkey_launch",
                    "script_version": "2.0.0",
                },
            ],
        },
        run_type="MANUAL",
        triggered_by="test",
    )
    db_session.add(pr)
    db_session.commit()

    scripts = expected_scripts_for_run(pr, db_session)

    assert scripts == [
        {
            "name": "check_device",
            "version": "1.0.0",
            "sha256": "aa" * 32,
            "nfs_path": "/scripts/check_device/v1.0.0/check_device.py",
        }
    ]


def test_expected_scripts_empty_when_snapshot_has_no_steps(db_session):
    plan = Plan(name="empty-plan", patrol_interval_seconds=60)
    db_session.add(plan)
    db_session.commit()

    pr = PlanRun(
        plan_id=plan.id,
        status="RUNNING",
        plan_snapshot={"plan_id": plan.id, "steps": []},
        run_type="MANUAL",
        triggered_by="test",
    )
    db_session.add(pr)
    db_session.commit()

    assert expected_scripts_for_run(pr, db_session) == []


def test_expected_scripts_carries_package_sha256_when_backfilled(db_session):
    """ADR-0051 D4：行上有 package_sha256 → 随 expected 下发；为空 → 不带键（老 Agent 不受影响）。"""
    plan = Plan(name="pkg-plan", patrol_interval_seconds=60)
    with_pkg = Script(name="a", script_type="python", version="1.0.0", nfs_path="/s/a/v1.0.0/a.py",
                      content_sha256="aa" * 32, package_sha256="cc" * 32, is_active=True, default_params={})
    without = Script(name="b", script_type="python", version="1.0.0", nfs_path="/s/b/v1.0.0/b.py",
                     content_sha256="bb" * 32, is_active=True, default_params={})
    db_session.add_all([plan, with_pkg, without])
    db_session.commit()
    pr = PlanRun(plan_id=plan.id, status="RUNNING", run_type="MANUAL", triggered_by="test",
                 plan_snapshot={"plan_id": plan.id, "steps": [
                     {"script_name": "a", "script_version": "1.0.0"},
                     {"script_name": "b", "script_version": "1.0.0"}]})
    db_session.add(pr)
    db_session.commit()
    out = {e["name"]: e for e in expected_scripts_for_run(pr, db_session)}
    assert out["a"]["package_sha256"] == "cc" * 32
    assert "package_sha256" not in out["b"]
