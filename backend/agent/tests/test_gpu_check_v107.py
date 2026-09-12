"""#831 —— gpu_check v1.0.7：崩溃循环提前判失败（未到 GPU_RUN_END 也检测）。

覆盖：
  - ``Process crashed`` / ``OK (0 tests)`` / 连续 rc<0 三类证据在无 END 时提前失败；
  - 中途单轮空跑后恢复（最后一个 OK N>0）与 healthy running 不误杀；
  - monitor 模式（protobuf only）无假阳性；
  - END 在场时仍走既有判定（不回归）；
  - ``crash_round_streak`` 参数覆盖与 0=关闭。

加载方式与状态文件 patch 对齐 test_check_state_per_job.py 先例。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
_V107 = _SCRIPTS / "gpu_check" / "v1.0.7"


def _load():
    lib_path = _V107 / "_lib.py"
    prev_lib = sys.modules.get("_lib")
    lib_spec = importlib.util.spec_from_file_location("_lib_gpu107", lib_path)
    lib_mod = importlib.util.module_from_spec(lib_spec)
    assert lib_spec and lib_spec.loader
    lib_spec.loader.exec_module(lib_mod)
    sys.modules["_lib"] = lib_mod
    try:
        spec = importlib.util.spec_from_file_location(
            "gpu_check_v107", _V107 / "gpu_check.py",
        )
        mod = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(mod)
    finally:
        if prev_lib is not None:
            sys.modules["_lib"] = prev_lib
        else:
            sys.modules.pop("_lib", None)
    return mod


@pytest.fixture
def gc(monkeypatch, tmp_path):
    mod = _load()
    # 状态文件指到 tmp_path（不触真实 /tmp）；job 身份固定
    state_file = tmp_path / "gpu107_state.json"
    state_file.write_text("{}", encoding="utf-8")
    mod._state_file = lambda: state_file
    stamps: list = []
    monkeypatch.setattr(mod, "progress_stamp", lambda payload: stamps.append(payload))
    monkeypatch.setattr(mod, "instrument_alive", lambda: True)
    monkeypatch.setattr(mod, "_grep_rounds_done", lambda: 0)
    monkeypatch.setattr(mod, "result_log_bytes", lambda: 0)
    return mod


def _wire(gc, monkeypatch, log: bytes, *, alive=True, rounds=0, streak=None):
    monkeypatch.setattr(gc, "instrument_alive", lambda: alive)
    monkeypatch.setattr(gc, "_grep_rounds_done", lambda: rounds)
    monkeypatch.setattr(gc, "result_log_bytes", lambda: len(log))
    monkeypatch.setattr(gc, "_read_log_cat", lambda: log)
    overrides = {}
    if streak is not None:
        overrides["crash_round_streak"] = str(streak)
    monkeypatch.setattr(
        gc, "param_or_env",
        lambda cfg, name, env, default: overrides.get(name, default),
    )


# ── 三类证据：无 END 提前失败 ─────────────────────────────────────────────


def test_process_crashed_without_end_fails_early(gc, monkeypatch):
    log = (
        b"GPU_RUN_START test_id=001 rounds=700\n"
        b"GPU_ROUND 1 rc=-3\n"
        b"shortMsg\x12\x10Process crashed.\n"
    )
    _wire(gc, monkeypatch, log, rounds=1)
    r = gc._run({})
    assert r["success"] is False
    assert "提前判定" in r["error_message"]
    assert r["progress"]["early_crash"] == "crashed"


def test_no_tests_without_end_fails_early(gc, monkeypatch):
    log = b"GPU_RUN_START test_id=001 rounds=700\nOK (0 tests)\nGPU_ROUND 1 rc=0\n"
    _wire(gc, monkeypatch, log, rounds=1)
    r = gc._run({})
    assert r["success"] is False
    assert "空跑" in r["error_message"]
    assert r["progress"]["early_crash"] == "no-tests"


def test_round_crash_streak_fails_early(gc, monkeypatch):
    log = (
        b"GPU_RUN_START test_id=001 rounds=700\n"
        b"GPU_ROUND 1 rc=-3\nGPU_ROUND 2 rc=-3\nGPU_ROUND 3 rc=-1\n"
    )
    _wire(gc, monkeypatch, log, rounds=3)
    r = gc._run({})
    assert r["success"] is False
    assert "连续 3 轮" in r["error_message"]
    assert r["progress"]["early_crash"] == "round-crash-loop"


# ── 防误杀 ────────────────────────────────────────────────────────────────


def test_last_ok_nonzero_after_transient_zero_not_failed(gc, monkeypatch):
    """中途一轮 0 tests 后恢复正常：取最后一个 OK 计数 → 不提前判失败。"""
    log = b"OK (0 tests)\nGPU_ROUND 1 rc=-1\nOK (3 tests)\nGPU_ROUND 2 rc=0\n"
    _wire(gc, monkeypatch, log, rounds=2)
    r = gc._run({})
    assert r["success"] is True


def test_round_crash_below_streak_not_failed(gc, monkeypatch):
    log = b"GPU_ROUND 1 rc=-3\nGPU_ROUND 2 rc=-3\n"
    _wire(gc, monkeypatch, log, rounds=2)
    r = gc._run({})
    assert r["success"] is True
    assert r["progress"]["run_finished"] is False


def test_healthy_running_no_false_positive(gc, monkeypatch):
    log = b"GPU_RUN_START test_id=001 rounds=700\nGPU_ROUND 1 rc=0\nGPU_ROUND 2 rc=0\n"
    _wire(gc, monkeypatch, log, rounds=2)
    r = gc._run({})
    assert r["success"] is True
    assert "early_crash" not in r.get("progress", {})


def test_monitor_mode_running_no_false_positive(gc, monkeypatch):
    """-m monitor 模式只有 protobuf，无 OK 文本 → 不误判空跑。"""
    log = (
        b"GPU_RUN_START test_id=002 rounds=700\n"
        b"\x00\x01\x18\x02\"\x01\x01test_result\x12\x05false\n"
        b"GPU_ROUND 1 rc=0\n"
    )
    _wire(gc, monkeypatch, log, rounds=1)
    r = gc._run({})
    assert r["success"] is True


# ── END 判定路径不回归 / 参数 ─────────────────────────────────────────────


def test_end_present_uses_existing_verdict_path(gc, monkeypatch):
    """END 在场时走既有 crashed 判定，不用提前判定文案。"""
    log = b"GPU_RUN_START test_id=001 rounds=1\nProcess crashed.\nGPU_RUN_END rc=1\n"
    _wire(gc, monkeypatch, log, rounds=1)
    r = gc._run({})
    assert r["success"] is False
    assert "提前判定" not in r["error_message"]


def test_streak_param_override(gc, monkeypatch):
    log = b"GPU_ROUND 1 rc=-3\nGPU_ROUND 2 rc=-3\n"
    _wire(gc, monkeypatch, log, rounds=2, streak=2)
    r = gc._run({})
    assert r["success"] is False
    assert "连续 2 轮" in r["error_message"]


def test_streak_zero_disables_rc_signal(gc):
    log = b"GPU_ROUND 1 rc=-1\nGPU_ROUND 2 rc=-1\nGPU_ROUND 3 rc=-1\n"
    assert gc._early_crash_verdict(log, rc_streak=0) is None
