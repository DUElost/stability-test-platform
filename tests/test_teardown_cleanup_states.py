# -*- coding: utf-8 -*-
"""#2162：设备端清理三态夹具的单测（纯离线，注入 fake 设备与 fake 被测模块）。

关键设计：fake 被测模块**按真实实现的方式**调用（`adb_shell_quiet` / `_lib.adb`），
因此 ③ 态的 serial 替换机制（`_install_probe_serial_swap`）在离线也在被测路径上。
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "teardown_cleanup_states", REPO_ROOT / "tools" / "dev" / "teardown_cleanup_states.py"
)
assert _spec and _spec.loader
tsc = importlib.util.module_from_spec(_spec)
sys.modules["teardown_cleanup_states"] = tsc
_spec.loader.exec_module(tsc)


# ── fake 设备 ────────────────────────────────────────────────────────────────

class FakeDevice(tsc.Device):
    """模拟设备：文件集合 + 可开关的「重建循环」+ rc 语义由 fake 模块消费。"""

    def __init__(self, serial: str = "FAKE", *, recreate: bool = True, busy: bool = False) -> None:
        super().__init__(serial)
        self.files: set[str] = set()
        self.loop = False
        self.loop_path = ""
        self.recreate = recreate
        self.busy = busy
        self.shells: list[str] = []

    # ── 覆盖 adb 交互 ──
    def connected(self) -> bool:  # noqa: D102
        return True

    def busy_conflicts(self) -> list[str]:  # noqa: D102
        return ["fake monkey proc"] if self.busy else []

    def shell(self, command: str, *, timeout=None):  # noqa: D102
        self.shells.append(command)
        if command.startswith("pkill"):
            self.loop = False
        return 0, "", ""

    def exists(self, path: str) -> bool:  # noqa: D102
        if self.loop and self.recreate and path == self.loop_path:
            self.files.add(path)          # 后台循环重建
        return path in self.files

    def touch(self, path: str) -> None:  # noqa: D102
        self.files.add(path)

    def remove(self, path: str) -> None:  # noqa: D102
        self.files.discard(path)

    def start_recreate_loop(self, path: str) -> None:  # noqa: D102
        self.loop = True
        self.loop_path = path

    def stop_recreate_loop(self) -> None:  # noqa: D102
        self.loop = False

    def loop_alive(self) -> bool:  # noqa: D102
        return self.loop


# ── fake 被测模块（按真实实现方式消费 adb） ──────────────────────────────────

class FakeMonkeyModule:
    """main 入口形态：rm + 探测都走被替换的 adb_shell_quiet。"""

    def __init__(self, device: FakeDevice, spec: tsc.Spec) -> None:
        self.device = device
        self.spec = spec
        self.output_result = lambda *a, **k: None

    def adb_shell_quiet(self, cmd: str, timeout: int = 10):
        if os.environ.get("STP_DEVICE_SERIAL") == tsc.BOGUS_SERIAL:
            return SimpleNamespace(returncode=1, stdout="", stderr="device not found")
        if cmd.startswith("rm -rf"):
            for p in self.spec.paths:
                self.device.remove(p)
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if cmd.startswith("for p in"):
            remains = [p for p in self.spec.paths if self.device.exists(p)]
            return SimpleNamespace(returncode=0,
                                   stdout="".join(f"REMAINS:{p}\n" for p in remains), stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    def main(self) -> None:
        self.adb_shell_quiet(f"rm -rf {' '.join(self.spec.paths)}")
        probe = self.adb_shell_quiet("for p in ...; do ...; done; true")
        errors = []
        if probe.returncode != 0:
            errors.append(f"cleanup verify rc={probe.returncode}（无法确认删除结果）")
        else:
            for line in probe.stdout.splitlines():
                if line.startswith("REMAINS:"):
                    errors.append(f"cleanup 后仍存在: {line.split(':', 1)[1]}")
        self.output_result(not errors, error_message="; ".join(errors),
                           metrics={"cleanup_remaining_count": len(errors)})


class FakeGpuModule:
    """函数入口形态：清理函数走 mod.adb（③ 态由 _lib 的 serial 替换生效）。"""

    def __init__(self, device: FakeDevice, spec: tsc.Spec, lib) -> None:
        self.device = device
        self.spec = spec
        self.lib = lib
        self.adb = lib.adb          # 实例属性，与被替换路径一致

    def _cleanup_device_script(self) -> None:
        path = self.spec.paths[0]
        rc, _o, _e = self.adb("shell", f"rm -f {path}", timeout=30)
        if rc != 0:
            raise RuntimeError(f"设备端脚本清理命令失败：rc={rc}")
        self.device.remove(path)
        rc, out, _e = self.adb("shell", f"[ -e {path} ] && echo REMAINS || echo CLEAN", timeout=30)
        if rc != 0:
            raise RuntimeError("清理验证不可用：rc=%d，无法确认是否已删除" % rc)
        if "REMAINS" in out:
            raise RuntimeError(f"设备端脚本清理失败：{path} 仍存在（#894）")
        if "CLEAN" not in out:
            raise RuntimeError(f"清理验证输出异常：{out!r}")


def make_fake_lib(device: FakeDevice):
    def adb(*args, **kw):
        if len(args) > 1 and "[ -e" in args[1]:
            if device.exists(device.loop_path or ""):
                return 0, "REMAINS", ""
            return 0, "CLEAN", ""
        return 0, "", ""

    lib = SimpleNamespace(adb=adb, device_serial=lambda: "FAKE")
    sys.modules["_lib"] = lib
    # 让 lib.adb 在 serial 被替换时返回 rc≠0（模拟真实 adb）
    real_adb = adb

    def adb_with_serial(*args, **kw):
        if lib.device_serial() == tsc.BOGUS_SERIAL:
            return 1, "", "device not found"
        return real_adb(*args, **kw)

    lib.adb = adb_with_serial
    return lib


def _patch(monkeypatch, device: FakeDevice, module_factory):
    monkeypatch.setattr(tsc, "Device", lambda serial, **kw: device)
    monkeypatch.setattr(tsc, "load_script_module", lambda vd, name: module_factory(device))
    monkeypatch.setattr(tsc, "pick_version_dir", lambda root, name, ver: Path("/fake/v1.0.0"))


# ── 纯逻辑 ──────────────────────────────────────────────────────────────────

def test_pick_version_dir_newest_and_explicit(tmp_path):
    for v in ("v1.0.0", "v1.0.9", "v1.0.10"):
        (tmp_path / "monkey_teardown" / v).mkdir(parents=True)
    assert tsc.pick_version_dir(tmp_path, "monkey_teardown", None).name == "v1.0.10"
    assert tsc.pick_version_dir(tmp_path, "monkey_teardown", "1.0.9").name == "v1.0.9"
    with pytest.raises(tsc.FixtureError):
        tsc.pick_version_dir(tmp_path, "monkey_teardown", "9.9.9")
    with pytest.raises(tsc.FixtureError):
        tsc.pick_version_dir(tmp_path, "gpu_finish", None)


def test_specs_registered_with_red_markers():
    for name, spec in tsc.SCRIPT_SPECS.items():
        assert spec.paths and spec.residue_path in spec.paths, name
        assert set(spec.red_markers) == {"residue", "probe"}, name


# ── 三态（main 入口形态） ────────────────────────────────────────────────────

def _run(monkeypatch, device, case):
    _patch(monkeypatch, device, lambda d: FakeMonkeyModule(d, tsc.SCRIPT_SPECS["monkey_teardown"]))
    return run_case(monkeypatch, device, "monkey_teardown", case)


def run_case(monkeypatch, device, script, case, **kw):
    summary = tsc.run_fixture(
        serial=device.serial, script=script, script_root=Path("/fake"),
        version=None, cases=[case], force=True, log=lambda _m: None, **kw,
    )
    return summary["results"][0]


def test_case_delete_ok_passes(monkeypatch):
    dev = FakeDevice()
    res = _run(monkeypatch, dev, "delete_ok")
    assert res["ok"] is True
    assert res["detail"]["still_present"] == []


def test_case_residue_red_passes(monkeypatch):
    dev = FakeDevice()
    res = _run(monkeypatch, dev, "residue_red")
    assert res["ok"] is True, res
    assert "仍存在" in (res["detail"]["error_message"] or "")
    assert dev.loop is False            # 收尾：循环必须被停掉


def test_case_probe_unavailable_red_passes(monkeypatch):
    dev = FakeDevice()
    res = _run(monkeypatch, dev, "probe_unavailable_red")
    assert res["ok"] is True, res
    assert "无法确认" in (res["detail"]["error_message"] or "")


def test_residue_preflight_failure_is_fixture_error(monkeypatch):
    """循环不重建 → 夹具错误（exit 2 语义），而不是把实现判成缺陷。"""
    dev = FakeDevice(recreate=False)
    _patch(monkeypatch, dev, lambda d: FakeMonkeyModule(d, tsc.SCRIPT_SPECS["monkey_teardown"]))
    with pytest.raises(tsc.FixtureError) as ei:
        tsc.run_fixture(serial=dev.serial, script="monkey_teardown", script_root=Path("/fake"),
                        version=None, cases=["residue_red"], force=True, log=lambda _m: None)
    assert "前置自检" in str(ei.value)


def test_safety_gate_blocks_without_force(monkeypatch):
    dev = FakeDevice(busy=True)
    _patch(monkeypatch, dev, lambda d: FakeMonkeyModule(d, tsc.SCRIPT_SPECS["monkey_teardown"]))
    with pytest.raises(tsc.FixtureError) as ei:
        tsc.run_fixture(serial=dev.serial, script="monkey_teardown", script_root=Path("/fake"),
                        version=None, cases=["delete_ok"], force=False, log=lambda _m: None)
    assert "拒绝" in str(ei.value)


# ── 三态（函数入口形态 / gpu_finish） ────────────────────────────────────────

def test_gpu_three_states(monkeypatch):
    for case, expect_ok in (("delete_ok", True), ("residue_red", True),
                            ("probe_unavailable_red", True)):
        dev = FakeDevice()
        lib = make_fake_lib(dev)
        monkeypatch.setattr(tsc, "Device", lambda serial, _dev=dev, **kw: _dev)
        monkeypatch.setattr(
            tsc, "load_script_module",
            lambda vd, name, _dev=dev, _lib=lib: FakeGpuModule(
                _dev, tsc.SCRIPT_SPECS["gpu_finish"], _lib),
        )
        monkeypatch.setattr(tsc, "pick_version_dir", lambda root, name, ver: Path("/fake/v1.0.5"))
        res = tsc.run_fixture(serial=dev.serial, script="gpu_finish", script_root=Path("/fake"),
                              version=None, cases=[case], force=True, log=lambda _m: None)["results"][0]
        assert res["ok"] is expect_ok, (case, res)
        if case == "residue_red":
            assert "仍存在" in res["detail"]["raised"]
        if case == "probe_unavailable_red":
            assert "清理验证不可用" in res["detail"]["raised"]


# ── CLI 层 ──────────────────────────────────────────────────────────────────

def test_main_exit_codes(monkeypatch, capsys):
    dev = FakeDevice()
    _patch(monkeypatch, dev, lambda d: FakeMonkeyModule(d, tsc.SCRIPT_SPECS["monkey_teardown"]))

    rc = tsc.main(["--serial", dev.serial, "--script", "monkey_teardown",
                   "--script-root", "/fake", "--json", "--force"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["passed"] == payload["total"] == 3

    # 夹具错误 → exit 2
    dev2 = FakeDevice(recreate=False)
    _patch(monkeypatch, dev2, lambda d: FakeMonkeyModule(d, tsc.SCRIPT_SPECS["monkey_teardown"]))
    rc2 = tsc.main(["--serial", dev2.serial, "--script", "monkey_teardown", "--script-root", "/fake",
                    "--case", "residue_red", "--force"])
    assert rc2 == 2
    assert "夹具错误" in capsys.readouterr().err


def test_main_unknown_case_is_arg_error(capsys):
    rc = tsc.main(["--serial", "X", "--script", "monkey_teardown", "--case", "nope"])
    assert rc == 2
    assert "未知用例" in capsys.readouterr().err


# ── 负向对照：夹具必须能抓住「探测丢 rc」的缺陷形态（#2146 的 v1.0.4 缺陷） ──

class RcLosingModule(FakeMonkeyModule):
    """缺陷形态：探测不看 rc，读不到就当干净（v1.0.4 的静默假绿）。"""

    def main(self) -> None:
        self.adb_shell_quiet(f"rm -rf {' '.join(self.spec.paths)}")
        probe = self.adb_shell_quiet("for p in ...; do ...; done; true")
        errors = []
        for line in probe.stdout.splitlines():          # 故意不检查 probe.returncode
            if line.startswith("REMAINS:"):
                errors.append(f"cleanup 后仍存在: {line.split(':', 1)[1]}")
        self.output_result(not errors, error_message="; ".join(errors),
                           metrics={"cleanup_remaining_count": len(errors)})


def test_fixture_catches_rc_losing_implementation(monkeypatch):
    """负向对照：实现丢 rc → ③ 态必须判 FAIL（否则夹具是空网）。"""
    dev = FakeDevice()
    _patch(monkeypatch, dev, lambda d: RcLosingModule(d, tsc.SCRIPT_SPECS["monkey_teardown"]))
    res = run_case(monkeypatch, dev, "monkey_teardown", "probe_unavailable_red")
    assert res["ok"] is False, res
