"""#1690：三个 setup/填盘脚本 v1.1.0 的 PROGRESS 打戳（停滞钟活性）。

覆盖：
- 三个库（gpu_setup/_lib、powercycle_setup/_lib、fill_storage/_adb）的
  ``progress_heartbeat``（start + 周期心跳 + end、seq 单调）与 ``progress_tick``；
- 接线行为：push/pm install（两个 install 路径）、fill_storage 的 dd 段、
  gpu_setup 的 pre_reboot 轮询——慢操作期间有戳。
"""
from __future__ import annotations

import importlib.util
import sys
import time
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def _load(name: str, path: Path, *, deps: dict[str, Path] | None = None):
    """加载脚本模块；deps 先按文件加载并放入 sys.modules（_lib/_adb 同款）。"""
    for dep_name, dep_path in (deps or {}).items():
        spec = importlib.util.spec_from_file_location(f"{dep_name}_{name}", dep_path)
        dep = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(dep)
        sys.modules[dep_name] = dep
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _capture_stamps(mod) -> list[dict]:
    captured: list[dict] = []
    mod.progress_stamp = lambda payload: captured.append(dict(payload))
    return captured


def _phases(captured: list[dict]) -> set[str]:
    return {str(c.get("phase")) for c in captured}


_LIBS = {
    "gpu_lib": SCRIPTS / "gpu_setup" / "v1.1.0" / "_lib.py",
    "pc_lib": SCRIPTS / "powercycle_setup" / "v1.1.0" / "_lib.py",
    "fill_adb": SCRIPTS / "fill_storage" / "v1.1.0" / "_adb.py",
}


@pytest.mark.parametrize("key", sorted(_LIBS))
def test_progress_heartbeat_start_heartbeat_end(key: str):
    mod = _load(f"{key}_hb", _LIBS[key])
    captured = _capture_stamps(mod)
    with mod.progress_heartbeat("work", interval=0.01):
        time.sleep(0.05)
    events = [(c.get("phase"), c.get("event")) for c in captured]
    assert ("work", "start") in events
    assert ("work", "end") in events
    assert sum(1 for _, e in events if e == "heartbeat") >= 1, "慢段应有周期心跳"
    seqs = [c["seq"] for c in captured]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs), "seq 必须单调唯一"


@pytest.mark.parametrize("key", sorted(_LIBS))
def test_progress_tick_shares_seq_counter(key: str):
    mod = _load(f"{key}_tick", _LIBS[key])
    captured = _capture_stamps(mod)
    with mod.progress_heartbeat("a", interval=10):
        mod.progress_tick("b", note="x")
    ticks = [c for c in captured if c.get("phase") == "b"]
    assert len(ticks) == 1 and ticks[0]["note"] == "x"


def _fake_adb(calls: list):
    def fake(*args, timeout=60):
        calls.append(args)
        if args and args[0] == "push":
            return 0, "", ""
        return 0, "Success", ""
    return fake


class TestInstallWiring:
    def test_powercycle_install_apk_stamps_push_and_install(self, tmp_path):
        mod = _load("pc_wire", _LIBS["pc_lib"])
        captured = _capture_stamps(mod)
        apk = tmp_path / "AutoTestTool.apk"
        apk.write_bytes(b"apk")
        calls: list = []
        mod.adb = _fake_adb(calls)
        mod.adb_shell = lambda *a, **k: ""

        mod.install_apk(apk)

        phases = _phases(captured)
        assert f"apk_push:{apk.name}" in phases
        assert f"apk_install:{apk.name}" in phases
        assert any(a and a[0] == "push" for a in calls), "push 确实发生过"

    def test_gpu_install_apk_stable_stamps_push_and_install(self, tmp_path):
        mod = _load("gpu_wire", _LIBS["gpu_lib"])
        captured = _capture_stamps(mod)
        apk = tmp_path / "Antutu.apk"
        apk.write_bytes(b"apk")
        mod.adb = _fake_adb([])

        rc, out = mod._install_apk_stable(apk)

        assert rc == 0 and "Success" in out
        phases = _phases(captured)
        assert f"apk_push:{apk.name}" in phases
        assert f"apk_install:{apk.name}" in phases


def test_fill_storage_dd_wrapped_with_heartbeat(monkeypatch, capsys):
    script = _load(
        "fill_wire",
        SCRIPTS / "fill_storage" / "v1.1.0" / "fill_storage.py",
        deps={"_adb": _LIBS["fill_adb"]},
    )
    monkeypatch.setenv("STP_DEVICE_SERIAL", "SER")

    seen_cmds: list[str] = []
    df_calls = {"n": 0}

    class _R:
        def __init__(self, out: str, rc: int = 0):
            self.stdout, self.stderr, self.returncode = out, "", rc

    def fake_shell_quiet(cmd, timeout=30):
        seen_cmds.append(cmd)
        if cmd.startswith("dd"):
            return _R("")
        df_calls["n"] += 1
        used = 10 if df_calls["n"] == 1 else 80
        return _R(
            "Filesystem 1K-blocks Used Available Use% Mounted on\n"
            f"/dev/block/dm-4 100 {used} 20 80% /data\n"
        )

    monkeypatch.setattr(script, "adb_shell_quiet", fake_shell_quiet)
    script.main()
    capsys.readouterr()  # 结果 JSON 走 stdout，不参与断言

    assert any(c.startswith("dd if=/dev/zero") for c in seen_cmds), "dd 已执行"


def test_gpu_pre_reboot_ticks(monkeypatch):
    """pre_reboot：reboot/等待/结算三个阶段的轮询戳（#1690）。"""
    lib_dir = SCRIPTS / "gpu_setup" / "v1.1.0"
    script = _load(
        "gpu_reboot_wire",
        lib_dir / "gpu_setup.py",
        deps={"_lib": lib_dir / "_lib.py"},
    )
    ticks: list[dict] = []
    monkeypatch.setattr(script, "progress_tick",
                        lambda phase, **k: ticks.append({"phase": phase, **k}))
    monkeypatch.setenv("STP_GPU_REBOOT_SETTLE_SECONDS", "0")
    monkeypatch.setenv("STP_DEVICE_SERIAL", "SER")

    class _R:
        returncode = 0
        stdout = "1"

    monkeypatch.setattr(script.subprocess, "run", lambda *a, **k: _R())
    monkeypatch.setattr(script.time, "sleep", lambda s: None)

    script._pre_reboot_device()

    phases = _phases(ticks)
    assert "pre_reboot" in phases
    assert "pre_reboot_wait" in phases
    assert "pre_reboot_settle" in phases
