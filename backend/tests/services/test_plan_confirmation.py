"""L1 确认指纹（#3653 §3.2 / §4.2 U2）。

SQLite 没有行锁语义。锁到冻结的证据只认隔离 PostgreSQL 上的 FOR SHARE。
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from backend.models.job import JobInstance
from backend.models.plan import Plan, PlanStep
from backend.models.plan_run import PlanRun, PlanRunTargetDevice
from backend.models.project import Specialty, TestProject
from backend.models.resource_pool import ResourceAllocation
from backend.models.script import Script
from backend.models.suite import TestSuite
from backend.services.plan_confirmation import (
    PlanConfirmationChanged,
    assert_confirmation_matches,
    canonical_confirmation_document,
    confirmation_fingerprint,
    fingerprint_format_ok,
    load_plan_graph_for_confirmation,
)
from backend.services.plan_dispatcher_sync import (
    _fetch_script_metadata,
    _prepare_queued_plan_run,
    dispatch_plan_sync,
    prepare_plan_run,
    preview_plan_dispatch_sync,
)
from backend.services.ai_assistant.dispatch import execute_dispatch_plan_run

FIXED = datetime(2026, 10, 10, 8, 0, 0, tzinfo=timezone.utc)
PREFIX = "stp-l1-v1:"


def _plan(**overrides) -> Plan:
    values = dict(
        id=7,
        name="smoke",
        description="desc",
        patrol_interval_seconds=60,
        timeout_seconds=120,
        barrier_timeout_seconds=30,
        barrier_max_wait_seconds=1800,
        auto_archive_interval_seconds=10,
        watcher_policy={"enabled": True, "paths": ["a"]},
        next_plan_id=None,
        project_id=1,
        specialty_id=2,
        suite_id=3,
        created_by="tester",
        created_at=FIXED,
        updated_at=FIXED,
    )
    values.update(overrides)
    return Plan(**values)


def _step(**overrides) -> PlanStep:
    values = dict(
        id=1,
        plan_id=7,
        step_key="check",
        script_name="check_device",
        script_version="1.0.0",
        stage="init",
        sort_order=0,
        timeout_seconds=30,
        stall_seconds=0,
        params={"timeout": 10},
        retry=0,
        enabled=True,
        created_at=FIXED,
    )
    values.update(overrides)
    return PlanStep(**values)


def _meta(**overrides):
    field = {"default": 5, "label": "时长", "description": "说明甲"}
    field.update(overrides.pop("field", {}))
    base = {
        ("check_device", "1.0.0"): {
            "param_schema": {"timeout": field},
            "default_params": {"timeout": 5},
            "nfs_path": "/nfs/secret",
        }
    }
    base.update(overrides)
    return base


def _fingerprint(plan, steps, meta) -> str:
    return confirmation_fingerprint(plan, steps, meta)


class TestCanonicalFingerprint:
    def test_same_inputs_repeat_and_format(self):
        plan, steps, meta = _plan(), [_step()], _meta()
        first = _fingerprint(plan, steps, meta)
        second = _fingerprint(plan, steps, meta)
        assert first == second
        assert first.startswith(PREFIX)
        assert len(first) == len(PREFIX) + 64
        assert fingerprint_format_ok(first)
        assert not fingerprint_format_ok("")
        assert not fingerprint_format_ok(PREFIX + "AB" * 32)
        assert not fingerprint_format_ok(PREFIX + "a" * 63)

    def test_plan_fields_change_the_token_without_moving_updated_at(self):
        base = _fingerprint(_plan(), [_step()], _meta())
        mutations = {
            "name": {"name": "other"},
            "description": {"description": "other"},
            "patrol_interval_seconds": {"patrol_interval_seconds": 61},
            "timeout_seconds": {"timeout_seconds": 121},
            "barrier_timeout_seconds": {"barrier_timeout_seconds": 31},
            "barrier_max_wait_seconds": {"barrier_max_wait_seconds": 1801},
            "auto_archive_interval_seconds": {"auto_archive_interval_seconds": 11},
            "watcher_policy": {"watcher_policy": {"enabled": False}},
            "next_plan_id": {"next_plan_id": 9},
            "project_id": {"project_id": 8},
            "specialty_id": {"specialty_id": 8},
            "suite_id": {"suite_id": 8},
            "created_by": {"created_by": "other"},
            "updated_at": {"updated_at": datetime(2026, 10, 10, 9, 0, tzinfo=timezone.utc)},
        }
        for name, overrides in mutations.items():
            token = _fingerprint(_plan(**overrides), [_step()], _meta())
            assert token != base, name
        assert _fingerprint(_plan(created_at=datetime(2020, 1, 1, tzinfo=timezone.utc)), [_step()], _meta()) == base

    def test_step_params_order_enabled_and_version_change_the_token(self):
        base_steps = [_step(), _step(id=2, step_key="td", stage="teardown", sort_order=1)]
        base = _fingerprint(_plan(), base_steps, _meta())
        variants = [
            [_step(params={"timeout": 11}), base_steps[1]],
            [_step(sort_order=3), base_steps[1]],
            [_step(enabled=False), base_steps[1]],
            [_step(script_version="1.0.2"), base_steps[1]],
            [_step(timeout_seconds=31), base_steps[1]],
            [_step(stall_seconds=4), base_steps[1]],
            [_step(retry=2), base_steps[1]],
            [_step(stage="teardown"), base_steps[1]],
        ]
        for steps in variants:
            assert _fingerprint(_plan(), steps, _meta()) != base
        reversed_steps = list(reversed(base_steps))
        assert _fingerprint(_plan(), reversed_steps, _meta()) == base

    def test_docs_and_dynamic_resources_stay_out_of_the_document(self):
        plan, steps = _plan(), [_step()]
        described = _meta()
        relabeled = _meta(field={"label": "别名", "description": "说明乙"})
        assert _fingerprint(plan, steps, described) == _fingerprint(plan, steps, relabeled)
        assert _fingerprint(plan, steps, _meta(field={"default": 9})) != _fingerprint(plan, steps, described)
        changed_defaults = _meta()
        changed_defaults[("check_device", "1.0.0")]["default_params"] = {"timeout": 8}
        assert _fingerprint(plan, steps, changed_defaults) != _fingerprint(plan, steps, described)

        policy_a = _plan(watcher_policy={"b": 2, "a": 1})
        policy_b = _plan(watcher_policy={"a": 1, "b": 2})
        assert _fingerprint(policy_a, steps, described) == _fingerprint(policy_b, steps, described)

        document = canonical_confirmation_document(plan, steps, described)
        blob = json.dumps(document, ensure_ascii=False)
        assert "device_ids" not in blob
        assert "wifi_pool_id" not in blob
        assert "nfs_path" not in blob
        assert "说明甲" not in blob
        assert set(document["scripts"][0]) == {
            "name", "version", "schema_defaults", "default_params",
        }
        assert document["scripts"][0]["schema_defaults"] == {"timeout": 5}

    def test_missing_metadata_still_identifies_the_script(self):
        present = _fingerprint(_plan(), [_step()], _meta())
        absent = _fingerprint(_plan(), [_step()], {})
        assert absent != present
        document = canonical_confirmation_document(_plan(), [_step()], {})
        assert document["scripts"] == [{
            "name": "check_device",
            "version": "1.0.0",
            "schema_defaults": {},
            "default_params": {},
        }]

    def test_omitted_token_skips_compare_and_a_wrong_nibble_rejects(self):
        plan, steps, meta = _plan(), [_step()], _meta()
        assert_confirmation_matches(plan, steps, meta, None)
        token = _fingerprint(plan, steps, meta)
        assert_confirmation_matches(plan, steps, meta, token)
        flipped = token[:-1] + ("a" if token[-1] != "a" else "b")
        with pytest.raises(PlanConfirmationChanged, match="计划配置已变化，请重新预览并确认"):
            assert_confirmation_matches(plan, steps, meta, flipped)


def _counts(db) -> tuple[int, int, int, int]:
    return (
        db.query(PlanRun).count(),
        db.query(PlanRunTargetDevice).count(),
        db.query(JobInstance).count(),
        db.query(ResourceAllocation).count(),
    )


def _issued_token(db, plan_id: int) -> str:
    plan, steps = load_plan_graph_for_confirmation(db, plan_id)
    meta = _fetch_script_metadata(db, steps)
    token = confirmation_fingerprint(plan, list(steps), meta)
    db.commit()
    return token


def _expect_reject(db, plan, device, token: str) -> None:
    before = _counts(db)
    with patch(
        "backend.services.plan_dispatcher_sync._prepare_queued_plan_run",
        side_effect=AssertionError("snapshot must not be built"),
    ):
        with pytest.raises(PlanConfirmationChanged):
            prepare_plan_run(
                plan.id,
                [device.id],
                "tester",
                db,
                confirmation_fingerprint=token,
            )
    assert _counts(db) == before


@pytest.fixture
def confirmable(db_session, sample_plan, sample_script, sample_device):
    plan = sample_plan
    plan.description = "baseline"
    plan.patrol_interval_seconds = 3600
    plan.timeout_seconds = 7200
    plan.barrier_timeout_seconds = 600
    plan.barrier_max_wait_seconds = 1800
    plan.auto_archive_interval_seconds = 86400
    plan.watcher_policy = {"enabled": True}
    db_session.add_all([
        PlanStep(
            plan_id=plan.id,
            step_key="patrol_step",
            script_name="check_device",
            script_version="v1.0.0",
            stage="patrol",
            sort_order=0,
            enabled=True,
        ),
        PlanStep(
            plan_id=plan.id,
            step_key="td_step",
            script_name="check_device",
            script_version="v1.0.0",
            stage="teardown",
            sort_order=0,
            enabled=True,
        ),
    ])
    db_session.commit()
    return plan, sample_device


class TestPrepareConfirmation:
    @pytest.mark.parametrize("field", [
        "name",
        "description",
        "patrol_interval_seconds",
        "timeout_seconds",
        "barrier_timeout_seconds",
        "barrier_max_wait_seconds",
        "auto_archive_interval_seconds",
        "watcher_policy",
        "created_by",
        "updated_at",
        "next_plan_id",
        "project_id",
        "specialty_id",
        "suite_id",
        "params",
        "sort_order",
        "enabled",
        "script_version",
        "timeout",
        "stall",
        "retry",
        "stage",
    ])
    def test_each_confirmed_change_rejects_before_any_row(self, db_session, confirmable, field):
        plan, device = confirmable
        token = _issued_token(db_session, plan.id)
        db_session.refresh(plan)
        step = (
            db_session.query(PlanStep)
            .filter_by(plan_id=plan.id, step_key="td_step")
            .one()
        )
        if field == "name":
            plan.name = "renamed"
        elif field == "description":
            plan.description = "changed"
        elif field == "patrol_interval_seconds":
            plan.patrol_interval_seconds = 120
        elif field == "timeout_seconds":
            plan.timeout_seconds = 50
        elif field == "barrier_timeout_seconds":
            plan.barrier_timeout_seconds = 90
        elif field == "barrier_max_wait_seconds":
            plan.barrier_max_wait_seconds = 90
        elif field == "auto_archive_interval_seconds":
            plan.auto_archive_interval_seconds = 30
        elif field == "watcher_policy":
            plan.watcher_policy = {"enabled": False}
        elif field == "created_by":
            plan.created_by = "other"
        elif field == "updated_at":
            plan.updated_at = datetime(2026, 1, 2, tzinfo=timezone.utc)
        elif field == "next_plan_id":
            nxt = Plan(name="next-plan", created_by="tester")
            db_session.add(nxt)
            db_session.flush()
            plan.next_plan_id = nxt.id
        elif field == "project_id":
            project = TestProject(project_key="u2-proj", display_name="U2")
            db_session.add(project)
            db_session.flush()
            plan.project_id = project.id
        elif field == "specialty_id":
            plan.specialty_id = db_session.query(Specialty).filter_by(key="ops").one().id
        elif field == "suite_id":
            suite = TestSuite(name="u2-suite", root_config={})
            db_session.add(suite)
            db_session.flush()
            plan.suite_id = suite.id
        elif field == "params":
            step.params = {"password": "SENTINEL_PASSWORD"}
        elif field == "sort_order":
            step.sort_order = 4
        elif field == "enabled":
            step.enabled = False
        elif field == "script_version":
            step.script_version = "1.0.0"
        elif field == "timeout":
            step.timeout_seconds = 45
        elif field == "stall":
            step.stall_seconds = 20
        elif field == "retry":
            step.retry = 2
        elif field == "stage":
            step.stage = "init"
        db_session.commit()
        _expect_reject(db_session, plan, device, token)

    def test_omitted_token_still_queues_for_manual_schedule_chain_and_ai(
        self, db_session, confirmable,
    ):
        plan, device = confirmable
        manual = prepare_plan_run(plan.id, [device.id], "tester", db_session)
        assert manual.status == "QUEUED"
        scheduled = dispatch_plan_sync(
            plan.id, [device.id], "cron", db_session, run_type="SCHEDULE",
        )
        assert scheduled.status == "QUEUED"
        chained = prepare_plan_run(
            plan.id, [device.id], "chain", db_session, run_type="CHAIN", commit=False,
        )
        assert chained.status == "QUEUED"
        db_session.commit()
        run_id, _summary = execute_dispatch_plan_run(
            db_session,
            {"plan_id": plan.id, "device_ids": [device.id]},
            triggered_by="ai",
        )
        assert run_id > 0

    def test_valid_token_snapshots_the_prepare_read(self, db_session, confirmable):
        plan, device = confirmable
        script = (
            db_session.query(Script)
            .filter_by(name="check_device", version="v1.0.0")
            .one()
        )
        script.default_params = {"marker": "from-prepare-read"}
        db_session.commit()
        token = _issued_token(db_session, plan.id)
        original_name = plan.name
        fetches = {"n": 0}
        real_fetch = _fetch_script_metadata

        def once(db, steps):
            fetches["n"] += 1
            return real_fetch(db, steps)

        loads = {"n": 0}
        real_load = load_plan_graph_for_confirmation

        def once_load(db, plan_id):
            loads["n"] += 1
            return real_load(db, plan_id)

        with (
            patch("backend.services.plan_dispatcher_sync._fetch_script_metadata", once),
            patch(
                "backend.services.plan_dispatcher_sync.load_plan_graph_for_confirmation",
                once_load,
            ),
        ):
            queued = prepare_plan_run(
                plan.id, [device.id], "tester", db_session,
                confirmation_fingerprint=token,
            )
        assert loads["n"] == 1
        assert fetches["n"] == 1
        assert queued.plan_snapshot["plan"]["name"] == original_name
        assert queued.plan_snapshot["steps"][0]["default_params"] == {
            "marker": "from-prepare-read",
        }

    def test_docs_do_not_change_preview_token_and_defaults_do(
        self, db_session, confirmable,
    ):
        plan, device = confirmable
        script = (
            db_session.query(Script)
            .filter_by(name="check_device", version="v1.0.0")
            .one()
        )
        script.param_schema = {"timeout": {"default": 5, "description": "说明甲", "label": "时长"}}
        script.default_params = {"timeout": 5}
        db_session.commit()
        first = preview_plan_dispatch_sync(plan.id, [device.id], db_session)
        script.param_schema = {"timeout": {"default": 5, "description": "说明乙", "label": "别名"}}
        db_session.commit()
        second = preview_plan_dispatch_sync(plan.id, [device.id, device.id + 99], db_session)
        assert first["confirmation_fingerprint"] == second["confirmation_fingerprint"]
        script.param_schema = {"timeout": {"default": 9, "description": "说明乙"}}
        db_session.commit()
        third = preview_plan_dispatch_sync(plan.id, [device.id], db_session)
        assert third["confirmation_fingerprint"] != first["confirmation_fingerprint"]
        script.param_schema = {"timeout": {"default": 9}}
        script.default_params = {"timeout": 8}
        db_session.commit()
        fourth = preview_plan_dispatch_sync(plan.id, [device.id], db_session)
        assert fourth["confirmation_fingerprint"] != third["confirmation_fingerprint"]

    def test_for_share_blocks_update_until_the_snapshot_freezes(
        self, db_session, engine, confirmable,
    ):
        bind = db_session.get_bind()
        assert bind is not None and bind.dialect.name == "postgresql", (
            "U2 锁证据必须来自 PostgreSQL，SQLite 不能代替"
        )
        plan, device = confirmable
        plan_id = plan.id
        device_id = device.id
        original_name = plan.name
        token = _issued_token(db_session, plan_id)
        db_session.commit()

        Session = sessionmaker(bind=engine, expire_on_commit=False)
        held = threading.Event()
        release = threading.Event()
        errors: list[BaseException] = []
        result: dict = {}

        def parked(**kwargs):
            held.set()
            assert release.wait(timeout=15), "主线程没有放开快照写入"
            return _prepare_queued_plan_run(**kwargs)

        def worker() -> None:
            try:
                with patch(
                    "backend.services.plan_dispatcher_sync._prepare_queued_plan_run",
                    parked,
                ):
                    with Session() as session:
                        queued = prepare_plan_run(
                            plan_id,
                            [device_id],
                            "tester",
                            session,
                            confirmation_fingerprint=token,
                        )
                        result["id"] = queued.id
                        result["name"] = queued.plan_snapshot["plan"]["name"]
            except BaseException as exc:  # noqa: BLE001 - 线程里的失败带回主断言
                errors.append(exc)

        thread = threading.Thread(target=worker)
        thread.start()
        assert held.wait(timeout=15), errors
        try:
            with engine.connect() as conn:
                modes = conn.execute(text(
                    "SELECT l.mode FROM pg_locks l "
                    "JOIN pg_class c ON c.oid = l.relation "
                    "WHERE c.relname = 'plan' AND l.granted"
                )).scalars().all()
            # FOR SHARE 的表锁是 RowShareLock。已授予的行锁写在元组头里，
            # pg_locks 通常不列出 ForShare；下面的 FOR UPDATE 超时才是行锁证据。
            assert "RowShareLock" in modes, modes
            with Session() as blocker:
                blocker.execute(text("SET LOCAL lock_timeout = '300ms'"))
                with pytest.raises(OperationalError):
                    blocker.execute(
                        text("SELECT id FROM plan WHERE id = :pid FOR UPDATE"),
                        {"pid": plan_id},
                    )
        finally:
            release.set()
            thread.join(timeout=20)

        assert not thread.is_alive()
        assert errors == [], errors
        assert result["name"] == original_name

        with Session() as writer:
            row = writer.get(Plan, plan_id)
            assert row is not None
            row.name = "changed-after-freeze"
            writer.commit()
        with Session() as reader:
            queued = reader.get(PlanRun, result["id"])
            assert queued is not None
            assert queued.plan_snapshot["plan"]["name"] == original_name
            assert reader.get(Plan, plan_id).name == "changed-after-freeze"
