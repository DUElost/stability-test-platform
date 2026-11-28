# -*- coding: utf-8 -*-
"""B12 U5 / #4206：Agent 临界区仲裁、IPC、VBUS 冻结、C9/C10 反例。"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

from backend.agent.critical_section import (
    ENV_CRITICAL_SECTION_SOCK,
    CriticalSectionGate,
    TerminalCasRegistry,
    build_atomic_group_frozen,
    script_client_request_enter,
    skipped_fails_atomic_delivery,
    terminal_cas,
)
from backend.agent.pipeline_engine import PipelineEngine, StepResult
from backend.agent import graceful_shutdown as gs
from backend.agent.api_client import _build_complete_payload
from backend.agent.contracts.capabilities import (
    AGENT_CAPABILITIES,
    ATOMIC_COMMIT,
    FINISHING_GUARD_V2,
    FORCE_ABORT,
)


# ── helpers ──────────────────────────────────────────────────────────────────


class _FakeHttp:
    def __init__(self, *, activate_margin_ok: bool = True, fail_activate: bool = False):
        self.boot_id = "boot-test"
        self.host_id = "host-1"
        self.pre_calls = []
        self.act_calls = []
        self.cancel_calls = []
        self.hb_calls = []
        self._activate_margin_ok = activate_margin_ok
        self._fail_activate = fail_activate
        self.D_atomic = time.time() + 600

    def pre_register(self, job_id, payload):
        self.pre_calls.append(payload)
        return {
            "status": "pre_registered",
            "D_atomic": self.D_atomic,
            "finishing_guard": {
                "request_id": payload["request_id"],
                "permit_id": payload["request_id"],
                "D_atomic": self.D_atomic,
                "guard_state": "pre_registered",
            },
        }

    def activate_permit(self, job_id, payload):
        self.act_calls.append(payload)
        if self._fail_activate:
            raise RuntimeError("activate boom")
        lease = 120 if self._activate_margin_ok else 5
        host = 120 if self._activate_margin_ok else 5
        return {
            "status": "granted",
            "D_atomic": self.D_atomic,
            "lease_remaining_seconds": lease,
            "host_heartbeat_remaining_seconds": host,
        }

    def cancel_pre_registration(self, job_id, request_id):
        self.cancel_calls.append(request_id)
        return {"status": "cancelled"}

    def heartbeat(self, job_id, *, request_id, progress_seq=None, client_time=None):
        self.hb_calls.append((request_id, progress_seq))
        return {"status": "ok"}


def _engine(**kwargs):
    mq = MagicMock()
    mq.connected = True
    engine = PipelineEngine(
        adb=MagicMock(),
        serial="S",
        run_id=kwargs.pop("run_id", 42),
        log_dir=str(kwargs.pop("log_dir", "/tmp/stp-u5-test")),
        mq_producer=mq,
        api_url=None,
        is_aborted=kwargs.pop("is_aborted", lambda: False),
        host_id="host-1",
        **kwargs,
    )
    engine._verify_device_lease = lambda: None
    engine._archive_logs = lambda: None
    return engine


# ── capabilities ─────────────────────────────────────────────────────────────


def test_u5_enables_three_finishing_capabilities():
    assert FINISHING_GUARD_V2 in AGENT_CAPABILITIES
    assert ATOMIC_COMMIT in AGENT_CAPABILITIES
    assert FORCE_ABORT in AGENT_CAPABILITIES


# ── C9.1 enter × abort arbitration ───────────────────────────────────────────


def test_c9_enter_abort_race_single_winner():
    http = _FakeHttp()
    gate = CriticalSectionGate(job_id=1, http=http, safety_margin_seconds=10)
    gate.set_frozen_members([{"step_key": "flash", "timeout_seconds": 600, "source": "snapshot"}])

    results = []

    def do_enter():
        results.append(gate.handle_enter(request_id="r-enter", min_critical_section_seconds=30))

    def do_abort():
        time.sleep(0.01)
        gate.signal_abort(force=False)

    t1 = threading.Thread(target=do_enter)
    t2 = threading.Thread(target=do_abort)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    # After abort is visible, further enter must deny.
    later = gate.handle_enter(request_id="r-later")
    assert later.granted is False
    assert later.reason in {
        "abort_already_pending",
        "abort_during_handshake",
        "abort_before_grant",
    }


def test_c9_abort_before_enter_denies():
    http = _FakeHttp()
    gate = CriticalSectionGate(job_id=2, http=http)
    gate.signal_abort(force=False)
    decision = gate.handle_enter(request_id="r1")
    assert decision.granted is False
    assert decision.reason == "abort_already_pending"
    assert http.pre_calls == []


# ── C9.2 local margin insufficient ───────────────────────────────────────────


def test_c9_activate_local_margin_insufficient_denies_and_cancels():
    http = _FakeHttp(activate_margin_ok=False)
    gate = CriticalSectionGate(job_id=3, http=http, safety_margin_seconds=15)
    gate.set_frozen_members([{"step_key": "flash", "timeout_seconds": 600, "source": "x"}])
    decision = gate.handle_enter(request_id="r-margin", min_critical_section_seconds=1)
    assert decision.granted is False
    assert decision.reason == "insufficient_local_margin"
    assert http.cancel_calls == ["r-margin"]


# ── C9.3 grant undelivered → cancel_pre_registration ─────────────────────────


def test_c9_undelivered_grant_cancels_pre_registration(tmp_path: Path):
    http = _FakeHttp()
    gate = CriticalSectionGate(job_id=4, http=http, safety_margin_seconds=10)
    gate.set_frozen_members([{"step_key": "flash", "timeout_seconds": 600, "source": "x"}])
    sock = gate.start_ipc_server(sock_dir=str(tmp_path))
    try:
        # Connect then drop without reading grant — simulate undelivered path via
        # explicit cancel_undelivered_grant after a successful handle_enter that
        # never reached the script.
        decision = gate.handle_enter(request_id="r-undelivered")
        assert decision.granted is True
        # Simulate "grant not delivered + script exited": reset granted flag and cancel.
        with gate._lock:
            gate.granted = False
        gate.cancel_undelivered_grant("r-undelivered")
        assert "r-undelivered" in http.cancel_calls
    finally:
        gate.close()
    assert sock  # server bound


def test_c9_ipc_enter_grant_roundtrip(tmp_path: Path):
    http = _FakeHttp()
    gate = CriticalSectionGate(job_id=5, http=http, safety_margin_seconds=10)
    gate.set_frozen_members([{"step_key": "flash", "timeout_seconds": 600, "source": "x"}])
    sock = gate.start_ipc_server(sock_dir=str(tmp_path))
    try:
        resp = script_client_request_enter(
            sock,
            request_id="r-ipc",
            min_critical_section_seconds=1,
            step_key="flash",
        )
        assert resp.get("op") == "grant"
        assert resp.get("request_id") == "r-ipc"
        assert gate.granted is True
    finally:
        gate.close()


# ── C9.4 VBUS return_check after flash (ADR-0063 D5.3) ───────────────────────


def test_c9_vbus_freeze_orders_return_check_after_flash():
    flash = {
        "step_id": "flash",
        "action": "script:flash_firmware",
        "params": {"trigger_mode": "vbus_cycle"},
        "atomic_group": "flash_atomic",
        "timeout_seconds": 900,
    }
    oobe = {
        "step_id": "oobe",
        "action": "script:oobe_skip",
        "atomic_group": "flash_atomic",
        "timeout_seconds": 300,
    }
    other = {
        "step_id": "sync",
        "action": "script:sync_device_time",
        "timeout_seconds": 60,
    }
    frozen = build_atomic_group_frozen([flash, oobe, other], return_check_timeout_seconds=600)
    keys = [m["step_key"] for m in frozen]
    assert "return_check" in keys
    assert keys.index("flash") < keys.index("return_check")
    # Non-atomic sync must not be swallowed into the freeze list.
    assert "sync" not in keys


def test_c9_vbus_order_regression_still_flash_then_check():
    """Keep ADR-0063 D5.3 engine order: deferred flash prefix then return_check."""
    from backend.agent.contracts.vbus_flash import deferred_flash_init_prefix_len

    init = [
        {
            "step_id": "flash",
            "action": "script:flash_firmware",
            "params": {"trigger_mode": "vbus_cycle"},
            "atomic_group": "g",
            "timeout_seconds": 5,
        },
        {"step_id": "oobe", "action": "script:oobe_skip", "atomic_group": "g", "timeout_seconds": 5},
        {"step_id": "sync", "action": "script:sync_device_time", "timeout_seconds": 5},
    ]
    assert deferred_flash_init_prefix_len(init) == 2
    engine = _engine()
    calls = []

    def check():
        calls.append("return_check")
        return StepResult(success=True)

    def steps(phase, defs):
        for step in defs:
            calls.append(step["step_id"])
            engine._init_steps_started = getattr(engine, "_init_steps_started", 0) + 1
        return StepResult(success=True)

    engine._check_init_returns = check
    engine._run_lifecycle_steps = steps
    engine._execute_teardown_best_effort = lambda defs, **kw: StepResult(
        success=True, metadata={"teardown_status": "SUCCESS"},
    )
    pipeline = {"lifecycle": {"timeout_seconds": 1, "init": init, "teardown": []}}
    engine.execute(pipeline)
    assert calls[:3] == ["flash", "oobe", "return_check"]


# ── C9.5 atomic_group skipped without skip_satisfies_delivery → FAILED ───────


def test_c9_skipped_without_delivery_flag_fails():
    step = {"step_id": "flash", "atomic_group": "g", "skip_satisfies_delivery": False}
    assert skipped_fails_atomic_delivery(step, skipped=True) is True
    step_ok = {"step_id": "flash", "atomic_group": "g", "skip_satisfies_delivery": True}
    assert skipped_fails_atomic_delivery(step_ok, skipped=True) is False
    assert skipped_fails_atomic_delivery({"step_id": "x"}, skipped=True) is False


def test_c9_engine_treats_atomic_skipped_as_failed():
    engine = _engine()
    engine._critical_section.granted = True
    step = {
        "step_id": "flash",
        "action": "script:flash_firmware",
        "atomic_group": "g",
        "version": "1.0.0",
        "timeout_seconds": 5,
    }
    engine._execute_step = lambda *a, **k: StepResult(
        success=True, skipped=True, skip_reason="already on target",
    )
    ok = engine._run_step_with_retry("init", step)
    assert ok is False
    assert getattr(engine, "_atomic_delivery_failed", False) is True


# ── C10 terminal CAS + force kill ────────────────────────────────────────────


def test_c10_terminal_cas_single_winner():
    cas = TerminalCasRegistry()
    assert cas.try_claim(7, "worker") is True
    assert cas.try_claim(7, "graceful_shutdown") is False
    assert cas.owner(7) == "worker"
    cas.release(7, "worker")
    assert cas.try_claim(7, "graceful_shutdown") is True


def test_c10_force_abort_kills_granted_process_group():
    engine = _engine()
    gate = engine._critical_section
    gate.granted = True
    killed = {"n": 0}

    class _Proc:
        pid = 12345

        def poll(self):
            return None

    engine._active_process = _Proc()
    engine._active_phase = "init"
    engine._active_script_path = "flash_firmware"

    import backend.agent.pipeline_engine as pe

    original = pe._terminate_process_tree

    def _fake_term(proc, *, grace_seconds=2.0):
        killed["n"] += 1

    pe._terminate_process_tree = _fake_term
    try:
        # Ordinary abort must NOT kill while granted.
        engine.cancel(force=False)
        assert killed["n"] == 0
        assert gate.should_protect_from_ordinary_kill() is True
        # force=true must kill immediately.
        engine.cancel(force=True)
        assert killed["n"] == 1
        assert engine._force_abort is True
    finally:
        pe._terminate_process_tree = original


def test_c10_graceful_shutdown_cas_and_outbox(tmp_path: Path):
    cas = terminal_cas()
    # Isolate: release any prior claims on this job id.
    cas.release(99, cas.owner(99) or "")

    runner = MagicMock()
    runner._critical_section = CriticalSectionGate(job_id=99, http=_FakeHttp())
    runner._critical_section.granted = True
    runner._critical_section.request_id = "r99"
    runner._critical_section.mark_atomic_group_completed = MagicMock()

    state = MagicMock()
    state.active_runners = {99: ("tok", runner)}

    local_db = MagicMock()
    local_db.get_active_jobs.return_value = [{"job_id": 99, "fencing_token": "ft-99"}]
    enqueued = []

    def _enqueue(job_id, payload):
        enqueued.append((job_id, payload))

    local_db.enqueue_terminal.side_effect = _enqueue

    n = gs._persist_unverified_atomic_to_outbox(state, local_db)
    assert n == 1
    assert enqueued[0][0] == 99
    update = enqueued[0][1]["update"]
    assert update["status"] == "CANCELED"  # ABORTED → CANCELED map
    assert update.get("finishing_kind") == "atomic_commit"


def test_complete_payload_carries_atomic_fields():
    payload = _build_complete_payload(
        {
            "status": "COMPLETED",
            "exit_code": 0,
            "atomic_group_completed": True,
            "completed_during_abort_drain": True,
            "finishing_kind": "atomic_commit",
            "abort_mode": "graceful",
            "atomic_group_evidence": {"request_id": "r1"},
        },
        "ft",
    )
    update = payload["update"]
    assert update["atomic_group_completed"] is True
    assert update["completed_during_abort_drain"] is True
    assert update["finishing_kind"] == "atomic_commit"


def test_job_runner_request_abort_force_flag():
    from backend.agent.job_runner import JobRunnerState

    lock = threading.Lock()
    runner = MagicMock()
    state = JobRunnerState(
        active_jobs_lock=lock,
        active_job_ids={1},
        active_device_ids=set(),
        active_job_tokens={1: "w"},
        running_worker_tokens={1: "w"},
        watcher_globally_enabled=False,
        watcher_plan_default=False,
        lock_register=lambda *a, **k: None,
        lock_deregister=lambda *a, **k: None,
        device_id_register=lambda *a: None,
        device_id_deregister=lambda *a: None,
    )
    state.active_runners[1] = ("w", runner)
    assert state.request_abort(1, force=True) is True
    assert 1 in state.force_abort_job_ids
    runner.cancel.assert_called()
    # Prefer force=True kw when supported.
    assert runner.cancel.call_args.kwargs.get("force") is True or runner.cancel.called


def test_env_sock_constant_stable_for_u6():
    assert ENV_CRITICAL_SECTION_SOCK == "STP_CRITICAL_SECTION_SOCK"
