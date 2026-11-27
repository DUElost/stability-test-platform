# -*- coding: utf-8 -*-
"""Tinno 原厂 R30 AutoTestTool 变体（``apk_flavor="r30"``）四件套单元测试。

覆盖：
- ``powercycle_setup``：全新启动（Intent extras + appops）、续跑（不带 extras 保留 ``restarted_times``）、
  非法 ``apk_flavor`` fail-fast、默认 ``"stp"`` 路径不受影响；
- ``powercycle_check``：``RebootService`` 存活判定、``restart_preference.xml`` 进度读取、
  ``time implement result`` grep 兜底、``Test result``/``Total :`` 完结识别、暂停/恢复；
- ``powercycle_disarm``：``reboot_service_going=true`` 足迹识别与停服清标后回读 ``clear``；
- ``powercycle_finish``：拉取 ``ShutdownTest/powercycle_result.txt`` 并解析 PASS / FAIL /
  亮灭屏检查失败 / INCOMPLETE。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
_SCRIPTS = REPO_ROOT / "backend" / "agent" / "scripts"

_R30_PREFS_RUNNING = """<?xml version='1.0' encoding='utf-8' standalone='yes' ?>
<map>
    <int name="Num" value="500" />
    <int name="restarted_times" value="12" />
    <int name="passtimes" value="12" />
    <int name="reboot_wait_time" value="30" />
    <boolean name="reboot_service_going" value="true" />
    <string name="on_off_filename">powercycle_result.txt</string>
</map>
"""

_R30_PREFS_CLEAR = """<?xml version='1.0' encoding='utf-8' standalone='yes' ?>
<map>
    <int name="Num" value="0" />
    <int name="restarted_times" value="0" />
    <boolean name="reboot_service_going" value="false" />
</map>
"""

_R30_RESULT_PASS = (
    "--------------------AutoTestTool_M03_M01_R30--------------------\n"
    "The 1 time implement result---------------------PASS\n"
    "screenOnCheck true, screenOffCheck true\n"
    "The 2 time implement result---------------------PASS\n"
    "screenOnCheck true, screenOffCheck true\n"
    "Test result： Total :2 / NotExecuted :0 / Pass :2 / Fail :0\n"
).encode("utf-8")

_R30_RESULT_FAIL = (
    "The 1 time implement result---------------------PASS\n"
    "screenOnCheck true, screenOffCheck true\n"
    "The 2 time implement result---------------------FAIL\n"
    "screenOnCheck false, screenOffCheck true\n"
    "Test result： Total :2 / NotExecuted :0 / Pass :1 / Fail :1\n"
).encode("utf-8")

_R30_RESULT_INCOMPLETE = (
    "The 1 time implement result---------------------PASS\n"
    "screenOnCheck true, screenOffCheck true\n"
).encode("utf-8")


def _load(name: str, rel_path: str):
    path = _SCRIPTS / rel_path
    sys.path.insert(0, str(path.parent))
    try:
        sys.modules.pop("_lib", None)
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec and spec.loader, f"cannot locate {path}"
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        sys.modules.pop("_lib", None)
        sys.path.remove(str(path.parent))


class _AdvancingClock:
    def __init__(self, start: float = 1_000_000.0) -> None:
        self.t = start

    def time(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.t += max(0.0, float(seconds))

    def strftime(self, fmt: str) -> str:
        return "20261126_160000"


# ---------------------------------------------------------------------------
# powercycle_setup (v1.2.11)
# ---------------------------------------------------------------------------


def test_setup_config_rejects_invalid_apk_flavor(monkeypatch):
    lib = _load("pc_setup_lib_invalid", "powercycle_setup/_lib.py")
    monkeypatch.delenv("STP_POWER_CYCLE_APK_FLAVOR", raising=False)
    with pytest.raises(ValueError, match="apk_flavor"):
        lib.powercycle_config({"apk_flavor": "unknown"})


def test_setup_r30_fresh_start_passes_expected_extras(monkeypatch, tmp_path):
    mod = _load("pc_setup_mod_r30_fresh", "powercycle_setup/powercycle_setup.py")
    lib = sys.modules.get("_lib") or _load("pc_setup_lib_r30_fresh", "powercycle_setup/_lib.py")
    assert lib is not None

    apk = tmp_path / "AutoTestTool.apk"
    apk.write_bytes(b"fake-r30-apk")

    shell_cmds: list[str] = []
    # Rebind helpers through the module loaded alongside its own _lib
    monkeypatch.setattr(mod, "resources_dir", lambda cfg: tmp_path)
    monkeypatch.setattr(mod, "try_adb_root", lambda: True)
    monkeypatch.setattr(mod, "install_apk", lambda p: None)
    monkeypatch.setattr(mod, "clear_cross_prefs", lambda: None)
    monkeypatch.setattr(mod, "check_reboot_permission", lambda: "system_permission")
    monkeypatch.setattr(mod, "set_device_stability", lambda: None)
    monkeypatch.setattr(mod, "grant_storage", lambda: None)
    monkeypatch.setattr(mod, "service_probe", lambda apk_flavor="stp": (apk_flavor == "r30", 0, "RebootService"))
    monkeypatch.setattr(mod, "service_alive", lambda apk_flavor="stp": apk_flavor == "r30")
    monkeypatch.setattr(mod, "time", _AdvancingClock())

    setup_lib_globals = mod.prepare_r30_state.__globals__
    monkeypatch.setitem(setup_lib_globals, "is_root", lambda: True)
    monkeypatch.setitem(setup_lib_globals, "adb_shell", lambda cmd, timeout=30: (shell_cmds.append(cmd) or ""))

    res = mod._run({
        "project": "P525AA",
        "apk_flavor": "r30",
        "test_times": 999999,
        "wait_seconds": 30,
        "reset_count": True,
        "auto_resume": True,
    })

    assert res["apk_flavor"] == "r30"
    assert res["service_started"] is True
    assert res["current_count"] == 0
    assert any("SYSTEM_ALERT_WINDOW allow" in c for c in shell_cmds)
    assert any("restart_preference.xml" in c and "rm -f" in c for c in shell_cmds)
    start_cmds = [c for c in shell_cmds if "am start-foreground-service" in c]
    assert len(start_cmds) == 1
    cmd = start_cmds[0]
    assert ".reboot.RebootService" in cmd
    assert "--ei Num 999999" in cmd
    assert "--ei reboot_wait_time 30" in cmd
    assert "--ei reboot_fill_size 0" in cmd
    assert "--ei SENSOR_NUM_CHECK -1" in cmd
    assert "--ez reboot_service_going true" in cmd
    assert "--ez power_off_wakeup_or_reboot false" in cmd
    assert "--es on_off_filename powercycle_result.txt" in cmd


def test_setup_r30_resume_preserves_count_without_extras(monkeypatch):
    lib = _load("pc_setup_lib_r30_resume", "powercycle_setup/_lib.py")
    shell_cmds: list[str] = []
    monkeypatch.setattr(lib, "is_root", lambda: True)
    monkeypatch.setattr(lib, "adb_shell", lambda cmd, timeout=30: (shell_cmds.append(cmd) or ""))
    monkeypatch.setattr(lib, "adb", lambda *args, timeout=30: (0, _R30_PREFS_RUNNING, ""))

    cur, resume_only = lib.prepare_r30_state({"reset_count": False})
    assert cur == 12
    assert resume_only is True

    lib.start_task_r30({"test_times": 500, "wait_seconds": 30, "auto_resume": True}, resume_only=True)
    start_cmds = [c for c in shell_cmds if "am start-foreground-service" in c]
    assert len(start_cmds) == 1
    assert start_cmds[0].endswith(".reboot.RebootService")
    assert "--ei Num" not in start_cmds[0]


# ---------------------------------------------------------------------------
# powercycle_check (v1.0.12)
# ---------------------------------------------------------------------------


def test_check_r30_reads_prefs_grep_finished_and_pause_resume(monkeypatch):
    mod = _load("pc_check_mod_r30", "powercycle_check/powercycle_check.py")
    lib_globals = mod.get_r30_prefs_xml.__globals__

    monkeypatch.setattr(mod, "get_r30_prefs_xml", lambda: _R30_PREFS_RUNNING)
    assert mod._read_prefs_progress("r30") == (12, 500)

    seen_patterns: list[tuple[str, str]] = []
    monkeypatch.setattr(
        mod,
        "_adb_grep",
        lambda path, pattern="cycle ": (seen_patterns.append((path, pattern)) or (0, "7\n", "")),
    )
    assert mod._grep_cycle_count("r30") == 7
    assert seen_patterns == [
        (
            "/sdcard/Android/data/com.tinno.autotesttool/files/AutoTestTool/ShutdownTest/powercycle_result.txt",
            "time implement result",
        )
    ]

    class _FakeCompleted:
        def __init__(self, stdout: str) -> None:
            self.returncode = 0
            self.stdout = stdout
            self.stderr = ""

    monkeypatch.setenv("STP_DEVICE_SERIAL", "SER-R30")
    monkeypatch.setattr(
        mod.subprocess,
        "run",
        lambda *a, **kw: _FakeCompleted(_R30_RESULT_PASS.decode("utf-8")),
    )
    assert mod._run_finished("r30") is True

    pushed_xml: list[str] = []
    shell_cmds: list[str] = []
    monkeypatch.setitem(lib_globals, "adb_shell", lambda cmd, timeout=30: (shell_cmds.append(cmd) or ""))
    monkeypatch.setitem(lib_globals, "get_r30_prefs_xml", lambda: _R30_PREFS_RUNNING)
    monkeypatch.setitem(lib_globals, "_push_r30_prefs_xml", lambda xml: pushed_xml.append(xml))

    mod.pause_task_r30()
    assert any("am force-stop com.tinno.autotesttool" in c for c in shell_cmds)
    assert 'name="reboot_service_going" value="false"' in pushed_xml[-1]

    mod.resume_task_r30()
    assert 'name="reboot_service_going" value="true"' in pushed_xml[-1]
    assert any("am start-foreground-service -n com.tinno.autotesttool/.reboot.RebootService" in c for c in shell_cmds)


# ---------------------------------------------------------------------------
# powercycle_disarm (v1.0.10)
# ---------------------------------------------------------------------------


def test_disarm_r30_footprint_then_clear_readback(monkeypatch):
    mod = _load("pc_disarm_mod_r30", "powercycle_disarm/powercycle_disarm.py")
    lib_globals = mod.stop_task_r30.__globals__

    monkeypatch.setenv("STP_DEVICE_SERIAL", "SER-R30")
    monkeypatch.setattr(mod, "is_root", lambda: True)
    monkeypatch.setattr(mod, "device_online", lambda timeout=15: True)
    monkeypatch.setattr(mod, "adb_shell", lambda cmd, timeout=30: "1" if "sys.boot_completed" in cmd else "")
    monkeypatch.setattr(mod, "permission_model", lambda: "adb_root")
    monkeypatch.setattr(mod, "autotesttool_apk_sha256", lambda timeout=30: "deadbeef")
    monkeypatch.setattr(mod, "time", _AdvancingClock())

    state = {"xml": _R30_PREFS_RUNNING}

    def fake_read():
        return ("ok", state["xml"], True)

    def fake_stop(*, force: bool = True, kind: str = "", installed=True):
        state["xml"] = _R30_PREFS_CLEAR
        return {
            "force_stop_rc": 0,
            "service_state": "stopped",
            "write_ok": True,
            "write_method": "root_push",
            "write_error": "",
        }

    monkeypatch.setattr(mod, "read_r30_prefs_classified", fake_read)
    monkeypatch.setattr(mod, "stop_task_r30", fake_stop)
    monkeypatch.setitem(lib_globals, "is_root", lambda: True)

    # Probe sees active footprint
    probe_res = mod._run({"mode": "probe", "apk_flavor": "r30"})
    assert probe_res["readback"] == "footprint"
    assert probe_res["disarm_result"] == "failed"

    # Disarm stops RebootService + clears reboot_service_going → executed_unverified
    disarm_res = mod._run({"mode": "disarm", "apk_flavor": "r30"})
    assert disarm_res["readback"] == "clear"
    assert disarm_res["disarm_result"] == "executed_unverified"
    assert disarm_res["success"] is True


def test_disarm_r30_attempts_adb_root_when_non_root_after_reboot(monkeypatch):
    """P525 Release_user_root 重启后 adbd 回落 non-root：r30 路径先 try_adb_root，stp 路径不触发。"""
    mod = _load("pc_disarm_mod_r30_root", "powercycle_disarm/powercycle_disarm.py")
    lib_globals = mod.stop_task_r30.__globals__

    monkeypatch.setenv("STP_DEVICE_SERIAL", "SER-R30")
    monkeypatch.setattr(mod, "device_online", lambda timeout=15: True)
    monkeypatch.setattr(mod, "adb_shell", lambda cmd, timeout=30: "1" if "sys.boot_completed" in cmd else "")
    monkeypatch.setattr(mod, "autotesttool_apk_sha256", lambda timeout=30: "deadbeef")
    monkeypatch.setattr(mod, "time", _AdvancingClock())

    state = {"root": False, "xml": _R30_PREFS_RUNNING, "root_calls": 0}

    def fake_is_root():
        return state["root"]

    def fake_try_adb_root():
        state["root_calls"] += 1
        state["root"] = True
        return True

    def fake_read_r30():
        if not state["root"]:
            return ("denied", "", True)
        return ("ok", state["xml"], True)

    def fake_stop_r30(*, force: bool = True, kind: str = "", installed=True):
        assert state["root"] is True
        state["xml"] = _R30_PREFS_CLEAR
        return {
            "force_stop_rc": 0,
            "service_state": "stopped",
            "write_ok": True,
            "write_method": "root_push",
            "write_error": "",
        }

    monkeypatch.setattr(mod, "is_root", fake_is_root)
    monkeypatch.setattr(mod, "try_adb_root", fake_try_adb_root)
    monkeypatch.setattr(mod, "permission_model", lambda: "root" if state["root"] else "non-root")
    monkeypatch.setattr(mod, "read_r30_prefs_classified", fake_read_r30)
    monkeypatch.setattr(mod, "stop_task_r30", fake_stop_r30)
    monkeypatch.setitem(lib_globals, "is_root", fake_is_root)

    # 1. R30 disarm: non-root → try_adb_root → clear
    res = mod._run({"mode": "disarm", "apk_flavor": "r30"})
    assert state["root_calls"] == 1
    assert res["readback"] == "clear"
    assert res["disarm_result"] == "executed_unverified"
    assert res["success"] is True

    # 2. R30 probe when non-root again → calls try_adb_root once
    state["root"] = False
    state["xml"] = _R30_PREFS_RUNNING
    probe_res = mod._run({"mode": "probe", "apk_flavor": "r30"})
    assert state["root_calls"] == 2
    assert probe_res["readback"] == "footprint"

    # 3. Default STP path when non-root → must NOT call try_adb_root
    state["root"] = False
    monkeypatch.setattr(
        mod,
        "read_prefs_classified",
        lambda: ("ok", '<map><boolean name="running" value="false" /></map>', True),
    )
    stp_res = mod._run({"mode": "probe"})
    assert state["root_calls"] == 2
    assert stp_res["readback"] == "clear"


# ---------------------------------------------------------------------------
# powercycle_finish (v1.1.1)
# ---------------------------------------------------------------------------


def test_finish_r30_parse_pass_fail_and_incomplete():
    lib = _load("pc_finish_lib_r30", "powercycle_finish/_lib.py")

    p_pass = lib.parse_r30_powercycle_result(_R30_RESULT_PASS)
    assert p_pass["final_status"] == "PASS"
    assert p_pass["cycles_done"] == 2
    assert p_pass["expected_cycles"] == 2
    assert p_pass["reboot_failures"] == 0

    p_fail = lib.parse_r30_powercycle_result(_R30_RESULT_FAIL)
    assert p_fail["final_status"] == "FAIL"
    assert p_fail["cycles_done"] == 2
    assert p_fail["expected_cycles"] == 2
    assert p_fail["reboot_failures"] >= 1

    p_inc = lib.parse_r30_powercycle_result(_R30_RESULT_INCOMPLETE)
    assert p_inc["final_status"] is None
    assert p_inc["cycles_done"] == 1
    assert p_inc["reboot_failures"] == 0


def test_finish_r30_pulls_shutdown_test_result_path(monkeypatch, tmp_path):
    mod = _load("pc_finish_mod_r30", "powercycle_finish/powercycle_finish.py")
    monkeypatch.setenv("STP_DEVICE_SERIAL", "SER-R30")
    monkeypatch.setattr(mod, "time", _AdvancingClock())
    monkeypatch.setattr(mod, "_wait_device_online", lambda wait_s: True)
    monkeypatch.setattr(mod, "_mk_result_tmpdir", lambda: tmp_path)
    monkeypatch.setattr(mod, "results_dir", lambda project: tmp_path / "results" / project)

    pulled_remote: list[str] = []

    def fake_shell(cmd: str, timeout: int = 30) -> str:
        if cmd.startswith("ls -l "):
            return "-rw-rw---- 1 u0_a1 media_rw 256 2026-11-26 16:00 powercycle_result.txt"
        return ""

    def fake_adb(*args, timeout: int = 120):
        if args and args[0] == "pull":
            pulled_remote.append(args[1])
            Path(args[2]).write_bytes(_R30_RESULT_PASS)
            return 0, "", ""
        return 0, "", ""

    monkeypatch.setattr(mod, "adb_shell", fake_shell)
    monkeypatch.setattr(mod, "adb", fake_adb)

    res = mod._run({"apk_flavor": "r30", "project": "P525AA"})
    assert res["metrics"]["final_status"] == "PASS"
    assert res["metrics"]["cycles_done"] == 2
    assert res["metrics"]["expected_cycles"] == 2
    assert pulled_remote == [
        "/sdcard/Android/data/com.tinno.autotesttool/files/AutoTestTool/ShutdownTest/powercycle_result.txt"
    ]
