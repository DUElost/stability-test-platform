"""Unit tests for backend/agent/scripts/unisoc_sysdump_config/unisoc_sysdump_config.py."""

from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "unisoc_sysdump_config"
    / "unisoc_sysdump_config.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location("unisoc_sysdump_config_mod", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _patch_advancing_clock(monkeypatch, mod, start: float = 100.0):
    now = {"t": start}

    def fake_monotonic() -> float:
        return now["t"]

    def fake_sleep(sec: float) -> None:
        now["t"] += max(0.0, float(sec))

    monkeypatch.setattr(mod.time, "monotonic", fake_monotonic)
    monkeypatch.setattr(mod.time, "sleep", fake_sleep)
    return now


def _completed(stdout: str = "", stderr: str = "", rc: int = 0):
    return subprocess.CompletedProcess(args=["adb"], returncode=rc, stdout=stdout, stderr=stderr)


def _run_main(monkeypatch, capsys, *, serial: str = "DEV001", params: dict | None = None):
    mod = _load_module()
    monkeypatch.setenv("STP_DEVICE_SERIAL", serial)
    monkeypatch.setenv("STP_ADB_PATH", "adb")
    if params is None:
        monkeypatch.delenv("STP_STEP_PARAMS", raising=False)
    else:
        monkeypatch.setenv("STP_STEP_PARAMS", json.dumps(params))
    return mod, capsys


def test_missing_serial_fails(monkeypatch, capsys):
    mod = _load_module()
    monkeypatch.delenv("STP_DEVICE_SERIAL", raising=False)
    with pytest.raises(SystemExit) as exc:
        mod.main()
    assert exc.value.code == 1
    out = json.loads(capsys.readouterr().out)
    assert out["success"] is False
    assert "STP_DEVICE_SERIAL" in out["error_message"]


def test_non_unisoc_mtk_device_skips_without_root(monkeypatch, capsys):
    mod, _ = _run_main(monkeypatch, capsys)
    _patch_advancing_clock(monkeypatch, mod)
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return _completed(
            "board_platform=mt6789\n"
            "soc_manufacturer=Mediatek\n"
            "hardware=mt6789\n"
            "boot_hardware=mt6789\n"
            "uid=2000\n"
            "proc_exists=0\n"
        )

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    mod.main()
    out = json.loads(capsys.readouterr().out)
    assert out["success"] is True
    assert out["skipped"] is True
    assert out["skip_reason"] == "non_unisoc_platform"
    assert len(calls) == 1
    assert "root" not in calls[0]


def test_unisoc_enables_sysdump_with_root_and_readback(monkeypatch, capsys):
    mod, _ = _run_main(monkeypatch, capsys, params={"enabled": True})
    _patch_advancing_clock(monkeypatch, mod)
    seq = iter(
        [
            # 1. probe platform
            _completed(
                "board_platform=ums9230\n"
                "soc_manufacturer=Spreadtrum\n"
                "hardware=P525AA\n"
                "boot_hardware=P525AA\n"
                "uid=2000\n"
                "proc_exists=1\n"
            ),
            # 2. _ensure_root: id -u -> 2000
            _completed("2000\n"),
            # 3. _ensure_root: adb root
            _completed("restarting adbd as root\n"),
            # 4. _ensure_root: get-state
            _completed("device\n"),
            # 5. _ensure_root: id -u -> 0
            _completed("0\n"),
            # 6. before state: off / 0
            _completed("persist=off\ntrigger=\nproc_exists=1\nproc_raw=sysdump_status = 0\n"),
            # 7. setprop vendor.debug.sysdump.enabled true
            _completed(""),
            # 8. first poll still transitioning
            _completed("persist=off\ntrigger=true\nproc_exists=1\nproc_raw=sysdump_status = 0\n"),
            # 9. second poll converged
            _completed("persist=on\ntrigger=true\nproc_exists=1\nproc_raw=sysdump_status = 1\n"),
        ]
    )
    seen: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        seen.append(cmd)
        return next(seq)

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    mod.main()
    captured = capsys.readouterr()
    out = json.loads(captured.out)
    assert out["success"] is True
    assert out["skipped"] is False
    assert out["metrics"]["before"]["sysdump_status"] == "0"
    assert out["metrics"]["after"]["persist"] == "on"
    assert out["metrics"]["after"]["sysdump_status"] == "1"
    assert any("setprop vendor.debug.sysdump.enabled true" in " ".join(c) for c in seen)
    assert "PROGRESS " in captured.err


def test_unisoc_already_enabled_skips_idempotently(monkeypatch, capsys):
    mod, _ = _run_main(monkeypatch, capsys, params={"enabled": True})
    _patch_advancing_clock(monkeypatch, mod)
    seq = iter(
        [
            _completed(
                "board_platform=ums9230\n"
                "soc_manufacturer=UNISOC\n"
                "hardware=P525AAN\n"
                "boot_hardware=P525AAN\n"
                "uid=0\n"
                "proc_exists=1\n"
            ),
            _completed("0\n"),
            _completed("persist=on\ntrigger=true\nproc_exists=1\nproc_raw=sysdump_status = 1\n"),
        ]
    )
    monkeypatch.setattr(mod.subprocess, "run", lambda cmd, **kw: next(seq))
    mod.main()
    out = json.loads(capsys.readouterr().out)
    assert out["success"] is True
    assert out["skipped"] is True
    assert out["skip_reason"] == "already_configured"


def test_unisoc_disable_sysdump_succeeds(monkeypatch, capsys):
    mod, _ = _run_main(monkeypatch, capsys, params={"enabled": False})
    _patch_advancing_clock(monkeypatch, mod)
    seq = iter(
        [
            _completed(
                "board_platform=ums9230\n"
                "soc_manufacturer=Spreadtrum\n"
                "hardware=P525AA\n"
                "boot_hardware=P525AA\n"
                "uid=0\n"
                "proc_exists=1\n"
            ),
            _completed("0\n"),
            _completed("persist=on\ntrigger=true\nproc_exists=1\nproc_raw=sysdump_status = 1\n"),
            _completed(""),
            _completed("persist=off\ntrigger=false\nproc_exists=1\nproc_raw=sysdump_status = 0\n"),
        ]
    )
    seen: list[list[str]] = []

    def fake_run(cmd, **kw):
        seen.append(cmd)
        return next(seq)

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    mod.main()
    out = json.loads(capsys.readouterr().out)
    assert out["success"] is True
    assert out["skipped"] is False
    assert out["metrics"]["after"]["persist"] == "off"
    assert out["metrics"]["after"]["sysdump_status"] == "0"
    assert any("setprop vendor.debug.sysdump.enabled false" in " ".join(c) for c in seen)


def test_unisoc_missing_proc_node_skips_by_default_and_fails_when_strict(monkeypatch, capsys):
    for strict in (False, True):
        mod, _ = _run_main(
            monkeypatch, capsys, params={"enabled": True, "strict_unsupported": strict}
        )
        _patch_advancing_clock(monkeypatch, mod)
        seq = iter(
            [
                _completed(
                    "board_platform=ums9230\n"
                    "soc_manufacturer=Spreadtrum\n"
                    "hardware=P525AA\n"
                    "boot_hardware=P525AA\n"
                    "uid=0\n"
                    "proc_exists=0\n"
                ),
                _completed("0\n"),
                _completed("persist=\ntrigger=\nproc_exists=0\nproc_raw=\n"),
            ]
        )
        monkeypatch.setattr(mod.subprocess, "run", lambda cmd, _seq=seq, **kw: next(_seq))
        if strict:
            with pytest.raises(SystemExit) as exc:
                mod.main()
            assert exc.value.code == 1
            out = json.loads(capsys.readouterr().out)
            assert out["success"] is False
            assert "/proc/sprd_sysdump" in out["error_message"]
        else:
            mod.main()
            out = json.loads(capsys.readouterr().out)
            assert out["success"] is True
            assert out["skipped"] is True
            assert out["skip_reason"] == "sysdump_node_absent"


def test_unisoc_verify_timeout_fails_with_evidence(monkeypatch, capsys):
    mod, _ = _run_main(
        monkeypatch, capsys, params={"enabled": True, "verify_timeout_seconds": 1.0}
    )
    _patch_advancing_clock(monkeypatch, mod)
    call_idx = {"n": 0}

    def fake_run(cmd, **kw):
        call_idx["n"] += 1
        if call_idx["n"] == 1:
            return _completed(
                "board_platform=ums9230\nsoc_manufacturer=Spreadtrum\n"
                "hardware=P525AA\nboot_hardware=P525AA\nuid=0\nproc_exists=1\n"
            )
        if call_idx["n"] == 2:
            return _completed("0\n")
        if call_idx["n"] == 3:
            return _completed("persist=off\ntrigger=\nproc_exists=1\nproc_raw=sysdump_status = 0\n")
        if call_idx["n"] == 4:
            return _completed("")
        return _completed("persist=off\ntrigger=true\nproc_exists=1\nproc_raw=sysdump_status = 0\n")

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    with pytest.raises(SystemExit) as exc:
        mod.main()
    assert exc.value.code == 1
    out = json.loads(capsys.readouterr().out)
    assert out["success"] is False
    assert "verification mismatch" in out["error_message"]
    assert "sysdump_status='0'" in out["error_message"]


def test_unisoc_root_denied_fails_closed(monkeypatch, capsys):
    mod, _ = _run_main(
        monkeypatch, capsys, params={"enabled": True, "root_timeout_seconds": 2.0}
    )
    _patch_advancing_clock(monkeypatch, mod)
    seq = iter(
        [
            _completed(
                "board_platform=ums9230\nsoc_manufacturer=Spreadtrum\n"
                "hardware=P525AA\nboot_hardware=P525AA\nuid=2000\nproc_exists=1\n"
            ),
            _completed("2000\n"),
            _completed("", "adbd cannot run as root in production builds", rc=1),
        ]
    )
    monkeypatch.setattr(mod.subprocess, "run", lambda cmd, **kw: next(seq))
    with pytest.raises(SystemExit) as exc:
        mod.main()
    assert exc.value.code == 1
    out = json.loads(capsys.readouterr().out)
    assert out["success"] is False
    assert "requires adb root" in out["error_message"]
