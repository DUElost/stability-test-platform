"""Unit tests for backend/agent/scripts/log_prepare/log_prepare.py (v1.0.0, #4182 Phase 1)."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "log_prepare"
    / "log_prepare.py"
)


def _load_mod():
    spec = importlib.util.spec_from_file_location("log_prepare_v100", SCRIPT_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _patch_advancing_clock(monkeypatch, mod) -> None:
    """Satisfy #3202 clock-stub guard: advance monotonic clock whenever sleep is called."""
    now = [1000.0]

    def _monotonic() -> float:
        return now[0]

    def _sleep(seconds: float) -> None:
        now[0] += max(float(seconds), 0.1)

    monkeypatch.setattr(mod.time, "monotonic", _monotonic)
    monkeypatch.setattr(mod.time, "sleep", _sleep)


def _run_main(mod, monkeypatch, capsys, params: dict | None = None):
    monkeypatch.setenv("STP_DEVICE_SERIAL", "TESTSERIAL0001")
    monkeypatch.setenv("STP_ADB_PATH", "adb")
    if params is not None:
        monkeypatch.setenv("STP_STEP_PARAMS", json.dumps(params))
    else:
        monkeypatch.delenv("STP_STEP_PARAMS", raising=False)
    rc = mod.main()
    captured = capsys.readouterr()
    lines = [ln for ln in captured.out.splitlines() if ln.strip()]
    assert lines, f"no stdout JSON emitted; stderr={captured.err!r}"
    return rc, json.loads(lines[-1]), captured.err


def _mtk_probe_stdout(uid: str = "2000", has_loggerui: str = "1", build_type: str = "user") -> str:
    return "\n".join([
        "board_platform=mt6835",
        "soc_manufacturer=Mediatek",
        "hardware=mt6835",
        "boot_hardware=mt6835",
        f"build_type={build_type}",
        f"uid={uid}",
        "has_ylogctl=0",
        f"has_loggerui={has_loggerui}",
    ])


def _unisoc_probe_stdout(uid: str = "0", has_ylogctl: str = "1", build_type: str = "userdebug") -> str:
    return "\n".join([
        "board_platform=ums9230",
        "soc_manufacturer=Unisoc",
        "hardware=P525AAN",
        "boot_hardware=P525AAN",
        f"build_type={build_type}",
        f"uid={uid}",
        f"has_ylogctl={has_ylogctl}",
        "has_loggerui=0",
    ])


def _ylog_query_output(*, status: str = "enable", android: str = "Open", kernel: str = "Open", size: int = 123456) -> str:
    return "\n".join([
        f"status = {status} ",
        f"file = {'/data/ylog/ap/001.ylog' if status == 'enable' else 'NULL'} ",
        f"size = {size if status == 'enable' else -1} ",
        "pid = 614 ",
        "[   632]  lastlog      -> Open    -> lastlog.log      [     42]->[      0]",
        f"[   638]  android      -> {android:<7} -> android.log      [  6605A]->[   64C8]",
        f"[   640]  kernel       -> {kernel:<7} -> kernel.log       [  60D71]->[   6D4C]",
        "[   645]  sysinfo      -> Open    -> sysinfo.log      [ 1D1D5E]->[   6C7C]",
    ])


def test_mtk_default_preserves_aee_mode_and_verifies_mb_running(monkeypatch, capsys):
    mod = _load_mod()
    _patch_advancing_clock(monkeypatch, mod)
    calls: list[list[str]] = []

    def fake_run(cmd, **_kwargs):
        calls.append(cmd)
        joined = " ".join(cmd)
        if "has_ylogctl=" in joined:
            return SimpleNamespace(returncode=0, stdout=_mtk_probe_stdout(uid="2000"), stderr="")
        if "vendor.MB.running" in joined:
            return SimpleNamespace(
                returncode=0,
                stdout=(
                    "aee_mode=4\n"
                    "aeev_mode=4\n"
                    "mb_running=1\n"
                    "mb_realpath=/data/debuglogger/mobilelog/APLog_1/\n"
                    "mb_sublog=0x003F6D81\n"
                ),
                stderr="",
            )
        if "settings get global development_settings_enabled" in joined:
            return SimpleNamespace(returncode=0, stdout="0\n", stderr="")
        if "am broadcast" in joined:
            return SimpleNamespace(returncode=0, stdout="Broadcast completed: result=0\n", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    rc, res, err = _run_main(mod, monkeypatch, capsys, {"mobilelog_size_mb": 10240})

    assert rc == 0
    assert res["success"] is True
    assert res["skipped"] is False
    assert res["metrics"]["detected_platform"] == "mtk"
    assert res["metrics"]["target_aee_mode"] is None
    assert res["metrics"]["after"]["mb_running"] == "1"
    # Default aee_mode=None must NOT invoke adb root or setprop persist.vendor.mtk.aee.mode
    assert not any("root" in c for c in calls)
    assert not any("persist.vendor.mtk.aee.mode" in " ".join(c) and "setprop" in " ".join(c) for c in calls)
    # Dev settings turned on then restored to original "0"
    assert any("development_settings_enabled 1" in " ".join(c) for c in calls)
    assert any("development_settings_enabled 0" in " ".join(c) for c in calls)
    # GPU 10240M broadcast sent
    assert any("set_total_log_size_10240" in " ".join(c) for c in calls)
    assert "PROGRESS " in err


def test_mtk_explicit_aee_mode_elevates_and_restores_dev_settings_on_failure(monkeypatch, capsys):
    mod = _load_mod()
    _patch_advancing_clock(monkeypatch, mod)
    calls: list[list[str]] = []

    def fake_run(cmd, **_kwargs):
        calls.append(cmd)
        joined = " ".join(cmd)
        if "has_ylogctl=" in joined:
            return SimpleNamespace(returncode=0, stdout=_mtk_probe_stdout(uid="2000"), stderr="")
        if "vendor.MB.running" in joined:
            return SimpleNamespace(
                returncode=0,
                stdout="aee_mode=4\naeev_mode=4\nmb_running=0\nmb_realpath=\nmb_sublog=\n",
                stderr="",
            )
        if "settings get global development_settings_enabled" in joined:
            return SimpleNamespace(returncode=0, stdout="0\n", stderr="")
        if cmd[-1] == "root":
            return SimpleNamespace(returncode=0, stdout="restarting adbd as root", stderr="")
        if cmd[-1] == "get-state":
            return SimpleNamespace(returncode=0, stdout="device", stderr="")
        if cmd[-1] == "id -u":
            return SimpleNamespace(returncode=0, stdout="0", stderr="")
        if "setprop persist.vendor.mtk.aee.mode 3" in joined:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if "getprop persist.vendor.mtk.aee.mode" in joined:
            return SimpleNamespace(returncode=0, stdout="4\n", stderr="")  # mismatch!
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    rc, res, _ = _run_main(mod, monkeypatch, capsys, {"aee_mode": "3"})

    assert rc == 0
    assert res["success"] is False
    assert "setprop persist.vendor.mtk.aee.mode=3 failed" in res["error_message"]
    # finally block must still restore development_settings_enabled to 0
    assert any("development_settings_enabled 0" in " ".join(c) for c in calls)


def test_mtk_fails_when_mb_running_never_converges(monkeypatch, capsys):
    mod = _load_mod()
    _patch_advancing_clock(monkeypatch, mod)

    def fake_run(cmd, **_kwargs):
        joined = " ".join(cmd)
        if "has_ylogctl=" in joined:
            return SimpleNamespace(returncode=0, stdout=_mtk_probe_stdout(uid="2000"), stderr="")
        if "vendor.MB.running" in joined:
            return SimpleNamespace(
                returncode=0,
                stdout="aee_mode=3\naeev_mode=3\nmb_running=0\nmb_realpath=\nmb_sublog=\n",
                stderr="",
            )
        if "settings get global development_settings_enabled" in joined:
            return SimpleNamespace(returncode=0, stdout="1\n", stderr="")
        if "am broadcast" in joined:
            return SimpleNamespace(returncode=0, stdout="Broadcast completed: result=0\n", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    rc, res, _ = _run_main(mod, monkeypatch, capsys, {"verify_timeout_seconds": 2})

    assert rc == 0
    assert res["success"] is False
    assert "vendor.MB.running='0'" in res["error_message"]


def test_unisoc_p525aa_happy_path_sets_cap_and_verifies_open_streams(monkeypatch, capsys):
    mod = _load_mod()
    _patch_advancing_clock(monkeypatch, mod)
    calls: list[str] = []

    def fake_run(cmd, **_kwargs):
        joined = " ".join(cmd)
        calls.append(joined)
        if "has_ylogctl=" in joined:
            return SimpleNamespace(returncode=0, stdout=_unisoc_probe_stdout(uid="0"), stderr="")
        if "ylogctl version" in joined:
            return SimpleNamespace(returncode=0, stdout=" 5.0.0\n", stderr="")
        if "ylogctl query" in joined:
            return SimpleNamespace(returncode=0, stdout=_ylog_query_output(), stderr="")
        if "ylogctl space" in joined:
            cap = 4096 if any("aplogmaxsize 4096" in c for c in calls) else 106018
            return SimpleNamespace(
                returncode=0,
                stdout=f"Root:/data/ylog/ap/   APLogSize:2203 APLogMaxSize:{cap} DiskFreeSpace:101820  DiskReserved:128",
                stderr="",
            )
        return SimpleNamespace(returncode=0, stdout="ok\n", stderr="")

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    rc, res, _ = _run_main(mod, monkeypatch, capsys, {"ylog_size_mb": 4096})

    assert rc == 0
    assert res["success"] is True
    assert res["skipped"] is False
    assert res["metrics"]["detected_platform"] == "unisoc"
    assert res["metrics"]["ylog_version"] == "5.0.0"
    assert res["metrics"]["substream_recovery_method"] == "none"
    assert res["metrics"]["after"]["aplog_max_size_mb"] == 4096
    assert res["metrics"]["after"]["streams"]["android"] == "Open"
    assert res["metrics"]["after"]["streams"]["kernel"] == "Open"
    assert not any("resetsettings" in c for c in calls)


def test_unisoc_a57_recovers_closed_android_kernel_via_resetsettings_and_reapplies_cap(monkeypatch, capsys):
    mod = _load_mod()
    _patch_advancing_clock(monkeypatch, mod)
    state = {"reset_done": False, "cap": 99999}

    def fake_run(cmd, **_kwargs):
        joined = " ".join(cmd)
        if "has_ylogctl=" in joined:
            return SimpleNamespace(returncode=0, stdout=_unisoc_probe_stdout(uid="0", build_type="userdebug"), stderr="")
        if "ylogctl version" in joined:
            return SimpleNamespace(returncode=0, stdout="5.0.0", stderr="")
        if "ylogctl resetsettings" in joined:
            state["reset_done"] = True
            state["cap"] = 99999  # resetsettings resets cap to 99%
            return SimpleNamespace(returncode=0, stdout="1", stderr="")
        if "ylogctl aplogmaxsize 4096" in joined:
            state["cap"] = 4096
            return SimpleNamespace(returncode=0, stdout="4096", stderr="")
        if "ylogctl query" in joined:
            if state["reset_done"]:
                return SimpleNamespace(returncode=0, stdout=_ylog_query_output(android="Open", kernel="Open"), stderr="")
            return SimpleNamespace(returncode=0, stdout=_ylog_query_output(android="Close", kernel="Close"), stderr="")
        if "ylogctl space" in joined:
            return SimpleNamespace(
                returncode=0,
                stdout=f"Root:/data/ylog/ap/   APLogSize:22 APLogMaxSize:{state['cap']} DiskFreeSpace:93492  DiskReserved:128",
                stderr="",
            )
        return SimpleNamespace(returncode=0, stdout="enable", stderr="")

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    rc, res, _ = _run_main(mod, monkeypatch, capsys, {"ylog_size_mb": 4096})

    assert rc == 0
    assert res["success"] is True
    assert res["metrics"]["substream_recovery_method"] == "resetsettings"
    assert res["metrics"]["after"]["aplog_max_size_mb"] == 4096
    assert res["metrics"]["after"]["streams"]["android"] == "Open"
    assert res["metrics"]["after"]["streams"]["kernel"] == "Open"


def test_unisoc_user_build_falls_back_to_ylog_conf_rewrite_or_fails_fast(monkeypatch, capsys):
    mod = _load_mod()
    _patch_advancing_clock(monkeypatch, mod)
    state = {"conf_rewritten": False}

    def fake_run(cmd, **_kwargs):
        joined = " ".join(cmd)
        if "has_ylogctl=" in joined:
            return SimpleNamespace(returncode=0, stdout=_unisoc_probe_stdout(uid="0", build_type="user"), stderr="")
        if "ylogctl version" in joined:
            return SimpleNamespace(returncode=0, stdout="5.0.0", stderr="")
        if "cat /data/ylog/ylog.conf" in joined:
            return SimpleNamespace(
                returncode=0,
                stdout="VERSION,1\nstatus,disable\naplogrotate,enable\naplogmaxsize,99%\nandroid,0\nkernel,0\n",
                stderr="",
            )
        if "stop ylog && start ylog" in joined:
            assert "android,1" in joined
            assert "kernel,1" in joined
            assert "aplogmaxsize,4096" in joined
            state["conf_rewritten"] = True
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if "ylogctl query" in joined:
            if state["conf_rewritten"]:
                return SimpleNamespace(returncode=0, stdout=_ylog_query_output(android="Open", kernel="Open"), stderr="")
            return SimpleNamespace(returncode=0, stdout=_ylog_query_output(android="Close", kernel="Close"), stderr="")
        if "ylogctl space" in joined:
            return SimpleNamespace(
                returncode=0,
                stdout="Root:/data/ylog/ap/   APLogSize:10 APLogMaxSize:4096 DiskFreeSpace:50000  DiskReserved:128",
                stderr="",
            )
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    rc, res, _ = _run_main(mod, monkeypatch, capsys, {"ylog_size_mb": 4096})

    assert rc == 0
    assert res["success"] is True
    assert res["metrics"]["substream_recovery_method"] == "ylog_conf_restart"


def test_unisoc_fails_fast_when_required_streams_stay_closed(monkeypatch, capsys):
    mod = _load_mod()
    _patch_advancing_clock(monkeypatch, mod)

    def fake_run(cmd, **_kwargs):
        joined = " ".join(cmd)
        if "has_ylogctl=" in joined:
            return SimpleNamespace(returncode=0, stdout=_unisoc_probe_stdout(uid="2000", build_type="user"), stderr="")
        if "ylogctl version" in joined:
            return SimpleNamespace(returncode=0, stdout="5.0.0", stderr="")
        if cmd[-1] == "root":
            return SimpleNamespace(returncode=1, stdout="", stderr="adbd cannot run as root in production builds")
        if "ylogctl query" in joined:
            return SimpleNamespace(returncode=0, stdout=_ylog_query_output(android="Close", kernel="Close"), stderr="")
        if "ylogctl space" in joined:
            return SimpleNamespace(
                returncode=0,
                stdout="Root:/data/ylog/ap/   APLogSize:0 APLogMaxSize:4096 DiskFreeSpace:50000  DiskReserved:128",
                stderr="",
            )
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    rc, res, _ = _run_main(mod, monkeypatch, capsys, {"ylog_size_mb": 4096, "verify_timeout_seconds": 1})

    assert rc == 0
    assert res["success"] is False
    assert "unopened_streams=['android', 'kernel']" in res["error_message"]


def test_unknown_platform_skips_by_default_and_fails_when_strict(monkeypatch, capsys):
    mod = _load_mod()
    _patch_advancing_clock(monkeypatch, mod)

    def fake_run(cmd, **_kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout="\n".join([
                "board_platform=kalama",
                "soc_manufacturer=Qualcomm",
                "hardware=qcom",
                "boot_hardware=qcom",
                "build_type=user",
                "uid=2000",
                "has_ylogctl=0",
                "has_loggerui=0",
            ]),
            stderr="",
        )

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    rc1, res1, _ = _run_main(mod, monkeypatch, capsys, {})
    assert rc1 == 0
    assert res1["success"] is True
    assert res1["skipped"] is True
    assert res1["skip_reason"] == "unsupported_platform"

    rc2, res2, _ = _run_main(mod, monkeypatch, capsys, {"strict_unsupported": True})
    assert rc2 == 0
    assert res2["success"] is False
    assert "unsupported platform" in res2["error_message"]
