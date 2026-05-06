"""ADR-0020 — Plan / PlanStep / PlanRun / PlanMigrationAudit model tests."""

import pytest
from sqlalchemy.exc import IntegrityError

from backend.models.enums import DeviceStatus, HostStatus, JobStatus
from backend.models.host import Device, Host
from backend.models.job import JobInstance
from backend.models.plan import Plan, PlanStep
from backend.models.plan_migration_audit import PlanMigrationAudit
from backend.models.plan_run import PlanRun
from backend.models.script import Script


class TestPlanModel:
    def test_create_minimal_plan(self, db_session):
        plan = Plan(name="test-plan", lifecycle={"init": [], "teardown": []})
        db_session.add(plan)
        db_session.commit()
        assert plan.id is not None
        assert plan.failure_threshold == 0.05
        assert plan.next_plan_id is None

    def test_self_chain_rejected(self, db_session):
        plan = Plan(name="self-chain", lifecycle={"init": [], "teardown": []})
        db_session.add(plan)
        db_session.commit()
        plan.next_plan_id = plan.id
        db_session.add(plan)
        with pytest.raises(IntegrityError):
            db_session.commit()

    def test_chain_to_other_plan(self, db_session):
        plan_a = Plan(name="plan-a", lifecycle={"init": [], "teardown": []})
        plan_b = Plan(name="plan-b", lifecycle={"init": [], "teardown": []})
        db_session.add_all([plan_a, plan_b])
        db_session.commit()
        plan_a.next_plan_id = plan_b.id
        db_session.add(plan_a)
        db_session.commit()


class TestPlanStepModel:
    def test_create_step(self, db_session):
        plan = Plan(name="p", lifecycle={"init": [], "teardown": []})
        db_session.add(plan)
        db_session.commit()
        step = PlanStep(
            plan_id=plan.id, step_key="init_0_check",
            script_name="check_device", script_version="1.0.0",
            stage="init", sort_order=0,
        )
        db_session.add(step)
        db_session.commit()
        assert step.id is not None

    def test_duplicate_step_key_rejected(self, db_session):
        plan = Plan(name="p", lifecycle={"init": [], "teardown": []})
        db_session.add(plan)
        db_session.commit()
        s1 = PlanStep(plan_id=plan.id, step_key="init_0_check",
                      script_name="a", script_version="1.0.0",
                      stage="init", sort_order=0)
        db_session.add(s1)
        db_session.commit()
        s2 = PlanStep(plan_id=plan.id, step_key="init_0_check",
                      script_name="b", script_version="1.0.0",
                      stage="init", sort_order=1)
        db_session.add(s2)
        with pytest.raises(IntegrityError):
            db_session.commit()

    def test_invalid_stage_rejected(self, db_session):
        plan = Plan(name="p", lifecycle={"init": [], "teardown": []})
        db_session.add(plan)
        db_session.commit()
        step = PlanStep(
            plan_id=plan.id, step_key="x",
            script_name="a", script_version="1.0.0",
            stage="invalid_stage", sort_order=0,
        )
        db_session.add(step)
        with pytest.raises(IntegrityError):
            db_session.commit()

    def test_cascade_delete(self, db_session):
        plan = Plan(name="p", lifecycle={"init": [], "teardown": []})
        db_session.add(plan)
        db_session.commit()
        step = PlanStep(plan_id=plan.id, step_key="s",
                        script_name="a", script_version="1.0.0",
                        stage="init", sort_order=0)
        db_session.add(step)
        db_session.commit()
        db_session.delete(plan)
        db_session.commit()
        assert db_session.query(PlanStep).count() == 0


class TestPlanRunModel:
    def _make_plan(self, db_session) -> Plan:
        plan = Plan(name="pr-plan", lifecycle={"init": [], "teardown": []})
        db_session.add(plan)
        db_session.commit()
        return plan

    def test_create_manual_run(self, db_session):
        plan = self._make_plan(db_session)
        pr = PlanRun(
            plan_id=plan.id, run_type="MANUAL",
            plan_snapshot={"name": plan.name, "lifecycle": plan.lifecycle},
        )
        db_session.add(pr)
        db_session.commit()
        assert pr.id is not None
        assert pr.next_plan_triggered == "0"

    def test_invalid_run_type_rejected(self, db_session):
        plan = self._make_plan(db_session)
        pr = PlanRun(
            plan_id=plan.id, run_type="INVALID",
            plan_snapshot={"name": plan.name},
        )
        db_session.add(pr)
        with pytest.raises(IntegrityError):
            db_session.commit()

    def test_chain_fields(self, db_session):
        plan = self._make_plan(db_session)
        root = PlanRun(
            plan_id=plan.id, run_type="MANUAL",
            plan_snapshot={"name": plan.name},
        )
        db_session.add(root)
        db_session.commit()

        child = PlanRun(
            plan_id=plan.id, run_type="CHAIN",
            plan_snapshot={"name": plan.name},
            parent_plan_run_id=root.id,
            root_plan_run_id=root.id,
            chain_index=1,
        )
        db_session.add(child)
        db_session.commit()
        assert child.chain_index == 1


class TestPlanMigrationAuditModel:
    def test_create_row(self, db_session):
        audit = PlanMigrationAudit(
            old_workflow_definition_id=1,
            old_task_template_id=2,
            new_plan_id=10,
            chain_index=0,
            note="test",
        )
        db_session.add(audit)
        db_session.commit()
        assert audit.id is not None
