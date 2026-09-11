# -*- coding: utf-8 -*-
"""设备脚本杂项批回归（#816）——install_apk / oobe_skip / connect_wifi /
aee_signal_trigger / aee_prepare / flash_firmware 新版本守门。

加载方式同 test_base_tools_rc_guards.py（importlib + _adb 缓存清理）。
"""
from __future__ import annotations

import importlib.util
import shlex
import sys
from pathlib import Path
from subprocess import CompletedProcess

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def _load(name: str, rel_path: str):
    path = _SCRIPTS / rel_path
    sys.path.insert(0, str(path.parent))
    try:
        sys.modules.pop("_adb", None)
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec and spec.loader, f"cannot locate {path}"
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        sys.modules.pop("_adb", None)
        sys.path.remove(str(path.parent))


class _Output:
    def __init__(self) -> None:
        self.payloads: list[dict] = []

    def __call__(self, success, **kwargs) -> None:
        self.payloads.append({"success": success, **kwargs})

    @property
    def last(self) -> dict:
        assert self.payloads, "output_result was not called"
        return self.payloads[-1]


def _cp(returncode: int = 0, stdout: str = "", stderr: str = "") -> CompletedProcess:
    return CompletedProcess(args=["adb"], returncode=returncode, stdout=stdout, stderr=stderr)


# ── 1. install_apk v1.0.2 ────────────────────────────────────────────────────


def _prep_install_apk(monkeypatch, mod, dumpsys_stdout):
    capture = _Output()
    monkeypatch.setattr(mod, "device_serial", lambda: "S")
    monkeypatch.setattr(mod, "output_result", capture)
    monkeypatch.setattr(
        mod,
        "params",
        lambda: {"apk_path": "/tmp/a.apk", "pkg_name": "com.x", "required_version": "1.0.1"},
    )

    def fake_run(cmd, **kw):
        if any("dumpsys" in str(c) for c in cmd):
            return _cp(0, dumpsys_stdout)
        return _cp(0, "Success")

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    return capture


def test_install_apk_near_version_is_not_skipped(monkeypatch):
    """#816：已装 1.0.10 不得因 required=1.0.1 子串命中被误判 skipped。"""
    mod = _load("install_apk_v102", "install_apk/v1.0.2/install_apk.py")
    capture = _prep_install_apk(monkeypatch, mod, "    versionName=1.0.10\n")

    mod.main()

    assert capture.last["success"] is True
    assert not any(p.get("skipped") for p in capture.payloads)


def test_install_apk_exact_version_skips(monkeypatch):
    mod = _load("install_apk_v102_exact", "install_apk/v1.0.2/install_apk.py")
    capture = _prep_install_apk(monkeypatch, mod, "    versionName=1.0.1\n")

    mod.main()

    assert capture.last.get("skipped") is True


# ── 2. oobe_skip v1.1.1 ──────────────────────────────────────────────────────


def test_oobe_skip_wait_adbd_ready_polls_until_device(monkeypatch):
    mod = _load("oobe_skip_v111", "oobe_skip/v1.1.1/oobe_skip.py")
    seq = [(-1, "error: device offline"), (0, "device")]
    state = {"n": 0}

    def fake_shell(serial, adb_path, args, timeout=15):
        i = min(state["n"], len(seq) - 1)
        state["n"] += 1
        return seq[i]

    monkeypatch.setattr(mod, "_adb_shell", fake_shell)
    monkeypatch.setattr(mod.time, "sleep", lambda _s: None)

    assert mod._wait_adbd_ready("S", "adb") is True
    assert state["n"] == 2


def test_oobe_skip_wait_adbd_ready_times_out(monkeypatch):
    mod = _load("oobe_skip_v111_to", "oobe_skip/v1.1.1/oobe_skip.py")
    monkeypatch.setattr(mod, "_adb_shell", lambda *a, **k: (-1, "offline"))
    ticks = iter([0.0, 100.0])
    monkeypatch.setattr(mod.time, "monotonic", lambda: next(ticks, 100.0))
    monkeypatch.setattr(mod.time, "sleep", lambda _s: None)

    assert mod._wait_adbd_ready("S", "adb") is False


# ── 3. connect_wifi v1.0.1 ───────────────────────────────────────────────────


def test_connect_wifi_quotes_credentials_and_verifies(monkeypatch):
    mod = _load("connect_wifi_v101", "connect_wifi/v1.0.1/connect_wifi.py")
    capture = _Output()
    ssid, password = 'my"ssid', "p$w`x"
    monkeypatch.setattr(mod, "device_serial", lambda: "S")
    monkeypatch.setattr(mod, "output_result", capture)
    monkeypatch.setattr(
        mod, "params", lambda: {"ssid": ssid, "password": password, "timeout_seconds": 5}
    )
    monkeypatch.setattr(mod.time, "sleep", lambda _s: None)
    monkeypatch.setattr(mod, "adb_shell", lambda cmd, timeout=10: "")
    cmds: list[str] = []

    def fake_quiet(cmd, timeout=10):
        cmds.append(cmd)
        return _cp(0, "")

    monkeypatch.setattr(mod, "adb_shell_quiet", fake_quiet)
    conn_state = [False, True]  # 开头未连接 → 命令后回读已连接

    def fake_connected(serial, s):
        return conn_state.pop(0) if conn_state else True

    monkeypatch.setattr(mod, "_is_connected", fake_connected)

    mod.main()

    connect_cmd = next(c for c in cmds if c.startswith("cmd -w wifi connect-network"))
    assert shlex.quote(ssid) in connect_cmd
    assert shlex.quote(password) in connect_cmd
    assert f'"{ssid}"' not in connect_cmd  # 原拼接形态不得出现
    assert capture.last["success"] is True


def test_connect_wifi_rc_failure_reports_error(monkeypatch):
    mod = _load("connect_wifi_v101_rc", "connect_wifi/v1.0.1/connect_wifi.py")
    capture = _Output()
    monkeypatch.setattr(mod, "device_serial", lambda: "S")
    monkeypatch.setattr(mod, "output_result", capture)
    monkeypatch.setattr(mod, "params", lambda: {"ssid": "ok", "password": "p"})
    monkeypatch.setattr(mod.time, "sleep", lambda _s: None)
    monkeypatch.setattr(mod, "adb_shell", lambda cmd, timeout=10: "")
    monkeypatch.setattr(mod, "_is_connected", lambda serial, ssid: False)

    def fake_quiet(cmd, timeout=10):
        if cmd.startswith("cmd -w wifi connect-network"):
            return _cp(1, "", "Error: bad password")
        return _cp(0, "")

    monkeypatch.setattr(mod, "adb_shell_quiet", fake_quiet)

    mod.main()

    assert capture.last["success"] is False
    assert "WiFi connect failed" in capture.last["error_message"]


# ── 4. aee_signal_trigger v1.0.1 ─────────────────────────────────────────────


def _aee_line(pkg: str) -> str:
    return f"/data/aee_exp/db.01,Native (NE),0,0,0,0,0,0,{pkg},2026-09-11 10:00:00,extra"


def _prep_trigger(monkeypatch, mod, lines_seq):
    capture = _Output()
    monkeypatch.setattr(mod, "device_serial", lambda: "S")
    monkeypatch.setattr(mod, "output_result", capture)
    monkeypatch.setattr(
        mod,
        "params",
        lambda: {
            "package_name": "com.mine",
            "poll_timeout_seconds": 0.05,
            "poll_interval_seconds": 0.01,
        },
    )
    monkeypatch.setattr(mod, "_ensure_root", lambda: None)
    monkeypatch.setattr(mod, "_resolve_pid", lambda pkg: "111")
    monkeypatch.setattr(mod, "_run_adb", lambda *a, timeout=30: (0, "", ""))
    monkeypatch.setattr(mod, "_db_history_hash", lambda: "h")
    monkeypatch.setattr(mod, "_count_files_in_dir", lambda p: 1)
    monkeypatch.setattr(mod.time, "sleep", lambda _s: None)
    state = {"n": 0}

    def fake_lines():
        i = min(state["n"], len(lines_seq) - 1)
        state["n"] += 1
        return lines_seq[i]

    monkeypatch.setattr(mod, "_db_history_lines", fake_lines)
    return capture


def test_aee_signal_trigger_ignores_other_package_lines(monkeypatch):
    """#816：并发来源（其它包）新行不得被认领；等到本包行才成功。"""
    mod = _load("aee_trigger_v101", "aee_signal_trigger/v1.0.1/aee_signal_trigger.py")
    other, mine = _aee_line("com.other"), _aee_line("com.mine")
    capture = _prep_trigger(monkeypatch, mod, [[], [other], [other, mine]])

    mod.main()

    assert capture.last["success"] is True
    assert capture.last["metrics"]["package_name"] == "com.mine"
    assert capture.last["metrics"]["killed_pid"] == "111"


def test_aee_signal_trigger_rejects_unmatched_only(monkeypatch):
    """#816：窗口内只有其它包的新行——明确失败（拒绝上报），不误吸。"""
    mod = _load(
        "aee_trigger_v101_unmatched", "aee_signal_trigger/v1.0.1/aee_signal_trigger.py"
    )
    other = _aee_line("com.other")
    capture = _prep_trigger(monkeypatch, mod, [[], [other]])

    mod.main()

    assert capture.last["success"] is False
    assert "归属不匹配" in capture.last["error_message"]


def test_aee_signal_trigger_kill_failure(monkeypatch):
    mod = _load("aee_trigger_v101_kill", "aee_signal_trigger/v1.0.1/aee_signal_trigger.py")
    capture = _Output()
    monkeypatch.setattr(mod, "device_serial", lambda: "S")
    monkeypatch.setattr(mod, "output_result", capture)
    monkeypatch.setattr(
        mod,
        "params",
        lambda: {
            "package_name": "com.mine",
            "poll_timeout_seconds": 0.05,
            "poll_interval_seconds": 0.01,
        },
    )
    monkeypatch.setattr(mod, "_ensure_root", lambda: None)
    monkeypatch.setattr(mod, "_resolve_pid", lambda pkg: "111")
    calls = {"n": 0}

    def fake_adb(*a, timeout=30):
        calls["n"] += 1
        return (0, "", "") if calls["n"] == 1 else (1, "", "EPERM")

    monkeypatch.setattr(mod, "_run_adb", fake_adb)
    monkeypatch.setattr(mod, "_db_history_hash", lambda: "h")
    monkeypatch.setattr(mod, "_db_history_lines", lambda: [])
    monkeypatch.setattr(mod.time, "sleep", lambda _s: None)

    mod.main()

    assert capture.last["success"] is False
    assert "kill -11 111 failed" in capture.last["error_message"]


# ── 5. aee_prepare v1.0.1 ────────────────────────────────────────────────────


def test_aee_prepare_restores_dev_settings_on_failure_path(monkeypatch):
    """#816：模式设置失败早退时，finally 仍须恢复开发设置。"""
    mod = _load("aee_prepare_v101", "aee_prepare/v1.0.1/aee_prepare.py")
    capture = _Output()
    monkeypatch.setattr(mod, "device_serial", lambda: "S")
    monkeypatch.setattr(mod, "output_result", capture)
    monkeypatch.setattr(mod, "_wait_adbd_ready", lambda serial, **k: True)
    calls: list[str] = []

    def fake_shell(serial, cmd, timeout=30):
        calls.append(cmd)
        if cmd.startswith(f"setprop {mod._AEE_MODE_PROP}"):
            return (1, "")  # setprop 失败 → 早退
        if cmd.startswith(f"getprop {mod._AEE_MODE_PROP}"):
            return (0, "4")
        return (0, "")

    monkeypatch.setattr(mod, "_shell", fake_shell)
    monkeypatch.setattr(mod, "_run_adb", lambda serial, args, timeout=30: (0, ""))

    mod.main()

    assert capture.last["success"] is False
    assert any("development_settings_enabled 0" in c for c in calls)


# ── 6. flash_firmware v1.3.12 ────────────────────────────────────────────────


def test_flash_firmware_lock_rejects_symlink(monkeypatch):
    mod = _load("flash_v1312", "flash_firmware/v1.3.12/flash_firmware.py")
    monkeypatch.setattr(mod.platform, "system", lambda: "Linux")

    def fake_open(*a, **k):
        raise OSError(40, "Too many levels of symbolic links")

    monkeypatch.setattr(mod.os, "open", fake_open)
    raised = False
    try:
        mod._acquire_host_lock()
    except RuntimeError as exc:
        raised = True
        assert "symlink" in str(exc)
    assert raised, "symlink 预置时必须明确拒绝（RuntimeError）"


def test_flash_firmware_lock_source_uses_o_nofollow():
    text = (_SCRIPTS / "flash_firmware/v1.3.12/flash_firmware.py").read_text(
        encoding="utf-8"
    )
    assert "os.O_NOFOLLOW" in text
    assert 'os.fdopen(fd, "w")' in text  # 不得回退裸 open("w")
