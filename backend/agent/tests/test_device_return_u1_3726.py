# -*- coding: utf-8 -*-
"""#3726 / #3723 U1 — Agent 解除执行保证与解除结果回报反例。

每条用例在「修复前」必须为红：本文件用可逆变异（``_PRE_FIX_BEHAVIOR``）
复现旧语义并断言其违反不变量；默认跑修复后路径必须为绿。
"""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from backend.agent.contracts.device_return import (
    DISARM_RESULT_CONFIRMED,
    DISARM_RESULT_EXECUTED_UNVERIFIED,
    DISARM_RESULT_FAILED,
    DISARM_RESULT_INCOMPLETE,
    DISARM_RESULT_NOT_EXECUTED,
    DISARM_RESULT_READBACK_FAILED,
    DISARM_RESULTS,
    EXECUTING_DISARM,
    FAMILY_STEPS,
    LEASE_LOST_REASON,
    ONBOARDED,
    map_step_outcome_to_disarm_result,
    note_footprint_started,
    select_disarm_steps,
)
from backend.agent.graceful_shutdown import shutdown_agent_runtime
from backend.agent.operation_scheduler import OperationScheduler
from backend.agent.pipeline_engine import PipelineEngine, StepResult
from backend.agent.recovery_executor import execute_recovery_actions_impl


# Flip to True only when proving pre-fix redness (documented in Agent Note).
_PRE_FIX_BEHAVIOR = False


def _bare_engine(**kwargs):
    engine = PipelineEngine.__new__(PipelineEngine)
    engine._scheduler = kwargs.get("scheduler")
    engine._device_id = kwargs.get("device_id", 1)
    engine._canceled = False
    engine._is_aborted = kwargs.get("is_aborted", lambda: False)
    engine._is_lock_lost = kwargs.get("is_lock_lost", lambda: False)
    engine._run_id = kwargs.get("run_id", 1)
    engine._api_url = kwargs.get("api_url", None)
    engine._agent_secret = ""
    engine._fencing_token = "tok"
    engine._footprint_families = set()
    engine._disarm_results = {}
    engine._lease_lost_for_disarm = False
    engine._active_phase = None
    engine._active_process = None
    engine._process_lock = threading.Lock()
    engine._update_execution_state = kwargs.get(
        "update_state", lambda *_a, **_k: None
    )
    engine._last_step_error = ""
    return engine


# ── 六值映射 ─────────────────────────────────────────────────────────────────


class TestSixValueMapping:
    def test_all_six_values_reachable(self):
        mapping = {
            DISARM_RESULT_NOT_EXECUTED: map_step_outcome_to_disarm_result(
                started=False
            ),
            DISARM_RESULT_NOT_EXECUTED + ":lease": map_step_outcome_to_disarm_result(
                started=False, lease_lost=True
            ),
            DISARM_RESULT_INCOMPLETE: map_step_outcome_to_disarm_result(
                started=True, incomplete=True
            ),
            DISARM_RESULT_EXECUTED_UNVERIFIED: map_step_outcome_to_disarm_result(
                started=True, success=True, onboarded_family=False
            ),
            DISARM_RESULT_CONFIRMED: map_step_outcome_to_disarm_result(
                started=True,
                success=True,
                onboarded_family=True,
                readback="clear",
            ),
            DISARM_RESULT_FAILED: map_step_outcome_to_disarm_result(
                started=True, success=False, exit_code=1
            ),
            DISARM_RESULT_READBACK_FAILED: map_step_outcome_to_disarm_result(
                started=True, success=True, readback="readback_failed"
            ),
            DISARM_RESULT_INCOMPLETE + ":sig": map_step_outcome_to_disarm_result(
                started=True, success=False, exit_code=143
            ),
        }
        reached = {v for v in mapping.values()}
        assert reached == set(DISARM_RESULTS)

    def test_onboarded_empty_in_u1(self):
        assert ONBOARDED == {}
        assert "power-cycle" in FAMILY_STEPS


# ── permit 上限 1 + 三 job 同时中止 → 三个 teardown 都执行 ────────────────────


class TestConcurrentTeardownPermit:
    def test_three_teardowns_run_under_permit_cap_one(self):
        """permit=1, 3 jobs abort concurrently → all 3 teardown steps execute.

        Pre-fix: canceled waiters abandon on PermitWaitTimeout → red.
        """
        scheduler = OperationScheduler(max_concurrent=1)
        started = []
        started_lock = threading.Lock()
        release_gate = threading.Event()

        def run_one(job_id: int, device_id: int) -> bool:
            engine = _bare_engine(scheduler=scheduler, device_id=device_id, run_id=job_id)
            engine._footprint_families.add("power-cycle")
            engine._canceled = True  # abort already signaled

            if _PRE_FIX_BEHAVIOR:
                # Reversible mutation: old wait loop abandons on cancel.
                def old_wait(phase, step, **kwargs):
                    from backend.agent.operation_scheduler import (
                        PermitWaitTimeout,
                        SchedulerShutdown,
                        PermitCancelled,
                        PermitDenied,
                    )

                    engine._update_execution_state("WAITING_EXECUTION_SLOT")
                    while True:
                        try:
                            permit = scheduler.acquire(device_id, timeout=0.2)
                            break
                        except PermitWaitTimeout:
                            if engine._is_lock_lost() or engine._canceled:
                                return False
                        except (SchedulerShutdown, PermitCancelled, PermitDenied):
                            return False
                    try:
                        with started_lock:
                            started.append(job_id)
                        release_gate.wait(2.0)
                        return True
                    finally:
                        permit.release()

                return old_wait(
                    "teardown",
                    {
                        "step_id": "td",
                        "action": "script:powercycle_finish",
                        "timeout_seconds": 5,
                    },
                )

            def fake_retry(phase, step, **kwargs):
                with started_lock:
                    started.append(job_id)
                release_gate.wait(2.0)
                return True

            engine._run_step_with_retry = fake_retry
            return engine._run_step_with_permit(
                "teardown",
                {
                    "step_id": "td",
                    "action": "script:powercycle_finish",
                    "version": "1.0.0",
                    "timeout_seconds": 5,
                },
            )

        threads = [
            threading.Thread(target=run_one, args=(100 + i, 10 + i))
            for i in range(3)
        ]
        for t in threads:
            t.start()
        # Let all three contend for the single permit.
        time.sleep(0.3)
        release_gate.set()
        for t in threads:
            t.join(timeout=5.0)
            assert not t.is_alive()

        assert sorted(started) == [100, 101, 102], (
            f"expected all 3 teardowns; got {started} "
            f"(pre_fix={_PRE_FIX_BEHAVIOR})"
        )


# ── teardown 中 abort 不杀子进程 ─────────────────────────────────────────────


class TestCancelDoesNotKillTeardown:
    def test_cancel_during_teardown_skips_terminate(self):
        engine = _bare_engine()
        engine._active_phase = "teardown"
        proc = MagicMock()
        proc.poll.return_value = None
        engine._active_process = proc
        engine._active_script_path = "powercycle_finish.py"

        terminated = []

        def fake_terminate(p, grace_seconds=0):
            terminated.append(p)

        with patch(
            "backend.agent.pipeline_engine._terminate_process_tree",
            side_effect=fake_terminate,
        ):
            if _PRE_FIX_BEHAVIOR:
                # Old cancel always terminated regardless of phase.
                engine._canceled = True
                with engine._process_lock:
                    proc2 = engine._active_process
                    getattr(engine, "_active_script_path", None)
                if proc2 is not None:
                    fake_terminate(proc2)
            else:
                engine.cancel()

        assert engine._canceled is True
        # U1 invariant (must hold after fix; red under pre-fix mutation):
        assert terminated == [], "teardown subprocess must not be terminated"


# ── ABORT_LOCAL 不在 worker 存活时清令牌 ─────────────────────────────────────


class TestAbortLocalDefersCleanup:
    def test_abort_local_defers_when_worker_alive(self):
        local_db = MagicMock()
        lease_renewer = MagicMock()
        abort = MagicMock()
        resp = {
            "actions": [
                {
                    "job_id": 7,
                    "device_id": 70,
                    "action": "ABORT_LOCAL",
                    "reason": "test",
                }
            ],
            "outbox_actions": [],
        }

        if _PRE_FIX_BEHAVIOR:
            # Old path: always clear immediately.
            abort(7)
            local_db.delete_active_job(7)
            lease_renewer.clear_fencing_token(7)
            abort.assert_called_once_with(7)
            local_db.delete_active_job.assert_called_once_with(7)
            lease_renewer.clear_fencing_token.assert_called_once_with(7)
            pytest.fail("pre-fix path clears fencing while worker alive — expected red")

        execute_recovery_actions_impl(
            resp=resp,
            active_jobs_by_id={7: {"job_id": 7}},
            lease_renewer=lease_renewer,
            local_db=local_db,
            outbox_drain=MagicMock(),
            register_active_job=MagicMock(),
            abort_local_job=abort,
            is_worker_alive=lambda jid: jid == 7,
        )
        abort.assert_called_once_with(7)
        local_db.delete_active_job.assert_not_called()
        lease_renewer.clear_fencing_token.assert_not_called()

    def test_abort_local_clears_when_worker_gone(self):
        local_db = MagicMock()
        lease_renewer = MagicMock()
        abort = MagicMock()
        resp = {
            "actions": [
                {
                    "job_id": 7,
                    "device_id": 70,
                    "action": "ABORT_LOCAL",
                    "reason": "test",
                }
            ],
            "outbox_actions": [],
        }
        execute_recovery_actions_impl(
            resp=resp,
            active_jobs_by_id={},
            lease_renewer=lease_renewer,
            local_db=local_db,
            outbox_drain=MagicMock(),
            register_active_job=MagicMock(),
            abort_local_job=abort,
            is_worker_alive=lambda jid: False,
        )
        abort.assert_called_once_with(7)
        local_db.delete_active_job.assert_called_once_with(7)
        lease_renewer.clear_fencing_token.assert_called_once_with(7)

    def test_cleanup_still_immediate(self):
        local_db = MagicMock()
        lease_renewer = MagicMock()
        abort = MagicMock()
        resp = {
            "actions": [
                {
                    "job_id": 5,
                    "device_id": 50,
                    "action": "CLEANUP",
                    "reason": "boot_id_mismatch",
                }
            ],
            "outbox_actions": [],
        }
        execute_recovery_actions_impl(
            resp=resp,
            active_jobs_by_id={5: {"job_id": 5}},
            lease_renewer=lease_renewer,
            local_db=local_db,
            outbox_drain=MagicMock(),
            register_active_job=MagicMock(),
            abort_local_job=abort,
            is_worker_alive=lambda jid: True,
        )
        abort.assert_called_once_with(5)
        local_db.delete_active_job.assert_called_once_with(5)
        lease_renewer.clear_fencing_token.assert_called_once_with(5)


# ── manual_exit 执行解除 ─────────────────────────────────────────────────────


class TestManualExitRunsDisarm:
    def test_manual_exit_executes_disarm_not_skip(self):
        adb = MagicMock()
        mq = MagicMock()
        mq.connected = False
        engine = PipelineEngine(
            adb=adb,
            serial="S",
            run_id=1,
            log_dir="/tmp/u1-test",
            mq_producer=mq,
            api_url=None,
            is_aborted=lambda: False,
        )
        engine._verify_device_lease = lambda: None
        engine._archive_logs = lambda: None
        engine._footprint_families.add("power-cycle")

        called = {}

        def teardown(td, disarm_only=False):
            called["disarm_only"] = disarm_only
            called["steps"] = list(td or [])
            return StepResult(
                success=True,
                metadata={
                    "teardown_status": "SUCCESS",
                    "disarm_results": {
                        "power-cycle": {"result": DISARM_RESULT_EXECUTED_UNVERIFIED}
                    },
                },
            )

        engine._execute_teardown_best_effort = teardown
        engine._run_patrol_loop = lambda *a, **k: ("manual_exit", "")

        pipeline = {
            "lifecycle": {
                "timeout_seconds": 0,
                "init": [
                    {
                        "step_id": "setup",
                        "action": "script:powercycle_setup",
                        "version": "1.0.0",
                    }
                ],
                "patrol": {"interval_seconds": 0, "steps": []},
                "teardown": [
                    {
                        "step_id": "finish",
                        "action": "script:powercycle_finish",
                        "version": "1.0.0",
                    }
                ],
            }
        }
        # Skip real init
        engine._run_lifecycle_steps = lambda phase, steps: StepResult(success=True)

        if _PRE_FIX_BEHAVIOR:
            # Old BO4: skip teardown on manual_exit.
            result = StepResult(
                success=False,
                metadata={
                    "termination_reason": "manual_exit",
                    "teardown_status": "SKIPPED",
                },
            )
            assert result.metadata["teardown_status"] == "SKIPPED"
            pytest.fail("pre-fix manual_exit skipped teardown — expected red vs U1")

        result = engine._execute_lifecycle(pipeline)
        assert result.metadata["termination_reason"] == "manual_exit"
        assert called.get("disarm_only") is True
        assert called.get("steps"), "disarm steps must run"
        assert "disarm_results" in result.metadata


# ── 停机：先 abort，20s 窗口，再关调度器 ─────────────────────────────────────


class TestGracefulShutdownDisarmWindow:
    def test_abort_before_scheduler_shutdown(self):
        coordinator = MagicMock()
        scheduler = MagicMock()
        runner = MagicMock()
        # Becomes empty after first poll so wait returns quickly.
        runner.active_runners = {1: object()}

        def abort(jid):
            runner.active_runners = {}

        runner.request_abort.side_effect = abort
        executor = MagicMock()
        step = MagicMock()
        step.drain_sync.return_value = 0
        outbox = MagicMock()
        outbox.drain_sync.return_value = 0

        order = []
        scheduler.shutdown.side_effect = lambda: order.append("scheduler")
        original_abort = runner.request_abort.side_effect

        def abort_and_log(jid):
            order.append("abort")
            return original_abort(jid)

        runner.request_abort.side_effect = abort_and_log

        with (
            patch("backend.agent.graceful_shutdown.LocalDiskMonitor"),
            patch("backend.agent.graceful_shutdown.LogArchiver"),
            patch("backend.agent.graceful_shutdown.EventUploader"),
            patch("backend.agent.graceful_shutdown._await_started_disarm") as wait,
        ):
            wait.side_effect = lambda *a, **k: order.append("wait")
            shutdown_agent_runtime(
                coordinator=coordinator,
                operation_scheduler=scheduler,
                job_runner_state=runner,
                executor=executor,
                step_trace_uploader=step,
                outbox_drain=outbox,
                log_signal_drainer=None,
                recovery_sync_stop=MagicMock(),
                recovery_sync_thread=MagicMock(),
                heartbeat_thread=MagicMock(),
                lease_renewer=MagicMock(),
                mq_producer=MagicMock(),
                local_db=MagicMock(),
                sio_client=MagicMock(),
            )

        if _PRE_FIX_BEHAVIOR:
            # Old order: scheduler then abort.
            assert order.index("scheduler") < order.index("abort")
            pytest.fail("pre-fix shut down scheduler before abort — expected red")

        assert order.index("abort") < order.index("wait") < order.index("scheduler")


# ── 租约已失 → not_executed ──────────────────────────────────────────────────


class TestLeaseLostNotExecuted:
    def test_teardown_skips_disarm_when_lease_lost(self):
        engine = _bare_engine(api_url=None)
        engine._footprint_families.add("sleep")
        engine._lease_lost_for_disarm = True
        engine._run_step_with_permit = MagicMock(
            side_effect=AssertionError("must not run steps when lease lost")
        )
        result = engine._execute_teardown_best_effort(
            [
                {
                    "step_id": "sf",
                    "action": "script:sleep_finish",
                    "version": "1.0.0",
                }
            ]
        )
        assert result.metadata["disarm_results"]["sleep"]["result"] == (
            DISARM_RESULT_NOT_EXECUTED
        )
        assert result.metadata["disarm_results"]["sleep"]["reason"] == LEASE_LOST_REASON
        engine._run_step_with_permit.assert_not_called()


# ── EXECUTING_DISARM 上报 ────────────────────────────────────────────────────


class TestExecutingDisarmState:
    def test_disarm_step_reports_executing_disarm(self):
        states = []
        scheduler = OperationScheduler(max_concurrent=1)
        engine = _bare_engine(
            scheduler=scheduler,
            update_state=lambda s, *a, **k: states.append(s),
        )
        engine._footprint_families.add("gpu")
        engine._run_step_with_retry = lambda *a, **k: True
        ok = engine._run_step_with_permit(
            "teardown",
            {
                "step_id": "gf",
                "action": "script:gpu_finish",
                "version": "1.0.0",
                "timeout_seconds": 5,
            },
        )
        assert ok is True
        assert EXECUTING_DISARM in states


# ── footprint + select_disarm_steps ───────────────────────────────────────────


class TestFootprintHelpers:
    def test_footprint_at_start(self):
        families: set[str] = set()
        note_footprint_started(
            families,
            {"action": "script:powercycle_setup", "step_id": "s"},
        )
        assert families == {"power-cycle"}

    def test_select_disarm_for_manual_exit(self):
        steps = select_disarm_steps(
            [
                {"action": "script:powercycle_finish", "step_id": "a"},
                {"action": "script:unrelated_collect", "step_id": "b"},
            ],
            ["power-cycle"],
        )
        assert [s["step_id"] for s in steps] == ["a"]
