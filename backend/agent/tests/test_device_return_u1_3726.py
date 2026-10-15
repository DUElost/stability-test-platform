# -*- coding: utf-8 -*-
"""#3726 / #3723 U1 — Agent 解除执行保证与解除结果回报反例。

每条用例在「修复前」必须为红：本文件用可逆变异（``_PRE_FIX_BEHAVIOR``）
复现旧语义并断言其违反不变量；默认跑修复后路径必须为绿。
"""

from __future__ import annotations

import json
import sqlite3
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
from backend.agent import api_client
from backend.agent.graceful_shutdown import shutdown_agent_runtime
from backend.agent.job_runner import JobRunnerState
from backend.agent.registry.local_db import LocalDB
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
                started=False, incomplete=True  # SchedulerShutdown shape
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



# ── 停机：20s 后有界进入 outbox（不得无限 wait=True）─────────────────────────


class TestGracefulShutdownBoundedOutbox:
    def test_reaches_outbox_drain_without_unbounded_executor_wait(self):
        """After disarm window, outbox_drain.drain_sync must run.

        Pre-fix: executor.shutdown(wait=True) with a never-finishing active
        runner blocks forever → drain_sync never reached (red).
        Post-fix: wait=False → drain_sync is called even if runners remain.
        """
        coordinator = MagicMock()
        scheduler = MagicMock()
        runner = MagicMock()
        # Leftover active runner after the 20s window (started disarm still going).
        runner.active_runners = {42: object()}
        runner.request_abort.side_effect = lambda jid: None
        step = MagicMock()
        step.drain_sync.return_value = 0
        outbox = MagicMock()
        outbox.drain_sync.return_value = 1
        drain_calls = []

        def record_drain():
            drain_calls.append("drain")
            return 1

        outbox.drain_sync.side_effect = record_drain

        hung = {"blocked": False}

        def maybe_hang_shutdown(wait=True, cancel_futures=False):
            if wait:
                # Simulate unbounded wait=True: never return to caller.
                hung["blocked"] = True
                if _PRE_FIX_BEHAVIOR:
                    while True:
                        time.sleep(0.05)
                        if hung.get("stop"):
                            return
                raise AssertionError(
                    "post-fix must not call executor.shutdown(wait=True)"
                )

        executor = MagicMock()
        executor.shutdown.side_effect = maybe_hang_shutdown

        if _PRE_FIX_BEHAVIOR:
            # Old fragment: wait=True then outbox — hang prevents drain.
            t = threading.Thread(
                target=lambda: executor.shutdown(wait=True, cancel_futures=False),
                daemon=True,
            )
            t.start()
            time.sleep(0.15)
            assert hung["blocked"] is True
            assert drain_calls == []
            hung["stop"] = True
            t.join(timeout=1.0)
            pytest.fail(
                "pre-fix wait=True blocks before outbox drain — expected red"
            )

        with (
            patch("backend.agent.graceful_shutdown.LocalDiskMonitor"),
            patch("backend.agent.graceful_shutdown.LogArchiver"),
            patch("backend.agent.graceful_shutdown.EventUploader"),
            patch("backend.agent.graceful_shutdown._await_started_disarm"),
        ):
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

        executor.shutdown.assert_called_once_with(wait=False, cancel_futures=False)
        assert hung["blocked"] is False
        assert drain_calls == ["drain"], "outbox drain must run after bounded shutdown"
        outbox.drain_sync.assert_called()


# ── SchedulerShutdown → incomplete（非 not_executed）─────────────────────────


class TestSchedulerShutdownIncomplete:
    def test_map_incomplete_beats_not_started(self):
        """§3.2.1: SchedulerShutdown records incomplete even when not started.

        Pre-fix mapping checked ``not started`` before ``incomplete`` →
        ``not_executed`` (red). lease_lost still wins.
        """
        if _PRE_FIX_BEHAVIOR:
            # Old priority (lease_lost | not started) before incomplete.
            def old_map(*, started, lease_lost=False, incomplete=False, **_kw):
                if lease_lost or not started:
                    return DISARM_RESULT_NOT_EXECUTED
                if incomplete:
                    return DISARM_RESULT_INCOMPLETE
                return DISARM_RESULT_EXECUTED_UNVERIFIED

            assert old_map(started=False, incomplete=True) == DISARM_RESULT_NOT_EXECUTED
            pytest.fail(
                "pre-fix maps started=False+incomplete → not_executed — expected red"
            )

        assert (
            map_step_outcome_to_disarm_result(started=False, incomplete=True)
            == DISARM_RESULT_INCOMPLETE
        )
        assert (
            map_step_outcome_to_disarm_result(
                started=False, incomplete=True, lease_lost=True
            )
            == DISARM_RESULT_NOT_EXECUTED
        )

    def test_permit_wait_scheduler_shutdown_records_incomplete(self):
        """End-to-end: teardown permit wait + SchedulerShutdown → incomplete."""
        scheduler = OperationScheduler(max_concurrent=1)
        held = scheduler.acquire(99)  # occupy the only slot
        engine = _bare_engine(scheduler=scheduler, device_id=1, run_id=7)
        engine._footprint_families.add("power-cycle")
        box: dict = {}

        def run():
            ok = engine._run_step_with_permit(
                "teardown",
                {
                    "step_id": "td",
                    "action": "script:powercycle_finish",
                    "version": "1.0.0",
                    "timeout_seconds": 30,
                },
            )
            box["ok"] = ok
            box["disarm"] = dict(engine._disarm_results)

        t = threading.Thread(target=run)
        t.start()
        # Wait until the engine is blocked in permit wait.
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if engine._last_step_error or getattr(engine, "_disarm_results", None):
                break
            # Heuristic: acquire has queued — peek scheduler waiters if exposed.
            waiters = getattr(scheduler, "_waiters", None)
            if waiters is not None and 1 in waiters:
                break
            time.sleep(0.05)
        scheduler.shutdown()
        t.join(timeout=3.0)
        assert not t.is_alive()
        held.release()

        if _PRE_FIX_BEHAVIOR:
            # Would have been not_executed under old map; force-red documentation.
            pytest.fail("pre-fix SchedulerShutdown → not_executed — expected red")

        assert box.get("ok") is False
        entry = box.get("disarm", {}).get("power-cycle")
        assert entry is not None, box
        assert entry["result"] == DISARM_RESULT_INCOMPLETE, entry


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


# ── 停机：未完成解除在关库前写入 outbox（§3.2.4 持久化）─────────────────────


class TestGracefulShutdownIncompleteOutboxDurability:
    def test_leftover_disarm_persisted_before_local_db_close(self, tmp_path):
        """§3.2.4: leftovers after disarm window must hit outbox before close.

        Pre-fix (``wait=False`` only): drain runs, then LocalDB closes while the
        disarm worker is still running → outbox empty; late ``complete_job`` hits
        ``LocalDB is closed`` / ``TerminalReportLostError``.
        Post-fix: shutdown path enqueues ``incomplete`` while DB is open.
        """
        db_path = tmp_path / "isolated-agent.db"
        db = LocalDB()
        db.initialize(str(db_path))
        scheduler = OperationScheduler(max_concurrent=1)
        engine = _bare_engine(scheduler=scheduler, run_id=42, device_id=1)
        engine._footprint_families = {"power-cycle"}
        engine._disarm_results = {}
        state = JobRunnerState(
            active_jobs_lock=threading.RLock(),
            active_job_ids={42},
            active_device_ids={1},
            active_job_tokens={42: "test-token"},
            running_worker_tokens={42: "test-token"},
            watcher_globally_enabled=False,
            watcher_plan_default=False,
            lock_register=MagicMock(),
            lock_deregister=MagicMock(),
            device_id_register=MagicMock(),
            device_id_deregister=MagicMock(),
        )
        state.attach_runner(42, "test-token", engine)

        started = threading.Event()
        release = threading.Event()

        def slow_step(*_a, **_kw):
            started.set()
            release.wait(timeout=25)
            return StepResult(success=True, exit_code=0)

        engine._execute_step = slow_step
        step = {
            "step_id": "finish",
            "action": "script:powercycle_finish",
            "timeout_seconds": 600,
        }

        def worker():
            result = engine._execute_teardown_best_effort([step])
            api_client.complete_job(
                "http://example.invalid",
                42,
                {
                    "status": "CANCELED",
                    "exit_code": 0,
                    "disarm_results": result.metadata["disarm_results"],
                },
                fencing_token="test-token",
                local_db=db,
            )

        from concurrent.futures import ThreadPoolExecutor

        pool = ThreadPoolExecutor(max_workers=1)
        worker_error = None
        durable = []
        still_running_at_close = None

        def _shutdown_without_persist(**kwargs):
            # Pre-fix fragment: bounded wait=False + drain + close, no
            # incomplete enqueue for leftovers still in active_runners.
            kwargs = dict(kwargs)
            local_db = kwargs["local_db"]
            outbox_drain = kwargs["outbox_drain"]
            executor = kwargs["executor"]
            kwargs["coordinator"].stop()
            for jid in list(kwargs["job_runner_state"].active_runners):
                kwargs["job_runner_state"].request_abort(jid)
            # Skip real 20s window — leftover remains by construction.
            kwargs["operation_scheduler"].shutdown()
            executor.shutdown(wait=False, cancel_futures=False)
            outbox_drain.drain_sync()
            outbox_drain.stop()
            local_db.close()

        with (
            patch.object(
                api_client, "_post_with_retry", side_effect=RuntimeError("offline")
            ),
            patch.object(api_client, "_terminal_abort_jitter_seconds", return_value=0),
            patch("backend.agent.graceful_shutdown.LocalDiskMonitor"),
            patch("backend.agent.graceful_shutdown.LogArchiver"),
            patch("backend.agent.graceful_shutdown.EventUploader"),
            patch(
                "backend.agent.graceful_shutdown._await_started_disarm",
                lambda *a, **k: None,
            ),
        ):
            task = pool.submit(worker)
            assert started.wait(timeout=2)
            try:
                if _PRE_FIX_BEHAVIOR:
                    _shutdown_without_persist(
                        coordinator=MagicMock(),
                        operation_scheduler=scheduler,
                        job_runner_state=state,
                        executor=pool,
                        step_trace_uploader=MagicMock(),
                        outbox_drain=MagicMock(),
                        log_signal_drainer=None,
                        recovery_sync_stop=MagicMock(),
                        recovery_sync_thread=MagicMock(),
                        heartbeat_thread=MagicMock(),
                        lease_renewer=MagicMock(),
                        mq_producer=MagicMock(),
                        local_db=db,
                        sio_client=MagicMock(),
                    )
                else:
                    shutdown_agent_runtime(
                        coordinator=MagicMock(),
                        operation_scheduler=scheduler,
                        job_runner_state=state,
                        executor=pool,
                        step_trace_uploader=MagicMock(),
                        outbox_drain=MagicMock(),
                        log_signal_drainer=None,
                        recovery_sync_stop=MagicMock(),
                        recovery_sync_thread=MagicMock(),
                        heartbeat_thread=MagicMock(),
                        lease_renewer=MagicMock(),
                        mq_producer=MagicMock(),
                        local_db=db,
                        sio_client=MagicMock(),
                    )
                still_running_at_close = not task.done()
                with sqlite3.connect(db_path) as check:
                    durable = [
                        json.loads(row[0])
                        for row in check.execute(
                            "select payload from job_terminal_outbox where job_id = 42"
                        )
                    ]
            finally:
                release.set()
                try:
                    task.result(timeout=2)
                except Exception as exc:
                    worker_error = type(exc).__name__
                pool.shutdown(wait=True)
                try:
                    db.close()
                except Exception:
                    pass

        if _PRE_FIX_BEHAVIOR:
            assert not durable, (
                "pre-fix must leave outbox empty when worker still running at close"
            )
            pytest.fail(
                "pre-fix empty outbox + late "
                f"{worker_error} (still_running={still_running_at_close}) — expected red"
            )

        assert durable, (
            f"still_running_at_db_close={still_running_at_close}; "
            f"durable_outbox={durable}; late_completion={worker_error}"
        )
        assert (
            durable[0]["disarm_results"]["power-cycle"]["result"]
            == DISARM_RESULT_INCOMPLETE
        )

    def test_persist_runs_before_outbox_drain_and_db_close(self):
        """Ordering: persist incomplete → drain_sync → local_db.close."""
        coordinator = MagicMock()
        scheduler = MagicMock()
        runner = MagicMock()
        engine = _bare_engine(run_id=7)
        engine._footprint_families = {"power-cycle"}
        engine._disarm_results = {}
        runner.active_runners = {7: ("tok-7", engine)}
        runner.active_job_tokens = {7: "tok-7"}
        runner.request_abort.side_effect = lambda jid: None

        order: list[str] = []
        local_db = MagicMock()

        def enqueue(job_id, payload):
            order.append("persist")
            assert payload["disarm_results"]["power-cycle"]["result"] == (
                DISARM_RESULT_INCOMPLETE
            )
            return 1

        local_db.enqueue_terminal.side_effect = enqueue
        local_db.close.side_effect = lambda: order.append("close")

        outbox = MagicMock()
        outbox.drain_sync.side_effect = lambda: order.append("drain") or 0

        with (
            patch("backend.agent.graceful_shutdown.LocalDiskMonitor"),
            patch("backend.agent.graceful_shutdown.LogArchiver"),
            patch("backend.agent.graceful_shutdown.EventUploader"),
            patch("backend.agent.graceful_shutdown._await_started_disarm"),
        ):
            if _PRE_FIX_BEHAVIOR:
                # Old: no persist hook — drain then close only.
                outbox.drain_sync()
                local_db.close()
                assert order == ["drain", "close"]
                pytest.fail("pre-fix missing persist-before-close — expected red")

            shutdown_agent_runtime(
                coordinator=coordinator,
                operation_scheduler=scheduler,
                job_runner_state=runner,
                executor=MagicMock(),
                step_trace_uploader=MagicMock(),
                outbox_drain=outbox,
                log_signal_drainer=None,
                recovery_sync_stop=MagicMock(),
                recovery_sync_thread=MagicMock(),
                heartbeat_thread=MagicMock(),
                lease_renewer=MagicMock(),
                mq_producer=MagicMock(),
                local_db=local_db,
                sio_client=MagicMock(),
            )

        assert order.index("persist") < order.index("drain") < order.index("close")
        local_db.enqueue_terminal.assert_called()
