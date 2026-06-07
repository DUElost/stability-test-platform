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
  - devices derives ui_status correctly for completed/running/failed/unknown/backoff
  - watcher-summary buckets log_signals by category with trend vs prev window
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

import pytest

from backend.models.audit import AuditLog
from backend.api.routes import plan_runs as plan_run_routes
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
    dev1 = Device(
        serial="dev-aa-01", host_id="host-101", status="ONLINE",
        adb_connected=True, adb_state="device",
    )
    dev2 = Device(
        serial="dev-aa-02", host_id="host-101", status="BUSY",
        adb_connected=True, adb_state="device",
    )
    dev3 = Device(
        serial="dev-bb-01", host_id="host-102", status="OFFLINE",
        adb_connected=False, adb_state="offline",
    )
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
    j2 = JobInstance(  # Running, has 1 log_signal but should still remain running
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
        # j1=COMPLETED → completed; j2=RUNNING+log_signal_count>0 → running;
        # j3=FAILED → failed
        assert bs.get("completed") == 1
        assert bs.get("running") == 1
        assert bs.get("failed") == 1
        # Host facet
        bh = data["by_host"]
        assert bh.get("host-101") == 2
        assert bh.get("host-102") == 1
        # Per-device entries carry patrol heartbeat fields
        by_serial = {d["device_serial"]: d for d in data["devices"]}
        running = by_serial["dev-aa-02"]
        assert running["ui_status"] == "running"
        assert running["patrol_cycle_count"] == 12
        assert running["log_signal_count"] == 1
        assert running["current_step"] == "patrol.monkey_launch"

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
        # j2 (RUNNING) — fixture leaves status_reason unset
        running = by_serial["dev-aa-02"]
        assert running["status_reason"] is None
        # j1 (COMPLETED) — likewise unset
        completed = by_serial["dev-aa-01"]
        assert completed["status_reason"] is None

    def test_devices_running_job_with_offline_device_maps_to_unknown(
        self, client, auth_headers, db_session, chain_setup,
    ):
        from backend.models.enums import DeviceStatus

        j2 = chain_setup["job_running"]
        dev = chain_setup["device_running"]
        dev.status = DeviceStatus.OFFLINE.value
        dev.adb_connected = False
        dev.adb_state = "offline"
        db_session.commit()

        cur_run = chain_setup["current_run"]
        resp = client.get(
            f"/api/v1/plan-runs/{cur_run.id}/devices", headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        by_serial = {d["device_serial"]: d for d in data["devices"]}
        unknown = by_serial["dev-aa-02"]
        assert unknown["ui_status"] == "unknown"
        assert data["by_status"].get("unknown") == 1

    def test_devices_unknown_job_with_online_device_maps_to_failed(
        self, client, auth_headers, db_session, chain_setup,
    ):
        from backend.models.enums import DeviceStatus, JobStatus

        j3 = chain_setup["job_failed"]
        dev = chain_setup["device_failed"]
        j3.status = JobStatus.UNKNOWN.value
        j3.status_reason = "lease_expired"
        dev.status = DeviceStatus.ONLINE.value
        dev.adb_connected = True
        dev.adb_state = "device"
        db_session.commit()

        cur_run = chain_setup["current_run"]
        resp = client.get(
            f"/api/v1/plan-runs/{cur_run.id}/devices", headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        by_serial = {d["device_serial"]: d for d in data["devices"]}
        failed = by_serial["dev-bb-01"]
        assert failed["ui_status"] == "failed"
        assert data["by_status"].get("failed") == 1

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

    # ------------------------------------------------------------------
    # M0/PR #2: aee_breakdown JSONB 聚合
    # ------------------------------------------------------------------

    def test_watcher_summary_aee_breakdown_aggregates_by_package(
        self, client, auth_headers, chain_setup, db_session,
    ):
        """reconciler signal 按 package_name 聚合;crash/vendor_crash/anr 三类互斥。

        seed:
          - 2× AEE+CRASH for "com.app.a"(不同 nfs_path → 2 crash)
          - 1× VENDOR_AEE+CRASH for "com.vendor.b"
          - 1× AEE 但 extra.event_type=ANR for "com.app.c" → 计 anr 不计 crash
        预期:crash_total=2, vendor_crash=1, anr=2(fixture unknown ANR + com.app.c)
        """
        cur_run = chain_setup["current_run"]
        j1 = chain_setup["job_completed"]
        j2 = chain_setup["job_running"]

        db_session.add_all([
            JobLogSignal(
                id=20001,
                job_id=j1.id, host_id="host-101",
                device_serial=chain_setup["device_completed"].serial,
                seq_no=100, category="AEE", source="reconciler",
                path_on_device="/data/aee_exp/db.A1",
                detected_at=_now() - timedelta(minutes=2),
                extra={
                    "event_type": "CRASH",
                    "package_name": "com.app.a",
                    "aee_ts": "2026-05-28 10:00:00.000",
                    "nfs_path": f"/mnt/nfs/jobs/{j1.id}/AEE/db.A1",
                    "pull_source": "reconciler",
                },
            ),
            JobLogSignal(
                id=20002,
                job_id=j1.id, host_id="host-101",
                device_serial=chain_setup["device_completed"].serial,
                seq_no=101, category="AEE", source="reconciler",
                path_on_device="/data/aee_exp/db.A2",
                detected_at=_now() - timedelta(minutes=2),
                extra={
                    "event_type": "CRASH",
                    "package_name": "com.app.a",
                    "nfs_path": f"/mnt/nfs/jobs/{j1.id}/AEE/db.A2",
                    "pull_source": "reconciler",
                },
            ),
            JobLogSignal(
                id=20003,
                job_id=j2.id, host_id="host-101",
                device_serial=chain_setup["device_running"].serial,
                seq_no=20, category="VENDOR_AEE", source="reconciler",
                path_on_device="/data/vendor/aee_exp/db.B1",
                detected_at=_now() - timedelta(minutes=2),
                extra={
                    "event_type": "CRASH",
                    "package_name": "com.vendor.b",
                    "nfs_path": f"/mnt/nfs/jobs/{j2.id}/VENDOR_AEE/db.B1",
                    "pull_source": "reconciler",
                },
            ),
            JobLogSignal(
                id=20004,
                job_id=j2.id, host_id="host-101",
                device_serial=chain_setup["device_running"].serial,
                seq_no=21, category="AEE", source="reconciler",
                path_on_device="/data/aee_exp/db.C1",
                detected_at=_now() - timedelta(minutes=2),
                extra={
                    "event_type": "ANR",   # AEE 桶里塞了 ANR → 计 anr 不计 crash
                    "package_name": "com.app.c",
                    "nfs_path": f"/mnt/nfs/jobs/{j2.id}/AEE/db.C1",
                    "pull_source": "reconciler",
                },
            ),
        ])
        db_session.commit()

        resp = client.get(
            f"/api/v1/plan-runs/{cur_run.id}/watcher-summary?window_minutes=60",
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        bd = resp.json()["data"]["aee_breakdown"]
        assert bd is not None

        assert bd["crash_count"] == 2
        assert bd["vendor_crash_count"] == 1
        # fixture legacy ANR(id=10002,unknown 桶,path_on_device 兜底)+ com.app.c
        assert bd["anr_count"] == 2

        assert set(bd["packages"]) == {
            "com.app.a", "com.app.c", "com.vendor.b", "unknown",
        }

        # ORDER BY (crash+vendor+anr) DESC, pkg ASC:
        #   com.app.a=2 → first;平局 1 的按字典序:com.app.c < com.vendor.b < unknown
        pkg_order = [p["package_name"] for p in bd["by_package"]]
        assert pkg_order == ["com.app.a", "com.app.c", "com.vendor.b", "unknown"]

        a = next(p for p in bd["by_package"] if p["package_name"] == "com.app.a")
        assert a == {
            "package_name": "com.app.a",
            "crash_count": 2,
            "vendor_crash_count": 0,
            "anr_count": 0,
            "latest_detected_at": a["latest_detected_at"],   # 仅断言存在
        }
        c = next(p for p in bd["by_package"] if p["package_name"] == "com.app.c")
        assert c["crash_count"] == 0 and c["vendor_crash_count"] == 0
        assert c["anr_count"] == 1

    def test_watcher_summary_packages_empty_name_falls_into_unknown(
        self, client, auth_headers, chain_setup, db_session,
    ):
        """extra.package_name = "" 或缺失 → 归 unknown 桶,nfs_path 仍参与 DISTINCT。"""
        cur_run = chain_setup["current_run"]
        j1 = chain_setup["job_completed"]

        db_session.add_all([
            JobLogSignal(
                id=21001,
                job_id=j1.id, host_id="host-101",
                device_serial=chain_setup["device_completed"].serial,
                seq_no=200, category="AEE", source="reconciler",
                path_on_device="/data/aee_exp/db.E1",
                detected_at=_now() - timedelta(minutes=2),
                extra={"event_type": "CRASH", "package_name": "", "nfs_path": "/nfs/E1"},
            ),
            JobLogSignal(
                id=21002,
                job_id=j1.id, host_id="host-101",
                device_serial=chain_setup["device_completed"].serial,
                seq_no=201, category="AEE", source="reconciler",
                path_on_device="/data/aee_exp/db.E2",
                detected_at=_now() - timedelta(minutes=2),
                extra={"event_type": "CRASH", "nfs_path": "/nfs/E2"},  # 缺 package_name
            ),
        ])
        db_session.commit()

        resp = client.get(
            f"/api/v1/plan-runs/{cur_run.id}/watcher-summary?window_minutes=60",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        bd = resp.json()["data"]["aee_breakdown"]

        # 两条新 signal + fixture legacy 全部归 unknown
        assert bd["packages"] == ["unknown"]
        assert len(bd["by_package"]) == 1
        unknown = bd["by_package"][0]
        assert unknown["package_name"] == "unknown"
        # E1/E2 两个不同 nfs_path → crash_count=2;fixture AEE 因 nfs_path=NULL 不计
        assert unknown["crash_count"] == 2
        # fixture legacy ANR id=10002
        assert unknown["anr_count"] == 1

    def test_watcher_summary_aee_breakdown_treats_non_anr_event_type_as_crash(
        self, client, auth_headers, chain_setup, db_session,
    ):
        """真实设备 db_history 里的 JAVA (JE) / SIGSEGV 应落 crash,不能被 crash_count 漏掉。"""
        cur_run = chain_setup["current_run"]
        j1 = chain_setup["job_completed"]

        db_session.add(JobLogSignal(
            id=21011,
            job_id=j1.id, host_id="host-101",
            device_serial=chain_setup["device_completed"].serial,
            seq_no=211, category="AEE", source="reconciler",
            path_on_device="/data/aee_exp/db.LEGACY",
            detected_at=_now() - timedelta(minutes=2),
            extra={
                "event_type": "JAVA (JE)",
                "package_name": "com.legacy.crash",
                "nfs_path": "/nfs/legacy-je",
                "pull_source": "reconciler",
            },
        ))
        db_session.commit()

        resp = client.get(
            f"/api/v1/plan-runs/{cur_run.id}/watcher-summary?window_minutes=60",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        bd = resp.json()["data"]["aee_breakdown"]

        assert bd["crash_count"] == 1
        legacy = next(p for p in bd["by_package"] if p["package_name"] == "com.legacy.crash")
        assert legacy["crash_count"] == 1
        assert legacy["anr_count"] == 0

    def test_watcher_summary_dedup_crash_by_nfs_path(
        self, client, auth_headers, chain_setup, db_session,
    ):
        """同一 nfs_path 在不同 job/seq_no 上出现两次 → DISTINCT 后只计 1 次。"""
        cur_run = chain_setup["current_run"]
        j1 = chain_setup["job_completed"]
        j2 = chain_setup["job_running"]

        same_nfs = "/mnt/nfs/jobs/shared/AEE/db.duplicate"
        db_session.add_all([
            JobLogSignal(
                id=22001,
                job_id=j1.id, host_id="host-101",
                device_serial=chain_setup["device_completed"].serial,
                seq_no=300, category="AEE", source="reconciler",
                path_on_device="/data/aee_exp/db.duplicate",
                detected_at=_now() - timedelta(minutes=2),
                extra={
                    "event_type": "CRASH",
                    "package_name": "com.dup",
                    "nfs_path": same_nfs,
                },
            ),
            JobLogSignal(
                id=22002,
                job_id=j2.id, host_id="host-101",
                device_serial=chain_setup["device_running"].serial,
                seq_no=300, category="AEE", source="reconciler",
                path_on_device="/data/aee_exp/db.duplicate",
                detected_at=_now() - timedelta(minutes=1),
                extra={
                    "event_type": "CRASH",
                    "package_name": "com.dup",
                    "nfs_path": same_nfs,    # 同 nfs_path
                },
            ),
        ])
        db_session.commit()

        resp = client.get(
            f"/api/v1/plan-runs/{cur_run.id}/watcher-summary?window_minutes=60",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        bd = resp.json()["data"]["aee_breakdown"]

        dup_row = next(p for p in bd["by_package"] if p["package_name"] == "com.dup")
        # 两条 signal 同 nfs_path → DISTINCT 去重 → crash_count=1
        assert dup_row["crash_count"] == 1
        # 全局 crash_count(跨包累加):仅 com.dup 贡献 1
        assert bd["crash_count"] == 1

    def test_watcher_summary_legacy_anr_counted_via_path_on_device(
        self, client, auth_headers, chain_setup, db_session,
    ):
        """legacy ANR signal(extra=NULL,inotifyd 路径)按 path_on_device 兜底计数,
        与 reconciler 携带 extra 的 ANR 在同一 unknown 桶汇总。"""
        cur_run = chain_setup["current_run"]
        j1 = chain_setup["job_completed"]

        db_session.add(JobLogSignal(
            id=23001,
            job_id=j1.id, host_id="host-101",
            device_serial=chain_setup["device_completed"].serial,
            seq_no=400, category="ANR", source="inotifyd",
            path_on_device="/data/anr/legacy_j1",
            detected_at=_now() - timedelta(minutes=2),
            # extra 故意留 NULL → 模拟 legacy inotifyd 路径
        ))
        db_session.commit()

        resp = client.get(
            f"/api/v1/plan-runs/{cur_run.id}/watcher-summary?window_minutes=60",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        bd = resp.json()["data"]["aee_breakdown"]

        # 两条 legacy ANR 都没有 extra → 都落 unknown 桶
        assert bd["packages"] == ["unknown"]
        # path_on_device DISTINCT:/data/anr/anr_001(fixture)+ /data/anr/legacy_j1 = 2
        assert bd["anr_count"] == 2
        unknown = bd["by_package"][0]
        assert unknown["anr_count"] == 2

    def test_watcher_summary_aee_breakdown_none_when_no_jobs(
        self, client, auth_headers, chain_setup,
    ):
        """无关联 Job 的 PlanRun(parent_run)走早返回路径 → aee_breakdown 字段为 None。"""
        parent_run = chain_setup["parent_run"]
        resp = client.get(
            f"/api/v1/plan-runs/{parent_run.id}/watcher-summary",
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        # parent_run 在 fixture 中没有 JobInstance → 早返回
        assert data["total_devices"] == 0
        assert data["aee_breakdown"] is None

    # ----------------------------------------------------------------------
    # M1/T1-3a: 双写灰度态字段 legacy_patrol_in_snapshot + pull_sources
    # ----------------------------------------------------------------------

    def test_watcher_summary_dual_write_fields_default_false_and_empty(
        self, client, auth_headers, chain_setup,
    ):
        """fixture 默认 plan_snapshot 无 lifecycle.patrol;log_signal 无 extra.pull_source
        → legacy_patrol_in_snapshot=False, pull_sources=[]。"""
        cur_run = chain_setup["current_run"]
        resp = client.get(
            f"/api/v1/plan-runs/{cur_run.id}/watcher-summary?window_minutes=60",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["legacy_patrol_in_snapshot"] is False
        assert data["pull_sources"] == []

    def test_watcher_summary_legacy_patrol_true_when_scan_aee_in_patrol(
        self, client, auth_headers, db_session, chain_setup,
    ):
        """plan_snapshot.lifecycle.patrol.steps 含 script:scan_aee → True。
        同时验证早返回路径(无 Job 的 parent_run)也能正确返回 legacy 字段。"""
        cur_run = chain_setup["current_run"]
        cur_run.plan_snapshot = {
            "plan": {"id": cur_run.plan_id},
            "lifecycle": {
                "init": [{"step_id": "ensure_root", "action": "script:ensure_root"}],
                "patrol": {
                    "interval_seconds": 60,
                    "steps": [
                        {"step_id": "monkey_check", "action": "script:monkey_check"},
                        {"step_id": "scan_aee", "action": "script:scan_aee"},
                        {"step_id": "export_mobilelogs", "action": "script:export_mobilelogs"},
                    ],
                },
                "teardown": [],
            },
        }
        db_session.add(cur_run)
        db_session.commit()

        resp = client.get(
            f"/api/v1/plan-runs/{cur_run.id}/watcher-summary?window_minutes=60",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["legacy_patrol_in_snapshot"] is True

        # 早返回路径:parent_run 无 lifecycle → False
        parent_run = chain_setup["parent_run"]
        resp2 = client.get(
            f"/api/v1/plan-runs/{parent_run.id}/watcher-summary?window_minutes=60",
            headers=auth_headers,
        )
        assert resp2.status_code == 200
        assert resp2.json()["data"]["legacy_patrol_in_snapshot"] is False

    def test_watcher_summary_legacy_patrol_true_when_scan_aee_in_snapshot_steps(
        self, client, auth_headers, db_session, chain_setup,
    ):
        """当前真实 PlanRun 快照是 {plan, steps} 结构,也必须识别 legacy patrol。"""
        cur_run = chain_setup["current_run"]
        cur_run.plan_snapshot = {
            "plan": {"id": cur_run.plan_id},
            "steps": [
                {"stage": "init", "script_name": "ensure_root", "step_key": "ensure_root"},
                {"stage": "patrol", "script_name": "monkey_check", "step_key": "monkey_check"},
                {"stage": "patrol", "script_name": "scan_aee", "step_key": "scan_aee"},
                {"stage": "patrol", "script_name": "export_mobilelogs", "step_key": "export_mobilelogs"},
            ],
        }
        db_session.add(cur_run)
        db_session.commit()

        resp = client.get(
            f"/api/v1/plan-runs/{cur_run.id}/watcher-summary?window_minutes=60",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["legacy_patrol_in_snapshot"] is True

    def test_watcher_summary_legacy_patrol_false_when_patrol_is_list_without_legacy(
        self, client, auth_headers, db_session, chain_setup,
    ):
        """patrol 是 list 形态(早期 fallback)且不含 scan_aee → False。"""
        cur_run = chain_setup["current_run"]
        cur_run.plan_snapshot = {
            "lifecycle": {
                "patrol": [
                    {"step_id": "monkey_check", "action": "script:monkey_check"},
                ],
            },
        }
        db_session.add(cur_run)
        db_session.commit()

        resp = client.get(
            f"/api/v1/plan-runs/{cur_run.id}/watcher-summary?window_minutes=60",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["legacy_patrol_in_snapshot"] is False

    def test_watcher_summary_pull_sources_collects_distinct_reconciler(
        self, client, auth_headers, db_session, chain_setup,
    ):
        """log_signal.extra.pull_source distinct 入 pull_sources;旧无 extra signal 不计入。"""
        cur_run = chain_setup["current_run"]
        j1 = chain_setup["job_completed"]

        db_session.add_all([
            JobLogSignal(
                id=30001,
                job_id=j1.id, host_id="host-101",
                device_serial=chain_setup["device_completed"].serial,
                seq_no=300, category="AEE", source="reconciler",
                path_on_device="/data/aee_exp/db.PS1",
                detected_at=_now() - timedelta(minutes=2),
                extra={"pull_source": "reconciler", "package_name": "com.x"},
            ),
            JobLogSignal(  # 第二条同 source — 验证 DISTINCT 去重
                id=30002,
                job_id=j1.id, host_id="host-101",
                device_serial=chain_setup["device_completed"].serial,
                seq_no=301, category="AEE", source="reconciler",
                path_on_device="/data/aee_exp/db.PS2",
                detected_at=_now() - timedelta(minutes=1),
                extra={"pull_source": "reconciler", "package_name": "com.y"},
            ),
        ])
        db_session.commit()

        resp = client.get(
            f"/api/v1/plan-runs/{cur_run.id}/watcher-summary?window_minutes=60",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        # fixture 中 10001/10002 无 extra,只有新加的 2 条带 pull_source=reconciler
        # → distinct 集合 = ["reconciler"]
        assert data["pull_sources"] == ["reconciler"]

    # ----------------------------------------------------------------------
    # M0/C-6 (§2.4 #5): watcher_capability 快照
    # ----------------------------------------------------------------------

    def test_watcher_summary_capability_none_when_jobs_have_no_snapshot(
        self, client, auth_headers, chain_setup,
    ):
        """fixture 的 Job 都没回填 watcher_capability → 字段为 None。"""
        cur_run = chain_setup["current_run"]
        resp = client.get(
            f"/api/v1/plan-runs/{cur_run.id}/watcher-summary?window_minutes=60",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["watcher_capability"] is None

    def test_watcher_summary_capability_picks_most_degraded(
        self, client, auth_headers, db_session, chain_setup,
    ):
        """混合能力时取最降级的一档:unavailable 严重度高于 inotifyd_realtime。"""
        j1 = chain_setup["job_completed"]
        j2 = chain_setup["job_running"]
        j1.watcher_capability = "inotifyd_realtime"
        j2.watcher_capability = "unavailable"
        db_session.add_all([j1, j2])
        db_session.commit()

        cur_run = chain_setup["current_run"]
        resp = client.get(
            f"/api/v1/plan-runs/{cur_run.id}/watcher-summary?window_minutes=60",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["watcher_capability"] == "unavailable"

    def test_watcher_summary_capability_normal_when_all_inotifyd(
        self, client, auth_headers, db_session, chain_setup,
    ):
        """全部 inotifyd_realtime → 返回该值(前端不会显示降级徽章)。"""
        for key in ("job_completed", "job_running", "job_failed"):
            j = chain_setup[key]
            j.watcher_capability = "inotifyd_realtime"
            db_session.add(j)
        db_session.commit()

        cur_run = chain_setup["current_run"]
        resp = client.get(
            f"/api/v1/plan-runs/{cur_run.id}/watcher-summary?window_minutes=60",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["watcher_capability"] == "inotifyd_realtime"

    def test_watcher_summary_capability_none_for_run_without_jobs(
        self, client, auth_headers, chain_setup,
    ):
        """无 Job 的 PlanRun(早返回路径)→ watcher_capability=None。"""
        parent_run = chain_setup["parent_run"]
        resp = client.get(
            f"/api/v1/plan-runs/{parent_run.id}/watcher-summary",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["watcher_capability"] is None


# ---------------------------------------------------------------------------
# /aee-reconciliation (M1 / T1-4)
# ---------------------------------------------------------------------------


class TestAeeReconciliationEndpoint:
    def test_reconciliation_groups_by_pull_source_and_serial(
        self, client, auth_headers, db_session, chain_setup,
    ):
        """三路 pull_source(reconciler/legacy_patrol/inotifyd)分桶 + by_serial 聚合;
        reconciler 按 nfs_path 去重(同目录算一次)。NFS 未配置 → nfs_dbg_files=None。"""
        cur_run = chain_setup["current_run"]
        j1 = chain_setup["job_completed"]
        j2 = chain_setup["job_running"]
        s1 = chain_setup["device_completed"].serial
        s2 = chain_setup["device_running"].serial

        db_session.add_all([
            # reconciler:2 不同 nfs_path → 2;另 1 条重复 nfs_path → 去重不增
            JobLogSignal(
                id=40001, job_id=j1.id, host_id="host-101", device_serial=s1,
                seq_no=400, category="AEE", source="reconciler",
                path_on_device="/data/aee_exp/db.R1",
                detected_at=_now() - timedelta(minutes=2),
                extra={"pull_source": "reconciler", "nfs_path": "/nfs/j1/AEE/db.R1"},
            ),
            JobLogSignal(
                id=40002, job_id=j1.id, host_id="host-101", device_serial=s1,
                seq_no=401, category="VENDOR_AEE", source="reconciler",
                path_on_device="/data/vendor/aee_exp/db.R2",
                detected_at=_now() - timedelta(minutes=2),
                extra={"pull_source": "reconciler", "nfs_path": "/nfs/j1/VENDOR_AEE/db.R2"},
            ),
            JobLogSignal(
                id=40003, job_id=j1.id, host_id="host-101", device_serial=s1,
                seq_no=402, category="AEE", source="reconciler",
                path_on_device="/data/aee_exp/db.R1.dup",
                detected_at=_now() - timedelta(minutes=1),
                extra={"pull_source": "reconciler", "nfs_path": "/nfs/j1/AEE/db.R1"},  # 同 nfs_path
            ),
            # legacy_patrol on serial 2
            JobLogSignal(
                id=40004, job_id=j2.id, host_id="host-101", device_serial=s2,
                seq_no=410, category="AEE", source="legacy_patrol",
                path_on_device="/data/aee_exp/db.L1",
                detected_at=_now() - timedelta(minutes=2),
                extra={"pull_source": "legacy_patrol", "nfs_path": "/nfs/j2/AEE/db.L1"},
            ),
            # inotifyd-sourced AEE (理论上 M0 已关闭,但历史数据可能存在)
            JobLogSignal(
                id=40005, job_id=j2.id, host_id="host-101", device_serial=s2,
                seq_no=411, category="AEE", source="inotifyd",
                path_on_device="/data/aee_exp/db.I1",
                detected_at=_now() - timedelta(minutes=2),
                extra={"pull_source": "inotifyd"},
            ),
        ])
        db_session.commit()

        resp = client.get(
            f"/api/v1/plan-runs/{cur_run.id}/aee-reconciliation",
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]

        assert data["plan_run_id"] == cur_run.id
        # reconciler:db.R1 + db.R2 = 2(db.R1 重复 nfs_path 去重)
        assert data["reconciler_emitted"] == 2
        assert data["legacy_patrol_emitted"] == 1
        assert data["inotifyd_emitted"] == 1
        # chain_setup fixture 预置 2 条无 pull_source 的 legacy AEE 信号
        #   (10001@s2 / 10003@s3)→ 落 "other" 桶。
        assert data["other_emitted"] == 2
        assert data["total_emitted"] == 6

        s3 = chain_setup["device_failed"].serial
        by_serial = {r["device_serial"]: r for r in data["by_serial"]}
        assert by_serial[s1]["reconciler"] == 2
        assert by_serial[s1]["total"] == 2
        assert by_serial[s2]["legacy_patrol"] == 1
        assert by_serial[s2]["inotifyd"] == 1
        assert by_serial[s2]["other"] == 1   # fixture 10001@s2
        assert by_serial[s2]["total"] == 3
        assert by_serial[s3]["other"] == 1   # fixture 10003@s3
        assert by_serial[s3]["total"] == 1

        # 无本 Run 可推导的 nfs_path / 设备目录 → NFS 侧为 None + note
        assert data["nfs_dbg_files"] is None
        assert data["nfs_root_scanned"] is None
        assert data["signal_nfs_paths_checked"] == 0
        assert data["nfs_entries_verified"] == 0
        assert data["missing_on_disk"] == []
        assert data["missing_in_signal"] == []
        assert any("无法限定本 Run 的 NFS 范围" in n for n in data["notes"])

    def test_reconciliation_ignores_non_aee_categories(
        self, client, auth_headers, db_session, chain_setup,
    ):
        """ANR/MOBILELOG 不计入 AEE 对账(端点只比 AEE/VENDOR_AEE)。"""
        cur_run = chain_setup["current_run"]
        j2 = chain_setup["job_running"]
        s2 = chain_setup["device_running"].serial
        db_session.add_all([
            JobLogSignal(
                id=41001, job_id=j2.id, host_id="host-101", device_serial=s2,
                seq_no=420, category="ANR", source="inotifyd",
                path_on_device="/data/anr/trace_x",
                detected_at=_now() - timedelta(minutes=2),
                extra={"pull_source": "inotifyd"},
            ),
            JobLogSignal(
                id=41002, job_id=j2.id, host_id="host-101", device_serial=s2,
                seq_no=421, category="AEE", source="reconciler",
                path_on_device="/data/aee_exp/db.OK",
                detected_at=_now() - timedelta(minutes=2),
                extra={"pull_source": "reconciler", "nfs_path": "/nfs/j2/AEE/db.OK"},
            ),
        ])
        db_session.commit()

        resp = client.get(
            f"/api/v1/plan-runs/{cur_run.id}/aee-reconciliation",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        # 仅 reconciler 的 AEE 计 1;ANR 不计入。
        # fixture 预置 2 条 legacy AEE(无 pull_source)落 other → total = 1 + 2。
        assert data["reconciler_emitted"] == 1
        assert data["other_emitted"] == 2
        assert data["total_emitted"] == 3

    def test_reconciliation_empty_for_run_without_jobs(
        self, client, auth_headers, chain_setup,
    ):
        """无 Job 的 PlanRun → 全 0 + note 提示。"""
        parent_run = chain_setup["parent_run"]
        resp = client.get(
            f"/api/v1/plan-runs/{parent_run.id}/aee-reconciliation",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["total_emitted"] == 0
        assert data["by_serial"] == []
        assert any("无关联 Job" in n for n in data["notes"])

    def test_reconciliation_404_for_unknown_run(self, client, auth_headers):
        resp = client.get(
            "/api/v1/plan-runs/999999/aee-reconciliation", headers=auth_headers,
        )
        assert resp.status_code == 404

    def test_reconciliation_nfs_scoped_to_plan_run_signals(
        self, client, auth_headers, db_session, chain_setup, monkeypatch, tmp_path,
    ):
        """NFS *.dbg 扫描限定在本 Run 信号 nfs_path 推导的设备目录,不扫全库。"""
        nfs_root = tmp_path / "aee_nfs"
        cur_run = chain_setup["current_run"]
        j1 = chain_setup["job_completed"]
        s1 = chain_setup["device_completed"].serial
        s2 = chain_setup["device_running"].serial

        run_a_dir = nfs_root / "RunA" / s1 / "AEE" / "crash_a"
        run_a_dir.mkdir(parents=True)
        (run_a_dir / "a1.dbg").write_text("a")
        (run_a_dir / "a2.dbg").write_text("b")

        run_b_dir = nfs_root / "RunB" / s2 / "AEE" / "crash_b"
        run_b_dir.mkdir(parents=True)
        for i in range(3):
            (run_b_dir / f"b{i}.dbg").write_text("x")

        monkeypatch.setenv("STP_AEE_NFS_ROOT", str(nfs_root))

        db_session.add(
            JobLogSignal(
                id=42001, job_id=j1.id, host_id="host-101", device_serial=s1,
                seq_no=500, category="AEE", source="reconciler",
                path_on_device="/data/aee_exp/db.A",
                detected_at=_now() - timedelta(minutes=2),
                extra={
                    "pull_source": "reconciler",
                    "nfs_path": str(run_a_dir),
                },
            ),
        )
        db_session.commit()

        resp = client.get(
            f"/api/v1/plan-runs/{cur_run.id}/aee-reconciliation",
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]

        assert data["nfs_dbg_files"] == 2
        assert "RunA" in (data["nfs_root_scanned"] or "")
        assert "RunB" not in (data["nfs_root_scanned"] or "")
        assert "crash_b" not in data["missing_in_signal"]
        assert data["missing_in_signal"] == []

    def test_reconciliation_skips_outside_artifact_roots_in_scan_summary(
        self, client, auth_headers, db_session, chain_setup, monkeypatch, tmp_path,
    ):
        """仅白名单内 nfs_path 计入 checked/scanned;白名单外路径不伪装成已扫描。"""
        nfs_root = tmp_path / "aee_nfs"
        cur_run = chain_setup["current_run"]
        j1 = chain_setup["job_completed"]
        s1 = chain_setup["device_completed"].serial

        allowed_dir = nfs_root / "RunA" / s1 / "AEE" / "crash_ok"
        allowed_dir.mkdir(parents=True)
        (allowed_dir / "ok.dbg").write_text("ok")

        monkeypatch.setenv("STP_AEE_NFS_ROOT", str(nfs_root))

        db_session.add_all([
            JobLogSignal(
                id=43001, job_id=j1.id, host_id="host-101", device_serial=s1,
                seq_no=510, category="AEE", source="reconciler",
                path_on_device="/data/aee_exp/db.OK",
                detected_at=_now() - timedelta(minutes=2),
                extra={
                    "pull_source": "reconciler",
                    "nfs_path": str(allowed_dir),
                },
            ),
            JobLogSignal(
                id=43002, job_id=j1.id, host_id="host-101", device_serial=s1,
                seq_no=511, category="AEE", source="legacy_patrol",
                path_on_device="/data/aee_exp/db.SKIP",
                detected_at=_now() - timedelta(minutes=2),
                extra={
                    "pull_source": "legacy_patrol",
                    "nfs_path": "/outside/root/crash_skip",
                },
            ),
        ])
        db_session.commit()

        resp = client.get(
            f"/api/v1/plan-runs/{cur_run.id}/aee-reconciliation",
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]

        assert data["nfs_dbg_files"] == 1
        assert data["signal_nfs_paths_checked"] == 1
        assert data["nfs_entries_verified"] == 1
        assert str(allowed_dir) in (data["nfs_root_scanned"] or "")
        assert "/outside/root/crash_skip" not in (data["nfs_root_scanned"] or "")
        assert any("白名单外 nfs_path" in n for n in data["notes"])

    def test_reconciliation_marks_remote_linux_nfs_path_as_unverifiable(
        self, client, auth_headers, db_session, chain_setup, monkeypatch, tmp_path,
    ):
        """Windows 后端看到远端 Linux host 路径时，不应误报成白名单外。"""
        nfs_root = tmp_path / "aee_nfs"
        cur_run = chain_setup["current_run"]
        j1 = chain_setup["job_completed"]
        serial = chain_setup["device_completed"].serial

        monkeypatch.setenv("STP_AEE_NFS_ROOT", str(nfs_root))
        monkeypatch.setattr(plan_run_routes, "_IS_WINDOWS", True)

        db_session.add(
            JobLogSignal(
                id=43501, job_id=j1.id, host_id="host-101", device_serial=serial,
                seq_no=512, category="AEE", source="reconciler",
                path_on_device="/data/aee_exp/db.REMOTE",
                detected_at=_now() - timedelta(minutes=2),
                extra={
                    "pull_source": "reconciler",
                    "nfs_path": "/home/android/sonic_agent/logs/ftp_log/job-13/db.27.JE",
                },
            ),
        )
        db_session.commit()

        resp = client.get(
            f"/api/v1/plan-runs/{cur_run.id}/aee-reconciliation",
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]

        assert data["signal_nfs_paths_checked"] == 0
        assert data["nfs_entries_verified"] == 0
        assert any("远端 nfs_path" in n for n in data["notes"])
        assert not any("白名单外 nfs_path" in n for n in data["notes"])

    def test_reconciliation_missing_entry_keeps_crash_dir_label(
        self, client, auth_headers, db_session, chain_setup, monkeypatch, tmp_path,
    ):
        """signal 指向缺失 crash 目录时，应标记该目录名，不回退到 AEE 父目录。"""
        nfs_root = tmp_path / "aee_nfs"
        cur_run = chain_setup["current_run"]
        j1 = chain_setup["job_completed"]
        s1 = chain_setup["device_completed"].serial

        missing_dir = nfs_root / "RunA" / s1 / "AEE" / "crash_missing"
        monkeypatch.setenv("STP_AEE_NFS_ROOT", str(nfs_root))

        db_session.add(
            JobLogSignal(
                id=44001, job_id=j1.id, host_id="host-101", device_serial=s1,
                seq_no=520, category="AEE", source="reconciler",
                path_on_device="/data/aee_exp/db.MISS",
                detected_at=_now() - timedelta(minutes=2),
                extra={
                    "pull_source": "reconciler",
                    "nfs_path": str(missing_dir),
                },
            ),
        )
        db_session.commit()

        resp = client.get(
            f"/api/v1/plan-runs/{cur_run.id}/aee-reconciliation",
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]

        assert data["signal_nfs_paths_checked"] == 1
        assert data["nfs_entries_verified"] == 0
        assert data["missing_on_disk"] == ["crash_missing"]
        assert "AEE" not in data["missing_on_disk"]

    def test_reconciliation_same_device_folder_two_entries_only_run_signals(
        self, client, auth_headers, db_session, chain_setup, monkeypatch, tmp_path,
    ):
        """同 {folder}/{serial} 下多 crash 条目(MMDD 共享布局);仅计本 Run signal 指向的条目。"""
        nfs_root = tmp_path / "aee_nfs"
        folder = "X6851-OP_16.3.0.022_SU_0530_MonkeyAEEinfo"
        serial = chain_setup["device_completed"].serial
        cur_run = chain_setup["current_run"]
        j1 = chain_setup["job_completed"]

        base = nfs_root / folder / serial / "aee_exp"
        entry_first = base / "20250530_103000_db.01"
        entry_second = base / "20250530_140000_db.02"
        entry_first.mkdir(parents=True)
        entry_second.mkdir(parents=True)
        (entry_first / "a1.dbg").write_text("a")
        (entry_first / "a2.dbg").write_text("b")
        (entry_second / "b1.dbg").write_text("c")

        monkeypatch.setenv("STP_AEE_NFS_ROOT", str(nfs_root))

        db_session.add(
            JobLogSignal(
                id=45001, job_id=j1.id, host_id="host-101", device_serial=serial,
                seq_no=530, category="AEE", source="reconciler",
                path_on_device="/data/aee_exp/db.02",
                detected_at=_now() - timedelta(minutes=2),
                extra={
                    "pull_source": "reconciler",
                    "nfs_path": str(entry_second),
                },
            ),
        )
        db_session.commit()

        resp = client.get(
            f"/api/v1/plan-runs/{cur_run.id}/aee-reconciliation",
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]

        assert data["nfs_dbg_files"] == 1
        assert data["signal_nfs_paths_checked"] == 1
        assert data["nfs_entries_verified"] == 1
        scanned = data["nfs_root_scanned"] or ""
        assert str(entry_second) in scanned
        assert str(entry_first) not in scanned
        assert data["missing_in_signal"] == []
