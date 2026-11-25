"""#736: SocketIO control handler extracted from main."""

from __future__ import annotations

from unittest.mock import MagicMock, patch
import logging

import pytest

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


def test_scan_now_preserves_legacy_call_and_forwards_round_fields():
    handle, _ = _handler()
    with patch("backend.agent.control_handler.ScanRunner.enqueue_scan_now") as enq:
        handle({"command": "scan_now", "payload": {"plan_run_id": 7, "is_final": True}})
        enq.assert_called_once_with(7, "h1", is_final=True, device_serials=[], run_date_stamps=[])
        enq.reset_mock()
        spill = [{"dle_id": "one", "remote_path": "/nfs/devices/7/one/event", "occurrence_key": "key"}]
        handle({"command": "scan_now", "payload": {"plan_run_id": 7, "fact_round_id": 3, "spill_inputs": spill}})
        enq.assert_called_once_with(7, "h1", is_final=False, device_serials=[], run_date_stamps=[],
                                    fact_round_id=3, spill_inputs=spill)


@pytest.mark.parametrize("fields", [
    {"fact_round_id": "7"}, {"fact_round_id": None}, {"fact_round_id": True},
    {"fact_round_id": 7.0}, {"fact_round_id": 0}, {"fact_round_id": -1},
    {"spill_inputs": {}}, {"spill_inputs": None}, {"spill_inputs": "[]"},
    {"fact_round_id": 7, "spill_inputs": ()},
])
def test_bad_round_payload_is_rejected_without_legacy_fallback(fields, caplog):
    handle, _ = _handler()
    caplog.set_level(logging.WARNING)
    with patch("backend.agent.control_handler.ScanRunner.enqueue_scan_now") as enq:
        ack = handle({"command": "scan_now", "payload": {"plan_run_id": 7, **fields}})
    assert ack == {"ok": False, "error": "invalid scan round payload"}
    enq.assert_not_called()
    assert "control_scan_now_invalid_round_payload" in caplog.text


@pytest.mark.parametrize("spill_inputs", [
    [],
    [{"dle_id": "one", "remote_path": "/nfs/devices/7/one/event", "occurrence_key": "key"}],
])
def test_spill_inputs_without_fact_round_id_is_rejected(spill_inputs, caplog):
    handle, _ = _handler()
    caplog.set_level(logging.WARNING)
    with patch("backend.agent.control_handler.ScanRunner.enqueue_scan_now") as enq:
        ack = handle({"command": "scan_now", "payload": {"plan_run_id": 7, "spill_inputs": spill_inputs}})
    assert ack == {"ok": False, "error": "invalid scan round payload"}
    enq.assert_not_called()
    assert "control_scan_now_invalid_round_payload" in caplog.text


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


def test_set_device_swipe_trail_rejects_bad_payload():
    handle, _ = _handler()
    assert handle({"command": "set_device_swipe_trail", "payload": {}}) == {
        "ok": False,
        "error": "serials must be a non-empty list",
    }
    assert handle(
        {"command": "set_device_swipe_trail", "payload": {"serials": ["a"], "enabled": 1}}
    ) == {"ok": False, "error": "enabled must be a bool"}


def test_set_device_swipe_trail_runs_both_settings(monkeypatch):
    handle, _ = _handler()
    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        return MagicMock(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(
        "backend.agent.control_handler.subprocess.run",
        fake_run,
    )
    ack = handle(
        {
            "command": "set_device_swipe_trail",
            "payload": {"serials": ["SER1", "SER2"], "enabled": True},
        }
    )
    assert ack["ok"] is True
    assert [r["ok"] for r in ack["results"]] == [True, True]
    # 每台两条：show_touches + pointer_location
    assert len(calls) == 4
    assert calls[0] == [
        "adb", "-s", "SER1", "shell", "settings", "put", "system", "show_touches", "1",
    ]
    # index 6 = settings 命名空间：两条同属 Settings.System。框架读点是
    # InputSettingsObserver（android15-release services/core/java/com/android/server/input/
    # InputSettingsObserver.java:182/:187 经 getBoolean(Settings.System.*) 取值），
    # Settings.Secure 下无此键。SettingsProvider 对任意 namespace 的任意 key 都接受写入，
    # 写错表不报错、叠加不生效却上报 ok——故此处断言整条 argv，把命名空间锁死在框架读点上。
    # #3578 回归 #3179 把 pointer_location 误判为 Settings.Secure 的改动。
    assert calls[1] == [
        "adb", "-s", "SER1", "shell", "settings", "put", "system", "pointer_location", "1",
    ]
    assert calls[2][2] == "SER2"
    assert all(c[-1] == "1" for c in calls)


def test_set_device_swipe_trail_disable_and_per_serial_failure(monkeypatch):
    handle, _ = _handler()

    def fake_run(argv, **kwargs):
        serial = argv[2]
        if serial == "BAD":
            return MagicMock(returncode=1, stdout="", stderr="device offline")
        return MagicMock(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(
        "backend.agent.control_handler.subprocess.run",
        fake_run,
    )
    ack = handle(
        {
            "command": "set_device_swipe_trail",
            "payload": {"serials": ["OK", "BAD"], "enabled": False},
        }
    )
    assert ack["ok"] is True
    by_serial = {r["serial"]: r for r in ack["results"]}
    assert by_serial["OK"]["ok"] is True
    assert by_serial["BAD"]["ok"] is False
    assert "offline" in by_serial["BAD"]["error"]


def test_skip_device_oobe_rejects_bad_payload_and_non_whitelist_script():
    handle, _ = _handler()
    assert handle({"command": "skip_device_oobe", "payload": {}}) == {
        "ok": False,
        "error": "serials must be a non-empty list",
    }
    valid_script = {
        "name": "oobe_skip",
        "version": "1.1.3",
        "script_type": "python",
        "nfs_path": "oobe_skip/v1.1.3/oobe_skip.py",
        "package_sha256": "a" * 64,
    }
    assert handle(
        {
            "command": "skip_device_oobe",
            "payload": {
                "serials": ["SER1"],
                "script": {**valid_script, "name": "flash_firmware"},
            },
        }
    ) == {"ok": False, "error": "invalid oobe_skip script descriptor"}
    assert handle(
        {
            "command": "skip_device_oobe",
            "payload": {
                "serials": ["SER1"],
                "script": {**valid_script, "package_sha256": ""},
            },
        }
    ) == {"ok": False, "error": "invalid oobe_skip script descriptor"}
    assert handle(
        {
            "command": "skip_device_oobe",
            "payload": {
                "serials": ["SER1"],
                "script": valid_script,
                "params": ["not-a-dict"],
            },
        }
    ) == {"ok": False, "error": "params must be a dict"}


def test_skip_device_oobe_fails_closed_when_package_unavailable(monkeypatch):
    from backend.agent.script_packages import PackageUnavailable

    handle, _ = _handler()

    def fake_resolve(_entry):
        raise PackageUnavailable("oobe_skip@1.1.3: package_unavailable")

    monkeypatch.setattr(
        "backend.agent.control_handler.resolve_script_path",
        fake_resolve,
    )
    ack = handle(
        {
            "command": "skip_device_oobe",
            "payload": {
                "serials": ["SER1"],
                "script": {
                    "name": "oobe_skip",
                    "version": "1.1.3",
                    "script_type": "python",
                    "nfs_path": "oobe_skip/v1.1.3/oobe_skip.py",
                    "package_sha256": "a" * 64,
                },
                "params": {"wait_for_device_seconds": 5},
            },
        }
    )
    assert ack["ok"] is False
    assert "script package unavailable" in ack["error"]


def test_skip_device_oobe_runs_packaged_script_and_parses_json_result(
    monkeypatch, tmp_path,
):
    from backend.agent.script_packages import ResolvedScript

    handle, _ = _handler()
    script_file = tmp_path / "oobe_skip.py"
    script_file.write_text("# stub\n", encoding="utf-8")

    seen_calls: list[dict] = []

    def fake_resolve(entry):
        assert entry.name == "oobe_skip"
        assert entry.version == "1.1.3"
        assert entry.package_sha256 == "b" * 64
        return ResolvedScript(path=str(script_file), cwd=str(tmp_path))

    def fake_run(argv, **kwargs):
        env = kwargs["env"]
        serial = env["STP_DEVICE_SERIAL"]
        seen_calls.append({"argv": list(argv), "env": dict(env), "cwd": kwargs["cwd"]})
        if serial == "SER_OK":
            return MagicMock(
                returncode=0,
                stdout='{"success": true, "skipped": false, "metrics": {}}\n',
                stderr='PROGRESS {"seq": 1, "step": "oobe_skip"}\n',
            )
        if serial == "SER_FAIL_JSON":
            # oobe_skip.py 失败时退出码仍为 0，靠 stdout JSON success=false 表达失败。
            return MagicMock(
                returncode=0,
                stdout='{"success": false, "skipped": false, "error_message": "device SER_FAIL_JSON not adb-ready"}\n',
                stderr="",
            )
        return MagicMock(returncode=1, stdout="", stderr="python crashed")

    monkeypatch.setattr("backend.agent.control_handler.resolve_script_path", fake_resolve)
    monkeypatch.setattr("backend.agent.control_handler.inject_required_tools", lambda _p, _e: None)
    monkeypatch.setattr("backend.agent.control_handler.subprocess.run", fake_run)

    ack = handle(
        {
            "command": "skip_device_oobe",
            "payload": {
                "serials": ["SER_OK", "SER_FAIL_JSON", "SER_RC1"],
                "script": {
                    "name": "oobe_skip",
                    "version": "1.1.3",
                    "script_type": "python",
                    "nfs_path": "oobe_skip/v1.1.3/oobe_skip.py",
                    "package_sha256": "b" * 64,
                },
                "params": {"wait_for_device_seconds": 5, "verify_retries": 1},
            },
        }
    )

    assert ack["ok"] is True
    assert ack["script_version"] == "1.1.3"
    assert [r["serial"] for r in ack["results"]] == ["SER_OK", "SER_FAIL_JSON", "SER_RC1"]
    by_serial = {r["serial"]: r for r in ack["results"]}
    assert by_serial["SER_OK"] == {"serial": "SER_OK", "ok": True}
    assert by_serial["SER_FAIL_JSON"]["ok"] is False
    assert "not adb-ready" in by_serial["SER_FAIL_JSON"]["error"]
    assert by_serial["SER_RC1"]["ok"] is False
    assert "crashed" in by_serial["SER_RC1"]["error"]
    assert len(seen_calls) == 3
    for call in seen_calls:
        assert call["argv"][1] == str(script_file)
        assert call["cwd"] == str(tmp_path)
        assert call["env"]["STP_ADB_PATH"] == "adb"
        assert '"wait_for_device_seconds": 5' in call["env"]["STP_STEP_PARAMS"]
