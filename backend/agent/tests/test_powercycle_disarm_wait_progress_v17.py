"""#3730 U5 v1.7.4：等待上线防忙等 + 上线后打戳（§3.6.2 v1.7.2/v1.7.3）。"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
SCRIPTS = REPO / "backend" / "agent" / "scripts"


def _load_disarm():
    path = SCRIPTS / "powercycle_disarm" / "powercycle_disarm.py"
    lib_path = path.parent / "_lib.py"
    lib_spec = importlib.util.spec_from_file_location("pc_disarm_wait_lib", lib_path)
    assert lib_spec and lib_spec.loader
    lib = importlib.util.module_from_spec(lib_spec)
    sys.modules["pc_disarm_wait_lib"] = lib
    lib_spec.loader.exec_module(lib)
    sys.modules["_lib"] = lib
    spec = importlib.util.spec_from_file_location("pc_disarm_wait", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["pc_disarm_wait"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def disarm_mod():
    return _load_disarm()


class Rig:
    """规划者第三轮评论第三节参考替身：必须消耗时间。"""

    def __init__(self, *, state_cost, boot_cost, online_at=None, raise_at=()):
        self.t = 0.0
        self.state_cost, self.boot_cost = state_cost, boot_cost
        self.online_at, self.raise_at = online_at, set(raise_at)
        self.poll_starts, self.stamps, self.timeouts = [], [], []
        self.state_calls = 0

    def clock(self):
        return self.t

    def sleep(self, dt):
        assert dt >= 0
        self.t += float(dt)

    def online(self, timeout):
        self.poll_starts.append(self.t)
        self.state_calls += 1
        self.timeouts.append(["state", timeout])
        assert 1 <= timeout <= 5
        if self.state_calls in self.raise_at:
            self.t += 0.05
            raise RuntimeError("adb boom")
        self.t += min(self.state_cost(self.state_calls), float(timeout))
        return self.online_at is not None and self.t >= self.online_at

    def boot(self, timeout):
        self.timeouts[-1].append(timeout)
        self.t += min(self.boot_cost(self.state_calls), float(timeout))
        return "1" if self.online_at is not None and self.t >= self.online_at else "0"

    def run(self, mod, timeout_s=600):
        ok = mod._wait_device_online(
            timeout_s,
            clock=self.clock,
            sleeper=self.sleep,
            online_fn=self.online,
            boot_prop_fn=self.boot,
            progress_fn=lambda p: self.stamps.append((self.t, dict(p))),
        )
        return ok, self.t

    def check_invariants(self):
        for a, b in zip(self.poll_starts, self.poll_starts[1:], strict=False):
            assert b - a >= 10.0 - 1e-9, (a, b, b - a)
        times = [t for t, _ in self.stamps]
        for a, b in zip(times, times[1:], strict=False):
            assert b - a <= 15.0 + 1e-9, (a, b, b - a)
        for entry in self.timeouts:
            assert sum(entry[1:]) <= 5, entry
        assert [p["seq"] for _, p in self.stamps] == list(range(1, len(self.stamps) + 1))


class TestWaitDeviceOnlineV174:
    def test_fast_fail_offline_600s(self, disarm_mod):
        rig = Rig(state_cost=lambda n: 0.05, boot_cost=lambda n: 0.0)
        ok, t_end = rig.run(disarm_mod)
        assert ok is False
        assert rig.state_calls <= 61
        assert 600.0 <= t_end < 600.0 + 1e-6
        # 截止后无戳：最后一次戳时刻 ≤ 截止
        if rig.stamps:
            assert rig.stamps[-1][0] <= 600.0 + 1e-6
        rig.check_invariants()

    def test_full_timeout_budget_keeps_adjacent_le_15(self, disarm_mod):
        """每次轮询耗满超时（2s + 3s）。"""
        rig = Rig(
            state_cost=lambda n: 2.0,
            boot_cost=lambda n: 3.0,
            online_at=45.0,
        )
        ok, _ = rig.run(disarm_mod, timeout_s=120)
        assert ok is True
        rig.check_invariants()
        for a, b in zip(rig.poll_starts, rig.poll_starts[1:], strict=False):
            assert b - a >= 10.0 - 1e-9

    def test_fast_fail_and_full_budget_alternate(self, disarm_mod):
        def state_cost(n):
            return 0.05 if n % 2 else 2.0

        def boot_cost(n):
            return 0.0 if n % 2 else 3.0

        rig = Rig(state_cost=state_cost, boot_cost=boot_cost, online_at=80.0)
        ok, _ = rig.run(disarm_mod, timeout_s=200)
        assert ok is True
        times = [t for t, _ in rig.stamps]
        for a, b in zip(times, times[1:], strict=False):
            assert b - a <= 15.0 + 1e-9, (a, b, b - a)

    def test_online_at_200s_returns_within_next_poll(self, disarm_mod):
        rig = Rig(
            state_cost=lambda n: 0.05,
            boot_cost=lambda n: 0.1,
            online_at=200.0,
        )
        ok, t_end = rig.run(disarm_mod)
        assert ok is True
        assert t_end <= 215.0
        assert t_end >= 200.0
        rig.check_invariants()

    def test_adb_raises_does_not_busy_wait(self, disarm_mod):
        rig = Rig(
            state_cost=lambda n: 0.05,
            boot_cost=lambda n: 0.0,
            raise_at=set(range(1, 70)),
        )
        ok, t_end = rig.run(disarm_mod)
        assert ok is False
        assert rig.state_calls <= 61
        assert any("error" in p for _, p in rig.stamps)
        assert t_end >= 600.0 - 1e-6

    @pytest.mark.parametrize("timeout_s", [0, 1, 5, 9, 10, 11, 25, 605])
    def test_deadline_edges(self, disarm_mod, timeout_s):
        rig = Rig(state_cost=lambda n: 0.05, boot_cost=lambda n: 0.0)
        ok, t_end = rig.run(disarm_mod, timeout_s=timeout_s)
        assert ok is False
        assert t_end >= float(timeout_s) - 1e-6
        assert t_end <= float(timeout_s) + 1e-6
        if rig.stamps:
            assert rig.stamps[-1][0] <= float(timeout_s) + 1e-6

    def test_frozen_clock_ends_within_iteration_bound(self, disarm_mod):
        """时钟冻结时循环在 timeout//10+2 轮内结束。"""
        stamps = []
        calls = {"n": 0}

        def clock():
            return 0.0

        def sleep(dt):
            return None

        def online(timeout):
            calls["n"] += 1
            return False

        ok = disarm_mod._wait_device_online(
            50,
            clock=clock,
            sleeper=sleep,
            online_fn=online,
            boot_prop_fn=lambda t: "0",
            progress_fn=lambda p: stamps.append(dict(p)),
        )
        assert ok is False
        assert calls["n"] <= 50 // 10 + 2
        assert len(stamps) <= 50 // 10 + 2


class TestPermissionModelAndOffline:
    def test_permission_model_values(self, disarm_mod, monkeypatch):
        lib = sys.modules["pc_disarm_wait_lib"]

        for raw, expected in [("0", "root"), ("2000", "non-root"), ("", "unknown")]:
            monkeypatch.setattr(lib, "adb_shell", lambda cmd, timeout=15, r=raw: r)
            assert lib.permission_model() == expected

        def boom(cmd, timeout=15):
            return ""  # 超时路径由 adb() 返回空串；此处模拟读不到

        monkeypatch.setattr(lib, "adb_shell", boom)
        assert lib.permission_model() == "unknown"

    def test_wait_timeout_skips_identity_adb(self, disarm_mod, monkeypatch):
        """等待超时后对设备的 adb 取证为 0 次，证据 unknown + null。"""
        lib = sys.modules["pc_disarm_wait_lib"]
        adb_calls = {"n": 0}
        real_adb = lib.adb

        def counting_adb(*args, timeout=60):
            adb_calls["n"] += 1
            return real_adb(*args, timeout=timeout)

        monkeypatch.setattr(lib, "adb", counting_adb)
        monkeypatch.setattr(disarm_mod, "device_serial", lambda: "S-OFF")
        monkeypatch.setattr(disarm_mod, "_wait_device_online", lambda s: False)
        # 若误调 identity，会计入 adb
        before = adb_calls["n"]
        out = disarm_mod._run({"mode": "disarm", "wait_device_online_seconds": 1})
        assert out["evidence"]["permission_model"] == "unknown"
        assert out["evidence"]["autotesttool_apk_sha256"] is None
        assert adb_calls["n"] == before


class TestProbeBootCompleted:
    def test_probe_boot_not_ready_skips_prefs(self, disarm_mod, monkeypatch):
        prefs_calls = {"n": 0}
        identity_calls = {"n": 0}
        monkeypatch.setattr(disarm_mod, "device_serial", lambda: "S-BOOT")
        monkeypatch.setattr(disarm_mod, "device_online", lambda: True)
        monkeypatch.setattr(
            disarm_mod,
            "adb_shell",
            lambda cmd, timeout=60: "0" if "boot_completed" in str(cmd) else "",
        )
        monkeypatch.setattr(
            disarm_mod,
            "_read_prefs_evidence",
            lambda: prefs_calls.__setitem__("n", prefs_calls["n"] + 1) or {},
        )
        monkeypatch.setattr(
            disarm_mod,
            "_device_identity_evidence",
            lambda: identity_calls.__setitem__("n", identity_calls["n"] + 1)
            or {"permission_model": "root", "autotesttool_apk_sha256": "x"},
        )
        out = disarm_mod._run({"mode": "probe"})
        assert out["readback"] == "readback_failed"
        assert out["disarm_result"] == "failed"
        assert out["evidence"]["online"] is True
        assert out["evidence"]["boot_completed"] == "0"
        assert out["evidence"]["permission_model"] == "unknown"
        assert out["evidence"]["autotesttool_apk_sha256"] is None
        assert prefs_calls["n"] == 0
        assert identity_calls["n"] == 0

    def test_probe_boot_empty_skips_prefs(self, disarm_mod, monkeypatch):
        monkeypatch.setattr(disarm_mod, "device_serial", lambda: "S-BOOT")
        monkeypatch.setattr(disarm_mod, "device_online", lambda: True)
        monkeypatch.setattr(
            disarm_mod,
            "adb_shell",
            lambda cmd, timeout=60: "" if "boot_completed" in str(cmd) else "",
        )
        monkeypatch.setattr(
            disarm_mod,
            "_read_prefs_evidence",
            lambda: (_ for _ in ()).throw(AssertionError("must not read prefs")),
        )
        out = disarm_mod._run({"mode": "probe"})
        assert out["evidence"]["boot_completed"] == ""
        assert out["evidence"]["permission_model"] == "unknown"

    def test_probe_boot_ok_reads_prefs(self, disarm_mod, monkeypatch):
        monkeypatch.setattr(disarm_mod, "device_serial", lambda: "S-BOOT")
        monkeypatch.setattr(disarm_mod, "device_online", lambda: True)
        monkeypatch.setattr(disarm_mod, "_detect_platform", lambda: "MTK")
        monkeypatch.setattr(disarm_mod, "_detect_model", lambda: "MLD_LX2")
        monkeypatch.setattr(
            disarm_mod,
            "adb_shell",
            lambda cmd, timeout=60: "1" if "boot_completed" in str(cmd) else "",
        )
        monkeypatch.setattr(
            disarm_mod,
            "_device_identity_evidence",
            lambda: {"permission_model": "root", "autotesttool_apk_sha256": "c" * 64},
        )
        monkeypatch.setattr(
            disarm_mod,
            "_read_prefs_evidence",
            lambda: {
                "readback": "clear",
                "running": False,
                "auto_resume": False,
                "evidence": {"kind": "ok"},
            },
        )
        out = disarm_mod._run({"mode": "probe"})
        assert out["readback"] == "clear"
        assert out["evidence"]["boot_completed"] == "1"
        assert out["evidence"]["permission_model"] == "root"


class TestExecProgressAfterOnline:
    def test_disarm_exec_progress_adjacent_le_15(self, disarm_mod, monkeypatch):
        """上线后每次 adb 耗时 3s；相邻 PROGRESS ≤15s（含脚本内 sleep）。"""
        lib = sys.modules["pc_disarm_wait_lib"]
        lib.reset_exec_progress()
        stamps = []
        clock = {"t": 0.0}

        def fake_progress(payload):
            stamps.append((clock["t"], dict(payload)))

        monkeypatch.setattr(lib, "progress_stamp", fake_progress)

        def fake_run(cmd, capture_output=True, timeout=60, input=None):
            clock["t"] += 3.0

            class R:
                returncode = 0
                stdout = b"ok\n"
                stderr = b""

            return R()

        monkeypatch.setattr(lib.subprocess, "run", fake_run)
        monkeypatch.setattr(
            lib.time, "sleep",
            lambda dt: clock.__setitem__("t", clock["t"] + float(dt)),
        )
        monkeypatch.setattr(lib, "device_serial", lambda: "S-EXEC")
        monkeypatch.setattr(lib, "adb_path", lambda: "adb")

        lib.reset_exec_progress()
        lib.enable_exec_progress()
        for _ in range(8):
            lib.adb("shell", "echo ok", timeout=10)
        lib.time.sleep(5)
        times = [tm for tm, payload in stamps if payload.get("phase") == "disarm_exec"]
        assert len(times) >= 8
        for a, b in zip(times, times[1:], strict=False):
            assert b - a <= 15.0 + 1e-9, (a, b, b - a)
        assert times[1] - times[0] == pytest.approx(3.0)
        assert clock["t"] - times[-1] == pytest.approx(5.0)

    def test_timeout_still_ticks(self, disarm_mod, monkeypatch):
        lib = sys.modules["pc_disarm_wait_lib"]
        lib.reset_exec_progress()
        stamps = []
        monkeypatch.setattr(lib, "progress_stamp", lambda payload: stamps.append(dict(payload)))
        monkeypatch.setattr(lib, "device_serial", lambda: "S-TO")
        monkeypatch.setattr(lib, "adb_path", lambda: "adb")

        def boom_run(cmd, capture_output=True, timeout=60, input=None):
            raise lib.subprocess.TimeoutExpired(cmd=cmd, timeout=timeout)

        monkeypatch.setattr(lib.subprocess, "run", boom_run)
        lib.enable_exec_progress()
        rc, out, err = lib.adb("shell", "hang", timeout=1)
        assert rc == -1 and err == "timeout"
        assert any(payload.get("phase") == "disarm_exec" for payload in stamps)

    def test_push_prefs_xml_direct_subprocess_ticks(self, disarm_mod, monkeypatch):
        lib = sys.modules["pc_disarm_wait_lib"]
        lib.reset_exec_progress()
        stamps = []
        monkeypatch.setattr(lib, "progress_stamp", lambda payload: stamps.append(dict(payload)))
        monkeypatch.setattr(lib, "device_serial", lambda: "S-PUSH")
        monkeypatch.setattr(lib, "adb_path", lambda: "adb")
        monkeypatch.setattr(lib, "is_root", lambda: False)

        def fake_run(cmd, capture_output=True, timeout=60, input=None):
            class R:
                returncode = 0
                stdout = b""
                stderr = b""
            return R()

        monkeypatch.setattr(lib.subprocess, "run", fake_run)
        lib.enable_exec_progress()
        lib.push_prefs_xml("<map></map>")
        assert any(payload.get("phase") == "disarm_exec" for payload in stamps)

    def test_probe_enables_exec_progress_after_boot(self, disarm_mod, monkeypatch):
        lib = sys.modules["pc_disarm_wait_lib"]
        lib.reset_exec_progress()
        monkeypatch.setattr(disarm_mod, "device_serial", lambda: "S-P")
        monkeypatch.setattr(disarm_mod, "device_online", lambda: True)
        monkeypatch.setattr(
            disarm_mod,
            "adb_shell",
            lambda cmd, timeout=60: "1" if "boot_completed" in str(cmd) else "0",
        )
        monkeypatch.setattr(disarm_mod, "_detect_platform", lambda: "MTK")
        monkeypatch.setattr(disarm_mod, "_detect_model", lambda: "X")
        monkeypatch.setattr(
            disarm_mod,
            "_device_identity_evidence",
            lambda: {"permission_model": "non-root", "autotesttool_apk_sha256": None},
        )
        monkeypatch.setattr(
            disarm_mod,
            "_read_prefs_evidence",
            lambda: {
                "readback": "footprint",
                "running": True,
                "auto_resume": True,
                "evidence": {"kind": "ok"},
            },
        )
        out = disarm_mod._run({"mode": "probe"})
        assert lib._EXEC_PROGRESS["on"] is True
        assert out["evidence"]["boot_completed"] == "1"
