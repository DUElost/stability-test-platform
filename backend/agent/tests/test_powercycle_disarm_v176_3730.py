"""#3730 / #3723 §3.6.6 v1.7.6：1.0.6 顺序、读取分类与最小 prefs 回归。"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parents[3]
SCRIPTS = REPO / "backend" / "agent" / "scripts"


def _load():
    path = SCRIPTS / "powercycle_disarm" / "powercycle_disarm.py"
    lib_path = path.parent / "_lib.py"
    lib_spec = importlib.util.spec_from_file_location("pc_disarm_v176_lib", lib_path)
    assert lib_spec and lib_spec.loader
    lib = importlib.util.module_from_spec(lib_spec)
    sys.modules["pc_disarm_v176_lib"] = lib
    lib_spec.loader.exec_module(lib)
    sys.modules["_lib"] = lib
    spec = importlib.util.spec_from_file_location("pc_disarm_v176", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["pc_disarm_v176"] = mod
    spec.loader.exec_module(mod)
    return mod, lib


PREFS_TRUE = (
    "<?xml version='1.0' encoding='utf-8' standalone='yes' ?>\n"
    "<map>\n"
    '    <boolean name="running" value="true" />\n'
    '    <boolean name="auto_resume" value="true" />\n'
    '    <int name="test_times" value="10" />\n'
    "</map>\n"
)


class FakeDevice:
    def __init__(
        self,
        *,
        installed: bool = True,
        prefs: str | None = PREFS_TRUE,
        prefs_state: str = "ok",
        service_running: bool = True,
        stop_rewrites: bool = True,
        uid: str = "2000",
        adb_cost: float = 0.0,
    ):
        self.installed = installed
        self.prefs = prefs
        self.prefs_state = prefs_state
        self.service_running = service_running
        self.stop_rewrites = stop_rewrites
        self.uid = uid
        self.adb_cost = adb_cost
        self.clock = 0.0
        self.events: list[dict] = []
        self.progress: list[tuple[float, dict]] = []

    def sleep(self, dt: float) -> None:
        self.clock += float(dt)

    def stamp(self, payload: dict) -> None:
        self.progress.append((self.clock, dict(payload)))

    def _event(self, kind: str, cmd: str = "") -> None:
        self.events.append({"kind": kind, "clock": self.clock, "cmd": cmd})

    def _read(self) -> tuple[int, str, str]:
        if not self.installed:
            return 1, "", "run-as: unknown package: com.tinno.autotesttool"
        if self.prefs_state == "denied":
            return 1, "", "cat: shared_prefs/powercycle_runner.xml: Permission denied"
        if self.prefs_state == "transient":
            return 1, "", ""
        if self.prefs is None:
            return 1, "", "cat: shared_prefs/powercycle_runner.xml: No such file or directory"
        return 0, self.prefs, ""

    def run(self, cmd, capture_output=True, timeout=60, input=None):
        self.clock += self.adb_cost
        args = list(cmd)
        if len(args) >= 4 and args[3] == "get-state":
            return SimpleNamespace(returncode=0, stdout=b"device\n", stderr=b"")
        # adb -s SERIAL shell <command>
        if len(args) >= 4 and args[3] == "push":
            self._event("prefs_write", " ".join(str(x) for x in args[3:]))
            local = args[4] if len(args) > 4 else ""
            if "powercycle_runner.xml" in str(args[-1]) and local:
                try:
                    self.prefs = Path(local).read_text(encoding="utf-8")
                    self.prefs_state = "ok"
                except OSError:
                    pass
            return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")
        if len(args) < 5 or args[3] != "shell":
            return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")
        shell_cmd = str(args[4])

        if shell_cmd.strip() == "id -u":
            return SimpleNamespace(returncode=0, stdout=self.uid.encode(), stderr=b"")
        if shell_cmd.strip() == "get-state":
            return SimpleNamespace(returncode=0, stdout=b"device\n", stderr=b"")
        if "sys.boot_completed" in shell_cmd:
            return SimpleNamespace(returncode=0, stdout=b"1\n", stderr=b"")
        if "ro.product.model" in shell_cmd:
            return SimpleNamespace(returncode=0, stdout=b"MLD-LX2\n", stderr=b"")
        if "ro.soc.manufacturer" in shell_cmd:
            return SimpleNamespace(returncode=0, stdout=b"mediatek\n", stderr=b"")
        if "ro.board.platform" in shell_cmd:
            return SimpleNamespace(returncode=0, stdout=b"mt6893\n", stderr=b"")

        if "pm path com.tinno.autotesttool" in shell_cmd:
            if self.installed:
                return SimpleNamespace(
                    returncode=0,
                    stdout=b"package:/data/app/fake/base.apk\n",
                    stderr=b"",
                )
            return SimpleNamespace(returncode=1, stdout=b"", stderr=b"")

        if "sha256sum" in shell_cmd:
            return SimpleNamespace(
                returncode=0,
                stdout=(b"a" * 64) + b"  /data/app/fake/base.apk\n",
                stderr=b"",
            )

        if "dumpsys package com.tinno.autotesttool" in shell_cmd:
            if self.installed:
                return SimpleNamespace(
                    returncode=0,
                    stdout=(
                        b"uid=10200 gids=[] type=0 prot=signature\n"
                        b"pkg=Package{fake com.tinno.autotesttool}\n"
                    ),
                    stderr=b"",
                )
            return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

        if "dumpsys activity services com.tinno.autotesttool" in shell_cmd:
            if self.service_running:
                return SimpleNamespace(
                    returncode=0,
                    stdout=(
                        b"ACTIVITY MANAGER SERVICES\n"
                        b"  * ServiceRecord{fake u0 com.tinno.autotesttool/"
                        b"com.mediatek.schpwronoff.powercycle.PowerCycleService}\n"
                    ),
                    stderr=b"",
                )
            return SimpleNamespace(
                returncode=0, stdout=b"ACTIVITY MANAGER SERVICES\n  (nothing)\n", stderr=b""
            )

        if "POWER_CYCLE_STOP" in shell_cmd:
            self._event("stop", shell_cmd)
            if self.service_running and self.stop_rewrites and self.prefs:
                self.prefs = self.prefs.replace(
                    'name="auto_resume" value="false"',
                    'name="auto_resume" value="true"',
                )
                self.prefs = self.prefs.replace(
                    'name="auto_resume" value="true" />',
                    'name="auto_resume" value="true" />',
                )
                if 'name="auto_resume"' not in self.prefs:
                    pass
            self.service_running = False
            return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

        if "am force-stop com.tinno.autotesttool" in shell_cmd:
            self._event("force_stop", shell_cmd)
            self.service_running = False
            return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

        if "run-as com.tinno.autotesttool mkdir -p shared_prefs" in shell_cmd:
            self._event("mkdir", shell_cmd)
            return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

        if "run-as com.tinno.autotesttool sh -c" in shell_cmd and "cat >" in shell_cmd:
            self._event("prefs_write", shell_cmd)
            if isinstance(input, bytes):
                self.prefs = input.decode("utf-8", "replace")
            else:
                self.prefs = str(input or "")
            self.prefs_state = "ok"
            return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

        if "run-as com.tinno.autotesttool cat" in shell_cmd:
            self._event("prefs_read", shell_cmd)
            rc, out, err = self._read()
            return SimpleNamespace(
                returncode=rc, stdout=out.encode(), stderr=err.encode()
            )

        if "if [ ! -f /data/data/com.tinno.autotesttool" in shell_cmd:
            self._event("prefs_read", shell_cmd)
            if self.prefs is None:
                return SimpleNamespace(
                    returncode=0, stdout=b"__STP_PREFS_ABSENT__\n", stderr=b""
                )
            if self.prefs_state == "denied":
                return SimpleNamespace(
                    returncode=0, stdout=b"", stderr=b"cat: Permission denied\n"
                )
            return SimpleNamespace(
                returncode=0, stdout=self.prefs.encode(), stderr=b""
            )

        if shell_cmd.startswith("cat /data/data/com.tinno.autotesttool"):
            self._event("prefs_read", shell_cmd)
            if self.prefs is None:
                return SimpleNamespace(
                    returncode=1,
                    stdout=b"",
                    stderr=b"cat: /data/data/com.tinno.autotesttool/shared_prefs/"
                    b"powercycle_runner.xml: No such file or directory\n",
                )
            if self.prefs_state == "denied":
                return SimpleNamespace(
                    returncode=1, stdout=b"", stderr=b"cat: Permission denied\n"
                )
            return SimpleNamespace(
                returncode=0, stdout=self.prefs.encode(), stderr=b""
            )

        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")


@pytest.fixture
def env(monkeypatch):
    mod, lib = _load()
    fake = FakeDevice()
    monkeypatch.setattr(lib.subprocess, "run", fake.run)
    monkeypatch.setattr(lib.time, "sleep", fake.sleep)
    monkeypatch.setattr(lib, "device_serial", lambda: "FAKE")
    monkeypatch.setattr(lib, "adb_path", lambda: "adb")
    monkeypatch.setattr(lib, "progress_stamp", fake.stamp)
    monkeypatch.setattr(mod, "progress_stamp", fake.stamp)
    monkeypatch.setattr(mod, "device_serial", lambda: "FAKE")
    monkeypatch.setattr(mod, "_wait_device_online", lambda seconds: True)
    monkeypatch.setattr(mod, "_detect_platform", lambda: "MTK")
    monkeypatch.setattr(mod, "_detect_model", lambda: "MLD-LX2")
    return mod, lib, fake


def _writes(fake: FakeDevice) -> list[str]:
    return [
        e["kind"]
        for e in fake.events
        if e["kind"] in ("stop", "force_stop", "prefs_write", "mkdir", "push")
    ]


def test_service_running_stop_rewrite_then_write_wins(env, monkeypatch):
    mod, lib, fake = env
    fake.prefs = PREFS_TRUE
    fake.service_running = True
    fake.stop_rewrites = True
    out = mod._run({"mode": "disarm", "wait_device_online_seconds": 30})
    assert out["disarm_result"] == "executed_unverified"
    assert out["readback"] == "clear"
    assert out["success"] is True
    assert 'name="running" value="false"' in fake.prefs
    assert 'name="auto_resume" value="false"' in fake.prefs
    kinds = [e["kind"] for e in fake.events]
    assert "stop" in kinds and "prefs_write" in kinds
    assert kinds.index("stop") < kinds.index("prefs_write")


def test_reversed_order_is_red(env, monkeypatch):
    mod, lib, fake = env
    fake.prefs = PREFS_TRUE
    fake.service_running = True
    fake.stop_rewrites = True

    def reversed_stop_task(force, kind="ok", installed=True):
        lib.set_stop_flags()
        lib._verify_stop_flags(kind)
        lib.stop_service(force)

    monkeypatch.setattr(mod, "stop_task", reversed_stop_task)
    out = mod._run({"mode": "disarm", "wait_device_online_seconds": 30})
    assert out["readback"] == "footprint"
    assert out["disarm_result"] == "failed"
    assert out["success"] is False


@pytest.mark.parametrize(
    "installed,prefs,prefs_state,expected_kind,expected_readback",
    [
        (False, None, "ok", "not_installed", "not_installed"),
        (True, None, "ok", "absent", "absent"),
        (True, PREFS_TRUE, "denied", "denied", "readback_failed"),
        (True, PREFS_TRUE, "transient", "transient", "readback_failed"),
    ],
)
def test_read_classification(
    env, installed, prefs, prefs_state, expected_kind, expected_readback
):
    mod, lib, fake = env
    fake.installed = installed
    fake.prefs = prefs
    fake.prefs_state = prefs_state
    fake.service_running = False
    kind, _xml, got_installed = lib.read_prefs_classified()
    assert kind == expected_kind
    assert got_installed is installed
    snap = mod._read_prefs_evidence()
    assert snap["readback"] == expected_readback
    assert snap["evidence"]["kind"] == expected_kind
    assert snap["evidence"]["autotesttool_installed"] is installed


@pytest.mark.parametrize(
    "installed,prefs,prefs_state,expected_readback",
    [
        (False, None, "ok", "not_installed"),
        (True, None, "ok", "absent"),
        (True, PREFS_TRUE, "denied", "readback_failed"),
        (True, PREFS_TRUE, "transient", "readback_failed"),
    ],
)
def test_probe_formal_values_are_failed(
    env, installed, prefs, prefs_state, expected_readback
):
    mod, lib, fake = env
    fake.installed = installed
    fake.prefs = prefs
    fake.prefs_state = prefs_state
    fake.service_running = False
    out = mod._run({"mode": "probe"})
    assert out["readback"] == expected_readback
    assert out["disarm_result"] == "failed"
    assert out["success"] is False


def test_not_installed_disarm_has_zero_writes(env):
    mod, lib, fake = env
    fake.installed = False
    fake.prefs = None
    fake.service_running = False
    out = mod._run({"mode": "disarm", "wait_device_online_seconds": 30})
    assert out["readback"] == "not_installed"
    assert out["disarm_result"] == "executed_unverified"
    assert out["success"] is True
    assert _writes(fake) == []


def test_root_not_installed_disarm_has_zero_writes(env):
    mod, lib, fake = env
    fake.uid = "0"
    fake.installed = False
    fake.prefs = None
    fake.service_running = False
    out = mod._run({"mode": "disarm", "wait_device_online_seconds": 30})
    assert out["readback"] == "not_installed"
    assert out["disarm_result"] == "executed_unverified"
    assert _writes(fake) == []


def test_root_absent_disarm_writes_minimal(env):
    mod, lib, fake = env
    fake.uid = "0"
    fake.installed = True
    fake.prefs = None
    fake.service_running = True
    fake.stop_rewrites = True
    out = mod._run({"mode": "disarm", "wait_device_online_seconds": 30})
    assert out["readback"] == "clear"
    assert out["disarm_result"] == "executed_unverified"
    assert fake.prefs is not None
    assert 'name="running" value="false"' in fake.prefs
    assert 'name="auto_resume" value="false"' in fake.prefs
    kinds = _writes(fake)
    assert "stop" in kinds and "prefs_write" in kinds
    assert kinds.index("stop") < kinds.index("prefs_write")


def test_absent_disarm_writes_minimal_and_verifies_both(env):
    mod, lib, fake = env
    fake.installed = True
    fake.prefs = None
    fake.service_running = True
    fake.stop_rewrites = True
    out = mod._run({"mode": "disarm", "wait_device_online_seconds": 30})
    assert out["readback"] == "clear"
    assert out["disarm_result"] == "executed_unverified"
    assert fake.prefs is not None
    assert 'name="running" value="false"' in fake.prefs
    assert 'name="auto_resume" value="false"' in fake.prefs
    assert "test_times" not in fake.prefs
    kinds = _writes(fake)
    assert "stop" in kinds and "mkdir" in kinds and "prefs_write" in kinds
    assert kinds.index("stop") < kinds.index("prefs_write")


def test_denied_disarm_does_not_write_prefs(env):
    mod, lib, fake = env
    fake.installed = True
    fake.prefs = PREFS_TRUE
    fake.prefs_state = "denied"
    fake.service_running = True
    out = mod._run({"mode": "disarm", "wait_device_online_seconds": 30})
    assert out["readback"] == "readback_failed"
    assert out["disarm_result"] == "failed"
    assert "prefs_write" not in _writes(fake)


def test_full_disarm_progress_adjacent_le_15(env):
    mod, lib, fake = env
    fake.prefs = PREFS_TRUE
    fake.service_running = True
    fake.stop_rewrites = True
    fake.adb_cost = 0.3
    out = mod._run({"mode": "disarm", "wait_device_online_seconds": 30})
    assert out["readback"] == "clear"
    exec_stamps = [
        t for t, payload in fake.progress if payload.get("phase") == "disarm_exec"
    ]
    assert len(exec_stamps) >= 8
    for a, b in zip(exec_stamps, exec_stamps[1:], strict=False):
        assert b - a <= 15.0 + 1e-9, (a, b, b - a)
    assert all(
        payload.get("phase") in {"powercycle_disarm", "disarm_exec", "stop_task_error"}
        for _t, payload in fake.progress
    )
