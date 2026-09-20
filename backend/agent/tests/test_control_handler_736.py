"""#736: SocketIO control handler extracted from main."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from backend.agent.control_handler import (
    ControlHandlerDeps,
    build_control_handler,
    parse_abort_job_ids,
)


def _handler(**overrides):
    deps = ControlHandlerDeps(
        job_runner_state=MagicMock(),
        coordinator=MagicMock(),
        operation_scheduler=MagicMock(reload_from_env=MagicMock(return_value=3)),
        heartbeat_thread=MagicMock(),
    )
    deps.job_runner_state.request_abort.return_value = True
    kwargs = dict(
        deps=deps,
        mq_producer=MagicMock(),
        host_id="h1",
        api_url="http://x",
        agent_secret="s",
        adb_path="adb",
        local_db=MagicMock(),
        active_jobs_lock=MagicMock(),
        active_job_ids=set(),
        ensure_adb_server=MagicMock(return_value=True),
    )
    kwargs.update(overrides)
    return build_control_handler(**kwargs), kwargs["deps"]


def test_parse_abort_job_ids_skips_bad_entries():
    assert parse_abort_job_ids({"job_ids": [1, "2", "oops", None, 3]}) == [1, 2, 3]


def test_abort_requests_runner_then_cancels_waiter():
    handle, deps = _handler()
    ack = handle({"command": "abort", "payload": {"job_ids": [9, "bad", 10]}})
    assert ack == {"ok": True}
    assert deps.job_runner_state.request_abort.call_count == 2
    deps.coordinator.cancel_waiting_job.assert_any_call(9)
    deps.coordinator.cancel_waiting_job.assert_any_call(10)


def test_scan_now_missing_plan_run_id():
    handle, _ = _handler()
    ack = handle({"command": "scan_now", "payload": {}})
    assert ack == {"ok": False, "error": "missing plan_run_id"}


def test_scan_now_enqueues():
    handle, _ = _handler()
    with patch("backend.agent.control_handler.ScanRunner.enqueue_scan_now") as enq:
        ack = handle(
            {
                "command": "scan_now",
                "payload": {"plan_run_id": 7, "is_final": True},
            }
        )
    assert ack == {"ok": True}
    enq.assert_called_once()


def test_reload_config_reapplies_runtime():
    handle, deps = _handler()
    with (
        patch(
            "backend.agent.control_handler.reload_runtime_env", return_value=True
        ) as reload_env,
        patch("backend.agent.control_handler.reset_agent_settings_caches") as reset,
        patch("backend.agent.control_handler.ScanRunner.instance") as scan,
        patch("backend.agent.control_handler.UnisocScanRunner.instance") as uscan,
        patch("backend.agent.control_handler.UploadManager.instance") as upload,
        patch("backend.agent.control_handler.EventUploader.instance") as event,
    ):
        scan.return_value.is_configured.return_value = True
        upload.return_value.is_configured.return_value = True
        event.return_value.configure.return_value = event.return_value
        ack = handle({"command": "reload_config", "payload": {}})

    assert ack == {"ok": True}
    reload_env.assert_called_once()
    reset.assert_called_once()
    scan.return_value.configure.assert_called_once_with(force=True)
    uscan.return_value.configure.assert_called_once_with(force=True)
    upload.return_value.configure.assert_called_once_with(force=True)
    deps.operation_scheduler.reload_from_env.assert_called_once()
    deps.heartbeat_thread.reload_from_settings.assert_called_once()
    deps.coordinator.reload_from_settings.assert_called_once()


def test_main_wires_control_handler_builder():
    import backend.agent.agent_application as app
    import backend.agent.job_runtime as job_runtime
    from tools.dev.source_anchor import SourceGuard

    # 正锚点：control_handler 组装在 AgentApplication；job_runner_state 晚绑定在 job_runtime
    guard = SourceGuard.of_module(app).anchored("build_control_handler(")
    guard.assert_absent(
        'elif command == "reload_config":',
        why="#736 control_handler 已抽出，编排层不得回潮内联 reload_config 分支",
    )
    SourceGuard.of_module(job_runtime).anchored(
        "control_deps.job_runner_state = job_runner_state"
    )
