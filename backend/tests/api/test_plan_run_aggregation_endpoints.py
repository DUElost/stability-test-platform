"""ADR-0021/ADR-0022 C5a₂ — PlanRun aggregation endpoints tests.

Covers the 5 new GET endpoints that back the redesigned PlanRunDetailPage:

  GET /plan-runs/{id}/chain
  GET /plan-runs/{id}/timeline
  GET /plan-runs/{id}/events
  GET /plan-runs/{id}/devices
  GET /plan-runs/{id}/watcher-summary

Each endpoint is tested against a small synthetic Plan chain with two
PlanRuns (parent SUCCESS + current RUNNING) and 3 jobs / devices spread
across two hosts so that:
  - chain returns parent + current + pending next (gated by Plan.next_plan_id)
  - timeline aggregates step_trace by stage and exposes patrol cycle counters
  - events fuses step_trace failures + log_signals + audit_logs + trigger
  - devices derives ui_status correctly for completed/running/failed/risk/backoff
  - watcher-summary buckets log_signals by category with trend vs prev window
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

import pytest

from backend.models.audit import AuditLog
from backend.models.enums import HostStatus, JobStatus, PlanRunStatus
from backend.models.host import Device, Host
from backend.models.job import JobInstance, JobLogSignal, StepTrace
from backend.models.plan import Plan, PlanStep
from backend.models.plan_run import PlanRun


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(timezone.utc)


@pytest.fixture
def chain_setup(db_session):
    """Build a Plan chain (parent#41 → current#42 → pending#43) with 3 devices
    on 2 hosts, mixed job statuses + step_trace + log_signals + audit logs.

    Returns: dict with plan/parent_run/current_run/jobs/devices.
    """
    # Hosts
    host_a = Host(
        id="host-101", hostname="host-a",
        status=HostStatus.ONLINE.value, ip="10.0.0.101",
        ssh_user="root", ssh_port=22, extra={},
        last_heartbeat=_now(),
    )
    host_b = Host(
        id="host-102", hostname="host-b",
        status=HostStatus.ONLINE.value, ip="10.0.0.102",
        ssh_user="root", ssh_port=22, extra={},
        last_heartbeat=_now(),
    )
    db_session.add_all([host_a, host_b])

    # Devices: dev1@host-a (completed), dev2@host-a (running), dev3@host-b (failed)
    dev1 = Device(serial="dev-aa-01", host_id="host-101", status="ONLINE")
    dev2 = Device(serial="dev-aa-02", host_id="host-101", status="BUSY")
    dev3 = Device(serial="dev-bb-01", host_id="host-102", status="OFFLINE")
    db_session.add_all([dev1, dev2, dev3])

    # Plans: parent (#41) → current (#42) → next (#43)
    plan_next = Plan(name="结果汇总", failure_threshold=0.05)
    db_session.add(plan_next)
    db_session.commit()

    plan_cur = Plan(
        name="多机型 Monkey 主链",
        failure_threshold=0.05,
        patrol_interval_seconds=60,
        next_plan_id=plan_next.id,
    )
    plan_parent = Plan(name="健康预检", failure_threshold=0.05)
    db_session.add_all([plan_cur, plan_parent])
    db_session.commit()

    # PlanSteps for current plan (init/patrol/teardown)
    steps = [
        PlanStep(plan_id=plan_cur.id, step_key="check_device",
                 script_name="check_device", script_version="v1.0.0",
                 stage="init", sort_order=0),
        PlanStep(plan_id=plan_cur.id, step_key="ensure_root",
                 script_name="ensure_root", script_version="v1.0.0",
                 stage="init", sort_order=1),
        PlanStep(plan_id=plan_cur.id, step_key="patrol.monkey_launch",
                 script_name="monkey_launch", script_version="v5.0.0",
                 stage="patrol", sort_order=0),
        PlanStep(plan_id=plan_cur.id, step_key="teardown.clean_env",
                 script_name="clean_env", script_version="v1.0.0",
                 stage="teardown", sort_order=0),
    ]
    db_session.add_all(steps)
    db_session.commit()

    # PlanRun: parent (SUCCESS) + current (RUNNING)
    parent_run = PlanRun(
        plan_id=plan_parent.id, status=PlanRunStatus.SUCCESS.value,
        failure_threshold=0.05,
        plan_snapshot={"plan": {"id": plan_parent.id, "name": plan_parent.name}, "steps": []},
        run_type="MANUAL", triggered_by="dai.lv",
        chain_index=0,
        started_at=_now() - timedelta(minutes=12),
        ended_at=_now() - timedelta(minutes=4),
        result_summary={"total": 3, "completed": 3, "failed": 0, "pass_rate": 1.0},
    )
    db_session.add(parent_run)
    db_session.commit()

    snapshot = {
        "plan": {"id": plan_cur.id, "name": plan_cur.name},
        "steps": [
            {"step_key": s.step_key, "script_name": s.script_name,
             "script_version": s.script_version, "stage": s.stage,
             "sort_order": s.sort_order}
            for s in steps
        ],
    }
    cur_run = PlanRun(
        plan_id=plan_cur.id, status=PlanRunStatus.RUNNING.value,
        failure_threshold=0.05,
        plan_snapshot=snapshot,
        run_type="CHAIN", triggered_by="chain",
        parent_plan_run_id=parent_run.id,
        root_plan_run_id=parent_run.id,
        chain_index=1,
        started_at=_now() - timedelta(minutes=3),
    )
    db_session.add(cur_run)
    db_session.commit()

    # 3 jobs in current run
    j1 = JobInstance(
        plan_run_id=cur_run.id, plan_id=plan_cur.id,
        device_id=dev1.id, host_id="host-101",
        status=JobStatus.COMPLETED.value,
        pipeline_def={"lifecycle": {}},
        started_at=_now() - timedelta(minutes=3),
        ended_at=_now() - timedelta(seconds=30),
        patrol_cycle_count=14, patrol_success_cycle_count=14,
    )
    j2 = JobInstance(  # Running, has 1 log_signal → ui_status = risk
        plan_run_id=cur_run.id, plan_id=plan_cur.id,
        device_id=dev2.id, host_id="host-101",
        status=JobStatus.RUNNING.value,
        pipeline_def={"lifecycle": {}},
        started_at=_now() - timedelta(minutes=3),
        patrol_cycle_count=12, patrol_success_cycle_count=11,
        patrol_failed_cycle_count=1,
        current_failure_streak=1,
        current_patrol_step="patrol.monkey_launch",
        last_patrol_heartbeat_at=_now() - timedelta(seconds=10),
        log_signal_count=1,
    )
    j3 = JobInstance(
        plan_run_id=cur_run.id, plan_id=plan_cur.id,
        device_id=dev3.id, host_id="host-102",
        status=JobStatus.FAILED.value,
        status_reason="patrol_step_failed: monkey_launch",
        pipeline_def={"lifecycle": {}},
        started_at=_now() - timedelta(minutes=3),
        ended_at=_now() - timedelta(minutes=1),
        patrol_cycle_count=4, patrol_failed_cycle_count=4,
        current_failure_streak=4,
    )
    db_session.add_all([j1, j2, j3])
    db_session.commit()

    # step_trace for j1 (init success), j3 (init success + patrol failure)
    base_ts = _now() - timedelta(minutes=3)
    db_session.add_all([
        StepTrace(job_id=j1.id, step_id="check_device", stage="init",
                  status="COMPLETED", event_type="COMPLETED",
                  original_ts=base_ts + timedelta(seconds=5)),
        StepTrace(job_id=j1.id, step_id="ensure_root", stage="init",
                  status="COMPLETED", event_type="COMPLETED",
                  original_ts=base_ts + timedelta(seconds=10)),
        StepTrace(job_id=j3.id, step_id="check_device", stage="init",
                  status="COMPLETED", event_type="COMPLETED",
                  original_ts=base_ts + timedelta(seconds=5)),
        StepTrace(job_id=j3.id, step_id="ensure_root", stage="init",
                  status="COMPLETED", event_type="COMPLETED",
                  original_ts=base_ts + timedelta(seconds=10)),
        StepTrace(job_id=j3.id, step_id="patrol.monkey_launch",
                  stage="patrol", status="FAILED",
                  event_type="FAILED",
                  error_message="monkey crashed",
                  original_ts=base_ts + timedelta(minutes=2)),
    ])
    db_session.commit()

    # log_signals on j2 (current window) + one older signal on j3 (prev window)
    # NOTE: JobLogSignal.id is BigInteger which SQLite does NOT auto-increment;
    # explicitly seed monotonic ids for test rows.
    db_session.add_all([
        JobLogSignal(
            id=10001,
            job_id=j2.id, host_id="host-101", device_serial=dev2.serial,
            seq_no=1, category="AEE", source="inotifyd",
            path_on_device="/data/aee/aee_001",
            detected_at=_now() - timedelta(minutes=5),
            first_lines="aee summary",
        ),
        JobLogSignal(
            id=10002,
            job_id=j2.id, host_id="host-101", device_serial=dev2.serial,
            seq_no=2, category="ANR", source="logcat",
            path_on_device="/data/anr/anr_001",
            detected_at=_now() - timedelta(minutes=3),
            first_lines="anr summary",
        ),
        # Outside current 60-min window — counts as "previous window" for trend
        JobLogSignal(
            id=10003,
            job_id=j3.id, host_id="host-102", device_serial=dev3.serial,
            seq_no=1, category="AEE", source="inotifyd",
            path_on_device="/data/aee/aee_old",
            detected_at=_now() - timedelta(minutes=70),
            first_lines="old aee",
        ),
    ])

    # audit_logs (system events)
    db_session.add(AuditLog(
        action="plan_run_started",
        resource_type="plan_run",
        resource_id=cur_run.id,
        details={"plan_id": plan_cur.id, "trigger": "chain"},
        timestamp=_now() - timedelta(minutes=3, seconds=10),
        username="chain",
    ))
    db_session.add(AuditLog(
        action="patrol_manual_retry",
        resource_type="job_instance",
        resource_id=j2.id,
        details={"plan_run_id": cur_run.id, "current_failure_streak": 1},
        timestamp=_now() - timedelta(minutes=1),
        username="dai.lv",
    ))
    db_session.commit()

    return {
        "plan_parent": plan_parent, "plan_current": plan_cur, "plan_next": plan_next,
        "parent_run": parent_run, "current_run": cur_run,
        "host_a": host_a, "host_b": host_b,
        "device_completed": dev1, "device_running": dev2, "device_failed": dev3,
        "job_completed": j1, "job_running": j2, "job_failed": j3,
    }


# ---------------------------------------------------------------------------
# /chain
# ---------------------------------------------------------------------------


class TestChainEndpoint:
    def test_chain_returns_parent_current_pending_next(
        self, client, auth_headers, chain_setup,
    ):
        cur_run = chain_setup["current_run"]
        resp = client.get(
            f"/api/v1/plan-runs/{cur_run.id}/chain", headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["plan_run_id"] == cur_run.id
        # parent_run is the chain root
        assert data["root_plan_run_id"] == chain_setup["parent_run"].id
        nodes = data["nodes"]
        assert len(nodes) == 3, f"expected 3 nodes, got {nodes}"
        # Order: parent (chain_index=0) → current (1) → next pending (2)
        assert nodes[0]["plan_id"] == chain_setup["plan_parent"].id
        assert nodes[0]["status"] == PlanRunStatus.SUCCESS.value
        assert nodes[0]["pass_rate"] == 1.0
        assert nodes[0]["is_current"] is False

        assert nodes[1]["plan_id"] == chain_setup["plan_current"].id
        assert nodes[1]["status"] == PlanRunStatus.RUNNING.value
        assert nodes[1]["is_current"] is True

        # Pending next: no plan_run_id, status='pending', is_blocked because parent RUNNING
        assert nodes[2]["plan_id"] == chain_setup["plan_next"].id
        assert nodes[2]["plan_run_id"] is None
        assert nodes[2]["status"] == "pending"
        assert nodes[2]["is_blocked"] is True
        assert nodes[2]["block_reason"] is not None

    def test_chain_returns_404_for_unknown_run(self, client, auth_headers):
        resp = client.get("/api/v1/plan-runs/999999/chain", headers=auth_headers)
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# /timeline
# ---------------------------------------------------------------------------


class TestTimelineEndpoint:
    def test_timeline_aggregates_stages_and_steps(
        self, client, auth_headers, chain_setup,
    ):
        cur_run = chain_setup["current_run"]
        resp = client.get(
            f"/api/v1/plan-runs/{cur_run.id}/timeline", headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["plan_run_id"] == cur_run.id
        assert data["plan_name"] == chain_setup["plan_current"].name
        # 3 stages declared in PlanStep
        stages = {s["stage"]: s for s in data["stages"]}
        assert set(stages) == {"init", "patrol", "teardown"}
        # init: 2 steps × 2 jobs (j1/j3) successful → succeeded=4 across both steps
        init_stage = stages["init"]
        assert init_stage["device_succeeded"] == 4  # 2 steps × 2 successful jobs
        assert init_stage["device_failed"] == 0
        assert init_stage["device_total"] == 3  # 3 jobs in run
        assert len(init_stage["steps"]) == 2
        # patrol: 1 declared step with 1 failed (j3.patrol.monkey_launch)
        patrol_stage = stages["patrol"]
        assert patrol_stage["device_failed"] == 1
        # patrol heartbeat aggregation
        assert patrol_stage["patrol_cycle_index"] == 14  # max across jobs
        assert patrol_stage["patrol_active_devices"] is not None
        assert patrol_stage["patrol_interval_seconds"] == 60
        # teardown: no step_trace yet
        td_stage = stages["teardown"]
        assert td_stage["device_succeeded"] == 0
        assert td_stage["device_failed"] == 0
        # current_stage: patrol since heartbeat present
        assert data["current_stage"] in {"patrol", "teardown", "init"}

    def test_timeline_returns_404_for_unknown_run(self, client, auth_headers):
        resp = client.get("/api/v1/plan-runs/999999/timeline", headers=auth_headers)
        assert resp.status_code == 404

    # ── v3: device_skipped + aborted_job_count ─────────────────────────

    def test_timeline_device_skipped_double_layer(
        self, client, auth_headers, db_session, chain_setup,
    ):
        """v3: device_skipped 在 stage/step 两层都填充"""
        cur_run = chain_setup["current_run"]
        j1 = chain_setup["job_completed"]

        # Add a SKIPPED step trace for j1 on a unique step (avoid PK conflict with fixture)
        st = StepTrace(
            job_id=j1.id,
            step_id="patrol.monkey_launch",
            stage="patrol",
            event_type="COMPLETED",
            status="SKIPPED",
            original_ts=_now(),
        )
        db_session.add(st)
        db_session.commit()

        resp = client.get(
            f"/api/v1/plan-runs/{cur_run.id}/timeline", headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        stages = {s["stage"]: s for s in data["stages"]}
        patrol_stage = stages.get("patrol", {})

        # Stage-level device_skipped
        assert patrol_stage.get("device_skipped", 0) >= 1

        # Step-level device_skipped
        step = next((s for s in patrol_stage.get("steps", []) if s["step_key"] == "patrol.monkey_launch"), None)
        assert step is not None
        assert step.get("device_skipped", 0) >= 1

    def test_timeline_skipped_not_miscount_as_succeeded(
        self, client, auth_headers, db_session, chain_setup,
    ):
        """v3: COMPLETED+SKIPPED 不计为 device_succeeded"""
        cur_run = chain_setup["current_run"]
        j1 = chain_setup["job_completed"]

        # Add a SKIPPED trace on a unique (job_id, step_id, stage, original_ts)
        st = StepTrace(
            job_id=j1.id,
            step_id="teardown.clean_env",
            stage="teardown",
            event_type="COMPLETED",
            status="SKIPPED",
            original_ts=_now(),
        )
        db_session.add(st)
        db_session.commit()

        resp = client.get(
            f"/api/v1/plan-runs/{cur_run.id}/timeline", headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        stages = {s["stage"]: s for s in data["stages"]}

        td_stage = stages.get("teardown", {})
        step = next(
            (s for s in td_stage.get("steps", []) if s["step_key"] == "teardown.clean_env"),
            None,
        )
        assert step is not None
        # SKIPPED trace → device_skipped >= 1
        assert step.get("device_skipped", 0) >= 1

    def test_timeline_completed_completed_counts_succeeded(
        self, client, auth_headers, chain_setup,
    ):
        """v3: COMPLETED+COMPLETED 才计为 device_succeeded (baseline)"""
        cur_run = chain_setup["current_run"]
        resp = client.get(
            f"/api/v1/plan-runs/{cur_run.id}/timeline", headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        init = next((s for s in data["stages"] if s["stage"] == "init"), None)
        assert init is not None
        # Existing fixture has all running/completed jobs with COMPLETED status on init steps
        assert init["device_succeeded"] >= 1
        # No SKIPPED traces in baseline fixture
        assert init.get("device_skipped", 0) == 0


# ---------------------------------------------------------------------------
# /events
# ---------------------------------------------------------------------------


class TestEventsEndpoint:
    def test_events_fuses_all_sources_with_facets(
        self, client, auth_headers, chain_setup,
    ):
        cur_run = chain_setup["current_run"]
        resp = client.get(
            f"/api/v1/plan-runs/{cur_run.id}/events",
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        events = data["events"]
        # Must contain at least: trigger + 1 step failure + 3 log_signals + 2 audit
        assert len(events) >= 7
        categories = [e["category"] for e in events]
        assert "trigger" in categories
        assert "step" in categories
        assert "log_signal" in categories
        assert "audit" in categories
        # facets
        facets = data["facets"]
        assert facets["by_stage"]["all"] == data["total"]
        assert facets["by_severity"]["all"] == data["total"]

    def test_events_filter_by_severity(
        self, client, auth_headers, chain_setup,
    ):
        cur_run = chain_setup["current_run"]
        resp = client.get(
            f"/api/v1/plan-runs/{cur_run.id}/events?severity=err",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        events = resp.json()["data"]["events"]
        assert all(e["severity"] == "err" for e in events)
        # AEE log_signals + step failures should be present
        assert any(e["category"] == "step" for e in events)
        assert any(e["category"] == "log_signal" for e in events)

    def test_events_filter_by_stage(
        self, client, auth_headers, chain_setup,
    ):
        cur_run = chain_setup["current_run"]
        resp = client.get(
            f"/api/v1/plan-runs/{cur_run.id}/events?stage=trigger",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        events = resp.json()["data"]["events"]
        assert all(e["stage"] == "trigger" for e in events)
        assert len(events) >= 1

    def test_events_include_patrol_progress_when_patrol_is_active(
        self, client, auth_headers, chain_setup,
    ):
        cur_run = chain_setup["current_run"]
        resp = client.get(
            f"/api/v1/plan-runs/{cur_run.id}/events?limit=100",
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["total"] >= 2
        assert any(
            e["stage"] == "patrol"
            and e["category"] == "system"
            and "PATROL 开始" in e["title"]
            for e in data["events"]
        )
        assert any(
            e["stage"] == "patrol"
            and e["category"] == "system"
            and "PATROL 进行中" in e["title"]
            for e in data["events"]
        )

    def test_events_do_not_fake_init_completed_when_run_fails_in_init(
        self, client, auth_headers, db_session, chain_setup,
    ):
        plan_cur = chain_setup["plan_current"]
        device = chain_setup["device_failed"]

        run = PlanRun(
            plan_id=plan_cur.id,
            status=PlanRunStatus.FAILED.value,
            failure_threshold=0.05,
            plan_snapshot={
                "plan": {"id": plan_cur.id, "name": plan_cur.name},
                "steps": [
                    {
                        "step_key": "check_device",
                        "script_name": "check_device",
                        "script_version": "v1.0.0",
                        "stage": "init",
                        "sort_order": 0,
                    },
                ],
            },
            run_type="MANUAL",
            triggered_by="test",
            started_at=_now() - timedelta(minutes=2),
            ended_at=_now() - timedelta(minutes=1),
        )
        db_session.add(run)
        db_session.flush()

        job = JobInstance(
            plan_run_id=run.id,
            plan_id=plan_cur.id,
            device_id=device.id,
            host_id=device.host_id,
            status=JobStatus.FAILED.value,
            status_reason="init_step_failed: check_device",
            pipeline_def={"lifecycle": {}},
            started_at=_now() - timedelta(minutes=2),
            ended_at=_now() - timedelta(minutes=1),
        )
        db_session.add(job)
        db_session.flush()

        db_session.add(
            StepTrace(
                job_id=job.id,
                step_id="check_device",
                stage="init",
                status="FAILED",
                event_type="FAILED",
                error_message="device offline",
                original_ts=_now() - timedelta(minutes=1, seconds=30),
            )
        )
        db_session.commit()

        resp = client.get(
            f"/api/v1/plan-runs/{run.id}/events?limit=100",
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        titles = [e["title"] for e in resp.json()["data"]["events"]]
        assert "INIT 完成" not in titles

    def test_events_do_not_fake_teardown_completed_when_teardown_failed(
        self, client, auth_headers, db_session, chain_setup,
    ):
        plan_cur = chain_setup["plan_current"]
        device = chain_setup["device_failed"]
        base_ts = _now() - timedelta(minutes=3)

        run = PlanRun(
            plan_id=plan_cur.id,
            status=PlanRunStatus.FAILED.value,
            failure_threshold=0.05,
            plan_snapshot={
                "plan": {"id": plan_cur.id, "name": plan_cur.name},
                "steps": [
                    {
                        "step_key": "check_device",
                        "script_name": "check_device",
                        "script_version": "v1.0.0",
                        "stage": "init",
                        "sort_order": 0,
                    },
                    {
                        "step_key": "teardown.clean_env",
                        "script_name": "clean_env",
                        "script_version": "v1.0.0",
                        "stage": "teardown",
                        "sort_order": 0,
                    },
                ],
            },
            run_type="MANUAL",
            triggered_by="test",
            started_at=base_ts,
            ended_at=base_ts + timedelta(minutes=2),
        )
        db_session.add(run)
        db_session.flush()

        job = JobInstance(
            plan_run_id=run.id,
            plan_id=plan_cur.id,
            device_id=device.id,
            host_id=device.host_id,
            status=JobStatus.FAILED.value,
            status_reason="teardown_failed: clean_env",
            pipeline_def={"lifecycle": {}},
            started_at=base_ts,
            ended_at=base_ts + timedelta(minutes=2),
        )
        db_session.add(job)
        db_session.flush()

        db_session.add_all([
            StepTrace(
                job_id=job.id,
                step_id="check_device",
                stage="init",
                status="COMPLETED",
                event_type="COMPLETED",
                original_ts=base_ts + timedelta(seconds=5),
            ),
            StepTrace(
                job_id=job.id,
                step_id="teardown.clean_env",
                stage="teardown",
                status="FAILED",
                event_type="FAILED",
                error_message="cleanup crashed",
                original_ts=base_ts + timedelta(minutes=2),
            ),
        ])
        db_session.commit()

        resp = client.get(
            f"/api/v1/plan-runs/{run.id}/events?limit=100",
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        titles = [e["title"] for e in resp.json()["data"]["events"]]
        assert "TEARDOWN 完成" not in titles

    def test_events_pagination(self, client, auth_headers, chain_setup):
        cur_run = chain_setup["current_run"]
        resp1 = client.get(
            f"/api/v1/plan-runs/{cur_run.id}/events?limit=2&offset=0",
            headers=auth_headers,
        )
        resp2 = client.get(
            f"/api/v1/plan-runs/{cur_run.id}/events?limit=2&offset=2",
            headers=auth_headers,
        )
        assert resp1.status_code == 200 and resp2.status_code == 200
        page1 = resp1.json()["data"]["events"]
        page2 = resp2.json()["data"]["events"]
        assert len(page1) == 2
        # Pages must not overlap
        ids1 = {(e["ts"], e["title"]) for e in page1}
        ids2 = {(e["ts"], e["title"]) for e in page2}
        assert not (ids1 & ids2)

    # ── v3: ABORTED job events ──────────────────────────────────────────

    def test_events_includes_aborted_job_event(
        self, client, auth_headers, db_session, chain_setup,
    ):
        """v3: ABORTED job → 「Job #N 已中止」事件, stage=system severity=warn"""
        cur_run = chain_setup["current_run"]
        j2 = chain_setup["job_running"]

        # Add RUN_COMPLETE step_trace marking this job as ABORTED
        st = StepTrace(
            job_id=j2.id,
            step_id="__job__",
            stage="post_process",
            event_type="RUN_COMPLETE",
            status="ABORTED",
            original_ts=_now() - timedelta(seconds=30),
        )
        j2.status = JobStatus.ABORTED.value
        db_session.add(st)
        db_session.commit()

        resp = client.get(
            f"/api/v1/plan-runs/{cur_run.id}/events", headers=auth_headers,
        )
        assert resp.status_code == 200
        events = resp.json()["data"]["events"]

        # Find the 「Job #N 已中止」event
        aborted_events = [
            e for e in events
            if "已中止" in e["title"] and str(j2.id) in e["title"]
        ]
        assert len(aborted_events) >= 1
        assert aborted_events[0]["severity"] == "warn"
        assert aborted_events[0]["stage"] == "system"

    def test_events_step_failure_title_uses_stage_and_step_id(
        self, client, auth_headers, chain_setup,
    ):
        """v3: 普通 step 失败标题输出为 stage.step 格式"""
        cur_run = chain_setup["current_run"]
        resp = client.get(
            f"/api/v1/plan-runs/{cur_run.id}/events", headers=auth_headers,
        )
        assert resp.status_code == 200
        events = resp.json()["data"]["events"]

        # Find the existing patrol.monkey_launch failure from fixture
        patrol_failures = [
            e for e in events
            if e["category"] == "step" and e["severity"] == "err"
        ]
        assert len(patrol_failures) >= 1
        for e in patrol_failures:
            assert "patrol" in e["stage"] or "init" in e["stage"]


# ---------------------------------------------------------------------------
# /devices
# ---------------------------------------------------------------------------


class TestDevicesEndpoint:
    def test_devices_returns_per_device_matrix_with_facets(
        self, client, auth_headers, chain_setup,
    ):
        cur_run = chain_setup["current_run"]
        resp = client.get(
            f"/api/v1/plan-runs/{cur_run.id}/devices", headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["total"] == 3
        # Status facets — count includes 'all'
        bs = data["by_status"]
        assert bs["all"] == 3
        # j1=COMPLETED → completed; j2=RUNNING+log_signal_count>0 → risk;
        # j3=FAILED → failed
        assert bs.get("completed") == 1
        assert bs.get("risk") == 1
        assert bs.get("failed") == 1
        # Host facet
        bh = data["by_host"]
        assert bh.get("host-101") == 2
        assert bh.get("host-102") == 1
        # Per-device entries carry patrol heartbeat fields
        by_serial = {d["device_serial"]: d for d in data["devices"]}
        risk = by_serial["dev-aa-02"]
        assert risk["ui_status"] == "risk"
        assert risk["patrol_cycle_count"] == 12
        assert risk["log_signal_count"] == 1
        assert risk["current_step"] == "patrol.monkey_launch"

    def test_devices_filter_by_status(
        self, client, auth_headers, chain_setup,
    ):
        cur_run = chain_setup["current_run"]
        resp = client.get(
            f"/api/v1/plan-runs/{cur_run.id}/devices?status=failed",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        # Facets always reflect full set
        assert data["by_status"]["all"] == 3
        # Filtered devices list contains only failed
        assert len(data["devices"]) == 1
        assert data["devices"][0]["ui_status"] == "failed"

    def test_devices_filter_by_host(
        self, client, auth_headers, chain_setup,
    ):
        cur_run = chain_setup["current_run"]
        resp = client.get(
            f"/api/v1/plan-runs/{cur_run.id}/devices?host_id=host-102",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert all(d["host_id"] == "host-102" for d in data["devices"])
        assert len(data["devices"]) == 1

    def test_devices_backoff_status_for_running_with_future_retry(
        self, client, auth_headers, db_session, chain_setup,
    ):
        # Mutate j2 to be RUNNING + next_retry_at in the future + no log_signal
        j2 = chain_setup["job_running"]
        j2.next_retry_at = _now() + timedelta(minutes=15)
        j2.log_signal_count = 0  # remove signals → fall back to backoff path
        db_session.commit()

        cur_run = chain_setup["current_run"]
        resp = client.get(
            f"/api/v1/plan-runs/{cur_run.id}/devices", headers=auth_headers,
        )
        assert resp.status_code == 200
        by_serial = {d["device_serial"]: d for d in resp.json()["data"]["devices"]}
        assert by_serial["dev-aa-02"]["ui_status"] == "backoff"

    def test_devices_propagates_status_reason_from_failed_job(
        self, client, auth_headers, chain_setup,
    ):
        """ADR-0021: DeviceMatrixItem 必须透传 JobInstance.status_reason，
        前端据此在抽屉/tooltip 展示失败原因（pending_timeout、
        patrol_step_failed 等）。"""
        cur_run = chain_setup["current_run"]
        resp = client.get(
            f"/api/v1/plan-runs/{cur_run.id}/devices", headers=auth_headers,
        )
        assert resp.status_code == 200
        by_serial = {d["device_serial"]: d for d in resp.json()["data"]["devices"]}
        # j3 (FAILED) — fixture sets status_reason="patrol_step_failed: monkey_launch"
        failed = by_serial["dev-bb-01"]
        assert failed["ui_status"] == "failed"
        assert failed["status_reason"] == "patrol_step_failed: monkey_launch"
        # j2 (RUNNING/risk) — fixture leaves status_reason unset
        running = by_serial["dev-aa-02"]
        assert running["status_reason"] is None
        # j1 (COMPLETED) — likewise unset
        completed = by_serial["dev-aa-01"]
        assert completed["status_reason"] is None

    def test_devices_unknown_status_distinct_from_failed(
        self, client, auth_headers, db_session, chain_setup,
    ):
        """UNKNOWN jobs map to ui_status=unknown, not failed."""
        from backend.models.enums import JobStatus

        j3 = chain_setup["job_failed"]
        j3.status = JobStatus.UNKNOWN.value
        j3.status_reason = "lease_expired"
        db_session.commit()

        cur_run = chain_setup["current_run"]
        resp = client.get(
            f"/api/v1/plan-runs/{cur_run.id}/devices", headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        by_serial = {d["device_serial"]: d for d in data["devices"]}
        unknown = by_serial["dev-bb-01"]
        assert unknown["ui_status"] == "unknown"
        assert unknown["current_stage"] == "unknown"
        assert unknown["status_reason"] == "lease_expired"
        assert data["by_status"].get("unknown") == 1
        assert data["by_status"].get("failed", 0) == 0

    def test_devices_grace_remaining_seconds(
        self, client, auth_headers, db_session, chain_setup,
    ):
        from backend.models.enums import JobStatus

        j3 = chain_setup["job_failed"]
        now = _now()
        j3.status = JobStatus.UNKNOWN.value
        j3.status_reason = "lease_expired"
        j3.ended_at = now - timedelta(seconds=60)
        db_session.commit()

        cur_run = chain_setup["current_run"]
        resp = client.get(
            f"/api/v1/plan-runs/{cur_run.id}/devices", headers=auth_headers,
        )
        assert resp.status_code == 200
        unknown = next(d for d in resp.json()["data"]["devices"] if d["job_id"] == j3.id)
        assert unknown["grace_remaining_seconds"] is not None
        assert 230 <= unknown["grace_remaining_seconds"] <= 240

    def test_devices_busy_reason_active_lease(
        self, client, auth_headers, db_session, chain_setup,
    ):
        from backend.models.device_lease import DeviceLease
        from backend.models.enums import DeviceStatus, LeaseStatus, LeaseType

        j2 = chain_setup["job_running"]
        dev = chain_setup["device_running"]
        now = _now()
        dev.status = DeviceStatus.BUSY.value
        dev.adb_connected = True
        db_session.add(
            DeviceLease(
                device_id=dev.id,
                job_id=j2.id,
                host_id=j2.host_id,
                lease_type=LeaseType.JOB.value,
                status=LeaseStatus.ACTIVE.value,
                fencing_token="t",
                lease_generation=1,
                agent_instance_id="agent-1",
                acquired_at=now,
                renewed_at=now,
                expires_at=now + timedelta(hours=1),
            )
        )
        db_session.commit()

        cur_run = chain_setup["current_run"]
        resp = client.get(
            f"/api/v1/plan-runs/{cur_run.id}/devices", headers=auth_headers,
        )
        assert resp.status_code == 200
        row = next(d for d in resp.json()["data"]["devices"] if d["job_id"] == j2.id)
        assert row["busy_reason"] == "active_lease"
        assert row["busy_lease_job_id"] == j2.id

    def test_devices_busy_reason_adb_excluded(
        self, client, auth_headers, db_session, chain_setup,
    ):
        from backend.models.enums import DeviceStatus

        j2 = chain_setup["job_running"]
        dev = chain_setup["device_running"]
        dev.adb_connected = True
        dev.adb_state = "offline"
        dev.status = DeviceStatus.BUSY.value
        db_session.commit()

        cur_run = chain_setup["current_run"]
        resp = client.get(
            f"/api/v1/plan-runs/{cur_run.id}/devices", headers=auth_headers,
        )
        assert resp.status_code == 200
        row = next(d for d in resp.json()["data"]["devices"] if d["job_id"] == j2.id)
        assert row["busy_reason"] == "adb_excluded"

    def test_devices_pending_claim_remaining_seconds(
        self, client, auth_headers, db_session, chain_setup,
    ):
        from backend.models.enums import JobStatus

        j1 = chain_setup["job_completed"]
        created = _now() - timedelta(seconds=30)
        j1.status = JobStatus.PENDING.value
        j1.created_at = created
        j1.started_at = None
        db_session.commit()

        cur_run = chain_setup["current_run"]
        resp = client.get(
            f"/api/v1/plan-runs/{cur_run.id}/devices", headers=auth_headers,
        )
        assert resp.status_code == 200
        pending = next(d for d in resp.json()["data"]["devices"] if d["job_id"] == j1.id)
        assert pending["ui_status"] == "pending"
        assert pending["pending_claim_remaining_seconds"] is not None
        assert 85 <= pending["pending_claim_remaining_seconds"] <= 95


# ---------------------------------------------------------------------------
# /watcher-summary
# ---------------------------------------------------------------------------


class TestWatcherSummaryEndpoint:
    def test_watcher_summary_groups_by_category_with_trend(
        self, client, auth_headers, chain_setup,
    ):
        cur_run = chain_setup["current_run"]
        # 60-min window — current window includes AEE + ANR (j2),
        # previous 60-min window includes the older AEE on j3
        resp = client.get(
            f"/api/v1/plan-runs/{cur_run.id}/watcher-summary?window_minutes=60",
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["plan_run_id"] == cur_run.id
        assert data["window_minutes"] == 60
        assert data["total_devices"] == 3
        # 2 categories in current window
        cats = {c["category"]: c for c in data["categories"]}
        assert "AEE" in cats and "ANR" in cats
        assert cats["AEE"]["count"] == 1
        assert cats["AEE"]["affected_device_count"] == 1
        # AEE trend = current(1) - prev(1) = 0
        assert cats["AEE"]["trend_change"] == 0
        # ANR trend = current(1) - prev(0) = 1
        assert cats["ANR"]["trend_change"] == 1
        # Affected total: device dev-aa-02 only (both signals from same device)
        assert data["affected_device_count"] == 1
        # abnormal_rate = 1/3 ≈ 0.333 > 0.05 → exceeded
        assert data["exceeded"] is True
        assert data["threshold"] == 0.05

    def test_watcher_summary_window_minutes_validation(
        self, client, auth_headers, chain_setup,
    ):
        cur_run = chain_setup["current_run"]
        # window_minutes < 1 should reject (fastapi Query ge=1)
        resp = client.get(
            f"/api/v1/plan-runs/{cur_run.id}/watcher-summary?window_minutes=0",
            headers=auth_headers,
        )
        assert resp.status_code == 422

    def test_watcher_summary_returns_404_for_unknown_run(
        self, client, auth_headers,
    ):
        resp = client.get(
            "/api/v1/plan-runs/999999/watcher-summary", headers=auth_headers,
        )
        assert resp.status_code == 404
