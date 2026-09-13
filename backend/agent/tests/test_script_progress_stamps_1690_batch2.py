"""#1690 第二批：push_resources / monkey_resource_push / install_apk /
monkey_launch / clean_env 补 PROGRESS 打戳（停滞钟活性）。

覆盖：
- 五个 ``_adb.py`` 的 ``progress_heartbeat``（start + 周期心跳 + end、seq
  单调）与 ``progress_tick``；
- 接线行为：push_resources 的逐文件 / bundle push+unpack、monkey_resource_push
  的 ``_push``、install_apk 的 install、monkey_launch 的两个轮询、
  clean_env 的 uninstall / clear_logs——慢操作期间有戳；
- 真实链路：push_resources 走真实 ``progress_stamp`` 时 stderr 上是
  ``PROGRESS {"seq": N, ...}`` 且 seq 单调；
- 五个新版本目录的 capabilities.json 声明 ``progress_stamps``。
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import time
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"

_LIBS = {
    "push_adb": SCRIPTS / "push_resources" / "v1.1.0" / "_adb.py",
    "mrp_adb": SCRIPTS / "monkey_resource_push" / "v1.1.0" / "_adb.py",
    "install_adb": SCRIPTS / "install_apk" / "v1.1.0" / "_adb.py",
    "launch_adb": SCRIPTS / "monkey_launch" / "v5.1.0" / "_adb.py",
    "clean_adb": SCRIPTS / "clean_env" / "v1.1.0" / "_adb.py",
}

_CAPABILITY_DIRS = (
    "push_resources/v1.1.0",
    "monkey_resource_push/v1.1.0",
    "install_apk/v1.1.0",
    "monkey_launch/v5.1.0",
    "clean_env/v1.1.0",
)


def _load(name: str, path: Path, *, deps: dict[str, Path] | None = None):
    """加载脚本模块；deps 先按文件加载并放入 sys.modules（_adb 同款）。

    返回 (模块, {依赖名: 依赖模块})——依赖模块用于替换 ``progress_stamp``。
    """
    loaded: dict[str, object] = {}
    for dep_name, dep_path in (deps or {}).items():
        spec = importlib.util.spec_from_file_location(f"{dep_name}_{name}", dep_path)
        dep = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(dep)
        sys.modules[dep_name] = dep
        loaded[dep_name] = dep
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod, loaded


def _capture_stamps(mod) -> list[dict]:
    captured: list[dict] = []
    mod.progress_stamp = lambda payload: captured.append(dict(payload))
    return captured


def _phases(captured: list[dict]) -> set[str]:
    return {str(c.get("phase")) for c in captured}


def _main_load(name: str, script_rel: str, lib_key: str):
    script_path = SCRIPTS / script_rel
    return _load(name, script_path, deps={"_adb": _LIBS[lib_key]})


# ---------------------------------------------------------------------------
# 辅助函数：五个 _adb.py 同款行为
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("key", sorted(_LIBS))
def test_progress_heartbeat_start_heartbeat_end(key: str):
    mod, _ = _load(f"{key}_hb", _LIBS[key])
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
    mod, _ = _load(f"{key}_tick", _LIBS[key])
    captured = _capture_stamps(mod)
    with mod.progress_heartbeat("a", interval=10):
        mod.progress_tick("b", note="x")
    ticks = [c for c in captured if c.get("phase") == "b"]
    assert len(ticks) == 1 and ticks[0]["note"] == "x"


@pytest.mark.parametrize("rel", _CAPABILITY_DIRS)
def test_capabilities_declared(rel: str):
    payload = json.loads((SCRIPTS / rel / "capabilities.json").read_text(encoding="utf-8"))
    assert "progress_stamps" in payload.get("capabilities", [])


# ---------------------------------------------------------------------------
# 接线行为
# ---------------------------------------------------------------------------


class TestPushResourcesWiring:
    def test_files_mode_stamps_each_push(self, monkeypatch):
        script, deps = _main_load(
            "push_files_wire", "push_resources/v1.1.0/push_resources.py", "push_adb",
        )
        captured = _capture_stamps(deps["_adb"])
        pushed: list[tuple[str, str]] = []
        monkeypatch.setattr(script, "adb_push", lambda local, remote: pushed.append((local, remote)))
        monkeypatch.setattr(script, "adb_shell", lambda *a, **k: "")
        monkeypatch.setenv("STP_DEVICE_SERIAL", "SER")
        monkeypatch.setenv(
            "STP_STEP_PARAMS",
            json.dumps({"files": [{"local": "/nfs/a.bin", "remote": "/sdcard/a.bin"}]}),
        )

        script.main()

        assert "push:a.bin" in _phases(captured), "逐文件 push 段必须有戳"
        assert pushed and pushed[0][0] == "/nfs/a.bin", "push 确实发生过"

    def test_bundle_mode_stamps_push_and_unpack(self, monkeypatch, tmp_path):
        script, deps = _main_load(
            "push_bundle_wire", "push_resources/v1.1.0/push_resources.py", "push_adb",
        )
        captured = _capture_stamps(deps["_adb"])
        bundle = tmp_path / "bundle.tar.gz"
        bundle.write_bytes(b"bundle-data")
        digest = hashlib.sha256(bundle.read_bytes()).hexdigest()
        manifest = tmp_path / "manifest.json"
        manifest.write_text(json.dumps({"bundle_sha256": digest}), encoding="utf-8")

        class _R:
            returncode = 0
            stdout = digest
            stderr = ""

        pushed: list[tuple[str, str]] = []
        monkeypatch.setattr(script, "adb_push", lambda local, remote: pushed.append((local, remote)))
        monkeypatch.setattr(script, "adb_shell", lambda *a, **k: "")
        monkeypatch.setattr(script, "adb_shell_quiet", lambda *a, **k: _R())
        monkeypatch.setenv("STP_DEVICE_SERIAL", "SER")
        monkeypatch.setenv(
            "STP_STEP_PARAMS",
            json.dumps({
                "bundle": str(bundle),
                "manifest": str(manifest),
                "remote_dir": "/sdcard/res",
                "skip_if_match": False,
            }),
        )

        script.main()

        phases = _phases(captured)
        assert "bundle_push:bundle.tar.gz" in phases, "bundle push 段必须有戳"
        assert "bundle_unpack:bundle.tar.gz" in phases, "tar 解包段必须有戳"
        assert len(pushed) == 2, "bundle + manifest 两次 push"

    def test_real_stamp_reaches_stderr(self, monkeypatch, tmp_path, capsys):
        """真实链路（不替换 progress_stamp）：stderr 上是 PROGRESS JSON。"""
        script, _ = _main_load(
            "push_real_wire", "push_resources/v1.1.0/push_resources.py", "push_adb",
        )
        fake_adb = tmp_path / "adb"
        fake_adb.write_text("#!/usr/bin/env python3\nimport sys\nsys.exit(0)\n", encoding="utf-8")
        fake_adb.chmod(0o755)
        src = tmp_path / "real.bin"
        src.write_bytes(b"x")
        monkeypatch.setenv("STP_ADB_PATH", str(fake_adb))
        monkeypatch.setenv("STP_DEVICE_SERIAL", "SER")
        monkeypatch.setenv(
            "STP_STEP_PARAMS",
            json.dumps({"files": [{"local": str(src), "remote": "/sdcard/real.bin"}]}),
        )

        script.main()

        out = capsys.readouterr()
        stamps = [
            json.loads(line[len("PROGRESS "):])
            for line in out.err.splitlines()
            if line.startswith("PROGRESS ")
        ]
        assert stamps, f"stderr 必须有 PROGRESS 戳：{out.err!r}"
        assert {s.get("phase") for s in stamps} == {"push:real.bin"}
        seqs = [s["seq"] for s in stamps]
        assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)
        assert '"success": true' in out.out


class TestMonkeyResourcePushWiring:
    def test_push_wrapped_with_heartbeat(self, monkeypatch, tmp_path):
        script, deps = _main_load(
            "mrp_wire", "monkey_resource_push/v1.1.0/monkey_resource_push.py", "mrp_adb",
        )
        captured = _capture_stamps(deps["_adb"])
        local = tmp_path / "aim.jar"
        local.write_bytes(b"jar")
        calls: list[tuple] = []

        def fake_run_adb(serial, args, timeout=30):
            calls.append((serial, args, timeout))
            return 0, "", ""

        monkeypatch.setattr(script, "_run_adb", fake_run_adb)

        assert script._push("SER", str(local), "/data/local/tmp/aim.jar") is True

        assert "push:aim.jar" in _phases(captured)
        assert calls and calls[0][1][0] == "push"

    def test_missing_local_is_not_stamped(self, monkeypatch):
        script, deps = _main_load(
            "mrp_missing", "monkey_resource_push/v1.1.0/monkey_resource_push.py", "mrp_adb",
        )
        captured = _capture_stamps(deps["_adb"])
        assert script._push("SER", "/nonexistent/x.bin", "/data/local/tmp/x.bin") is False
        assert captured == [], "未发生 push 不应打戳"


class TestInstallApkWiring:
    def test_install_wrapped_with_heartbeat(self, monkeypatch, tmp_path):
        script, deps = _main_load(
            "install_wire", "install_apk/v1.1.0/install_apk.py", "install_adb",
        )
        captured = _capture_stamps(deps["_adb"])
        apk = tmp_path / "app.apk"
        apk.write_bytes(b"apk")

        class _R:
            returncode = 0
            stdout = "Success"
            stderr = ""

        monkeypatch.setattr(script.subprocess, "run", lambda *a, **k: _R())
        monkeypatch.setenv("STP_DEVICE_SERIAL", "SER")
        monkeypatch.setenv("STP_STEP_PARAMS", json.dumps({"apk_path": str(apk)}))

        script.main()

        assert "apk_install:app.apk" in _phases(captured)


class TestMonkeyLaunchWiring:
    @staticmethod
    def _patch_clock(monkeypatch, script, step: float = 0.5):
        clock = {"t": 1000.0}
        monkeypatch.setattr(script.time, "time", lambda: clock["t"])
        monkeypatch.setattr(
            script.time, "sleep", lambda s: clock.__setitem__("t", clock["t"] + step),
        )
        return clock

    def test_watchdog_wait_ticks(self, monkeypatch):
        script, _ = _main_load(
            "launch_wire", "monkey_launch/v5.1.0/monkey_launch.py", "launch_adb",
        )
        ticks: list[dict] = []
        monkeypatch.setattr(script, "progress_tick", lambda phase, **k: ticks.append({"phase": phase, **k}))
        monkeypatch.setattr(script, "_ps_grep", lambda *a, **k: False)
        monkeypatch.setattr(script, "_shell", lambda *a, **k: (0, ""))
        self._patch_clock(monkeypatch, script)
        monkeypatch.setenv("STP_DEVICE_SERIAL", "SER")
        monkeypatch.setenv("STP_STEP_PARAMS", json.dumps({"max_wait_seconds": 1}))

        with pytest.raises(SystemExit):
            script.main()

        assert "watchdog_wait" in _phases(ticks), "看门狗等待轮询必须逐次打戳"

    def test_aimwd_wait_ticks(self, monkeypatch):
        script, _ = _main_load(
            "launch_aimwd", "monkey_launch/v5.1.0/monkey_launch.py", "launch_adb",
        )
        ticks: list[dict] = []
        monkeypatch.setattr(script, "progress_tick", lambda phase, **k: ticks.append({"phase": phase, **k}))
        # 启动前检查未见 → 走启动链；启动后第一个轮询立即命中，
        # 第二个轮询（MonkeyWatchdog）等到 deadline 逐次打戳
        seen_watchdog = {"n": 0}

        def fake_ps_grep(serial, pattern, timeout=10):
            if "MonkeyWatchdog" in pattern:
                return False
            seen_watchdog["n"] += 1
            return seen_watchdog["n"] > 1

        monkeypatch.setattr(script, "_ps_grep", fake_ps_grep)
        monkeypatch.setattr(script, "_shell", lambda *a, **k: (0, ""))
        self._patch_clock(monkeypatch, script)
        monkeypatch.setenv("STP_DEVICE_SERIAL", "SER")
        monkeypatch.setenv("STP_STEP_PARAMS", json.dumps({"max_wait_seconds": 1}))

        with pytest.raises(SystemExit):
            script.main()

        assert "aimwd_wait" in _phases(ticks), "aimwd 等待轮询必须逐次打戳"


class TestCleanEnvWiring:
    def test_uninstall_and_clear_logs_stamped(self, monkeypatch):
        script, deps = _main_load(
            "clean_wire", "clean_env/v1.1.0/clean_env.py", "clean_adb",
        )
        captured = _capture_stamps(deps["_adb"])
        seen: list[str] = []

        class _R:
            def __init__(self, out: str = "", rc: int = 0):
                self.stdout, self.stderr, self.returncode = out, "", rc

        def fake_shell_quiet(cmd, timeout=30):
            seen.append(cmd)
            if cmd.startswith("pm uninstall"):
                return _R("Success")
            return _R("")

        monkeypatch.setattr(script, "adb_shell_quiet", fake_shell_quiet)
        monkeypatch.setenv("STP_DEVICE_SERIAL", "SER")
        monkeypatch.setenv(
            "STP_STEP_PARAMS",
            json.dumps({
                "uninstall_packages": ["com.example.app"],
                "clear_logs": True,
                "log_dirs": ["/data/aee_exp"],
            }),
        )

        script.main()

        phases = _phases(captured)
        assert "uninstall:com.example.app" in phases
        assert "clear_logs:/data/aee_exp" in phases
        assert any(c.startswith("pm uninstall") for c in seen)
