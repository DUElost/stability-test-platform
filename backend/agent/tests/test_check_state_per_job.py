"""#1028（R08-F10）—— check 族巡检状态按 Job 隔离（新版本回归）。

覆盖四个同型脚本的新版本：
  - mtbf_check v1.4.0（重点：同设备连续两个 Job 的死亡计数不继承 + 同 Job 恢复）
  - gpu_check v1.0.6 / sleep_check v1.0.2（同型 dead_streak 重置，轻量）
  - powercycle_check v1.0.7（收取窗口标记 / last_online / seq 随 Job 重置）

状态文件经 monkeypatch 指到 tmp_path；adb/_lib 依赖全部 patch 在模块属性上。
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def _load(name: str, script_dir: Path):
    # 各脚本目录有自己的 _lib.py：importlib 按文件加载并临时占住
    # sys.modules["_lib"]（脚本顶层 `from _lib import ...` 需要它），执行完恢复
    # 原值——其他测试文件（如 test_gpu_power_sleep_resources）也在玩同一个
    # sys.modules 缓存键，必须精确还原而不是无条件 pop。
    lib_path = script_dir / "_lib.py"
    prev_lib = sys.modules.get("_lib")
    if lib_path.exists():
        lib_spec = importlib.util.spec_from_file_location(f"_lib_{name}", lib_path)
        lib_mod = importlib.util.module_from_spec(lib_spec)
        assert lib_spec and lib_spec.loader
        lib_spec.loader.exec_module(lib_mod)
        sys.modules["_lib"] = lib_mod
    try:
        spec = importlib.util.spec_from_file_location(name, script_dir / f"{_base(name)}.py")
        mod = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(mod)
    finally:
        if prev_lib is not None:
            sys.modules["_lib"] = prev_lib
        else:
            sys.modules.pop("_lib", None)
    return mod


def _base(mod_name: str) -> str:
    # 模块名形如 mtbf_check_v140 → 脚本文件名是其脚本名部分
    return {
        "mtbf_check_v140": "mtbf_check",
        "gpu_check_v106": "gpu_check",
        "powercycle_check_v107": "powercycle_check",
        "sleep_check_v102": "sleep_check",
    }[mod_name]


def _new_version_dirs():
    return {
        "mtbf_check_v140": _SCRIPTS / "mtbf_check" / "v1.4.0",
        "gpu_check_v106": _SCRIPTS / "gpu_check" / "v1.0.6",
        "powercycle_check_v107": _SCRIPTS / "powercycle_check" / "v1.0.7",
        "sleep_check_v102": _SCRIPTS / "sleep_check" / "v1.0.2",
    }


@pytest.fixture
def env_ids(monkeypatch):
    """按用例设置 job / 设备身份。"""
    def _set(job_id: str, serial: str = "SER-1028"):
        monkeypatch.setenv("STP_JOB_ID", job_id)
        monkeypatch.setenv("STP_DEVICE_SERIAL", serial)
    return _set


def _write_state(mod, tmp_path: Path, state: dict) -> Path:
    state_file = tmp_path / f"{mod.__name__}_state.json"
    state_file.write_text(json.dumps(state), encoding="utf-8")
    mod._state_file = lambda: state_file
    return state_file


def _read_state(mod) -> dict:
    return json.loads(mod._state_file().read_text())


# ── mtbf_check v1.4.0（验收标准主场景）────────────────────────────────────


@pytest.fixture
def mtbf(_load_modules):
    return _load_modules["mtbf_check_v140"]


@pytest.fixture
def _load_modules(monkeypatch, tmp_path):
    mods = {}
    for mod_name, d in _new_version_dirs().items():
        mods[mod_name] = _load(mod_name, d)
    return mods


def _mtbf_fake_adb(mod, monkeypatch, *, service_alive: bool, testpoints: int = 7):
    monkeypatch.setattr(mod, "adb_shell", lambda cmd, timeout=30: (
        "RunTaskService alive" if service_alive and cmd.startswith("dumpsys")
        else ("run-1" if cmd.startswith("ls /sdcard/results/realresult") else "")
    ))
    monkeypatch.setattr(mod, "_adb_grep",
                        lambda xml: (0, str(testpoints), ""))
    monkeypatch.setattr(mod, "_parse_size_from_ls", lambda ls: 0)
    stamps: list = []
    monkeypatch.setattr(mod, "progress_stamp", lambda payload: stamps.append(payload))
    return stamps


def test_mtbf_same_job_dead_streak_persists(mtbf, env_ids, monkeypatch, tmp_path):
    """同 Job 内连续死亡：计数跨周期累加（既有语义）。"""
    env_ids("100")
    _write_state(mtbf, tmp_path, {"job_id": "100"})
    _mtbf_fake_adb(mtbf, monkeypatch, service_alive=False)

    r1 = mtbf._run({})
    assert r1["success"] is True  # streak=1 < grace=2
    r2 = mtbf._run({})
    assert r2["success"] is False  # streak=2 >= grace
    assert "连续 2 个周期" in r2["error_message"]


def test_mtbf_new_job_does_not_inherit_dead_streak(mtbf, env_ids, monkeypatch, tmp_path):
    """#1028 核心：上一 Job 留下死亡计数后，新 Job 首个死亡周期不判死。"""
    env_ids("100")
    _write_state(mtbf, tmp_path, {"job_id": "100", "dead_streak": 2, "seq": 9})
    _mtbf_fake_adb(mtbf, monkeypatch, service_alive=False)

    r = mtbf._run({})  # 新 Job（STP_JOB_ID=200 由 env_ids 在下一步设置前先改）
    assert r["success"] is False  # 仍属 Job 100：streak 2→3 判死

    env_ids("200")
    r2 = mtbf._run({})
    assert r2["success"] is True, "新 Job 首个死亡周期必须重新计（streak=1<2）"
    state = _read_state(mtbf)
    assert state["dead_streak"] == 1
    assert state["job_id"] == "200"
    assert state["seq"] == 1, "周期序号也随 Job 重置"


def test_mtbf_same_job_recovery_resets_streak(mtbf, env_ids, monkeypatch, tmp_path):
    """同 Job 内服务恢复 → 清零（既有防护，不得被本单破坏）。"""
    env_ids("100")
    _write_state(mtbf, tmp_path, {"job_id": "100", "dead_streak": 2})
    _mtbf_fake_adb(mtbf, monkeypatch, service_alive=True)

    r = mtbf._run({})
    assert r["success"] is True
    assert _read_state(mtbf)["dead_streak"] == 0


# ── gpu_check / sleep_check（同型轻量）────────────────────────────────────


def test_gpu_check_new_job_resets_dead_streak(_load_modules, env_ids, monkeypatch, tmp_path):
    mod = _load_modules["gpu_check_v106"]
    _write_state(mod, tmp_path, {"job_id": "100", "dead_streak": 2, "seq": 4})
    monkeypatch.setattr(mod, "instrument_alive", lambda: False)
    monkeypatch.setattr(mod, "_grep_rounds_done", lambda: 0)
    monkeypatch.setattr(mod, "result_log_bytes", lambda: 0)
    monkeypatch.setattr(mod, "_run_finished", lambda: (False, ""))

    env_ids("100")
    r1 = mod._run({})
    assert r1["success"] is False  # 旧 Job：streak 2→3

    env_ids("200")
    r2 = mod._run({})
    assert r2["success"] is True
    assert _read_state(mod)["dead_streak"] == 1
    assert _read_state(mod)["seq"] == 1


def test_sleep_check_new_job_resets_dead_streak(_load_modules, env_ids, monkeypatch, tmp_path):
    mod = _load_modules["sleep_check_v102"]
    _write_state(mod, tmp_path, {"job_id": "100", "dead_streak": 2, "seq": 4})
    monkeypatch.setattr(mod, "service_alive", lambda: False)
    monkeypatch.setattr(mod, "_read_prefs_progress", lambda: None)
    monkeypatch.setattr(mod, "_grep_cycle_count", lambda: 0)
    monkeypatch.setattr(mod, "_result_bytes", lambda: 0)
    monkeypatch.setattr(mod, "_run_finished", lambda: False)

    env_ids("100")
    r1 = mod._run({})
    assert r1["success"] is False

    env_ids("200")
    r2 = mod._run({})
    assert r2["success"] is True
    assert _read_state(mod)["dead_streak"] == 1
    assert _read_state(mod)["seq"] == 1


# ── powercycle_check（收取窗口标记等富状态随 Job 重置）─────────────────────


def test_powercycle_check_new_job_resets_collect_window(_load_modules, env_ids, monkeypatch, tmp_path):
    mod = _load_modules["powercycle_check_v107"]
    _write_state(mod, tmp_path, {
        "job_id": "100", "seq": 8,
        "collecting_done_for_window": True, "last_collected": 9,
        "last_online": True,
    })
    monkeypatch.setattr(mod, "device_online", lambda: False)  # 走最短离线路径

    env_ids("200")
    r = mod._run({})
    assert r["success"] is True
    state = _read_state(mod)
    assert state["job_id"] == "200"
    assert state["seq"] == 1, "周期序号随 Job 重置"
    assert "collecting_done_for_window" not in state, "收取窗口标记不得跨 Job 继承"
    assert "last_collected" not in state
