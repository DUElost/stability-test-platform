"""#736: job runtime (pool + recovery) extracted from main."""

from __future__ import annotations

import threading
from unittest.mock import MagicMock, patch

from backend.agent.active_job_bindings import JobRunnerStateSlot
from backend.agent.control_handler import ControlHandlerDeps
from backend.agent.heartbeat_bindings import RecoveryActionsSlot
from backend.agent.job_runtime import start_job_runtime


def test_start_job_runtime_wires_and_starts():
    control_deps = ControlHandlerDeps()
    job_runner_slot = JobRunnerStateSlot()
    recovery_actions_slot = RecoveryActionsSlot()
    lock = threading.Lock()
    job_ids: set[int] = set()
    device_ids: set[int] = set()
    tokens: dict[int, str] = {}
    owners: dict[int, int] = {}
    register = MagicMock(name="register")
    deregister = MagicMock(name="deregister")
    device_reg = MagicMock(name="device_reg")
    device_dereg = MagicMock(name="device_dereg")

    with (
        patch("backend.agent.job_runtime.OutboxDrainThread") as outbox_cls,
        patch("backend.agent.job_runtime.build_cancel_recovery_job") as cancel,
        patch("backend.agent.job_runtime.build_execute_recovery_actions") as exec_act,
        patch("backend.agent.job_runtime.build_patrol_job_not_running_handler") as patrol,
        patch("backend.agent.job_runtime.StepTraceUploader") as uploader_cls,
        patch("backend.agent.job_runtime.ThreadPoolExecutor") as pool_cls,
        patch("backend.agent.job_runtime.JobRunnerState") as state_cls,
        patch("backend.agent.job_runtime.build_resume_recovered_job") as resume,
        patch("backend.agent.job_runtime.run_recovery_sync_if_needed") as sync_once,
        patch("backend.agent.job_runtime.start_periodic_recovery_sync") as sync_periodic,
    ):
        outbox = MagicMock()
        outbox_cls.return_value = outbox
        execute_fn = MagicMock(name="execute")
        exec_act.return_value = execute_fn
        cancel.return_value = MagicMock(name="cancel")
        patrol.return_value = MagicMock(name="patrol")
        uploader = MagicMock()
        uploader_cls.return_value = uploader
        pool = MagicMock()
        pool_cls.return_value = pool
        state = MagicMock()
        state_cls.return_value = state
        resume.return_value = MagicMock(name="resume_fn")
        stop = threading.Event()
        thread = MagicMock()
        sync_periodic.return_value = (stop, thread, 60.0)

        runtime = start_job_runtime(
            api_url="http://x",
            host_id="h",
            agent_instance_id="inst",
            boot_id="boot",
            agent_secret="s",
            local_db=MagicMock(),
            lease_renewer=MagicMock(),
            register_active_job=register,
            deregister_active_job=deregister,
            job_runner_slot=job_runner_slot,
            recovery_actions_slot=recovery_actions_slot,
            control_deps=control_deps,
            coordinator=MagicMock(),
            operation_scheduler=MagicMock(),
            adb=MagicMock(),
            mq_producer=MagicMock(),
            script_registry=MagicMock(),
            patrol_checkpoint_store=MagicMock(),
            run_task_wrapper=MagicMock(),
            active_jobs_lock=lock,
            active_job_ids=job_ids,
            active_device_ids=device_ids,
            active_job_tokens=tokens,
            active_device_owner=owners,
            device_id_register=device_reg,
            device_id_deregister=device_dereg,
            watcher_globally_enabled=True,
            watcher_plan_default=True,
        )

    outbox.start.assert_called_once()
    uploader.start.assert_called_once()
    sync_once.assert_called_once()
    sync_periodic.assert_called_once()
    assert recovery_actions_slot.value is execute_fn
    assert control_deps.job_runner_state is state
    assert job_runner_slot.value is state
    assert runtime.outbox_drain is outbox
    assert runtime.executor is pool
    assert runtime.job_runner_state is state
    assert runtime.step_trace_uploader is uploader
    assert runtime.recovery_sync_stop is stop
    assert runtime.recovery_sync_thread is thread
    assert runtime.execute_recovery_actions is execute_fn


def test_main_wires_job_runtime():
    import backend.agent.agent_application as app
    from tools.dev.source_anchor import SourceGuard

    guard = SourceGuard.of_module(app).anchored("start_job_runtime(")
    guard.assert_absent(
        "OutboxDrainThread(",
        why="#736 outbox 构造已迁出 main",
    )
    guard.assert_absent(
        "ThreadPoolExecutor(",
        why="#736 job pool 构造已迁出 main",
    )
    guard.assert_absent(
        "start_periodic_recovery_sync(",
        why="#736 recovery sync 已迁出 main",
    )
