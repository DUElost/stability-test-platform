"""#736: recovery sync wiring extracted from main."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from backend.agent.active_job_bindings import JobRunnerStateSlot
from backend.agent.recovery_runtime import (
    ResumeJobSlot,
    build_cancel_recovery_job,
    build_execute_recovery_actions,
    build_resume_recovered_job,
    start_periodic_recovery_sync,
)


def test_cancel_recovery_job_uses_late_bound_runner():
    slot = JobRunnerStateSlot()
    cancel = build_cancel_recovery_job(slot)
    cancel(9)  # no runner yet — no-op
    runner = MagicMock()
    slot.value = runner
    cancel(9)
    runner.request_abort.assert_called_once_with(9)


def test_execute_recovery_actions_forwards_to_impl():
    resume_slot = ResumeJobSlot()
    resume = MagicMock()
    resume_slot.value = resume
    cancel = MagicMock()
    execute = build_execute_recovery_actions(
        lease_renewer=MagicMock(),
        local_db=MagicMock(),
        outbox_drain=MagicMock(),
        register_active_job=MagicMock(),
        resume_slot=resume_slot,
        cancel_recovery_job=cancel,
    )
    with patch(
        "backend.agent.recovery_runtime.execute_recovery_actions_impl"
    ) as impl:
        execute({"actions": []}, {})
    impl.assert_called_once()
    assert impl.call_args.kwargs["resume_job"] is resume
    assert impl.call_args.kwargs["abort_local_job"] is cancel


def test_resume_recovered_job_submits_wrapper():
    executor = MagicMock()
    coordinator = MagicMock()
    wrapper = MagicMock()
    resume = build_resume_recovered_job(
        agent_instance_id="inst",
        coordinator=coordinator,
        executor=executor,
        adb=MagicMock(),
        api_url="http://x",
        host_id="h1",
        job_runner_state=MagicMock(),
        mq_producer=MagicMock(),
        script_registry=MagicMock(),
        local_db=MagicMock(),
        patrol_checkpoint_store=MagicMock(),
        operation_scheduler=MagicMock(),
        step_trace_uploader=MagicMock(),
        run_task_wrapper=wrapper,
    )
    resume({"id": 3, "device_id": 30, "plan_run_host_id": 1, "plan_run_id": 2})
    coordinator.register_job.assert_called_once_with(3)
    coordinator.register_job_device.assert_called_once_with(3, 30)
    coordinator.register_plan_run_host.assert_called_once_with(1, 2)
    executor.submit.assert_called_once()
    assert executor.submit.call_args.args[0] is wrapper


def test_start_periodic_recovery_sync_starts_thread():
    with patch(
        "backend.agent.recovery_runtime.run_recovery_sync_if_needed"
    ) as run_sync, patch(
        "backend.agent.recovery_runtime._coerce_recovery_interval",
        return_value=0.05,
    ):
        stop, thread, interval = start_periodic_recovery_sync(
            local_db=MagicMock(),
            api_url="http://x",
            host_id="h",
            agent_instance_id="a",
            boot_id="b",
            execute_actions=MagicMock(),
        )
        assert interval == 0.05
        assert thread.is_alive()
        stop.set()
        thread.join(timeout=2.0)
        assert not thread.is_alive()
        # may or may not have ticked; just ensure no crash
        assert run_sync.call_count >= 0


def test_main_wires_recovery_runtime():
    import backend.agent.main as agent_main
    from tools.dev.source_anchor import SourceGuard

    guard = (
        SourceGuard.of_module(agent_main)
        .anchored("build_execute_recovery_actions(")
        .anchored("build_resume_recovered_job(")
        .anchored("start_periodic_recovery_sync(")
    )
    guard.assert_absent(
        "def _resume_recovered_job_impl(",
        why="#736 recovery_runtime 已抽出，main 不得回潮内联 resume 闭包",
    )
