# -*- coding: utf-8 -*-
"""#894 teardown 清理完整化回归——monkey_teardown v1.0.2 / gpu_finish v1.0.4。

覆盖：默认开启的设备端资源删除、逐项回读验证（残留/探测不可用均报错）、
显式 opt-out、aimwd 停测清单、gpu 只删循环脚本保留 test_log。
加载方式同 test_teardown_rc_guards.py / test_gpu_scripts.py。
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from subprocess import CompletedProcess

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def _load(name: str, rel_path: str, helper: str):
    path = _SCRIPTS / rel_path
    sys.path.insert(0, str(path.parent))
    try:
        sys.modules.pop(helper, None)
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec and spec.loader, f"cannot locate {path}"
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        sys.modules.pop(helper, None)
        sys.path.remove(str(path.parent))


class _Output:
    def __init__(self) -> None:
        self.payloads: list[dict] = []

    def __call__(self, success, **kwargs) -> None:
        self.payloads.append({"success": success, **kwargs})

    @property
    def last(self) -> dict:
        assert self.payloads, "output_result was not called"
        return self.payloads[-1]


def _cp(returncode: int = 0, stdout: str = "", stderr: str = "") -> CompletedProcess:
    return CompletedProcess(args=["adb"], returncode=returncode, stdout=stdout, stderr=stderr)


# ── monkey_teardown v1.0.2 ───────────────────────────────────────────────────


def _load_mt():
    return _load("monkey_teardown_v102", "monkey_teardown/v1.0.2/monkey_teardown.py", "_adb")


def _prep_mt(monkeypatch, mod, tmp_path, *, step_params=None, probe_out="", probe_rc=0):
    capture = _Output()
    seen: list[str] = []
    kill_calls: list[list[str]] = []

    monkeypatch.setattr(mod, "device_serial", lambda: "SERIAL")
    monkeypatch.setattr(mod, "params", lambda: dict(step_params or {}))
    monkeypatch.setattr(mod, "output_result", capture)
    monkeypatch.setattr(mod.time, "sleep", lambda _s: None)

    def responder(cmd, timeout=10):
        seen.append(cmd)
        if cmd == "echo ready":
            return _cp(0)
        if cmd.startswith("rm -rf"):
            return _cp(0)
        if cmd.startswith("for p in"):
            return _cp(probe_rc, probe_out)
        return _cp(0)

    monkeypatch.setattr(mod, "adb_shell_quiet", responder)
    monkeypatch.setattr(
        mod,
        "_kill_processes",
        lambda serial, names: kill_calls.append(list(names)) or {"killed": [], "errors": []},
    )
    monkeypatch.setattr(mod, "_run_adb", lambda serial, args, timeout=60: 0)
    monkeypatch.setenv("STP_LOG_DIR", str(tmp_path))
    return capture, seen, kill_calls


def test_mt_cleanup_default_on_with_verification(monkeypatch, tmp_path):
    """默认（params 空）即删除全部推送物，且回读验证通过。"""
    mod = _load_mt()
    capture, seen, _ = _prep_mt(monkeypatch, mod, tmp_path)

    mod.main()

    rm = [c for c in seen if c.startswith("rm -rf")]
    assert len(rm) == 1
    for path in (
        "/data/local/tmp/MonkeyTest.sh",
        "/data/local/tmp/aim",
        "/data/local/tmp/aimwd",
        "/data/local/tmp/aim.jar",
        "/data/local/tmp/monkey.apk",
        "/data/local/tmp/arm64-v8a",
        "/data/local/tmp/armeabi-v7a",
        "/sdcard/blacklist.txt",
    ):
        assert path in rm[0], f"{path} 不在清理清单"
    assert any(c.startswith("for p in") for c in seen), "缺少回读验证探测"
    assert capture.last["success"] is True
    assert capture.last["metrics"]["cleanup_removed_count"] == 9
    assert capture.last["metrics"]["cleanup_remaining_count"] == 0


def test_mt_cleanup_residual_reports_failure(monkeypatch, tmp_path):
    """回读发现残留 → step 失败（不静默放行）。"""
    mod = _load_mt()
    capture, _, _ = _prep_mt(
        monkeypatch, mod, tmp_path, probe_out="REMAINS:/data/local/tmp/aim\n"
    )

    mod.main()

    assert capture.last["success"] is False
    assert "cleanup 后仍存在" in capture.last["error_message"]
    assert "/data/local/tmp/aim" in capture.last["error_message"]
    assert capture.last["metrics"]["cleanup_remaining_count"] == 1


def test_mt_cleanup_probe_unavailable_reports_failure(monkeypatch, tmp_path):
    """探测命令 rc 非零（无法确认删除结果）→ step 失败。"""
    mod = _load_mt()
    capture, _, _ = _prep_mt(monkeypatch, mod, tmp_path, probe_rc=1)

    mod.main()

    assert capture.last["success"] is False
    assert "无法确认删除结果" in capture.last["error_message"]


def test_mt_cleanup_probe_command_is_rc_neutral(monkeypatch, tmp_path):
    """探测命令必须以 true 收尾。

    设备端 for 循环的退出码取最后一次命令结果：最后一项不存在时 `[ -e ]`
    返回非零，会把「清理成功」误判为「探测不可用」。此处锁住命令形态。
    """
    import shlex
    import subprocess

    mod = _load_mt()
    _, seen, _ = _prep_mt(monkeypatch, mod, tmp_path)

    mod.main()

    probe = [c for c in seen if c.startswith("for p in")]
    assert probe, "缺少回读验证探测"
    # 在本地 shell 实跑同款命令（最后一项必然不存在）→ rc 必须为 0
    rc = subprocess.run(["sh", "-c", probe[0]], capture_output=True, text=True).returncode
    assert rc == 0, f"探测命令 rc={rc}（{shlex.quote(probe[0])}）"


def test_mt_cleanup_opt_out(monkeypatch, tmp_path):
    """显式 cleanup=false：不删设备端资源，也不验证。"""
    mod = _load_mt()
    capture, seen, _ = _prep_mt(monkeypatch, mod, tmp_path, step_params={"cleanup": False})

    mod.main()

    assert not [c for c in seen if c.startswith("rm -rf")]
    assert not [c for c in seen if c.startswith("for p in")]
    assert capture.last["success"] is True
    assert capture.last["metrics"]["cleanup_removed_count"] == 0


def test_mt_custom_cleanup_paths(monkeypatch, tmp_path):
    mod = _load_mt()
    capture, seen, _ = _prep_mt(
        monkeypatch, mod, tmp_path, step_params={"cleanup_paths": ["/data/local/tmp/aimwd"]}
    )

    mod.main()

    rm = [c for c in seen if c.startswith("rm -rf")]
    assert rm == ["rm -rf /data/local/tmp/aimwd"]
    assert capture.last["metrics"]["cleanup_removed_count"] == 1


def test_mt_aimwd_in_default_stop_list(monkeypatch, tmp_path):
    """v1.0.2：删脚本前先停 aimwd 看门狗进程。"""
    mod = _load_mt()
    _, _, kill_calls = _prep_mt(monkeypatch, mod, tmp_path)

    mod.main()

    assert kill_calls, "_kill_processes 未被调用"
    assert "/data/local/tmp/aimwd" in kill_calls[0]


# ── gpu_finish v1.0.4 ───────────────────────────────────────────────────────


def _load_gf():
    return _load("gpu_finish_v104", "gpu_finish/v1.0.4/gpu_finish.py", "_lib")


def _prep_gf(monkeypatch, mod, tmp_path, *, shell_probe="CLEAN", step_params=None):
    seen: list[str] = []

    def responder(cmd, timeout=30):
        seen.append(cmd)
        if cmd.startswith("[ -e"):
            return shell_probe
        return ""

    monkeypatch.setattr(mod, "device_serial", lambda: "GPU-S9")
    monkeypatch.setattr(mod, "stop_stress", lambda: None)
    monkeypatch.setattr(mod.time, "sleep", lambda _s: None)
    monkeypatch.setattr(mod, "adb_shell", responder)

    def fake_pull():
        local = tmp_path / "test_log.txt"
        local.write_text(
            "GPU_RUN_START test_id=001 rounds=1\nGPU_ROUND 1 rc=0\nGPU_RUN_END rc=0\n",
            encoding="utf-8",
        )
        return local

    monkeypatch.setattr(mod, "_pull_result_log", fake_pull)
    monkeypatch.setattr(mod, "results_dir", lambda project: tmp_path / "r")
    return seen, dict(step_params or {})


def test_gf_cleanup_device_script_after_result(monkeypatch, tmp_path):
    """结果 JSON 落盘后删循环脚本，metrics 标记已验证。"""
    mod = _load_gf()
    seen, cfg = _prep_gf(monkeypatch, mod, tmp_path)

    out = mod._run(cfg)

    assert any(c.startswith("rm -f /sdcard/Auto/gpu_stress_loop.sh") for c in seen)
    assert any(c.startswith("[ -e /sdcard/Auto/gpu_stress_loop.sh") for c in seen)
    assert out["metrics"]["cleanup_verified"] is True
    detail = Path(out["detail_uri"])
    assert detail.is_file()
    assert json.loads(detail.read_text(encoding="utf-8"))["metrics"]["final_status"] == "COMPLETED"


def test_gf_cleanup_keeps_test_log(monkeypatch, tmp_path):
    """只删脚本：rm 命令不得涉及 test_log.txt。"""
    mod = _load_gf()
    seen, cfg = _prep_gf(monkeypatch, mod, tmp_path)

    mod._run(cfg)

    rm = [c for c in seen if c.startswith("rm -f")]
    assert rm and all("test_log.txt" not in c for c in rm)


def test_gf_cleanup_residual_raises(monkeypatch, tmp_path):
    mod = _load_gf()
    seen, cfg = _prep_gf(monkeypatch, mod, tmp_path, shell_probe="REMAINS")

    with pytest.raises(RuntimeError) as ei:
        mod._run(cfg)

    assert "gpu_stress_loop.sh" in str(ei.value)


def test_gf_cleanup_opt_out(monkeypatch, tmp_path):
    mod = _load_gf()
    seen, cfg = _prep_gf(monkeypatch, mod, tmp_path, step_params={"cleanup": False})

    out = mod._run(cfg)

    assert not [c for c in seen if c.startswith("rm -f")]
    assert "cleanup_verified" not in out["metrics"]


# ── gpu_finish v1.0.5（#2146：清理验证「探测不可用」态）───────────────────────


def _load_gf_v105():
    return _load("gpu_finish_v105", "gpu_finish/v1.0.5/gpu_finish.py", "_lib")


def _fake_adb(*, rm_rc=0, probe_rc=0, probe_out="CLEAN"):
    """伪造 `_lib.adb(*args) -> (rc, stdout, stderr)`，按命令分流。"""
    calls: list[str] = []

    def _adb(*args, timeout=60, **_kw):
        cmd = args[1] if len(args) > 1 else ""
        calls.append(cmd)
        if cmd.startswith("rm -f"):
            return (rm_rc, "", "" if rm_rc == 0 else "rm: permission denied")
        return (probe_rc, probe_out, "" if probe_rc == 0 else "adb: device offline")

    return _adb, calls


def test_gf_v105_cleanup_ok(monkeypatch):
    mod = _load_gf_v105()
    fake, calls = _fake_adb()
    monkeypatch.setattr(mod, "adb", fake)

    mod._cleanup_device_script()      # 不抛即成功

    assert len(calls) == 2 and calls[0].startswith("rm -f") and "[ -e" in calls[1]


def test_gf_v105_rm_rc_failure_raises(monkeypatch):
    mod = _load_gf_v105()
    fake, _ = _fake_adb(rm_rc=1)
    monkeypatch.setattr(mod, "adb", fake)

    with pytest.raises(RuntimeError) as ei:
        mod._cleanup_device_script()

    assert "清理命令失败" in str(ei.value)


def test_gf_v105_probe_rc_failure_raises(monkeypatch):
    """探测不可用（设备离线/超时）必须转红——v1.0.4 的静默假绿回归点。"""
    mod = _load_gf_v105()
    fake, _ = _fake_adb(probe_rc=1)
    monkeypatch.setattr(mod, "adb", fake)

    with pytest.raises(RuntimeError) as ei:
        mod._cleanup_device_script()

    assert "清理验证不可用" in str(ei.value)


def test_gf_v105_empty_probe_output_raises(monkeypatch):
    """rc=0 但输出为空（命令未真正执行）同样不得当作「干净」。"""
    mod = _load_gf_v105()
    fake, _ = _fake_adb(probe_out="")
    monkeypatch.setattr(mod, "adb", fake)

    with pytest.raises(RuntimeError) as ei:
        mod._cleanup_device_script()

    assert "清理验证输出异常" in str(ei.value)


def test_gf_v105_residual_raises(monkeypatch):
    mod = _load_gf_v105()
    fake, _ = _fake_adb(probe_out="REMAINS")
    monkeypatch.setattr(mod, "adb", fake)

    with pytest.raises(RuntimeError) as ei:
        mod._cleanup_device_script()

    assert "仍存在" in str(ei.value)


def test_gf_v105_run_marks_verified_and_writes_detail(monkeypatch, tmp_path):
    """正常路径仍走完整 _run（结果落盘 + cleanup_verified）。"""
    mod = _load_gf_v105()
    fake, _ = _fake_adb()
    monkeypatch.setattr(mod, "adb", fake)
    monkeypatch.setattr(mod, "device_serial", lambda: "GPU-S9")
    monkeypatch.setattr(mod, "stop_stress", lambda: None)
    monkeypatch.setattr(mod.time, "sleep", lambda _s: None)

    def fake_pull():
        local = tmp_path / "test_log.txt"
        local.write_text(
            "GPU_RUN_START test_id=001 rounds=1\nGPU_ROUND 1 rc=0\nGPU_RUN_END rc=0\n",
            encoding="utf-8",
        )
        return local

    monkeypatch.setattr(mod, "_pull_result_log", fake_pull)
    monkeypatch.setattr(mod, "results_dir", lambda project: tmp_path / "r")
    out = mod._run({})

    assert out["metrics"]["cleanup_verified"] is True
    assert out["metrics"]["final_status"] == "COMPLETED"
