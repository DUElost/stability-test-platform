"""#736: thin AgentApplication shell extracted from main."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from backend.agent.agent_application import AgentApplication, run_agent_application


def test_agent_application_run_wires_planes_and_loop():
    app = AgentApplication()

    with (
        patch("backend.agent.agent_application.ensure_dirs"),
        patch("backend.agent.agent_application.bootstrap_process_identity") as ident,
        patch("backend.agent.agent_application.AdbWrapper"),
        patch("backend.agent.agent_application.ensure_adb_server_on_startup"),
        patch(
            "backend.agent.agent_application.connect_socketio_with_early_control"
        ) as sio,
        patch("backend.agent.agent_application.initialize_local_stores") as stores,
        patch("backend.agent.agent_application.start_disk_and_watcher_subsystems"),
        patch("backend.agent.agent_application.StepTraceWriter") as writer_cls,
        patch("backend.agent.agent_application.build_control_handler") as ctrl,
        patch("backend.agent.agent_application.check_agent_version"),
        patch("backend.agent.agent_application.build_heartbeat_thread") as hb,
        patch("backend.agent.agent_application.start_host_control_plane") as plane_fn,
        patch("backend.agent.agent_application.replay_early_control_commands"),
        patch("backend.agent.agent_application.start_job_runtime") as runtime_fn,
        patch("backend.agent.agent_application.run_agent_loop") as loop_fn,
    ):
        identity = MagicMock()
        identity.host_info = {}
        identity.agent_instance_id = "inst"
        identity.boot_id = "boot"
        identity.host_id = "h"
        identity.agent_version = "1.0"
        identity.agent_code_revision = "r1"
        identity.poll_interval = 1.0
        identity.mount_points = []
        identity.adb_path = "adb"
        identity.agent_secret = "s"
        ident.return_value = identity

        sio_client = MagicMock()
        sio.return_value = (sio_client, [])
        store = MagicMock()
        stores.return_value = store
        writer = MagicMock()
        writer_cls.return_value = writer
        ctrl.return_value = MagicMock(name="handle")
        heartbeat = MagicMock()
        hb.return_value = heartbeat
        plane = MagicMock()
        plane_fn.return_value = plane
        runtime = MagicMock()
        runtime_fn.return_value = runtime

        app.run()

    heartbeat.start.assert_called_once()
    sio_client.set_control_handler.assert_called_once()
    plane_fn.assert_called_once()
    runtime_fn.assert_called_once()
    loop_fn.assert_called_once()
    assert loop_fn.call_args.kwargs["plane"] is plane
    assert loop_fn.call_args.kwargs["runtime"] is runtime
    assert runtime_fn.call_args.kwargs["device_id_register"] == app._register_active_device


def test_run_agent_application_constructs_and_runs():
    with patch.object(AgentApplication, "run") as run:
        run_agent_application()
    run.assert_called_once()


def test_agent_application_exposes_lifecycle_phases():
    """#736 验收：阶段方法存在且单方法行数 ≤100。"""
    import inspect

    app = AgentApplication
    for name in (
        "initialize",
        "start_background_tasks",
        "register_handlers",
        "start_job_plane",
        "run_loop",
        "run",
    ):
        assert hasattr(app, name), name
        lines = len(inspect.getsourcelines(getattr(app, name))[0])
        assert lines <= 100, f"{name} has {lines} lines"


def test_main_delegates_to_agent_application():
    import backend.agent.main as agent_main
    from tools.dev.source_anchor import SourceGuard

    guard = SourceGuard.of_module(agent_main).anchored("run_agent_application(")
    guard.assert_absent(
        "start_host_control_plane(",
        why="#736 启动编排已迁出 main",
    )
    guard.assert_absent(
        "start_job_runtime(",
        why="#736 启动编排已迁出 main",
    )
    guard.assert_absent(
        "run_agent_loop(",
        why="#736 启动编排已迁出 main",
    )
