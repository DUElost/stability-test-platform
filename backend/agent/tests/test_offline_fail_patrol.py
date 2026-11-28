# -*- coding: utf-8 -*-
"""休眠唤醒（sleep_check@1.0.7）与 Monkey（monkey_check@2.0.4）连续长时间 ADB 离线判 fail 测试。

验证：
- 短暂离线（< max_offline_seconds，默认 1800s）保留既有瞬时容错语义，不发 ``patrol_fail``；
- 连续离线达到阈值时返回 ``success=False`` 且 ``patrol_fail=True``，触发引擎提前终结 Job 为 ``FAILED``；
- 恢复在线后自动清零离线计时；
- ``max_offline_seconds=0`` 可显式关闭离线判死；
- 不同 ``STP_JOB_ID`` 互不继承离线计时。
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
_SCRIPTS = REPO_ROOT / "backend" / "agent" / "scripts"


def _load(name: str, rel_path: str, helper_mod: str):
    path = _SCRIPTS / rel_path
    sys.path.insert(0, str(path.parent))
    try:
        sys.modules.pop(helper_mod, None)
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec and spec.loader, f"cannot locate {path}"
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        sys.modules.pop(helper_mod, None)
        sys.path.remove(str(path.parent))


class _Clock:
    def __init__(self, start: float = 1_700_000_000.0) -> None:
        self.t = start

    def time(self) -> float:
        return self.t

    def monotonic(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.t += max(0.0, float(seconds))


# ---------------------------------------------------------------------------
# sleep_check@1.0.7
# ---------------------------------------------------------------------------


def _patch_sleep_check(mod, monkeypatch, tmp_path, *, clock: _Clock, online_ref: dict):
    monkeypatch.setenv("STP_JOB_ID", online_ref.get("job_id", "job-sleep-1"))
    monkeypatch.delenv("STP_SLEEP_MAX_OFFLINE_SECONDS", raising=False)
    monkeypatch.setattr(mod, "time", clock)
    monkeypatch.setattr(mod, "device_serial", lambda: "SER-SLEEP-1")
    monkeypatch.setattr(mod, "_state_file", lambda: tmp_path / "sleep_state.json")
    monkeypatch.setattr(mod, "service_alive", lambda: online_ref["alive"])
    monkeypatch.setattr(mod, "device_online", lambda: online_ref["online"])
    monkeypatch.setattr(mod, "_read_prefs_progress", lambda: (5, 100) if online_ref["online"] else None)
    monkeypatch.setattr(mod, "_grep_cycle_count", lambda: 0)
    monkeypatch.setattr(mod, "_result_bytes", lambda: 128 if online_ref["online"] else 0)
    monkeypatch.setattr(mod, "_run_finished", lambda: False)
    monkeypatch.setattr(mod, "progress_stamp", lambda payload: None)


def test_sleep_check_transient_offline_then_prolonged_emits_patrol_fail(monkeypatch, tmp_path):
    mod = _load("sleep_check_offline_fail", "sleep_check/sleep_check.py", "_lib")
    clock = _Clock(1_000_000.0)
    ref = {"online": False, "alive": False, "job_id": "job-s1"}
    _patch_sleep_check(mod, monkeypatch, tmp_path, clock=clock, online_ref=ref)

    # T=0: 首次离线 → 短暂容忍（不判死）
    r1 = mod._run({})
    assert r1["success"] is True
    assert "patrol_fail" not in r1
    assert r1["progress"]["online"] is False
    assert r1["progress"]["offline_elapsed_seconds"] == 0

    # T=+1799s: 仍未达 1800s 阈值 → 继续放行
    clock.t += 1799.0
    r2 = mod._run({})
    assert r2["success"] is True
    assert r2["progress"]["offline_elapsed_seconds"] == 1799

    # T=+1800s: 达到 30 分钟阈值 → 判 fail 且带 patrol_fail=True
    clock.t += 1.0
    r3 = mod._run({})
    assert r3["success"] is False
    assert r3["patrol_fail"] is True
    assert "1800s" in r3["error_message"]


def test_sleep_check_recovery_and_zero_disable_and_job_isolation(monkeypatch, tmp_path):
    mod = _load("sleep_check_offline_reset", "sleep_check/sleep_check.py", "_lib")
    clock = _Clock(2_000_000.0)
    ref = {"online": False, "alive": False, "job_id": "job-s2"}
    _patch_sleep_check(mod, monkeypatch, tmp_path, clock=clock, online_ref=ref)

    assert mod._run({})["success"] is True
    clock.t += 1200.0

    # 恢复在线 → 清零 offline_since
    ref["online"] = True
    ref["alive"] = True
    assert mod._run({})["success"] is True

    # 再次离线 1000s → 重新从 0 计时，不触发 1800s 阈值
    ref["online"] = False
    ref["alive"] = False
    assert mod._run({})["success"] is True
    clock.t += 1000.0
    r_mid = mod._run({})
    assert r_mid["success"] is True
    assert r_mid["progress"]["offline_elapsed_seconds"] == 1000

    # max_offline_seconds=0 → 即使超过 2 小时也不触发 patrol_fail
    clock.t += 7200.0
    r_off = mod._run({"max_offline_seconds": 0})
    assert r_off["success"] is True

    # 新 Job ID → 隔离旧离线计时，首拍从 0 开始
    monkeypatch.setenv("STP_JOB_ID", "job-s3")
    r_new = mod._run({})
    assert r_new["success"] is True
    assert r_new["progress"]["offline_elapsed_seconds"] == 0


# ---------------------------------------------------------------------------
# monkey_check@2.0.4
# ---------------------------------------------------------------------------


def _run_monkey_main(mod, monkeypatch, tmp_path, *, clock: _Clock, reachable: bool, cfg: dict | None = None, job_id: str = "job-m1"):
    captured: list[dict] = []
    monkeypatch.setenv("STP_JOB_ID", job_id)
    monkeypatch.delenv("STP_MONKEY_MAX_OFFLINE_SECONDS", raising=False)
    monkeypatch.setattr(mod, "time", clock)
    monkeypatch.setattr(mod, "device_serial", lambda: "SER-MONKEY-1")
    monkeypatch.setattr(mod, "_state_file", lambda serial: tmp_path / f"monkey_{serial}.json")
    monkeypatch.setattr(mod, "params", lambda: dict(cfg or {}))
    monkeypatch.setattr(
        mod,
        "output_result",
        lambda success, **kw: captured.append({"success": success, **kw}),
    )

    def fake_shell(serial, cmd, timeout=30):
        if cmd == "echo ready":
            return (0, "ready") if reachable else (1, "")
        if "MonkeyTest.sh" in cmd:
            return (0, "root 100 1 sh MonkeyTest.sh")
        if "com.android.commands.monkey" in cmd:
            return (0, "root 101 1 com.android.commands.monkey")
        return (0, "")

    monkeypatch.setattr(mod, "_shell", fake_shell)

    exit_code = 0
    try:
        mod.main()
    except SystemExit as exc:
        exit_code = int(exc.code) if isinstance(exc.code, int) else 1
    assert len(captured) == 1
    return exit_code, captured[0]


def test_monkey_check_prolonged_offline_emits_patrol_fail(monkeypatch, tmp_path):
    mod = _load("monkey_check_offline_fail", "monkey_check/monkey_check.py", "_adb")
    clock = _Clock(3_000_000.0)

    # T=0: 首次不可达 → 常规失败，不带 patrol_fail
    rc1, out1 = _run_monkey_main(mod, monkeypatch, tmp_path, clock=clock, reachable=False)
    assert rc1 == 1
    assert out1["success"] is False
    assert out1.get("patrol_fail") is not True
    assert out1["metrics"]["offline_elapsed_seconds"] == 0

    # T=+1799s: 未到阈值 → 仍不带 patrol_fail
    clock.t += 1799.0
    rc2, out2 = _run_monkey_main(mod, monkeypatch, tmp_path, clock=clock, reachable=False)
    assert rc2 == 1
    assert out2.get("patrol_fail") is not True
    assert out2["metrics"]["offline_elapsed_seconds"] == 1799

    # T=+1800s: 达到阈值 → 带 patrol_fail=True
    clock.t += 1.0
    rc3, out3 = _run_monkey_main(mod, monkeypatch, tmp_path, clock=clock, reachable=False)
    assert rc3 == 1
    assert out3["success"] is False
    assert out3["patrol_fail"] is True
    assert "exceeded 1800s" in out3["error_message"]


def test_monkey_check_recovery_resets_offline_timer_and_zero_disables(monkeypatch, tmp_path):
    mod = _load("monkey_check_offline_reset", "monkey_check/monkey_check.py", "_adb")
    clock = _Clock(4_000_000.0)

    _run_monkey_main(mod, monkeypatch, tmp_path, clock=clock, reachable=False)
    clock.t += 1500.0

    # 恢复在线 → 清零计时
    rc_ok, out_ok = _run_monkey_main(mod, monkeypatch, tmp_path, clock=clock, reachable=True)
    assert rc_ok == 0
    assert out_ok["success"] is True

    # 再次离线 600s → 重新计时
    _run_monkey_main(mod, monkeypatch, tmp_path, clock=clock, reachable=False)
    clock.t += 600.0
    rc_mid, out_mid = _run_monkey_main(mod, monkeypatch, tmp_path, clock=clock, reachable=False)
    assert rc_mid == 1
    assert out_mid.get("patrol_fail") is not True
    assert out_mid["metrics"]["offline_elapsed_seconds"] == 600

    # max_offline_seconds=0 → 即使超 2h 也不发 patrol_fail
    clock.t += 7200.0
    rc_dis, out_dis = _run_monkey_main(
        mod, monkeypatch, tmp_path, clock=clock, reachable=False, cfg={"max_offline_seconds": 0}
    )
    assert rc_dis == 1
    assert out_dis.get("patrol_fail") is not True
    # 确认输出可 JSON 序列化
    assert json.dumps(out_dis)
