"""flash_preflight_unisoc：展锐刷机环境校验步的判据回归（不落设备，纯 fake）。"""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path

import pytest

_SCRIPT = (
    Path(__file__).resolve().parent.parent
    / "scripts" / "flash_preflight_unisoc" / "flash_preflight_unisoc.py"
)


@pytest.fixture(scope="module")
def mod():
    spec = importlib.util.spec_from_file_location("fpunisoc_under_test", _SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeProc:
    def __init__(self, stdout="", rc=0):
        self.stdout, self.returncode, self.stderr = stdout, rc, ""


def test_fastboot_honours_explicit_env(mod, tmp_path, monkeypatch):
    fb = tmp_path / "fb"
    fb.write_text("#!/bin/sh\nexit 0\n")
    fb.chmod(0o755)
    monkeypatch.setenv("STP_FASTBOOT_PATH", str(fb))
    assert mod.check_fastboot() == str(fb)


def test_fastboot_missing_raises(mod, monkeypatch):
    monkeypatch.delenv("STP_FASTBOOT_PATH", raising=False)
    monkeypatch.setattr(mod.shutil, "which", lambda x: None)
    monkeypatch.setattr(mod.os.path, "isfile", lambda x: False)
    with pytest.raises(RuntimeError, match="fastboot not found"):
        mod.check_fastboot()


def test_firmware_manifest_models_allow_and_deny(mod, tmp_path, monkeypatch):
    root = tmp_path / "nfs"
    fam = root / "firmware" / "A57" / "GEN_XV1.0.0B19"
    fam.mkdir(parents=True)
    (root / "firmware" / "A57" / "latest.json").write_text(
        json.dumps({"versions": {"Z2581": "GEN_XV1.0.0B19"}}))
    (fam / "manifest.json").write_text(json.dumps({"version": "GEN_XV1.0.0B19",
                                                   "models": ["Z2581", "P615F03"]}))
    monkeypatch.setenv("STP_NFS_ROOT", str(root))
    ok = mod.check_firmware("A57", {"model": "Z2581", "device": "P615F03"}, "")
    assert ok["version"] == "GEN_XV1.0.0B19"
    # pointer miss is itself fail-closed ...
    with pytest.raises(RuntimeError, match="no target version"):
        mod.check_firmware("A57", {"model": "P615F04", "device": "A77"}, "")
    # ... and with an explicit version the manifest models gate still denies
    with pytest.raises(RuntimeError, match="not allowed by manifest"):
        mod.check_firmware("A57", {"model": "P615F04", "device": "A77"}, "GEN_XV1.0.0B19")


def test_battery_gate(mod, monkeypatch):
    monkeypatch.setattr(mod, "_run", lambda argv, timeout=30: FakeProc("  level: 3\n"))
    with pytest.raises(RuntimeError, match="battery 3%"):
        mod.check_battery("S", 5)
    monkeypatch.setattr(mod, "_run", lambda argv, timeout=30: FakeProc("  level: 88\n"))
    assert mod.check_battery("S", 5) == 88
    monkeypatch.setattr(mod, "_run", lambda argv, timeout=30: FakeProc("no data"))
    with pytest.raises(RuntimeError, match="unreadable"):
        mod.check_battery("S", 5)


def test_host_lock_state_busy_detection(mod, tmp_path, monkeypatch):
    lock = tmp_path / "stp-flash-firmware.lock"
    monkeypatch.setattr(mod, "_HOST_LOCK_PATH", str(lock))
    assert mod.host_lock_state() is False
    fd = os.open(str(lock), os.O_WRONLY | os.O_CREAT, 0o600)
    import fcntl
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        assert mod.host_lock_state() is True
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
