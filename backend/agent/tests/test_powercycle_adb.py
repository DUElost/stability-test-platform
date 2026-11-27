"""宿主侧 ADB 驱动开关机专项（powercycle_adb_*）单元测试。"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

SCRIPTS_ROOT = Path(__file__).resolve().parents[1] / "scripts"


def _load_script_module(family: str):
    """按包布局隔离加载脚本入口及其同目录 _lib（避免跨族污染 sys.modules['_lib']）。"""
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
    monkeypatch.setenv("STP_DEVICE_SERIAL", "DEV_ADB_001")
    monkeypatch.setenv("STP_JOB_ID", "job-9001")
    monkeypatch.setenv("STP_AEE_NFS_ROOT", str(tmp_path / "nfs"))
    monkeypatch.setenv("TMPDIR", str(tmp_path / "tmp"))
    (tmp_path / "tmp").mkdir(parents=True, exist_ok=True)
    return tmp_path


def test_setup_patrol_finish_full_lifecycle_short_interval(isolated_env, monkeypatch):
    """端到端 2 轮短间隔（10s <= inline_wait_max_seconds=15s）闭环。"""
    setup_mod, setup_lib = _load_script_module("powercycle_adb_setup")
    patrol_mod, patrol_lib = _load_script_module("powercycle_adb_patrol")
    finish_mod, finish_lib = _load_script_module("powercycle_adb_finish")

    clock = _FakeClock()
    boot_ids = [
        "11111111-1111-4111-8111-111111111111",
        "22222222-2222-4222-8222-222222222222",
        "33333333-3333-4333-8333-333333333333",
    ]
    state = {"online": True, "booted": True, "boot_idx": 0, "reboots": 0, "unlocks": 0}

    def _patch_lib(lib):
        monkeypatch.setattr(lib, "device_online", lambda timeout=15: state["online"])
        monkeypatch.setattr(lib, "boot_completed", lambda timeout=15: state["booted"])
        monkeypatch.setattr(lib, "read_boot_id", lambda timeout=15: boot_ids[state["boot_idx"]])
        monkeypatch.setattr(lib, "set_device_stayon_best_effort", lambda: True)

        def _unlock(timeout=20):
            state["unlocks"] += 1
            return True

        def _reboot(timeout=30):
            state["reboots"] += 1
            state["online"] = False
            state["booted"] = False
            return 0, "", ""

        monkeypatch.setattr(lib, "unlock_to_home", _unlock)
        monkeypatch.setattr(lib, "trigger_adb_reboot", _reboot)

    for lib, mod in (
        (setup_lib, setup_mod),
        (patrol_lib, patrol_mod),
        (finish_lib, finish_mod),
    ):
        _patch_lib(lib)
        monkeypatch.setattr(mod, "unlock_to_home", lib.unlock_to_home, raising=False)
        monkeypatch.setattr(mod, "read_boot_id", lib.read_boot_id, raising=False)
        monkeypatch.setattr(mod, "system_ready", lib.system_ready, raising=False)
        monkeypatch.setattr(mod, "trigger_adb_reboot", lib.trigger_adb_reboot, raising=False)
        monkeypatch.setattr(mod, "set_device_stayon_best_effort", lib.set_device_stayon_best_effort, raising=False)

    # 1. Setup：记录初始 boot_id，不在 init 发重启
    setup_metrics = setup_mod._run(
        {"project": "proj_a", "test_times": 2, "wait_seconds": 10, "boot_timeout_seconds": 300},
        now_fn=clock.time,
        sleep_fn=clock.sleep,
    )
    assert setup_metrics["driver"] == "host_adb"
    assert setup_metrics["initial_boot_id"] == boot_ids[0]
    assert state["reboots"] == 0

    # 2. Patrol #1：IDLE → 下发第 1 次 reboot
    r1 = patrol_mod._run({}, now_fn=clock.time, sleep_fn=clock.sleep)
    assert r1["success"] is True
    assert state["reboots"] == 1
    assert r1["progress"]["phase"] == "WAIT_REBOOT"

    # 3. Patrol #2：重启中离线 35s → 正常等待不判死
    clock.advance(35)
    r2 = patrol_mod._run({}, now_fn=clock.time, sleep_fn=clock.sleep)
    assert r2["success"] is True
    assert "patrol_fail" not in r2
    assert r2["progress"]["device_online"] is False

    # 4. Patrol #3：设备完成第 1 轮重启（boot_id 变为 #2），短间隔 10s 内联等待后直接下发第 2 轮重启
    clock.advance(25)
    state["online"] = True
    state["booted"] = True
    state["boot_idx"] = 1
    r3 = patrol_mod._run({}, now_fn=clock.time, sleep_fn=clock.sleep)
    assert r3["success"] is True
    assert r3["progress"]["cycles_done"] == 1
    assert r3["progress"]["observed"] == "reboot_sent_inline"
    assert clock.slept[-1] == 10.0
    assert state["reboots"] == 2

    # 5. Patrol #4：设备完成第 2 轮重启（boot_id 变为 #3）→ 达成目标次数，返回 patrol_complete
    clock.advance(55)
    state["online"] = True
    state["booted"] = True
    state["boot_idx"] = 2
    r4 = patrol_mod._run({}, now_fn=clock.time, sleep_fn=clock.sleep)
    assert r4["success"] is True
    assert r4["patrol_complete"] is True
    assert r4["progress"]["cycles_done"] == 2
    assert r4["progress"]["run_finished"] is True

    # 6. Finish：写入中心存储 results JSON
    fin = finish_mod._run({"wait_device_online_seconds": 30}, now_fn=clock.time, sleep_fn=clock.sleep)
    metrics = fin["metrics"]
    assert metrics["driver"] == "host_adb"
    assert metrics["cycles_done"] == 2
    assert metrics["expected_cycles"] == 2
    assert metrics["reboot_failures"] == 0
    assert metrics["final_status"] == "PASS"
    assert metrics["device_online_on_finish"] is True
    detail_path = Path(fin["detail_uri"])
    assert detail_path.is_file()
    detail = json.loads(detail_path.read_text(encoding="utf-8"))
    assert len(detail["entries"]) == 2
    assert all(e["status"] == "PASS" for e in detail["entries"])


def test_patrol_long_interval_releases_permit_then_reboots(isolated_env, monkeypatch):
    """长间隔（60s > 15s）先释放 permit 进 WAIT_INTERVAL，倒计时剩 <=15s 时精确补齐重启。"""
    setup_mod, setup_lib = _load_script_module("powercycle_adb_setup")
    patrol_mod, patrol_lib = _load_script_module("powercycle_adb_patrol")

    clock = _FakeClock()
    boot_ids = [
        "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
    ]
    state = {"boot_idx": 0, "reboots": 0}

    for lib, mod in ((setup_lib, setup_mod), (patrol_lib, patrol_mod)):
        monkeypatch.setattr(lib, "device_online", lambda timeout=15: True)
        monkeypatch.setattr(lib, "boot_completed", lambda timeout=15: True)
        monkeypatch.setattr(lib, "read_boot_id", lambda timeout=15: boot_ids[state["boot_idx"]])
        monkeypatch.setattr(lib, "set_device_stayon_best_effort", lambda: True)
        monkeypatch.setattr(lib, "unlock_to_home", lambda timeout=20: True)

        def _reboot(timeout=30):
            state["reboots"] += 1
            return 0, "", ""

        monkeypatch.setattr(lib, "trigger_adb_reboot", _reboot)
        monkeypatch.setattr(mod, "unlock_to_home", lib.unlock_to_home, raising=False)
        monkeypatch.setattr(mod, "read_boot_id", lib.read_boot_id, raising=False)
        monkeypatch.setattr(mod, "system_ready", lib.system_ready, raising=False)
        monkeypatch.setattr(mod, "trigger_adb_reboot", lib.trigger_adb_reboot, raising=False)
        monkeypatch.setattr(mod, "set_device_stayon_best_effort", lib.set_device_stayon_best_effort, raising=False)

    setup_mod._run({"test_times": 5, "wait_seconds": 60}, now_fn=clock.time, sleep_fn=clock.sleep)
    patrol_mod._run({}, now_fn=clock.time, sleep_fn=clock.sleep)  # cycle 1 reboot sent

    # 模拟完成第 1 次重启
    clock.advance(50)
    state["boot_idx"] = 1
    r_boot = patrol_mod._run({}, now_fn=clock.time, sleep_fn=clock.sleep)
    assert r_boot["success"] is True
    assert r_boot["progress"]["phase"] == "WAIT_INTERVAL"
    assert r_boot["progress"]["observed"] == "wait_interval"
    assert state["reboots"] == 1
    assert clock.slept == []  # 未阻塞 permit

    # 过了 30s（还剩 30s > 15s）→ 继续等待
    clock.advance(30)
    r_mid = patrol_mod._run({}, now_fn=clock.time, sleep_fn=clock.sleep)
    assert r_mid["progress"]["phase"] == "WAIT_INTERVAL"
    assert state["reboots"] == 1

    # 再过 20s（已等 50s，剩 10s <= 15s）→ 内联补齐 10s 后发第 2 次 reboot
    clock.advance(20)
    r_fire = patrol_mod._run({}, now_fn=clock.time, sleep_fn=clock.sleep)
    assert r_fire["progress"]["phase"] == "WAIT_REBOOT"
    assert r_fire["progress"]["observed"] == "reboot_sent_inline"
    assert clock.slept == [10.0]
    assert state["reboots"] == 2


def test_patrol_boot_timeout_triggers_terminal_patrol_fail(isolated_env, monkeypatch):
    """卡开机超过 boot_timeout_seconds 时直接返回 patrol_fail=True 终止 Job，finish 记 FAIL。"""
    setup_mod, setup_lib = _load_script_module("powercycle_adb_setup")
    patrol_mod, patrol_lib = _load_script_module("powercycle_adb_patrol")
    finish_mod, finish_lib = _load_script_module("powercycle_adb_finish")

    clock = _FakeClock()
    online = {"val": True}
    boot_id = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"

    for lib, mod in ((setup_lib, setup_mod), (patrol_lib, patrol_mod), (finish_lib, finish_mod)):
        monkeypatch.setattr(lib, "device_online", lambda timeout=15: online["val"])
        monkeypatch.setattr(lib, "boot_completed", lambda timeout=15: online["val"])
        monkeypatch.setattr(lib, "read_boot_id", lambda timeout=15: boot_id)
        monkeypatch.setattr(lib, "set_device_stayon_best_effort", lambda: True)
        monkeypatch.setattr(lib, "unlock_to_home", lambda timeout=20: True)
        monkeypatch.setattr(lib, "trigger_adb_reboot", lambda timeout=30: (0, "", ""))
        monkeypatch.setattr(mod, "unlock_to_home", lib.unlock_to_home, raising=False)
        monkeypatch.setattr(mod, "read_boot_id", lib.read_boot_id, raising=False)
        monkeypatch.setattr(mod, "system_ready", lib.system_ready, raising=False)
        monkeypatch.setattr(mod, "trigger_adb_reboot", lib.trigger_adb_reboot, raising=False)
        monkeypatch.setattr(mod, "set_device_stayon_best_effort", lib.set_device_stayon_best_effort, raising=False)

    setup_mod._run({"test_times": 10, "boot_timeout_seconds": 120}, now_fn=clock.time, sleep_fn=clock.sleep)
    patrol_mod._run({}, now_fn=clock.time, sleep_fn=clock.sleep)

    online["val"] = False
    clock.advance(125)
    r_fail = patrol_mod._run({}, now_fn=clock.time, sleep_fn=clock.sleep)
    assert r_fail["success"] is False
    assert r_fail["patrol_fail"] is True
    assert "开机超时" in r_fail["error_message"]

    # teardown 即使设备依旧离线，也必须把失败结果落盘
    fin = finish_mod._run({"wait_device_online_seconds": 5}, now_fn=clock.time, sleep_fn=clock.sleep)
    assert fin["metrics"]["final_status"] == "FAIL"
    assert fin["metrics"]["reboot_failures"] == 1
    assert fin["metrics"]["device_online_on_finish"] is False
    assert Path(fin["detail_uri"]).is_file()


def test_patrol_same_boot_id_detects_ineffective_reboot(isolated_env, monkeypatch):
    """设备未真正重启（boot_id 长期不变）超过 reboot_confirm_timeout_seconds 判失败。"""
    setup_mod, setup_lib = _load_script_module("powercycle_adb_setup")
    patrol_mod, patrol_lib = _load_script_module("powercycle_adb_patrol")

    clock = _FakeClock()
    fixed_boot_id = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"

    for lib, mod in ((setup_lib, setup_mod), (patrol_lib, patrol_mod)):
        monkeypatch.setattr(lib, "device_online", lambda timeout=15: True)
        monkeypatch.setattr(lib, "boot_completed", lambda timeout=15: True)
        monkeypatch.setattr(lib, "read_boot_id", lambda timeout=15: fixed_boot_id)
        monkeypatch.setattr(lib, "set_device_stayon_best_effort", lambda: True)
        monkeypatch.setattr(lib, "unlock_to_home", lambda timeout=20: True)
        monkeypatch.setattr(lib, "trigger_adb_reboot", lambda timeout=30: (1, "", "reboot denied"))
        monkeypatch.setattr(mod, "unlock_to_home", lib.unlock_to_home, raising=False)
        monkeypatch.setattr(mod, "read_boot_id", lib.read_boot_id, raising=False)
        monkeypatch.setattr(mod, "system_ready", lib.system_ready, raising=False)
        monkeypatch.setattr(mod, "trigger_adb_reboot", lib.trigger_adb_reboot, raising=False)
        monkeypatch.setattr(mod, "set_device_stayon_best_effort", lib.set_device_stayon_best_effort, raising=False)

    setup_mod._run({"test_times": 5, "reboot_confirm_timeout_seconds": 25}, now_fn=clock.time, sleep_fn=clock.sleep)
    patrol_mod._run({}, now_fn=clock.time, sleep_fn=clock.sleep)

    # 10s 内仍在宽限期，不误判
    clock.advance(10)
    r_wait = patrol_mod._run({}, now_fn=clock.time, sleep_fn=clock.sleep)
    assert r_wait["success"] is True
    assert r_wait["progress"]["observed"] == "reboot_pending"

    # 30s 后 boot_id 仍不变 → 判定 adb reboot 未生效
    clock.advance(20)
    r_fail = patrol_mod._run({}, now_fn=clock.time, sleep_fn=clock.sleep)
    assert r_fail["success"] is False
    assert r_fail["patrol_fail"] is True
    assert "未生效" in r_fail["error_message"]


def test_unlock_command_uses_wakeup_dismiss_keyguard_3x_keyevent82_and_home():
    """解锁命令必须包含 WAKEUP + wm dismiss-keyguard + 3 次 keyevent 82 + HOME。"""
    _, lib = _load_script_module("powercycle_adb_patrol")
    cmd = lib._UNLOCK_TO_HOME_CMD
    assert "KEYCODE_WAKEUP" in cmd
    assert "wm dismiss-keyguard" in cmd
    assert cmd.count("input keyevent 82") == 3
    assert "KEYCODE_HOME" in cmd


def test_shared_lib_copies_are_byte_identical():
    """三族打包自包含的 _lib.py 必须完全同源，防止漂移。"""
    base = (SCRIPTS_ROOT / "powercycle_adb_setup" / "_lib.py").read_bytes()
    for fam in ("powercycle_adb_patrol", "powercycle_adb_finish"):
        assert (SCRIPTS_ROOT / fam / "_lib.py").read_bytes() == base
