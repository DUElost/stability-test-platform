# -*- coding: utf-8 -*-
"""Aging DDR 专项脚本四件套与 PipelineEngine patrol 提前终态单测。"""
from __future__ import annotations

import base64
import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock, patch

import pytest

from backend.agent.pipeline_engine import PipelineEngine, StepResult

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_ROOT = REPO_ROOT / "backend" / "agent" / "scripts"


def _load_script_module(family: str, module_name: str) -> ModuleType:
    """隔离加载指定脚本族下的模块（与 Agent 运行时同 cwd/sys.path 语义）。"""
    family_dir = str(SCRIPTS_ROOT / family)
    # 清除可能来自其它族的 _lib 缓存，避免跨族串扰
    sys.modules.pop("_lib", None)
    sys.path.insert(0, family_dir)
    try:
        lib_spec = importlib.util.spec_from_file_location("_lib", SCRIPTS_ROOT / family / "_lib.py")
        assert lib_spec and lib_spec.loader
        lib_mod = importlib.util.module_from_spec(lib_spec)
        sys.modules["_lib"] = lib_mod
        lib_spec.loader.exec_module(lib_mod)

        unique_name = f"stp_test_{family}_{module_name}"
        spec = importlib.util.spec_from_file_location(
            unique_name, SCRIPTS_ROOT / family / f"{module_name}.py"
        )
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        sys.modules[unique_name] = mod
        spec.loader.exec_module(mod)
        mod._loaded_lib = lib_mod  # type: ignore[attr-defined]
        return mod
    finally:
        if sys.path and sys.path[0] == family_dir:
            sys.path.pop(0)
        sys.modules.pop("_lib", None)


def _encode_b64_prefs(lib_mod: ModuleType, data: dict, **kwargs) -> str:
    raw = lib_mod.encode_prefs(data, **kwargs)
    return base64.b64encode(raw).decode("ascii")


# ---------------------------------------------------------------------------
# 1. Jetpack DataStore PreferencesProto 编解码往返测试
# ---------------------------------------------------------------------------


class TestDataStoreCodec:
    def test_roundtrip_all_types_and_long_strings(self):
        mod = _load_script_module("aging_ddr_setup", "aging_ddr_setup")
        lib = mod._loaded_lib
        long_records = "\n".join(f"2026-11-24 12:00:{i:02d} | ROUND_PASS | pass={i}" for i in range(40))
        payload = {
            "session_id": "11111111-2222-3333-4444-555555555555",
            "active": True,
            "last_pass": False,
            "started_at_ms": 1763960000123,
            "current_module_pass": 3,
            "negative_delta": -42,
            "records": long_records,
            "raw_bytes": b"\x00\x01\xfe\xff",
        }
        encoded = lib.encode_prefs(payload, int32_keys=frozenset({"current_module_pass"}))
        decoded = lib.decode_prefs(encoded)
        assert decoded["session_id"] == payload["session_id"]
        assert decoded["active"] is True
        assert decoded["last_pass"] is False
        assert decoded["started_at_ms"] == 1763960000123
        assert decoded["current_module_pass"] == 3
        assert decoded["negative_delta"] == -42
        assert decoded["records"] == long_records
        assert decoded["raw_bytes"] == b"\x00\x01\xfe\xff"

    def test_decode_rejects_truncated_buffer(self):
        mod = _load_script_module("aging_ddr_setup", "aging_ddr_setup")
        lib = mod._loaded_lib
        with pytest.raises(ValueError):
            lib.decode_prefs(b"\x0a\x10abc")


# ---------------------------------------------------------------------------
# 2. aging_ddr_setup 测试
# ---------------------------------------------------------------------------


class TestAgingDdrSetup:
    def test_parse_config_defaults_and_validation(self):
        mod = _load_script_module("aging_ddr_setup", "aging_ddr_setup")
        parsed = mod._parse_config({"aging_module_pass_count": 7})
        assert parsed["mode"] == "AGING"
        assert parsed["effective_pass_count"] == 7
        assert parsed["enabled_items"] == ["REBOOT_DST", "DDR_FLAG_CHECK", "REBOOT", "DB_HISTORY"]
        assert parsed["ddr_params"]["aging_module_pass_count"] == "7"

        with pytest.raises(ValueError, match="mode"):
            mod._parse_config({"mode": "INVALID"})
        with pytest.raises(ValueError, match="enabled_items"):
            mod._parse_config({"enabled_items": "UNKNOWN_ITEM"})
        with pytest.raises(ValueError, match=">= 1"):
            mod._parse_config({"aging_module_pass_count": 0})

    def test_preflight_fail_fast_checks(self, monkeypatch):
        mod = _load_script_module("aging_ddr_setup", "aging_ddr_setup")
        parsed = mod._parse_config({"expected_apk_version": "1.0.3.3", "min_battery_level": 20})

        monkeypatch.setattr(mod, "ensure_adb_root", lambda: False)
        with pytest.raises(RuntimeError, match="adb root"):
            mod._check_preflight(parsed)

        monkeypatch.setattr(mod, "ensure_adb_root", lambda: True)
        monkeypatch.setattr(
            mod,
            "preflight_device_info",
            lambda timeout=15: {"rc": 0, "uid": "0", "apk_version": "1.0.2.0", "battery_level": 70, "usb_powered": True},
        )
        with pytest.raises(RuntimeError, match="版本不匹配"):
            mod._check_preflight(parsed)

        monkeypatch.setattr(
            mod,
            "preflight_device_info",
            lambda timeout=15: {"rc": 0, "uid": "0", "apk_version": "1.0.3.3", "battery_level": 12, "usb_powered": False, "ac_powered": False},
        )
        with pytest.raises(RuntimeError, match="电量过低"):
            mod._check_preflight(parsed)

    def test_run_pure_adb_launch_and_dst_entry(self, monkeypatch):
        mod = _load_script_module("aging_ddr_setup", "aging_ddr_setup")
        monkeypatch.setenv("STP_DEVICE_SERIAL", "SER001")
        monkeypatch.setattr(mod.time, "sleep", lambda _s: None)
        monkeypatch.setattr(mod, "ensure_adb_root", lambda: True)
        monkeypatch.setattr(
            mod,
            "preflight_device_info",
            lambda timeout=15: {
                "rc": 0,
                "uid": "0",
                "board": "ums9230_525",
                "build": "P525AA-V318",
                "apk_version": "1.0.3.3",
                "battery_level": 70,
                "usb_powered": True,
                "boot_reason": "reboot,userrequested",
            },
        )

        written_files: dict[str, dict] = {}
        monkeypatch.setattr(
            mod,
            "write_datastore_files",
            lambda files, **kw: written_files.update(files),
        )

        adb_calls: list[tuple[str, ...]] = []
        logcat_poll = {"n": 0}

        def fake_adb(*args, timeout=60):
            adb_calls.append(args)
            if args[:2] == ("logcat", "-d"):
                logcat_poll["n"] += 1
                sid = written_files["aging_pipeline_session.preferences_pb"]["session_id"]
                if logcat_poll["n"] == 1:
                    return 0, f"Aging_: [Ddr] test started sessionId={sid} modulePassCount=7\nAging_: reboot dst round=1/1\n", ""
                return 1, "", "error: device offline"
            return 0, "", ""

        monkeypatch.setattr(mod, "adb", fake_adb)
        metrics = mod._run({"mode": "AGING", "aging_module_pass_count": 7, "verify_timeout_seconds": 5})

        assert metrics["status"] == "started_and_entering_dst"
        assert metrics["module_pass_count"] == 7
        assert metrics["started_marker"] is True
        assert metrics["dst_marker"] is True
        assert metrics["dropped_adb"] is True
        assert written_files["home_stage_selection.preferences_pb"] == {"selected_stages": "DDR"}
        assert written_files["aging_pipeline_session.preferences_pb"]["stage_status"] == "DDR:PENDING"

        # 确认执行顺序：WAKE -> kill -9 -> setprop boot reason -> BOOT_COMPLETED
        shell_cmds = [a[1] for a in adb_calls if a and a[0] == "shell"]
        wake_idx = next(i for i, c in enumerate(shell_cmds) if "com.ape.aging.WAKE" in c)
        kill_idx = next(i for i, c in enumerate(shell_cmds) if "kill -9" in c)
        prop_idx = next(i for i, c in enumerate(shell_cmds) if "sys.boot.reason.last" in c)
        boot_idx = next(i for i, c in enumerate(shell_cmds) if "BOOT_COMPLETED" in c)
        assert wake_idx < kill_idx < prop_idx < boot_idx


# ---------------------------------------------------------------------------
# 3. aging_ddr_check 测试
# ---------------------------------------------------------------------------


class TestAgingDdrCheck:
    def _setup_check(self, monkeypatch, tmp_path, *, serial="SER_CHK", job_id="1001"):
        mod = _load_script_module("aging_ddr_check", "aging_ddr_check")
        monkeypatch.setenv("STP_DEVICE_SERIAL", serial)
        monkeypatch.setenv("STP_JOB_ID", job_id)
        monkeypatch.setattr(mod, "_state_file", lambda s: tmp_path / f"state_{s}.json")
        return mod

    def test_offline_budget_then_timeout_and_job_isolation(self, monkeypatch, tmp_path):
        mod = self._setup_check(monkeypatch, tmp_path, job_id="2001")
        monkeypatch.setattr(mod, "device_online", lambda timeout=10: False)

        ok1, p1 = mod._evaluate({"max_offline_seconds": 1800}, now_fn=lambda: 1000.0)
        assert ok1 is True
        assert p1["progress"]["state"] == "offline_in_dst_or_reboot"
        assert p1["progress"]["offline_elapsed_seconds"] == 0

        ok2, p2 = mod._evaluate({"max_offline_seconds": 1800}, now_fn=lambda: 2500.0)
        assert ok2 is True
        assert p2["progress"]["offline_elapsed_seconds"] == 1500

        ok3, p3 = mod._evaluate({"max_offline_seconds": 1800}, now_fn=lambda: 2805.0)
        assert ok3 is False
        assert "patrol_fail" not in p3
        assert "连续离线" in p3["error_message"]

        # 新 Job ID 必须清零旧离线计时
        monkeypatch.setenv("STP_JOB_ID", "2002")
        ok4, p4 = mod._evaluate({"max_offline_seconds": 1800}, now_fn=lambda: 2810.0)
        assert ok4 is True
        assert p4["progress"]["offline_elapsed_seconds"] == 0

    def test_done_pass_emits_patrol_complete(self, monkeypatch, tmp_path):
        mod = self._setup_check(monkeypatch, tmp_path)
        monkeypatch.setattr(mod, "device_online", lambda timeout=10: True)
        monkeypatch.setattr(mod, "adb_shell", lambda cmd, timeout=10: "1")
        monkeypatch.setattr(mod, "ddr_service_alive", lambda timeout=10: (False, 0))
        ds_map = {
            "aging_pipeline_session.preferences_pb": (
                "ok",
                {"session_id": "sid-pass", "active": False, "status": "COMPLETED", "stage_status": "DDR:DONE_PASS"},
            ),
            "ddr_test_session.preferences_pb": (
                "ok",
                {"running": False, "current_module_pass": 7, "module_pass_count": 7},
            ),
            "ddr_test_report.preferences_pb": ("ok", {"result": "PASS", "records": "TEST_END | PASS"}),
        }
        monkeypatch.setattr(mod, "read_datastore_file", lambda fn, timeout=10: ds_map[fn])

        ok, payload = mod._evaluate({}, now_fn=lambda: 5000.0)
        assert ok is True
        assert payload["patrol_complete"] is True
        assert payload["progress"]["final_status"] == "PASS"

    def test_done_fail_emits_patrol_fail(self, monkeypatch, tmp_path):
        mod = self._setup_check(monkeypatch, tmp_path)
        monkeypatch.setattr(mod, "device_online", lambda timeout=10: True)
        monkeypatch.setattr(mod, "adb_shell", lambda cmd, timeout=10: "1")
        monkeypatch.setattr(mod, "ddr_service_alive", lambda timeout=10: (False, 0))
        ds_map = {
            "aging_pipeline_session.preferences_pb": (
                "ok",
                {"session_id": "sid-fail", "active": False, "status": "FAILED", "stage_status": "DDR:DONE_FAIL", "last_error": "dst_flag_fail"},
            ),
            "ddr_test_session.preferences_pb": ("ok", {"running": False}),
            "ddr_test_report.preferences_pb": ("ok", {"result": "FAIL", "records": "TEST_END | FAIL"}),
        }
        monkeypatch.setattr(mod, "read_datastore_file", lambda fn, timeout=10: ds_map[fn])

        ok, payload = mod._evaluate({}, now_fn=lambda: 5000.0)
        assert ok is False
        assert payload["patrol_fail"] is True
        assert payload["progress"]["final_status"] == "FAIL"
        assert "dst_flag_fail" in payload["error_message"]

    def test_online_running_progress_and_stall_detection(self, monkeypatch, tmp_path):
        mod = self._setup_check(monkeypatch, tmp_path)
        monkeypatch.setattr(mod, "device_online", lambda timeout=10: True)
        monkeypatch.setattr(mod, "adb_shell", lambda cmd, timeout=10: "1")
        monkeypatch.setattr(mod, "ddr_service_alive", lambda timeout=10: (True, 0))
        ds_map = {
            "aging_pipeline_session.preferences_pb": (
                "ok",
                {"session_id": "sid-run", "active": True, "status": "RUNNING", "stage_status": "DDR:RUNNING"},
            ),
            "ddr_test_session.preferences_pb": (
                "ok",
                {"running": True, "current_module_pass": 2, "module_pass_count": 7, "current_item_index": 2, "current_reboot_count": 3},
            ),
            "ddr_test_report.preferences_pb": ("ok", {"records": "ITEM_START"}),
        }
        monkeypatch.setattr(mod, "read_datastore_file", lambda fn, timeout=10: ds_map[fn])

        ok1, p1 = mod._evaluate({"online_stall_cycles": 2}, now_fn=lambda: 100.0)
        assert ok1 is True
        assert p1["progress"]["stall_streak"] == 0

        ok2, p2 = mod._evaluate({"online_stall_cycles": 2}, now_fn=lambda: 130.0)
        assert ok2 is True
        assert p2["progress"]["stall_streak"] == 1

        ok3, p3 = mod._evaluate({"online_stall_cycles": 2}, now_fn=lambda: 160.0)
        assert ok3 is False
        assert "无进度推进" in p3["error_message"]

    def test_offline_to_online_grace_exempts_first_cycle(self, monkeypatch, tmp_path):
        mod = self._setup_check(monkeypatch, tmp_path)
        monkeypatch.setattr(mod, "device_online", lambda timeout=10: False)
        ok_off, _ = mod._evaluate({"dead_grace_cycles": 1}, now_fn=lambda: 100.0)
        assert ok_off is True

        # 刚恢复上线但服务尚未拉起：首轮豁免
        monkeypatch.setattr(mod, "device_online", lambda timeout=10: True)
        monkeypatch.setattr(mod, "adb_shell", lambda cmd, timeout=10: "1")
        monkeypatch.setattr(mod, "ddr_service_alive", lambda timeout=10: (False, 0))
        monkeypatch.setattr(mod, "read_datastore_file", lambda fn, timeout=10: ("absent", {}))

        ok_grace, p_grace = mod._evaluate({"dead_grace_cycles": 1}, now_fn=lambda: 130.0)
        assert ok_grace is True
        assert p_grace["progress"]["state"] == "boot_resume_grace"

        # 下一轮仍未运行 -> 命中 dead_grace_cycles
        ok_dead, p_dead = mod._evaluate({"dead_grace_cycles": 1}, now_fn=lambda: 160.0)
        assert ok_dead is False
        assert "未运行" in p_dead["error_message"]


# ---------------------------------------------------------------------------
# 4. aging_ddr_disarm 测试
# ---------------------------------------------------------------------------


class TestAgingDdrDisarm:
    def test_disarm_stops_clears_and_preserves_completed_status(self, monkeypatch):
        mod = _load_script_module("aging_ddr_disarm", "aging_ddr_disarm")
        monkeypatch.setenv("STP_DEVICE_SERIAL", "SER_DIS")
        monkeypatch.setattr(mod.time, "sleep", lambda _s: None)
        monkeypatch.setattr(mod, "_wait_device_online", lambda timeout_s: True)
        monkeypatch.setattr(mod, "adb", lambda *args, timeout=15: (0, "", ""))
        monkeypatch.setattr(mod, "is_root", lambda: True)
        monkeypatch.setattr(mod, "ddr_service_state", lambda timeout=10: ("stopped", 0))

        # 模拟运行中会话 -> disarm 改写 active=False / status=STOPPED / running=False
        store = {
            "aging_pipeline_session.preferences_pb": {
                "session_id": "sid-1",
                "active": True,
                "status": "RUNNING",
                "stage_status": "DDR:RUNNING",
            },
            "ddr_test_session.preferences_pb": {
                "session_id": "sid-1",
                "running": True,
                "current_module_pass": 3,
            },
        }
        monkeypatch.setattr(mod, "read_datastore_file", lambda fn, timeout=10: ("ok", dict(store[fn])))

        def fake_write(fn, payload, **kw):
            store[fn] = dict(payload)

        monkeypatch.setattr(mod, "write_datastore_file", fake_write)

        res = mod._run({"mode": "disarm", "wait_device_online_seconds": 30})
        assert res["success"] is True
        assert res["disarm_result"] == "confirmed"
        assert res["readback"] == "clear"
        assert store["aging_pipeline_session.preferences_pb"]["active"] is False
        assert store["aging_pipeline_session.preferences_pb"]["status"] == "STOPPED"
        assert store["ddr_test_session.preferences_pb"]["running"] is False

    def test_disarm_does_not_overwrite_terminal_pass_session(self, monkeypatch):
        mod = _load_script_module("aging_ddr_disarm", "aging_ddr_disarm")
        monkeypatch.setenv("STP_DEVICE_SERIAL", "SER_DIS2")
        monkeypatch.setattr(mod.time, "sleep", lambda _s: None)
        monkeypatch.setattr(mod, "_wait_device_online", lambda timeout_s: True)
        monkeypatch.setattr(mod, "adb", lambda *args, timeout=15: (0, "", ""))
        monkeypatch.setattr(mod, "is_root", lambda: True)
        monkeypatch.setattr(mod, "ddr_service_state", lambda timeout=10: ("stopped", 0))

        store = {
            "aging_pipeline_session.preferences_pb": {
                "session_id": "sid-done",
                "active": False,
                "status": "COMPLETED",
                "stage_status": "DDR:DONE_PASS",
            },
            "ddr_test_session.preferences_pb": {"running": False},
        }
        writes: list[str] = []
        monkeypatch.setattr(mod, "read_datastore_file", lambda fn, timeout=10: ("ok", dict(store[fn])))
        monkeypatch.setattr(mod, "write_datastore_file", lambda fn, payload, **kw: writes.append(fn))

        res = mod._run({"mode": "disarm"})
        assert res["success"] is True
        assert writes == []


# ---------------------------------------------------------------------------
# 5. aging_ddr_finish 测试
# ---------------------------------------------------------------------------


class TestAgingDdrFinish:
    def test_finish_collects_datastore_csv_and_cleans_tmpdirs(self, monkeypatch, tmp_path):
        mod = _load_script_module("aging_ddr_finish", "aging_ddr_finish")
        nfs_root = tmp_path / "nfs"
        monkeypatch.setenv("STP_DEVICE_SERIAL", "SER_FIN")
        monkeypatch.setenv("STP_AEE_NFS_ROOT", str(nfs_root))
        monkeypatch.setattr(mod, "_wait_device_online", lambda wait_s: True)
        monkeypatch.setattr(mod, "device_online", lambda timeout=5: True)

        ds_map = {
            "aging_pipeline_session.preferences_pb": (
                "ok",
                {"session_id": "sid-fin", "status": "COMPLETED", "stage_status": "DDR:DONE_PASS"},
            ),
            "ddr_test_session.preferences_pb": (
                "ok",
                {"current_module_pass": 7, "module_pass_count": 7, "current_reboot_count": 10},
            ),
            "ddr_test_report.preferences_pb": (
                "ok",
                {
                    "result": "PASS",
                    "records": "TEST_END | PASS\nEXPORT:/storage/emulated/0/Android/data/com.ape.aging/files/exports/ddr_report_20261124.csv",
                },
            ),
            "ddr_test_params.preferences_pb": ("ok", {"aging_module_pass_count": "7"}),
        }
        monkeypatch.setattr(mod, "read_datastore_file", lambda fn, timeout=15: ds_map[fn])

        created_tmps: list[Path] = []
        orig_mk = mod._mk_result_tmpdir

        def track_mk():
            p = orig_mk()
            created_tmps.append(p)
            return p

        monkeypatch.setattr(mod, "_mk_result_tmpdir", track_mk)

        def fake_adb(*args, timeout=60):
            if args and args[0] == "pull":
                Path(args[2]).write_text("col1,col2\nPASS,7\n", encoding="utf-8")
                return 0, "", ""
            if args[:2] == ("shell", "cat /cache/ddr_test_flag 2>/dev/null"):
                return 0, "PASS", ""
            return 0, "", ""

        monkeypatch.setattr(mod, "adb", fake_adb)

        try:
            res = mod._run({"project": "p525", "collect_attempts": 1, "collect_retry_wait_seconds": 10})
        finally:
            mod._discard_result_tmpdirs()

        assert res["metrics"]["final_status"] == "PASS"
        assert res["metrics"]["session_id"] == "sid-fin"
        assert res["metrics"]["ddr_test_flag"] == "PASS"
        assert res["metrics"]["csv_filename"] == "ddr_report_20261124.csv"
        assert Path(res["detail_uri"]).is_file()
        assert Path(res["csv_uri"]).is_file()
        # 临时目录已被回收
        assert created_tmps and all(not p.exists() for p in created_tmps)


# ---------------------------------------------------------------------------
# 6. PipelineEngine patrol 提前终态（patrol_complete / patrol_fail）测试
# ---------------------------------------------------------------------------


def _make_engine():
    uploader = MagicMock()
    uploader.send.return_value = {"manual_action": None}
    mq = MagicMock()
    mq.connected = True
    engine = PipelineEngine(
        adb=MagicMock(),
        serial="SER_ENG",
        run_id=4242,
        log_dir=None,
        mq_producer=mq,
        api_url=None,
        is_aborted=lambda: False,
        patrol_heartbeat_uploader=uploader,
    )
    engine._verify_device_lease = lambda: None
    engine._archive_logs = lambda: None
    return engine, mq, uploader


def _lifecycle_pipeline():
    return {
        "lifecycle": {
            "timeout_seconds": 604800,
            "init": [{"step_id": "setup", "action": "script:aging_ddr_setup", "version": "1.0.0"}],
            "patrol": {
                "interval_seconds": 30,
                "steps": [{"step_id": "check", "action": "script:aging_ddr_check", "version": "1.0.0"}],
            },
            "teardown": [
                {"step_id": "disarm", "action": "script:aging_ddr_disarm", "version": "1.0.0"},
                {"step_id": "finish", "action": "script:aging_ddr_finish", "version": "1.0.0"},
            ],
        }
    }


class TestPipelineEnginePatrolEarlyExit:
    @patch("backend.agent.pipeline_engine.time.sleep", return_value=None)
    def test_patrol_complete_exits_early_and_runs_teardown(self, mock_sleep):
        engine, mq, uploader = _make_engine()
        phases_seen: list[tuple[str, str]] = []
        patrol_calls = {"n": 0}

        def fake_execute(phase, step, **kwargs):
            phases_seen.append((phase, step["step_id"]))
            if phase == "patrol":
                patrol_calls["n"] += 1
                if patrol_calls["n"] == 1:
                    return StepResult(success=True)
                return StepResult(success=True, metadata={"patrol_complete": True})
            return StepResult(success=True)

        engine._execute_step = MagicMock(side_effect=fake_execute)
        res = engine._execute_lifecycle(_lifecycle_pipeline())

        assert res.success is True
        assert res.exit_code == 0
        assert res.metadata["termination_reason"] == "completed"
        assert patrol_calls["n"] == 2
        assert ("teardown", "disarm") in phases_seen
        assert ("teardown", "finish") in phases_seen
        assert uploader.send.call_count == 2
        terminal_statuses = [c.args[1] for c in mq.send_job_status.call_args_list]
        assert terminal_statuses[-1] == "COMPLETED"

    @patch("backend.agent.pipeline_engine.time.sleep", return_value=None)
    def test_patrol_fail_exits_early_as_failed_and_runs_teardown(self, mock_sleep):
        engine, mq, uploader = _make_engine()
        phases_seen: list[tuple[str, str]] = []

        def fake_execute(phase, step, **kwargs):
            phases_seen.append((phase, step["step_id"]))
            if phase == "patrol":
                return StepResult(
                    success=False,
                    exit_code=1,
                    error_message="Aging DDR 测试失败: DDR:DONE_FAIL",
                    metadata={"patrol_fail": True},
                )
            return StepResult(success=True)

        engine._execute_step = MagicMock(side_effect=fake_execute)
        res = engine._execute_lifecycle(_lifecycle_pipeline())

        assert res.success is False
        assert res.exit_code == 1
        assert res.metadata["termination_reason"] == "patrol_failure"
        assert "DDR:DONE_FAIL" in res.error_message
        assert ("teardown", "disarm") in phases_seen
        assert ("teardown", "finish") in phases_seen
        assert uploader.send.call_count == 1
        assert uploader.send.call_args.kwargs["failed_delta"] == 1
        terminal_statuses = [c.args[1] for c in mq.send_job_status.call_args_list]
        assert terminal_statuses[-1] == "FAILED"

    def test_run_script_action_propagates_patrol_flags_from_stdout_json(self, tmp_path, monkeypatch):
        script_pass = tmp_path / "pass_script.py"
        script_pass.write_text(
            'import json; print(json.dumps({"success": True, "patrol_complete": True, "metrics": {"k": 1}}))\n',
            encoding="utf-8",
        )
        script_fail = tmp_path / "fail_script.py"
        script_fail.write_text(
            'import json, sys; print(json.dumps({"success": False, "patrol_fail": True, "error_message": "ddr failed"})); sys.exit(1)\n',
            encoding="utf-8",
        )

        class _Entry:
            def __init__(self, name, path):
                self.name = name
                self.version = "1.0.0"
                self.script_type = "python"
                self.nfs_path = str(path)
                self.package_sha256 = None

        class _Reg:
            def __init__(self, mapping):
                self._m = mapping

            def resolve(self, name, version):
                return _Entry(name, self._m[name])

        from backend.agent import pipeline_engine as pe_mod
        from backend.agent.script_packages import ResolvedScript

        monkeypatch.setattr(
            pe_mod,
            "resolve_script_path",
            lambda entry: ResolvedScript(path=entry.nfs_path, cwd=str(tmp_path), source="test"),
        )

        engine = PipelineEngine(
            adb=MagicMock(),
            serial="SER_JSON",
            run_id=99,
            script_registry=_Reg({"p": script_pass, "f": script_fail}),
        )
        from backend.agent.pipeline_engine import StepContext

        ctx = StepContext(adb=MagicMock(), serial="SER_JSON", params={}, run_id=99, step_id=0, logger=MagicMock(), phase="patrol")

        res_pass = engine._run_script_action(ctx, {"action": "script:p", "version": "1.0.0", "timeout_seconds": 30})
        assert res_pass.success is True
        assert res_pass.metadata == {"patrol_complete": True}

        res_fail = engine._run_script_action(ctx, {"action": "script:f", "version": "1.0.0", "timeout_seconds": 30})
        assert res_fail.success is False
        assert res_fail.exit_code == 1
        assert res_fail.metadata == {"patrol_fail": True}
        assert res_fail.error_message == "ddr failed"
