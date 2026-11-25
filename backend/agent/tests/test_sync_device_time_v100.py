"""sync_device_time v1.0.0：测试前置设备时区与系统时钟强制同步。

验证：
- 主路径（cmd alarm set-timezone + set-time，无需 root）成功 + 回读核验 + metrics 留痕；
- 主路径失败（如 Android ≤ 11 或裁剪权限）→ 自动走 root 兜底（adb root + setprop + date + hwclock）并回读通过；
- 主路径与兜底均失败（非 root 无法提权 / 回读仍偏斜）→ 显式失败并带完整设备诊断信息；
- 参数校验：缺 serial、非法 timezone（含命令注入形态）、非法 max_drift_seconds 一律 fail-closed；
- 进度戳（PROGRESS）与 `_wait_adbd_ready` 双上界（不触发推进时钟死循环）。
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path

_SCRIPT_PATH = (
    Path(__file__).resolve().parents[2]
    / "agent" / "scripts" / "sync_device_time" / "sync_device_time.py"
)

_spec = importlib.util.spec_from_file_location("sync_device_time_v100", _SCRIPT_PATH)
assert _spec is not None and _spec.loader is not None
sdt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sdt)


def _patch_advancing_clock(monkeypatch, mod, *, start: float = 1_000_000.0):
    """Replace mod.time.sleep/time so sleep advances a fake clock (no wall wait).

    同构自 `test_powercycle_scripts.py`（#3202 守卫：sleep 换 no-op 却不推进时钟
    = 等待环靠真墙钟判 deadline 的忙等形态）。
    """
    state = {"now": start}

    def fake_time() -> float:
        return state["now"]

    def fake_sleep(seconds: float) -> None:
        state["now"] += float(seconds)

    monkeypatch.setattr(mod.time, "time", fake_time)
    monkeypatch.setattr(mod.time, "sleep", fake_sleep)
    return state


def _proc(rc: int = 0, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=["adb"], returncode=rc, stdout=stdout, stderr=stderr)


def _wire_device(
    monkeypatch,
    *,
    host_epoch: int = 1795575200,
    dev_epoch_before: int = 1795000000,
    tz_before: str = "UTC",
    uid: int = 2000,
    sdk: str = "36",
    build_type: str = "user",
    debuggable: str = "1",
    alarm_tz_rc: int = 0,
    alarm_tz_err: str = "",
    alarm_time_rc: int = 0,
    alarm_time_err: str = "",
    root_rc: int = 0,
    root_uid_after: int = 0,
    root_setprop_rc: int = 0,
    root_date_rc: int = 0,
    hwclock_rc: int = 0,
    force_verify_drift: int | None = None,
):
    """构造可观测的模拟设备状态机（通过 subprocess_run 注入）。"""
    state = {
        "uid": uid,
        "tz": tz_before,
        "epoch": dev_epoch_before,
        "calls": [],
    }
    monkeypatch.setattr(sdt.time, "time", lambda: float(host_epoch))
    monkeypatch.setattr(sdt.time, "sleep", lambda _s: None)

    def fake_run(argv, **_kwargs):
        state["calls"].append(argv)
        args = argv[3:]  # strip [adb, -s, serial]
        if args == ["root"]:
            if root_rc == 0:
                state["uid"] = root_uid_after
            return _proc(root_rc, "" if root_rc == 0 else "adbd cannot run as root")
        if args == ["get-state"]:
            return _proc(0, "device\n")
        if args[:1] == ["shell"]:
            sargs = args[1:]
            if len(sargs) == 1 and "UID=" in sargs[0] and "EPOCH=" in sargs[0]:
                return _proc(
                    0,
                    f"UID={state['uid']}\nSDK={sdk}\nBTYPE={build_type}\n"
                    f"DBG={debuggable}\nTZ={state['tz']}\nEPOCH={state['epoch']}\n",
                )
            if len(sargs) == 1 and "TZ=" in sargs[0] and "EPOCH=" in sargs[0]:
                reported_epoch = (
                    host_epoch + force_verify_drift
                    if force_verify_drift is not None
                    else state["epoch"]
                )
                return _proc(0, f"TZ={state['tz']}\nEPOCH={reported_epoch}\n")
            if sargs[:3] == ["cmd", "alarm", "set-timezone"]:
                if alarm_tz_rc == 0 and not alarm_tz_err:
                    state["tz"] = sargs[3]
                return _proc(alarm_tz_rc, "", alarm_tz_err)
            if sargs[:3] == ["cmd", "alarm", "set-time"]:
                if alarm_time_rc == 0 and not alarm_time_err:
                    state["epoch"] = int(sargs[3]) // 1000
                return _proc(alarm_time_rc, "", alarm_time_err)
            if sargs == ["id", "-u"]:
                return _proc(0, f"{state['uid']}\n")
            if sargs[:2] == ["setprop", "persist.sys.timezone"]:
                if root_setprop_rc == 0:
                    state["tz"] = sargs[2]
                return _proc(root_setprop_rc, "", "" if root_setprop_rc == 0 else "setprop denied")
            if sargs[:2] == ["date", "-u"]:
                if root_date_rc == 0 and sargs[2].startswith("@"):
                    state["epoch"] = int(sargs[2][1:])
                return _proc(root_date_rc, "", "" if root_date_rc == 0 else "Operation not permitted")
            if sargs[:2] == ["hwclock", "-w"]:
                return _proc(hwclock_rc, "", "" if hwclock_rc == 0 else "hwclock unsupported")
        return _proc(1, "", f"unexpected argv: {argv}")

    monkeypatch.setattr(sdt, "subprocess_run", fake_run)
    return state


class TestSyncDeviceTime:
    def test_primary_cmd_alarm_non_root_success(self, monkeypatch, capsys):
        state = _wire_device(monkeypatch, uid=2000, tz_before="UTC")
        monkeypatch.setenv("STP_DEVICE_SERIAL", "SER-NONROOT")
        monkeypatch.setenv("STP_STEP_PARAMS", json.dumps({"timezone": "Asia/Shanghai"}))

        sdt.main()
        captured = capsys.readouterr()
        payload = json.loads(captured.out.strip())

        assert payload["success"] is True
        assert payload["skipped"] is False
        m = payload["metrics"]
        assert m["method"] == "cmd_alarm"
        assert m["timezone_before"] == "UTC"
        assert m["timezone_after"] == "Asia/Shanghai"
        assert m["drift_before_seconds"] == -575200
        assert m["drift_after_seconds"] == 0
        assert m["device_uid"] == 2000
        assert m["alarm_cmd_ok"] is True
        # 不应触发 adb root
        assert not any(argv[3:] == ["root"] for argv in state["calls"])
        # 进度戳发出
        stamps = [ln for ln in captured.err.splitlines() if ln.startswith("PROGRESS ")]
        assert any('"stage": "sync-alarm"' in s for s in stamps)
        assert any('"stage": "done"' in s for s in stamps)

    def test_fallback_to_root_when_cmd_alarm_unsupported(self, monkeypatch, capsys):
        state = _wire_device(
            monkeypatch,
            uid=2000,
            sdk="30",
            alarm_tz_rc=1,
            alarm_tz_err="Unknown command: set-timezone",
            alarm_time_rc=1,
            alarm_time_err="Unknown command: set-time",
            root_rc=0,
            root_uid_after=0,
            hwclock_rc=1,  # hwclock 是 best-effort，不应阻断成功
        )
        monkeypatch.setenv("STP_DEVICE_SERIAL", "SER-A11")
        monkeypatch.setenv("STP_STEP_PARAMS", json.dumps({"timezone": "Asia/Shanghai"}))

        sdt.main()
        payload = json.loads(capsys.readouterr().out.strip())

        assert payload["success"] is True
        m = payload["metrics"]
        assert m["method"] == "root_fallback"
        assert m["timezone_after"] == "Asia/Shanghai"
        assert m["drift_after_seconds"] == 0
        assert m["alarm_cmd_ok"] is False
        assert any(argv[3:] == ["root"] for argv in state["calls"])
        assert any(argv[3:6] == ["shell", "setprop", "persist.sys.timezone"] for argv in state["calls"])
        assert any(argv[3:6] == ["shell", "date", "-u"] for argv in state["calls"])

    def test_fails_cleanly_when_cmd_alarm_denied_and_no_root(self, monkeypatch, capsys):
        _wire_device(
            monkeypatch,
            uid=2000,
            build_type="user",
            debuggable="0",
            alarm_tz_rc=0,
            alarm_tz_err="java.lang.SecurityException: neither user 2000 has SET_TIME_ZONE",
            alarm_time_rc=0,
            alarm_time_err="java.lang.SecurityException: neither user 2000 has SET_TIME",
            root_rc=1,
            root_uid_after=2000,
        )
        monkeypatch.setenv("STP_DEVICE_SERIAL", "SER-LOCKED")
        monkeypatch.setenv("STP_STEP_PARAMS", json.dumps({"timezone": "Asia/Shanghai"}))

        sdt.main()
        payload = json.loads(capsys.readouterr().out.strip())

        assert payload["success"] is False
        assert "verification failed" in payload["error_message"]
        assert "SecurityException" in payload["error_message"]
        assert "adb root unavailable" in payload["error_message"]
        assert payload["metrics"]["method"] == "failed"

    def test_fails_when_readback_drift_exceeds_threshold(self, monkeypatch, capsys):
        _wire_device(monkeypatch, force_verify_drift=42)
        monkeypatch.setenv("STP_DEVICE_SERIAL", "SER-DRIFT")
        monkeypatch.setenv(
            "STP_STEP_PARAMS",
            json.dumps({
                "timezone": "Asia/Shanghai",
                "max_drift_seconds": 5,
                "allow_root_fallback": False,
            }),
        )

        sdt.main()
        payload = json.loads(capsys.readouterr().out.strip())

        assert payload["success"] is False
        assert "drift=42s" in payload["error_message"]
        assert payload["metrics"]["drift_after_seconds"] == 42

    def test_rejects_missing_serial(self, monkeypatch, capsys):
        monkeypatch.delenv("STP_DEVICE_SERIAL", raising=False)
        sdt.main()
        payload = json.loads(capsys.readouterr().out.strip())
        assert payload["success"] is False
        assert "STP_DEVICE_SERIAL" in payload["error_message"]

    def test_rejects_shell_injection_in_timezone(self, monkeypatch, capsys):
        called = []
        monkeypatch.setattr(sdt, "subprocess_run", lambda *a, **kw: called.append(a))
        monkeypatch.setenv("STP_DEVICE_SERIAL", "SER-INJ")
        monkeypatch.setenv("STP_STEP_PARAMS", json.dumps({"timezone": "UTC; rm -rf /"}))

        sdt.main()
        payload = json.loads(capsys.readouterr().out.strip())

        assert payload["success"] is False
        assert "invalid timezone" in payload["error_message"]
        assert called == []

    def test_rejects_invalid_max_drift_param(self, monkeypatch, capsys):
        monkeypatch.setenv("STP_DEVICE_SERIAL", "SER-BADPARAM")
        monkeypatch.setenv("STP_STEP_PARAMS", json.dumps({"max_drift_seconds": 0}))

        sdt.main()
        payload = json.loads(capsys.readouterr().out.strip())
        assert payload["success"] is False
        assert "max_drift_seconds" in payload["error_message"]

    def test_wait_adbd_ready_respects_poll_bound(self, monkeypatch):
        # monotonic 每调 +0.1，保证 15 轮内墙钟截止不会先触发——本用例钉的是
        # 迭代上界（_ROOT_WAIT_MAX_POLLS），不是 deadline。
        ticks = [0.0]

        def advancing_mono():
            ticks[0] += 0.1
            return ticks[0]

        polls = [0]

        def fake_run(_argv, **_kwargs):
            polls[0] += 1
            return _proc(1, "", "offline")

        _patch_advancing_clock(monkeypatch, sdt)
        monkeypatch.setattr(sdt.time, "monotonic", advancing_mono)
        monkeypatch.setattr(sdt, "subprocess_run", fake_run)

        assert sdt._wait_adbd_ready("SER-OFF", "adb", timeout=5) is False
        assert polls[0] == sdt._ROOT_WAIT_MAX_POLLS
