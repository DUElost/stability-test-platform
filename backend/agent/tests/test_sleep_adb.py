"""宿主侧 ADB 驱动休眠唤醒专项（sleep_adb_*）单元测试。"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPTS_ROOT = Path(__file__).resolve().parents[1] / "scripts"


def _load_script_module(family: str):
    fam_dir = SCRIPTS_ROOT / family
    entry = fam_dir / f"{family}.py"
    old_lib = sys.modules.pop("_lib", None)
    sys.path.insert(0, str(fam_dir))
    try:
        spec = importlib.util.spec_from_file_location(f"_test_{family}", entry)
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        lib_mod = sys.modules["_lib"]
        return mod, lib_mod
    finally:
        sys.path.remove(str(fam_dir))
        sys.modules.pop("_lib", None)
        if old_lib is not None:
            sys.modules["_lib"] = old_lib


class _FakeClock:
    def __init__(self, start: float = 1_700_000_000.0) -> None:
        self.now = start
        self.slept: list[float] = []

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += max(0.0, float(seconds))

    def advance(self, seconds: float) -> None:
        self.now += float(seconds)


@pytest.fixture()
def isolated_env(monkeypatch, tmp_path):
    monkeypatch.setenv("STP_DEVICE_SERIAL", "DEV_SLEEP_001")
    monkeypatch.setenv("STP_JOB_ID", "job-9200")
    monkeypatch.setenv("STP_AEE_NFS_ROOT", str(tmp_path / "nfs"))
    monkeypatch.setenv("TMPDIR", str(tmp_path / "tmp"))
    (tmp_path / "tmp").mkdir(parents=True, exist_ok=True)
    return tmp_path


def _wire_fake_device(monkeypatch, mods_and_libs, state):
    for mod, lib in mods_and_libs:
        monkeypatch.setattr(lib, "device_online", lambda timeout=15: state["online"])
        monkeypatch.setattr(lib, "boot_completed", lambda timeout=15: state["online"])
        monkeypatch.setattr(lib, "configure_sleep_environment", lambda: True)
        monkeypatch.setattr(lib, "restore_awake_environment", lambda: state.__setitem__("restored", True))
        monkeypatch.setattr(lib, "read_wakefulness", lambda timeout=15: state["wakefulness"] if state["online"] else "")

        def _sleep(timeout=15):
            state["sleep_calls"] += 1
            if state["allow_sleep"]:
                state["wakefulness"] = "Asleep"
            return True

        def _wake(*, unlock=True, timeout=20):
            state["wake_calls"] += 1
            if state["allow_wake"]:
                state["wakefulness"] = "Awake"
            return True

        monkeypatch.setattr(lib, "trigger_sleep", _sleep)
        monkeypatch.setattr(lib, "trigger_wakeup", _wake)

        for fn in (
            "device_online",
            "configure_sleep_environment",
            "restore_awake_environment",
            "read_wakefulness",
            "trigger_sleep",
            "trigger_wakeup",
        ):
            monkeypatch.setattr(mod, fn, getattr(lib, fn), raising=False)


def test_sleep_adb_full_lifecycle_two_cycles(isolated_env, monkeypatch):
    setup_mod, setup_lib = _load_script_module("sleep_adb_setup")
    patrol_mod, patrol_lib = _load_script_module("sleep_adb_patrol")
    finish_mod, finish_lib = _load_script_module("sleep_adb_finish")

    clock = _FakeClock()
    state = {
        "online": True,
        "wakefulness": "Awake",
        "allow_sleep": True,
        "allow_wake": True,
        "sleep_calls": 0,
        "wake_calls": 0,
        "restored": False,
    }
    _wire_fake_device(monkeypatch, ((setup_mod, setup_lib), (patrol_mod, patrol_lib), (finish_mod, finish_lib)), state)

    setup_metrics = setup_mod._run(
        {"project": "chain", "test_times": 2, "wake_seconds": 60, "sleep_seconds": 300},
        now_fn=clock.time,
        sleep_fn=clock.sleep,
    )
    assert setup_metrics["driver"] == "host_adb"
    assert setup_metrics["initial_wakefulness"] == "Awake"

    # WAIT_WAKE 30s -> still waiting (30s left > inline 20s)
    clock.advance(30)
    r1 = patrol_mod._run({}, now_fn=clock.time, sleep_fn=clock.sleep)
    assert r1["progress"]["phase"] == "WAIT_WAKE"
    assert r1["progress"]["observed"] == "waiting_wake"

    # Advance 15s (15s left <= inline 20s) -> inline wait & enter sleep
    clock.advance(15)
    r2 = patrol_mod._run({}, now_fn=clock.time, sleep_fn=clock.sleep)
    assert r2["progress"]["phase"] == "WAIT_SLEEP"
    assert r2["progress"]["observed"] == "entered_sleep"
    assert state["wakefulness"] == "Asleep"

    # WAIT_SLEEP 150s -> still sleeping
    clock.advance(150)
    r3 = patrol_mod._run({}, now_fn=clock.time, sleep_fn=clock.sleep)
    assert r3["progress"]["phase"] == "WAIT_SLEEP"
    assert r3["progress"]["observed"] == "sleeping"

    # Advance 140s (10s left <= inline 20s) -> inline wait & wake -> cycle 1 PASS
    clock.advance(140)
    r4 = patrol_mod._run({}, now_fn=clock.time, sleep_fn=clock.sleep)
    assert r4["progress"]["cycles_done"] == 1
    assert r4["progress"]["phase"] == "WAIT_WAKE"
    assert r4["progress"]["observed"] == "cycle_pass_awake"
    assert state["wakefulness"] == "Awake"

    # Cycle 2: wake 60s -> sleep 300s -> complete
    clock.advance(60)
    r5 = patrol_mod._run({}, now_fn=clock.time, sleep_fn=clock.sleep)
    assert r5["progress"]["phase"] == "WAIT_SLEEP"

    clock.advance(300)
    r6 = patrol_mod._run({}, now_fn=clock.time, sleep_fn=clock.sleep)
    assert r6["success"] is True
    assert r6["patrol_complete"] is True
    assert r6["progress"]["cycles_done"] == 2

    fin = finish_mod._run({}, now_fn=clock.time, sleep_fn=clock.sleep)
    assert fin["metrics"]["driver"] == "host_adb"
    assert fin["metrics"]["final_status"] == "PASS"
    assert fin["metrics"]["cycles_done"] == 2
    assert fin["metrics"]["wake_failures"] == 0
    assert fin["metrics"]["awake_on_finish"] is True
    assert state["restored"] is True
    assert Path(fin["detail_uri"]).is_file()


def test_sleep_adb_wake_failure_triggers_terminal_patrol_fail(isolated_env, monkeypatch):
    setup_mod, setup_lib = _load_script_module("sleep_adb_setup")
    patrol_mod, patrol_lib = _load_script_module("sleep_adb_patrol")
    finish_mod, finish_lib = _load_script_module("sleep_adb_finish")

    clock = _FakeClock()
    state = {
        "online": True,
        "wakefulness": "Awake",
        "allow_sleep": True,
        "allow_wake": True,
        "sleep_calls": 0,
        "wake_calls": 0,
        "restored": False,
    }
    _wire_fake_device(monkeypatch, ((setup_mod, setup_lib), (patrol_mod, patrol_lib), (finish_mod, finish_lib)), state)

    setup_mod._run({"test_times": 5, "wake_seconds": 10, "sleep_seconds": 30}, now_fn=clock.time, sleep_fn=clock.sleep)
    clock.advance(10)
    r1 = patrol_mod._run({}, now_fn=clock.time, sleep_fn=clock.sleep)
    assert r1["progress"]["phase"] == "WAIT_SLEEP"

    # 唤醒失败：始终停留在 Asleep
    state["allow_wake"] = False
    clock.advance(30)
    r_fail = patrol_mod._run({}, now_fn=clock.time, sleep_fn=clock.sleep)
    assert r_fail["success"] is False
    assert r_fail["patrol_fail"] is True
    assert "唤醒失败" in r_fail["error_message"]

    fin = finish_mod._run({}, now_fn=clock.time, sleep_fn=clock.sleep)
    assert fin["metrics"]["final_status"] == "FAIL"
    assert fin["metrics"]["wake_failures"] == 1


def test_sleep_adb_unexpected_awake_records_anomaly_and_resleeps(isolated_env, monkeypatch):
    setup_mod, setup_lib = _load_script_module("sleep_adb_setup")
    patrol_mod, patrol_lib = _load_script_module("sleep_adb_patrol")

    clock = _FakeClock()
    state = {
        "online": True,
        "wakefulness": "Awake",
        "allow_sleep": True,
        "allow_wake": True,
        "sleep_calls": 0,
        "wake_calls": 0,
        "restored": False,
    }
    _wire_fake_device(monkeypatch, ((setup_mod, setup_lib), (patrol_mod, patrol_lib)), state)

    setup_mod._run({"test_times": 5, "wake_seconds": 10, "sleep_seconds": 300}, now_fn=clock.time, sleep_fn=clock.sleep)
    clock.advance(10)
    patrol_mod._run({}, now_fn=clock.time, sleep_fn=clock.sleep)

    # 休眠期中途意外自己亮屏
    state["wakefulness"] = "Awake"
    clock.advance(60)
    r = patrol_mod._run({}, now_fn=clock.time, sleep_fn=clock.sleep)
    assert r["success"] is True
    assert r["progress"]["observed"] == "unexpected_awake_re_sleep"
    assert r["progress"]["sleep_anomalies"] == 1
    assert state["wakefulness"] == "Asleep"


def test_shared_lib_copies_are_byte_identical():
    base = (SCRIPTS_ROOT / "sleep_adb_setup" / "_lib.py").read_bytes()
    for fam in ("sleep_adb_patrol", "sleep_adb_finish"):
        assert (SCRIPTS_ROOT / fam / "_lib.py").read_bytes() == base
