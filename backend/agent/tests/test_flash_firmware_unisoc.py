"""Regression tests for backend/agent/scripts/flash_firmware_unisoc/.

Loaded via importlib because the script lives outside the backend.agent package.
No device is touched: adb/fastboot are replaced by fakes.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest


_SCRIPT = (
    Path(__file__).resolve().parent.parent
    / "scripts" / "flash_firmware_unisoc" / "flash_firmware_unisoc.py"
)


@pytest.fixture(scope="module")
def mod():
    spec = importlib.util.spec_from_file_location("flash_firmware_unisoc_under_test", _SCRIPT)
    assert spec and spec.loader, f"cannot locate {_SCRIPT}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeCmd:
    """Records argv and answers via a callable."""

    def __init__(self, answer=None):
        self.calls = []
        self.timeouts = []
        self._answer = answer or (lambda argv: "")

    def run(self, argv, timeout=60, check=False):
        self.calls.append(argv)
        self.timeouts.append(timeout)
        out = self._answer(argv) or ""
        return subprocess.CompletedProcess(args=argv, returncode=0, stdout=out, stderr="")


class FakeFlakyCmd(FakeCmd):
    """Raises RuntimeError on the first ``fail_first`` run() calls.

    Mirrors ``Cmd.run`` translating subprocess.TimeoutExpired into
    RuntimeError("timeout after Ns: ...") -- the 1.0.3 failure mode on
    slow-enumeration hosts (first getvar answer 11-22s on 172.21.15.75).
    """

    def __init__(self, answer=None, fail_first=1):
        super().__init__(answer)
        self._fail_first = fail_first

    def run(self, argv, timeout=60, check=False):
        if self._fail_first > 0:
            self._fail_first -= 1
            self.calls.append(argv)
            self.timeouts.append(timeout)
            raise RuntimeError(f"timeout after {timeout}s: fastboot -s SERIAL {' '.join(argv)}")
        return super().run(argv, timeout=timeout, check=check)


def _patch_advancing_clock(monkeypatch, mod, *, start: float = 1_000_000.0):
    """Replace mod.time.sleep/time so sleep advances a fake clock (no wall wait).

    同构自 `test_clear_recents_v106.py`（#3202 守卫：sleep 换 no-op 却不推进时钟
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


def _write(path: Path, content: bytes = b"x"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


# ---------------------------------------------------------------------------
# version helpers
# ---------------------------------------------------------------------------

def test_version_key_reads_trailing_build_number(mod):
    assert mod.version_key("GEN_NEEA_P615F03V1.0.0B17") == (1, 0, 0, 17)
    assert mod.version_key("GEN_NEEA_P615F03V1.0.0B19") == (1, 0, 0, 19)
    assert mod.version_key("GEN_NEEA_P615F03V1.0.0B19") > mod.version_key("GEN_NEEA_P615F03V1.0.0B17")
    assert mod.version_key("") == ()


def test_read_latest_version_accepts_model_variants_and_top_level(mod):
    assert mod._read_latest_version({"versions": {"Z2581": "V1"}}, "Z2581") == "V1"
    assert mod._read_latest_version({"versions": {"Z2581_GEN": "V2"}}, "Z2581-GEN") == "V2"
    assert mod._read_latest_version({"versions": {"Z2581-GEN": "V3"}}, "Z2581_GEN") == "V3"
    assert mod._read_latest_version({"version": "V4"}, "Z2581") == "V4"
    # per-model map is authoritative: a miss must not silently fall back
    assert mod._read_latest_version({"versions": {"OTHER": "V5"}}, "Z2581") == ""


# ---------------------------------------------------------------------------
# routing / manifest validation
# ---------------------------------------------------------------------------

def test_resolve_explicit_dir_rejects_manifest_version_mismatch(mod, tmp_path, monkeypatch):
    version_dir = tmp_path / "A57" / "B19"
    (version_dir).mkdir(parents=True)
    (version_dir / "manifest.json").write_text(json.dumps({"version": "B17"}), encoding="utf-8")
    monkeypatch.setenv("STP_NFS_ROOT", str(tmp_path))
    with pytest.raises(RuntimeError, match="manifest version"):
        mod.resolve_firmware({"firmware_dir": "A57/B19"}, "", mod.Progress(), [])


def test_resolve_by_pointer_rejects_model_outside_manifest(mod, tmp_path, monkeypatch):
    family = tmp_path / "firmware" / "A57"
    version_dir = family / "B19"
    version_dir.mkdir(parents=True)
    (family / "latest.json").write_text(
        json.dumps({"versions": {"Z2581": "B19"}}), encoding="utf-8")
    (version_dir / "manifest.json").write_text(
        json.dumps({"version": "B19", "models": ["OTHER"]}), encoding="utf-8")
    monkeypatch.setenv("STP_NFS_ROOT", str(tmp_path))
    monkeypatch.setattr(mod, "getprop", lambda cmd, name: "Z2581")
    with pytest.raises(RuntimeError, match="not in manifest models"):
        mod.resolve_firmware({}, "SERIAL", mod.Progress(), [])


# ---------------------------------------------------------------------------
# fastboot preflight (v1.0.1: fail before rebooting into bootloader)
# ---------------------------------------------------------------------------

def test_resolve_fastboot_honours_explicit_env(mod, tmp_path, monkeypatch):
    exe = tmp_path / "fastboot"
    exe.write_text("#!/bin/sh\n", encoding="utf-8")
    exe.chmod(0o755)
    monkeypatch.setenv("STP_FASTBOOT_PATH", str(exe))
    assert mod.resolve_fastboot() == str(exe)


def test_resolve_fastboot_rejects_missing_tool(mod, monkeypatch):
    monkeypatch.setenv("STP_FASTBOOT_PATH", "/nonexistent/fastboot")
    with pytest.raises(RuntimeError, match="not executable"):
        mod.resolve_fastboot()

    monkeypatch.delenv("STP_FASTBOOT_PATH", raising=False)
    monkeypatch.setattr(mod.shutil, "which", lambda name: None)
    monkeypatch.setattr(mod, "_FASTBOOT_CANDIDATES", ())
    with pytest.raises(RuntimeError, match="fastboot not found"):
        mod.resolve_fastboot()


# ---------------------------------------------------------------------------
# flash ordering / preserve list
# ---------------------------------------------------------------------------

def test_flash_partitions_orders_super_last_and_skips_preserved(mod, tmp_path, monkeypatch):
    fw = tmp_path / "fw"
    for name in ("boot.img", "sml-sign.bin", "super.img", "nv.bin"):
        _write(fw / "images" / name)
    entries = [
        {"id": "Super", "file": "super.img", "partitions": ["super"]},
        {"id": "BOOT", "file": "boot.img", "partitions": ["boot_a"]},
        {"id": "SML", "file": "sml-sign.bin", "partitions": ["sml_a"]},
        {"id": "NV_LTE", "file": "nv.bin", "partitions": ["l_fixnv1_a"], "preserve": True},
    ]
    flashed: list = []
    monkeypatch.setattr(mod, "_flash_stream",
                        lambda part, path, serial, timeout, progress, idx, total, log:
                        flashed.append(part))

    mod.flash_partitions(str(fw), entries, "SERIAL", {}, mod.Progress(), [])

    assert flashed == ["sml_a", "boot_a", "super"]
    assert "l_fixnv1_a" not in flashed


def test_flash_partitions_respects_flash_bootloader_false(mod, tmp_path, monkeypatch):
    fw = tmp_path / "fw"
    for name in ("boot.img", "lk-sign.bin"):
        _write(fw / "images" / name)
    entries = [
        {"id": "UBOOTLoader", "file": "lk-sign.bin", "partitions": ["uboot_a"]},
        {"id": "BOOT", "file": "boot.img", "partitions": ["boot_a"]},
    ]
    flashed: list = []
    monkeypatch.setattr(mod, "_flash_stream",
                        lambda part, path, serial, timeout, progress, idx, total, log:
                        flashed.append(part))

    mod.flash_partitions(str(fw), entries, "SERIAL", {"flash_bootloader": False},
                         mod.Progress(), [])
    assert flashed == ["boot_a"]


# ---------------------------------------------------------------------------
# recovery mode: device parked in fastboot
# ---------------------------------------------------------------------------

def test_probe_device_prefers_adb(mod, monkeypatch):
    adb_fake = FakeCmd(lambda argv: "device")
    fb_fake = FakeCmd(lambda argv: "current-slot: a")
    monkeypatch.setattr(mod, "adb", lambda serial, progress, log: adb_fake)
    monkeypatch.setattr(mod, "fastboot", lambda serial, progress, log: fb_fake)

    mode, dev, fb = mod.probe_device("SERIAL", {"adb_probe_seconds": 1}, mod.Progress(), [])
    assert mode == "adb" and dev is adb_fake and fb is None


def test_probe_device_falls_back_to_fastboot(mod, monkeypatch):
    adb_fake = FakeCmd(lambda argv: "")          # no adb device
    fb_fake = FakeCmd(lambda argv: "current-slot: a")
    monkeypatch.setattr(mod, "adb", lambda serial, progress, log: adb_fake)
    monkeypatch.setattr(mod, "fastboot", lambda serial, progress, log: fb_fake)
    _patch_advancing_clock(monkeypatch, mod)

    mode, dev, fb = mod.probe_device("SERIAL", {"adb_probe_seconds": 1}, mod.Progress(), [])
    assert mode == "fastboot" and dev is None and fb is fb_fake


# ---------------------------------------------------------------------------
# slow fastboot enumeration (1.0.4: first getvar answer can take 11-22s; the
# 10s budget used to raise through the wait loops and strand the device in
# fastboot -- runs 1043/1044/1045 on 172.21.15.75).
# ---------------------------------------------------------------------------

def test_probe_device_survives_slow_getvar(mod, monkeypatch):
    adb_fake = FakeCmd(lambda argv: "")
    fb_fake = FakeFlakyCmd(lambda argv: "current-slot: a", fail_first=1)
    monkeypatch.setattr(mod, "adb", lambda serial, progress, log: adb_fake)
    monkeypatch.setattr(mod, "fastboot", lambda serial, progress, log: fb_fake)
    _patch_advancing_clock(monkeypatch, mod)

    mode, dev, fb = mod.probe_device("SERIAL", {"adb_probe_seconds": 1}, mod.Progress(), [])
    assert mode == "fastboot" and fb is fb_fake
    getvars = [t for c, t in zip(fb_fake.calls, fb_fake.timeouts, strict=True) if c[:1] == ["getvar"]]
    assert len(getvars) == 2, "one slow probe must be retried, not fatal"
    assert all(t > 10 for t in getvars), "readiness probe must budget above slow-host latency"


def test_wait_fastboot_survives_getvar_timeout(mod, monkeypatch):
    fb_fake = FakeFlakyCmd(lambda argv: "current-slot: a", fail_first=2)
    monkeypatch.setattr(mod, "fastboot", lambda serial, progress, log: fb_fake)
    _patch_advancing_clock(monkeypatch, mod)

    fb = mod.wait_fastboot("SERIAL", mod.Progress(), [], 90)
    assert fb is fb_fake
    assert sum(1 for c in fb_fake.calls if c[:1] == ["getvar"]) == 3


def test_boot_slot_probe_budget_above_slow_latency(mod, monkeypatch):
    fb_fake = FakeCmd(lambda argv: "current-slot: a")
    monkeypatch.setattr(mod, "fastboot", lambda serial, progress, log: fb_fake)

    assert mod.boot_slot("SERIAL", mod.Progress(), []) == "a"
    assert fb_fake.timeouts and all(t > 10 for t in fb_fake.timeouts)


# ---------------------------------------------------------------------------
# reboot handover (1.0.5: 'fastboot reboot' can time out on slow hosts AFTER
# every partition is written -- the teardown, not the flash, eats the budget;
# the 20s budget turned a complete flash into a retried whole re-flash).
# ---------------------------------------------------------------------------

def test_reboot_landed_when_device_left_bus(mod, monkeypatch):
    calls = []

    class GoneAfterReboot:
        def run(self, argv, timeout=60, check=False):
            calls.append((argv[0], timeout))
            if argv[0] == "reboot":
                raise RuntimeError("timeout after %ds: fastboot reboot" % timeout)
            return subprocess.CompletedProcess(args=argv, returncode=1,
                                               stdout="", stderr="no fastboot device")

    monkeypatch.setattr(mod, "fastboot", lambda serial, progress, log: GoneAfterReboot())
    mod.reboot_out_of_fastboot(GoneAfterReboot(), mod.Progress(), [])
    assert [c[0] for c in calls] == ["reboot", "getvar"]
    assert calls[0][1] >= 30 and calls[1][1] > 10


def test_reboot_retried_when_still_resident(mod, monkeypatch):
    calls = []

    class StillResident:
        def run(self, argv, timeout=60, check=False):
            calls.append((argv[0], timeout))
            if argv[0] == "reboot" and sum(1 for a, _ in calls if a == "reboot") == 1:
                raise RuntimeError("timeout after %ds: fastboot reboot" % timeout)
            return subprocess.CompletedProcess(args=argv, returncode=0,
                                               stdout="current-slot: a", stderr="")

    monkeypatch.setattr(mod, "fastboot", lambda serial, progress, log: StillResident())
    mod.reboot_out_of_fastboot(StillResident(), mod.Progress(), [])
    assert [c[0] for c in calls] == ["reboot", "getvar", "reboot"]


# ---------------------------------------------------------------------------
# host lock (1.0.6: same flock as MTK; serial within host, parallel across)
# ---------------------------------------------------------------------------

def test_acquire_host_lock_stamps_wait_until_free(mod, tmp_path, monkeypatch):
    lockfile = tmp_path / "stp-flash-firmware.lock"
    monkeypatch.setattr(mod, "_HOST_LOCK_PATH", str(lockfile))
    sleeps = []
    monkeypatch.setattr(mod.time, "sleep", lambda sec: sleeps.append(sec))

    acquired = []

    class FakeProgress:
        seq = 0
        def stamp(self, **f):
            self.seq += 1
            acquired.append(f.get("stage"))

    import os as _os
    blocker_fd = _os.open(str(lockfile), _os.O_WRONLY | _os.O_CREAT, 0o600)
    import fcntl as _fcntl
    _fcntl.flock(blocker_fd, _fcntl.LOCK_EX)

    def unlock_after_two_waits(_sec):
        nonlocal blocker_fd
        sleeps.append(_sec)
        if len(sleeps) >= 2 and blocker_fd != -1:
            _fcntl.flock(blocker_fd, _fcntl.LOCK_UN)
            _os.close(blocker_fd)
            blocker_fd = -1

    monkeypatch.setattr(mod.time, "sleep", unlock_after_two_waits)
    fd = mod.acquire_host_lock(FakeProgress())
    assert fd >= 0
    assert acquired[:2] == ["lock-wait", "lock-wait"]
    assert acquired[-1] == "lock-held"
    import fcntl, os as _os2
    fcntl.flock(fd, fcntl.LOCK_UN)
    _os2.close(fd)


# ---------------------------------------------------------------------------
# SPL decision (auto = compare, never silently rewrite boot0)
# ---------------------------------------------------------------------------

def test_spl_decision_auto_skips_when_hash_matches(mod, tmp_path, monkeypatch):
    fw = tmp_path / "fw"
    image = fw / "images" / "u-boot-spl-16k-emmc-sign.bin"
    _write(image, b"SPL-B19")
    entries = [{"id": "SPLLoaderEMMC", "file": image.name, "partitions": ["splloader"]}]
    digest = __import__("hashlib").sha256(b"SPL-B19").hexdigest()
    fake = FakeCmd(lambda argv: "sprdboot.flash=emmc" if argv[:2] == ["shell", "cat"]
                   else f"{digest}  -\n")
    monkeypatch.setattr(mod, "adb", lambda serial, progress, log: fake)

    decision = mod.spl_decision(str(fw), entries, "SERIAL", {}, mod.Progress(), [])
    assert decision["action"] == "skip"
    assert decision["storage"] == "emmc"


def test_spl_decision_auto_reports_differ(mod, tmp_path, monkeypatch):
    fw = tmp_path / "fw"
    image = fw / "images" / "u-boot-spl-16k-emmc-sign.bin"
    _write(image, b"SPL-B19")
    entries = [{"id": "SPLLoaderEMMC", "file": image.name, "partitions": ["splloader"]}]
    fake = FakeCmd(lambda argv: "sprdboot.flash=emmc" if argv[:2] == ["shell", "cat"]
                   else "deadbeef  -\n")
    monkeypatch.setattr(mod, "adb", lambda serial, progress, log: fake)

    decision = mod.spl_decision(str(fw), entries, "SERIAL", {}, mod.Progress(), [])
    assert decision["action"] == "differ"
    assert decision["device_sha256"] == "deadbeef"


# ---------------------------------------------------------------------------
# wipe
# ---------------------------------------------------------------------------

def test_wipe_erases_userdata_metadata_misc(mod, monkeypatch):
    fake = FakeCmd(lambda argv: "OKAY")
    monkeypatch.setattr(mod, "fastboot", lambda serial, progress, log: fake)

    mod.wipe("SERIAL", mod.Progress(), [], {})
    erased = [c[1] for c in fake.calls if c[0] == "erase"]
    assert erased == ["userdata", "metadata", "misc"]
