"""flash_firmware v1.4.0（#3892 / ADR-0063 D3）：关机设备经 USB 切电进入 BROM。

守什么：
1. ``trigger_mode`` 闭集，缺省 ``adb_reboot``；vbus 参数越界直接拒绝；
2. 槽位映射与 wrapper 同口径：整份文件有一处不合法就拒绝；
3. 取锁前的闸（映射 / wrapper 能力 / adb 不可见 / 槽位口无开机态 USB 枚举）任一不过
   → 失败且**零切电动作**；锁内切电前再复核一次槽位口；
4. 门控与 ``-p`` 目标口取自槽位，不按 serial 反查 sysfs；
5. 切电时序：每次尝试先断电、工具就绪回调里上电；BROM 捕获超时可重试，
   绑错口不重试；
6. 上电后任一失败（含信号路径）在交出锁前断电隔离；成功不断电；
7. 声明机型路由刷后必须回读核对；
8. 缺省路径不调 usb-power。
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPT_DIR = Path(__file__).resolve().parents[2] / "agent" / "scripts" / "flash_firmware"

_spec = importlib.util.spec_from_file_location(
    "flash_firmware_v140", _SCRIPT_DIR / "flash_firmware.py"
)
ff = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(ff)

SERIAL = "AHFFNX6930000169"
_PASS_OUTPUT = "connect brom successed.\n10% of data has been sent\nAll command exec done!\n"

_V6_MANIFEST = {
    "family": "MLD",
    "version": "P535AA_EVT_16.0_NX2_USERROOT_260928V9",
    "version_prop": "ro.build.version.incremental",
    "tool_generation": "v6",
    "flash_xml_file": "download_agent/flash.xml",
    "models": ["MLD_NX2", "MLD-NX2"],
}


# ── 1. 参数 ──────────────────────────────────────────────────────────


def _patch_advancing_clock(monkeypatch, mod, *, start: float = 1_000_000.0):
    """sleep 推进假时钟（time / monotonic 同步），不做墙钟等待。

    同构自 `test_flash_firmware_unisoc.py`（#3202 守卫：sleep 换 no-op 却不推进时钟
    = 等待环靠真墙钟判 deadline 的忙等形态）。本脚本的等待环用 `time.monotonic`，
    所以两者一起替换。
    """
    state = {"now": start}

    def fake_now() -> float:
        return state["now"]

    def fake_sleep(seconds: float) -> None:
        state["now"] += float(seconds)

    monkeypatch.setattr(mod.time, "time", fake_now)
    monkeypatch.setattr(mod.time, "monotonic", fake_now)
    monkeypatch.setattr(mod.time, "sleep", fake_sleep)
    return state


def test_trigger_mode_closed_set() -> None:
    assert ff._resolve_trigger_mode({}) == ("adb_reboot", None)
    assert ff._resolve_trigger_mode({"trigger_mode": " VBUS_CYCLE "}) == ("vbus_cycle", None)
    mode, err = ff._resolve_trigger_mode({"trigger_mode": "power_button"})
    assert mode is None and "unknown trigger_mode" in err


def test_vbus_params_defaults_and_bounds() -> None:
    params, err = ff._vbus_params({})
    assert err is None
    assert params == {"off_settle_seconds": 12, "poweroff_timeout_seconds": 60,
                      "brom_timeout_seconds": 120,
                      "boot_timeout_seconds": 300, "oobe_timeout_seconds": 60,
                      "verify_timeout_seconds": 60}
    for bad in (9, 31, "x", True):
        assert ff._vbus_params({"vbus_off_settle_seconds": bad})[1]
    assert ff._vbus_params({"vbus_off_settle_seconds": "10"})[0]["off_settle_seconds"] == 10
    assert ff._vbus_params({"vbus_boot_timeout_seconds": 0})[1]


# ── 2. 槽位映射 ──────────────────────────────────────────────────────


def _write_slots(path: Path, slots, schema=1) -> Path:
    path.write_text(json.dumps({"schema": schema, "slots": slots}), encoding="utf-8")
    return path


def test_slot_lookup_ok(tmp_path) -> None:
    path = _write_slots(tmp_path / "s.json", [
        {"serial": SERIAL, "hub_location": "1-5.1", "port": 3},
        {"serial": "OTHER", "hub_location": "1-5.1", "port": 4},
    ])
    slot, err = ff._load_vbus_slot(SERIAL, str(path))
    assert err is None
    assert slot == {"hub_location": "1-5.1", "port": 3, "gate_port": "1-5.1.3"}


@pytest.mark.parametrize("slots,needle", [
    ([{"serial": "OTHER", "hub_location": "1-5", "port": 1}], "has no usb power slot"),
    ([{"serial": SERIAL, "hub_location": "1-5", "port": 1},
      {"serial": SERIAL, "hub_location": "1-5", "port": 2}], "duplicate"),
    ([{"serial": SERIAL, "hub_location": "1-5", "port": 1},
      {"serial": "OTHER", "hub_location": "1-5", "port": 1}], "duplicate"),
    ([{"serial": SERIAL, "hub_location": "1-5", "port": 17}], "row invalid"),
    ([{"serial": SERIAL, "hub_location": "1-5", "port": True}], "row invalid"),
    ([{"serial": SERIAL, "hub_location": "../../x", "port": 1}], "row invalid"),
])
def test_slot_map_rejects_whole_file(tmp_path, slots, needle) -> None:
    slot, err = ff._load_vbus_slot(SERIAL, str(_write_slots(tmp_path / "s.json", slots)))
    assert slot is None and needle in err


def test_slot_map_missing_or_bad_schema(tmp_path) -> None:
    assert "not found" in ff._load_vbus_slot(SERIAL, str(tmp_path / "nope.json"))[1]
    bad = _write_slots(tmp_path / "s.json", [], schema=2)
    assert "schema invalid" in ff._load_vbus_slot(SERIAL, str(bad))[1]
    assert "STP_DEVICE_SERIAL" in ff._load_vbus_slot("", str(bad))[1]


# ── 3. 工具运行环：poll_check 判 BROM 捕获超时 ─────────────────────────


def test_poll_check_kills_silent_tool_with_kind() -> None:
    cmd = [sys.executable, "-c", "import time; time.sleep(30)"]
    with pytest.raises(ff.FlashToolAborted) as exc:
        ff._run_flash_tool_with_progress(
            cmd, cwd=".", env=None, timeout=20,
            on_stage=lambda t: None, on_percent=lambda p: None,
            poll_check=lambda: ("brom-timeout", "no BROM"),
        )
    assert exc.value.kind == "brom-timeout"
    assert exc.value.reason == "no BROM"


def test_flash_tool_aborted_default_kind_is_port_mismatch() -> None:
    assert ff.FlashToolAborted("x").kind == "port-mismatch"


# ── 4. main() 接线 ───────────────────────────────────────────────────


def _v6_tree(tmp_path: Path) -> Path:
    fw = tmp_path / _V6_MANIFEST["version"]
    (fw / "download_agent").mkdir(parents=True)
    (fw / "download_agent" / "flash.xml").write_text("<flash-mode/>\n", encoding="utf-8")
    (fw / "manifest.json").write_text(json.dumps(_V6_MANIFEST), encoding="utf-8")
    tool = tmp_path / "SP_Flash_Tool_V6"
    tool.mkdir()
    exe = tool / "SPFlashToolV6"
    exe.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    exe.chmod(0o755)
    return fw


class Harness:
    """把 main() 外部依赖全换成可观测的假件。"""

    def __init__(self, monkeypatch, tmp_path: Path, params: dict, *,
                 adb_state: str = "no-device", capability: bool = True,
                 slots: "list | None" = None) -> None:
        self.power: list[str] = []
        self.locked: list[bool] = []
        self.launched: list[list] = []
        self.launch_kwargs: list[dict] = []
        self.settle = None
        fw = _v6_tree(tmp_path)
        params = {"firmware_dir": str(fw), "trigger_mode": "vbus_cycle", **params}
        if params.get("firmware_dir") is None:
            params.pop("firmware_dir")
        monkeypatch.setenv("STP_STEP_PARAMS", json.dumps(params))
        monkeypatch.setenv("STP_DEVICE_SERIAL", SERIAL)
        monkeypatch.setenv("STP_NFS_ROOT", str(tmp_path / "unused"))
        monkeypatch.setenv("STP_FLASH_TOOL_V6_DIR", str(tmp_path))
        monkeypatch.delenv("STP_FLASH_TOOL_DIR", raising=False)
        slots_path = tmp_path / "usb-power-slots.json"
        if slots is None:
            slots = [{"serial": SERIAL, "hub_location": "1-5.1", "port": 3}]
        if slots != "absent":
            _write_slots(slots_path, slots)
        monkeypatch.setattr(ff, "_VBUS_SLOTS_PATH", str(slots_path))
        load = ff._load_vbus_slot
        monkeypatch.setattr(ff, "_load_vbus_slot",
                            lambda serial, path=None: load(serial, str(slots_path)))
        monkeypatch.setattr(ff, "_is_linux", lambda: True)
        (tmp_path / "sysfs").mkdir(exist_ok=True)
        monkeypatch.setattr(ff, "_SYSFS_USB_BASE", str(tmp_path / "sysfs"))
        monkeypatch.setattr(ff, "_adb_getprop", lambda prop, adb, serial, timeout=10: None)
        monkeypatch.setattr(ff, "_adb_device_state", lambda s, p: adb_state)
        monkeypatch.setattr(ff, "_priv_has_usb_power", lambda: capability)
        monkeypatch.setattr(ff, "_precheck_environment",
                            lambda *a, **k: (True, {"items": [], "warnings": []}))
        monkeypatch.setattr(ff, "_restore_orphaned_gates",
                            lambda *a, **k: {"restored": [], "errors": {}})
        monkeypatch.setattr(ff, "_acquire_host_lock",
                            lambda *a, **k: (self.locked.append(True), None)[1])
        monkeypatch.setattr(ff, "_release_host_lock", lambda fd: None)
        # 复核 #3924 第 2 轮附注：结算会删门控状态文件，测试不得碰真实 /tmp 路径。
        gate_state = str(tmp_path / "gated.json")
        monkeypatch.setattr(ff, "_GATE_STATE_PATH", gate_state)
        clear = ff._clear_gated_state
        monkeypatch.setattr(ff, "_clear_gated_state", lambda path=gate_state: clear(path))
        persist = ff._persist_gated_ports
        monkeypatch.setattr(ff, "_persist_gated_ports",
                            lambda ports, *a, **k: persist(ports, *a, **{**k, "path": gate_state}))
        monkeypatch.setattr(ff, "_gate_other_mtk",
                            lambda port, base=None, hold_pids=None, before_write=None:
                            {"hidden": [], "errors": {}, "target_port": port})
        monkeypatch.setattr(ff, "_install_termination_cleanup",
                            lambda settle: setattr(self, "settle", settle))

        def _no_reverse_lookup(*a, **k):
            raise AssertionError("vbus_cycle 不得按 serial 反查 sysfs 端口")

        monkeypatch.setattr(ff, "_port_for_serial", _no_reverse_lookup)
        monkeypatch.setattr(ff, "_resolve_target_identity", _no_reverse_lookup)
        monkeypatch.setattr(ff, "_usb_power", self._fake_power)
        _patch_advancing_clock(monkeypatch, ff)
        monkeypatch.setattr(ff, "_vbus_post_flash", self._fake_post_flash)
        self.post_flash_ok = True
        self.echo_slot = {"hub_location": "1-5.1", "port": 3}
        # wrapper 查表得到的槽位；与脚本传的 --expect-slot 不一致时 wrapper 在切电前拒绝。
        self.map_slot = {"hub_location": "1-5.1", "port": 3}
        self.refused: list[str] = []
        # {(action, 第几次该动作): 回显槽位}：模拟 wrapper 回显另一个槽位。
        self.echo_overrides: dict = {}
        self.launch_script: list = ["pass"]
        # 复核 #3902 B4：True 时模拟工具在第一次 poll_check 之前就退出。
        self.skip_poll = False
        self.poll_verdicts: list = []

    def _fake_power(self, serial, action, slot):
        assert serial == SERIAL
        if (slot["hub_location"], slot["port"]) != (
                self.map_slot["hub_location"], self.map_slot["port"]):
            self.refused.append(action)
            tail = "STP_AGENT_PRIV_ERROR STP_USB_POWER_SLOT_MISMATCH serial=%s" % serial
            return {"action": action, "ok": False, "rc": 2, "tail": tail,
                    "seconds": 0.0, "echo": None, "slot_refused": True}
        self.power.append(action)
        nth = self.power.count(action)
        echo = dict(self.echo_overrides.get((action, nth), self.echo_slot),
                    serial=serial, action=action)
        return {"action": action, "ok": True, "rc": 0, "tail": "", "seconds": 0.0,
                "echo": echo}

    def _fake_post_flash(self, route, serial, adb_path, cfg, report, *a):
        if self.post_flash_ok:
            return True, True, {"stage": None, "current": route["version"]}, {"ok": True}
        return True, False, {"stage": "oobe", "error": "OOBE flags not set"}, {"ok": True}

    def launch(self, cmd, *a, **k):
        self.launched.append(cmd)
        self.launch_kwargs.append(k)
        k["on_running"]()
        poll = None if self.skip_poll else k.get("poll_check")
        verdict = poll() if poll is not None else None
        self.poll_verdicts.append(verdict)
        if verdict:
            raise ff.FlashToolAborted(verdict[1], verdict[0])
        step = self.launch_script.pop(0) if self.launch_script else "pass"
        if step == "fail":
            return "flash failed: no verdict\n", 1
        if step == "brom-timeout":
            raise ff.FlashToolAborted("no BROM capture", "brom-timeout")
        if step == "port-mismatch":
            raise ff.FlashToolAborted("bound 1-8")
        if step == "signal":
            self.settle(15)
        return _PASS_OUTPUT, 0

    def run(self, monkeypatch, capsys) -> dict:
        monkeypatch.setattr(ff, "_run_flash_tool_with_progress", self.launch)
        ff.main()
        return json.loads(capsys.readouterr().out.strip().splitlines()[-1])


@pytest.mark.parametrize("stdout,rc,expected", [
    ("usb-power\nusb-power/expect-slot\n", 0, True),
    # 旧 wrapper：有 usb-power 子命令但未实现 --expect-slot（复核 #3899 P2-1）
    ("usb-power\nsync-env/retired-keys\n", 0, False),
    ("usb-power/expect-slot\n", 0, False),
    ("", 0, False),
    ("usb-power\nusb-power/expect-slot\n", 1, False),
])
def test_priv_capability_requires_expect_slot_marker(monkeypatch, stdout, rc,
                                                     expected) -> None:
    def fake_run(argv, **kw):
        assert argv[-1] == "capabilities"
        return subprocess.CompletedProcess(argv, rc, stdout=stdout, stderr="")
    monkeypatch.setattr(ff.subprocess, "run", fake_run)
    assert ff._priv_has_usb_power() is expected


@pytest.mark.parametrize("kwargs,needle", [
    ({"slots": "absent"}, "slot map not found"),
    ({"capability": False}, "lacks the usb-power capability"),
    ({"adb_state": "unauthorized"}, "not controllable (state unauthorized)"),
])
def test_prelock_gates_fail_without_power_actions(tmp_path, monkeypatch, capsys,
                                                  kwargs, needle) -> None:
    h = Harness(monkeypatch, tmp_path, {}, **kwargs)
    payload = h.run(monkeypatch, capsys)
    assert payload["success"] is False
    assert needle in payload["error_message"]
    assert h.power == [] and h.locked == [] and h.launched == []
    assert payload["metrics"]["trigger_mode"] == "vbus_cycle"


def test_vbus_requires_model_without_firmware_dir(tmp_path, monkeypatch, capsys) -> None:
    h = Harness(monkeypatch, tmp_path, {"firmware_dir": None})
    payload = h.run(monkeypatch, capsys)
    assert payload["success"] is False and "requires firmware_dir or model" in payload["error_message"]
    assert h.power == []


def test_vbus_requires_version_verify(tmp_path, monkeypatch, capsys) -> None:
    h = Harness(monkeypatch, tmp_path, {"verify_version": False})
    payload = h.run(monkeypatch, capsys)
    assert payload["success"] is False and "verify_version" in payload["error_message"]
    assert h.power == []


def test_vbus_happy_path(tmp_path, monkeypatch, capsys) -> None:
    h = Harness(monkeypatch, tmp_path, {})
    payload = h.run(monkeypatch, capsys)
    assert payload["success"] is True, payload
    assert h.power == ["off", "on"], "成功路径：断电→上电，收尾不得断电"
    assert h.launched[-1][-2:] == ["-p", "1-5.1.3"], "目标口取自槽位"
    m = payload["metrics"]
    assert m["trigger_mode"] == "vbus_cycle"
    assert m["target_port"] == "1-5.1.3"
    assert m["vbus"]["slot"]["gate_port"] == "1-5.1.3"
    assert m["vbus"]["isolated"] is False
    assert [a["phase"] for a in m["vbus"]["power_actions"]] == ["pre-flash", "trigger"]
    assert m["pre_reboot"]["attempted"] is False, "vbus 不发 adb reboot"
    assert h.launch_kwargs[-1].get("poll_check") is not None


def test_brom_timeout_retries_with_fresh_power_cycle(tmp_path, monkeypatch, capsys) -> None:
    h = Harness(monkeypatch, tmp_path, {})
    h.launch_script = ["brom-timeout", "pass"]
    payload = h.run(monkeypatch, capsys)
    assert payload["success"] is True, payload
    assert h.power == ["off", "on", "off", "on"]
    assert [a["outcome"] for a in payload["metrics"]["attempts"]] == ["brom-timeout", "ok"]


def test_brom_timeout_exhausted_isolates(tmp_path, monkeypatch, capsys) -> None:
    h = Harness(monkeypatch, tmp_path, {"max_attempts": 1})
    h.launch_script = ["brom-timeout"]
    payload = h.run(monkeypatch, capsys)
    assert payload["success"] is False
    assert h.power == ["off", "on", "off"]
    assert payload["metrics"]["vbus"]["isolated"] is True
    assert payload["metrics"]["recovery"]["skipped_reason"].startswith("vbus_cycle")


def test_port_mismatch_does_not_retry_and_isolates(tmp_path, monkeypatch, capsys) -> None:
    h = Harness(monkeypatch, tmp_path, {"max_attempts": 3})
    h.launch_script = ["port-mismatch"]
    payload = h.run(monkeypatch, capsys)
    assert payload["success"] is False
    assert len(h.launched) == 1
    assert h.power == ["off", "on", "off"]


def test_post_flash_failure_isolates(tmp_path, monkeypatch, capsys) -> None:
    h = Harness(monkeypatch, tmp_path, {})
    h.post_flash_ok = False
    payload = h.run(monkeypatch, capsys)
    assert payload["success"] is False
    assert "OOBE flags not set" in payload["error_message"]
    assert h.power == ["off", "on", "off"]
    assert payload["metrics"]["vbus"]["isolated"] is True


def test_post_flash_exception_isolates(tmp_path, monkeypatch, capsys) -> None:
    # 复核 #3902 B1：刷后阶段抛异常（而不是返回失败）同样要先断电再交出锁。
    h = Harness(monkeypatch, tmp_path, {})

    def _boom(*a, **k):
        raise RuntimeError("adb exploded")

    monkeypatch.setattr(ff, "_vbus_post_flash", _boom)
    with pytest.raises(RuntimeError, match="adb exploded"):
        h.run(monkeypatch, capsys)
    assert h.power == ["off", "on", "off"]


@pytest.mark.parametrize("params", [
    {"require_port_binding": False},
    {"require_port_binding": "false"},
])
def test_vbus_refuses_without_port_binding(tmp_path, monkeypatch, capsys,
                                           params) -> None:
    # 复核 #3902 B2：vbus 下不得关掉 V6 -p 绑定（绑错口止损也随之消失）。
    h = Harness(monkeypatch, tmp_path, params)
    payload = h.run(monkeypatch, capsys)
    assert payload["success"] is False
    assert "requires port binding" in payload["error_message"]
    assert h.power == []
    assert h.launch_kwargs == []


def test_vbus_refuses_when_binding_defaults_off(tmp_path, monkeypatch, capsys) -> None:
    # 绑定缺省只在 V6 + Linux 打开；缺省为关时（V5 或非 Linux）vbus 同样拒绝。
    h = Harness(monkeypatch, tmp_path, {})
    monkeypatch.setattr(ff, "_is_linux", lambda: False)
    payload = h.run(monkeypatch, capsys)
    assert payload["success"] is False
    assert "requires port binding" in payload["error_message"], payload
    assert h.power == []


def test_signal_path_isolates_once(tmp_path, monkeypatch, capsys) -> None:
    h = Harness(monkeypatch, tmp_path, {})
    h.launch_script = ["signal"]
    h.run(monkeypatch, capsys)
    # 信号结算断电一次；之后成功路径再结算是幂等的，不再切电。
    assert h.power == ["off", "on", "off"]


def test_default_path_never_touches_usb_power(tmp_path, monkeypatch, capsys) -> None:
    h = Harness(monkeypatch, tmp_path, {"trigger_mode": None})
    monkeypatch.setattr(ff, "_resolve_target_identity",
                        lambda **k: ("1-10", {"checked": True}))
    monkeypatch.setattr(ff, "_wait_device_back", lambda **k: True)
    monkeypatch.setattr(ff, "_wait_boot_stable", lambda **k: {"ok": True})
    monkeypatch.setattr(ff, "_verify_after_flash", lambda *a, **k: (True, {}))
    monkeypatch.setattr(ff, "_reboot_into_flash_mode",
                        lambda **k: {"attempted": True})
    payload = h.run(monkeypatch, capsys)
    assert payload["success"] is True, payload
    assert h.power == []
    assert payload["metrics"]["trigger_mode"] == "adb_reboot"
    assert "vbus" not in payload["metrics"]
    assert h.launch_kwargs[-1].get("poll_check") is None


# ── 5. 刷后阶段 ──────────────────────────────────────────────────────


def _post_flash(monkeypatch, *, booted=True, oobe_ok=True, version_ok=True,
                actual_model="MLD_NX2", decided_by="declared_model"):
    monkeypatch.setattr(ff, "_wait_boot_to_adb",
                        lambda *a, **k: (booted, "device" if booted else "no-device"))
    monkeypatch.setattr(ff, "_wait_boot_stable", lambda **k: {"ok": True})
    monkeypatch.setattr(ff, "_vbus_skip_oobe",
                        lambda *a, **k: {"ok": oobe_ok,
                                         **({} if oobe_ok else {"error": "flags"})})
    monkeypatch.setattr(ff, "_verify_after_flash",
                        lambda *a, **k: (version_ok, {} if version_ok else {"error": "mismatch"}))
    monkeypatch.setattr(ff, "_adb_getprop", lambda prop, adb, serial, timeout=10: actual_model)
    cfg, _ = ff._vbus_params({})
    route = {"decided_by": decided_by, "model": "MLD_NX2", "version": "V9"}
    return ff._vbus_post_flash(route, SERIAL, "adb", cfg, {}, 20, 120, [0])


def test_post_flash_stages(monkeypatch) -> None:
    assert _post_flash(monkeypatch)[1] is True
    _r, ok, report, _b = _post_flash(monkeypatch, booted=False)
    assert ok is False and report["stage"] == "boot_to_adb"
    _r, ok, report, _b = _post_flash(monkeypatch, oobe_ok=False)
    assert ok is False and report["stage"] == "oobe"
    _r, ok, report, _b = _post_flash(monkeypatch, version_ok=False)
    assert ok is False and report["stage"] == "verify"


def test_post_flash_declared_model_mismatch_fails(monkeypatch) -> None:
    _r, ok, report, _b = _post_flash(monkeypatch, actual_model="MLD_LX2")
    assert ok is False and "declared model" in report["error"]
    # 指纹路由读出来的机型无需再核。
    assert _post_flash(monkeypatch, actual_model="X", decided_by="params")[1] is True


def test_declared_model_routing_marks_route(tmp_path, monkeypatch) -> None:
    fw = _v6_tree(tmp_path)
    root = tmp_path / "root"
    (root / "MLD").mkdir(parents=True)
    fw.rename(root / "MLD" / _V6_MANIFEST["version"])
    (root / "MLD" / "latest.json").write_text(
        json.dumps({"version": _V6_MANIFEST["version"]}), encoding="utf-8")

    def _no_adb(*a, **k):
        raise AssertionError("声明机型路由不得读 adb")

    monkeypatch.setattr(ff, "_adb_getprop", _no_adb)
    route, err = ff._resolve_by_fingerprint(
        {"firmware_root": str(root), "family": "MLD"}, SERIAL, "adb",
        model_override="MLD_NX2")
    assert err is None and route["decided_by"] == "declared_model"
    _route, err = ff._resolve_by_fingerprint(
        {"firmware_root": str(root), "family": "MLD"}, SERIAL, "adb",
        model_override="MLD_LX9")
    assert "not in manifest models" in err


# ── 6. OOBE 配方 ─────────────────────────────────────────────────────


def test_oobe_skip_sequence_and_verdict(monkeypatch) -> None:
    calls: list[list[str]] = []

    def _shell(serial, adb, args, timeout=15):
        calls.append(args)
        if args[:2] == ["settings", "get"]:
            return 0, "1\n"
        return 0, ""

    monkeypatch.setattr(ff, "_adb_shell_rc", _shell)
    report = ff._vbus_skip_oobe(SERIAL, "adb", 60)
    assert report["ok"] is True
    clear = [c for c in calls if c[0] in ("am", "input")]
    assert clear == [["am", "force-stop", "com.google.android.setupwizard"],
                     ["input", "keyevent", "224"], ["input", "keyevent", "82"],
                     ["input", "keyevent", "3"]]


def test_oobe_skip_times_out(monkeypatch) -> None:
    monkeypatch.setattr(ff, "_adb_shell_rc", lambda *a, **k: (0, "0\n"))
    _patch_advancing_clock(monkeypatch, ff)
    report = ff._vbus_skip_oobe(SERIAL, "adb", 60)
    assert report["ok"] is False and "not set within 60s" in report["error"]


def test_usb_power_argv(monkeypatch) -> None:
    seen: list = []

    def _run(argv, **k):
        seen.append(argv)
        return subprocess.CompletedProcess(argv, 0, "STP_USB_POWER_OK\n", "")

    monkeypatch.setattr(ff.subprocess, "run", _run)
    result = ff._usb_power(SERIAL, "off", {"hub_location": "1-5.1", "port": 3})
    assert result["ok"] is True and result["slot_refused"] is False
    assert seen[-1] == ["sudo", "-n", ff._PRIV_WRAPPER, "usb-power",
                        "--serial", SERIAL, "--expect-slot", "1-5.1:3", "--action", "off"]


# ── 7. 设备必须已关机（USB 层识别开机但无 ADB 的设备）────────────────


def _mk_port(base: Path, port: str, vid: str, pid: str) -> None:
    d = base / port
    d.mkdir(parents=True, exist_ok=True)
    (d / "idVendor").write_text(vid + "\n", encoding="utf-8")
    (d / "idProduct").write_text(pid + "\n", encoding="utf-8")


def test_port_occupant_classification(tmp_path) -> None:
    assert ff._vbus_port_powered_on("1-5.1.3", str(tmp_path)) is None
    _mk_port(tmp_path, "1-5.1.3", "0e8d", "2000")
    assert ff._vbus_port_powered_on("1-5.1.3", str(tmp_path)) is None, "BROM 态放行"
    _mk_port(tmp_path, "1-5.1.3", "0e8d", "2046")
    assert ff._vbus_port_powered_on("1-5.1.3", str(tmp_path)) is None, \
        "关机态 2046 放行（Owner 2026-11-07 现场 lsusb：关机 MLD-LX3 = 0e8d:2046）"
    _mk_port(tmp_path, "1-5.1.3", "0e8d", "201c")
    assert ff._vbus_port_powered_on("1-5.1.3", str(tmp_path)) == "0e8d:201c"
    _mk_port(tmp_path, "1-5.1.4", "18d1", "4ee7")
    assert ff._vbus_port_powered_on("1-5.1.4", str(tmp_path)) == "18d1:4ee7"


def test_powered_on_device_without_adb_is_refused(tmp_path, monkeypatch, capsys) -> None:
    h = Harness(monkeypatch, tmp_path, {})
    base = tmp_path / "sysfs"
    _mk_port(base, "1-5.1.3", "0e8d", "201c")
    monkeypatch.setattr(ff, "_SYSFS_USB_BASE", str(base))
    payload = h.run(monkeypatch, capsys)
    assert payload["success"] is False
    assert "looks powered on" in payload["error_message"]
    assert h.power == [] and h.locked == []


def test_device_powered_on_while_waiting_lock(tmp_path, monkeypatch, capsys) -> None:
    h = Harness(monkeypatch, tmp_path, {})
    seen: list = []

    def _occupant(port, base=None):
        seen.append(port)
        return None if len(seen) == 1 else "0e8d:201c"

    monkeypatch.setattr(ff, "_vbus_port_powered_on", _occupant)
    payload = h.run(monkeypatch, capsys)
    assert payload["success"] is False
    assert "must be powered off" in payload["error_message"]
    assert h.locked == [True] and h.power == [], "锁内复核失败不碰供电"
    assert h.launched == []


# ── 8. 前置关机：ADB 可控的设备先用命令关机 ────────────────────────────


def test_adb_online_device_is_powered_off_first(tmp_path, monkeypatch, capsys) -> None:
    h = Harness(monkeypatch, tmp_path, {}, adb_state="device")
    calls: list = []

    def _poweroff(serial, adb, gate_port, timeout, base=None):
        calls.append((serial, gate_port, timeout))
        return {"ok": True, "last_state": "no-device", "occupant": None}

    monkeypatch.setattr(ff, "_vbus_adb_power_off", _poweroff)
    monkeypatch.setattr(ff, "_port_for_serial", lambda serial, base=None: "1-5.1.3")
    # 开机态的口上是 201c：取锁前不得据此拒绝（关机在锁内做）。
    _mk_port(tmp_path / "sysfs", "1-5.1.3", "0e8d", "201c")
    order: list = []
    lock = ff._acquire_host_lock
    monkeypatch.setattr(ff, "_acquire_host_lock",
                        lambda *a, **k: (order.append("lock"), lock(*a, **k))[1])
    inner = _poweroff
    monkeypatch.setattr(ff, "_vbus_adb_power_off",
                        lambda *a, **k: (order.append("adb-poweroff"),
                                         (__import__("shutil").rmtree(tmp_path / "sysfs" / "1-5.1.3")),
                                         inner(*a, **k))[2])
    payload = h.run(monkeypatch, capsys)
    assert payload["success"] is True, payload
    assert calls == [(SERIAL, "1-5.1.3", 60)]
    assert order == ["lock", "adb-poweroff"], "adb reboot -p 必须在取锁之后"
    assert payload["metrics"]["identity"]["adb_poweroff"]["ok"] is True
    assert h.power == ["off", "on"]


def test_adb_poweroff_timeout_refuses_without_power(tmp_path, monkeypatch, capsys) -> None:
    h = Harness(monkeypatch, tmp_path, {"vbus_poweroff_timeout_seconds": 5},
                adb_state="device")
    monkeypatch.setattr(ff, "_vbus_adb_power_off",
                        lambda *a, **k: {"ok": False, "last_state": "device",
                                         "occupant": "0e8d:201c"})
    monkeypatch.setattr(ff, "_port_for_serial", lambda serial, base=None: "1-5.1.3")
    payload = h.run(monkeypatch, capsys)
    assert payload["success"] is False
    assert "did not power off via adb within 5s" in payload["error_message"]
    assert h.locked == [True] and h.power == [], "关机失败时不做任何切电"


def test_adb_power_off_waits_for_adb_and_port(tmp_path, monkeypatch) -> None:
    argv: list = []
    states = iter(["device", "no-device", "no-device"])
    occupants = iter(["0e8d:201c", "0e8d:201c", None])
    monkeypatch.setattr(ff.subprocess, "run",
                        lambda a, **k: (argv.append(a),
                                        subprocess.CompletedProcess(a, 0, "", ""))[1])
    monkeypatch.setattr(ff, "_adb_device_state", lambda s, p: next(states))
    monkeypatch.setattr(ff, "_vbus_port_powered_on", lambda port, base=None: next(occupants))
    _patch_advancing_clock(monkeypatch, ff)
    report = ff._vbus_adb_power_off(SERIAL, "adb", "1-5.1.3", 60)
    assert report["ok"] is True
    assert argv == [["adb", "-s", SERIAL, "reboot", "-p"]]


@pytest.mark.parametrize("state", ["offline", "unknown", "no-device"])
def test_non_unauthorized_states_proceed_to_power_cycle(tmp_path, monkeypatch, capsys,
                                                        state) -> None:
    h = Harness(monkeypatch, tmp_path, {}, adb_state=state)
    payload = h.run(monkeypatch, capsys)
    assert payload["success"] is True, payload
    assert h.power == ["off", "on"]


def test_powered_off_2046_device_proceeds(tmp_path, monkeypatch, capsys) -> None:
    h = Harness(monkeypatch, tmp_path, {})
    _mk_port(tmp_path / "sysfs", "1-5.1.3", "0e8d", "2046")
    payload = h.run(monkeypatch, capsys)
    assert payload["success"] is True, payload
    assert h.power == ["off", "on"]


def test_adb_online_device_on_wrong_port_is_refused_before_lock(tmp_path, monkeypatch,
                                                                capsys) -> None:
    h = Harness(monkeypatch, tmp_path, {}, adb_state="device")
    monkeypatch.setattr(ff, "_port_for_serial", lambda serial, base=None: "1-7")
    monkeypatch.setattr(ff, "_vbus_adb_power_off",
                        lambda *a, **k: pytest.fail("映射槽位不符时不得先关机"))
    payload = h.run(monkeypatch, capsys)
    assert payload["success"] is False
    assert "not on its mapped slot port" in payload["error_message"]
    assert h.locked == [] and h.power == []


def test_wrapper_slot_echo_mismatch_stops_all_power_actions(tmp_path, monkeypatch,
                                                            capsys) -> None:
    h = Harness(monkeypatch, tmp_path, {})
    h.echo_slot = {"hub_location": "1-5.2", "port": 1}
    payload = h.run(monkeypatch, capsys)
    assert payload["success"] is False
    assert "slot map changed" in payload["error_message"]
    assert h.power == ["off"], "回显不符后不得再切电（含隔离）"
    assert h.launched == []


def test_wrapper_refuses_changed_slot_before_any_power_action(tmp_path, monkeypatch,
                                                              capsys) -> None:
    # 复核 #3899 comment 6708089815：映射在脚本与 wrapper 两次读取之间被改写时，
    # wrapper 按 --expect-slot 在切电前拒绝；没有任何口被切，隔离也被同样拒绝。
    h = Harness(monkeypatch, tmp_path, {})
    h.map_slot = {"hub_location": "1-5.2", "port": 1}
    payload = h.run(monkeypatch, capsys)
    assert payload["success"] is False
    assert "refused before powering anything" in payload["error_message"]
    assert h.power == []
    assert h.refused == ["off", "off"]
    assert h.launched == []
    assert payload["metrics"]["vbus"]["isolated"] is False


def test_usb_power_passes_expect_slot(monkeypatch) -> None:
    seen = {}

    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        return subprocess.CompletedProcess(
            argv, 2, "", "STP_AGENT_PRIV_ERROR STP_USB_POWER_SLOT_MISMATCH serial=S1")

    monkeypatch.setattr(ff.subprocess, "run", fake_run)
    result = ff._usb_power("S1", "off", {"hub_location": "1-5.1", "port": 3})
    assert seen["argv"][-6:] == ["--serial", "S1", "--expect-slot", "1-5.1:3",
                                 "--action", "off"]
    assert result["ok"] is False and result["slot_refused"] is True


def test_power_on_echo_mismatch_kills_tool_and_stops(tmp_path, monkeypatch, capsys) -> None:
    # 复核 #3902 B3：上电的回显指向另一个槽位时，杀工具、不重试、不再切电（含隔离）。
    h = Harness(monkeypatch, tmp_path, {})
    h.echo_overrides[("on", 1)] = {"hub_location": "1-5.2", "port": 1}
    payload = h.run(monkeypatch, capsys)
    assert payload["success"] is False
    assert "wrapper powered on slot" in payload["error_message"]
    assert h.power == ["off", "on"]
    assert len(h.launched) == 1
    # 工具仍在运行时由 poll_check 杀树（与 B4 的返回后补查分开断言）
    assert h.poll_verdicts[0][0] == "slot-mismatch"


def test_power_on_failure_stops_without_retry(tmp_path, monkeypatch, capsys) -> None:
    h = Harness(monkeypatch, tmp_path, {})
    orig = h._fake_power

    def failing_on(serial, action, slot):
        if action == "on":
            h.power.append("on")
            return {"action": "on", "ok": False, "rc": 3, "tail": "STP_USB_POWER_FAILED rc=1",
                    "seconds": 0.0, "echo": None, "slot_refused": False}
        return orig(serial, action, slot)

    monkeypatch.setattr(ff, "_usb_power", failing_on)
    payload = h.run(monkeypatch, capsys)
    assert payload["success"] is False
    assert "usb-power on failed" in payload["error_message"]
    assert h.power == ["off", "on", "off"]
    assert len(h.launched) == 1
    assert h.poll_verdicts[0][0] == "vbus-power"


@pytest.mark.parametrize("fault", ["echo-mismatch", "on-failed"])
@pytest.mark.parametrize("tool_result", ["pass", "fail"])
def test_power_on_fault_stops_even_if_tool_exits_before_poll(
        tmp_path, monkeypatch, capsys, fault, tool_result) -> None:
    # 复核 #3902 B4：工具在第一次 poll_check 前退出，上电那一步的 abort 仍须终止
    # 尝试环——不得按工具输出判成功，也不得重试。
    h = Harness(monkeypatch, tmp_path, {})
    h.skip_poll = True
    h.launch_script = [tool_result, tool_result]
    if fault == "echo-mismatch":
        h.echo_overrides[("on", 1)] = {"hub_location": "1-5.2", "port": 1}
        needle, expected_power = "wrapper powered on slot", ["off", "on"]
    else:
        orig = h._fake_power

        def failing_on(serial, action, slot):
            if action == "on":
                h.power.append("on")
                return {"action": "on", "ok": False, "rc": 3,
                        "tail": "STP_USB_POWER_FAILED rc=1", "seconds": 0.0,
                        "echo": None, "slot_refused": False}
            return orig(serial, action, slot)

        monkeypatch.setattr(ff, "_usb_power", failing_on)
        needle, expected_power = "usb-power on failed", ["off", "on", "off"]
    payload = h.run(monkeypatch, capsys)
    assert payload["success"] is False
    assert needle in payload["error_message"]
    assert h.power == expected_power
    assert len(h.launched) == 1


def test_isolation_echo_mismatch_is_not_reported_isolated(tmp_path, monkeypatch,
                                                          capsys) -> None:
    h = Harness(monkeypatch, tmp_path, {})
    h.post_flash_ok = False
    h.echo_overrides[("off", 2)] = {"hub_location": "1-5.2", "port": 1}
    payload = h.run(monkeypatch, capsys)
    assert payload["success"] is False
    assert h.power == ["off", "on", "off"]
    vbus = payload["metrics"]["vbus"]
    assert vbus["isolated"] is False
    assert vbus["isolate_echo_mismatch"] is True


def test_vbus_refuses_non_v6_native_even_if_binding_true(tmp_path, monkeypatch,
                                                         capsys) -> None:
    # 复核 #3902 B2：显式 require_port_binding=true 也不能绕过「V6 + Linux」。V5 与非 Linux
    # 走同一个判据 v6_native（generation == V6 and _is_linux()）；V5 在本 harness 里会先停在
    # 工具目录检查，所以这里用非 Linux 覆盖 v6_native=False。
    h = Harness(monkeypatch, tmp_path, {"require_port_binding": True})
    monkeypatch.setattr(ff, "_is_linux", lambda: False)
    payload = h.run(monkeypatch, capsys)
    assert payload["success"] is False
    assert "requires port binding" in payload["error_message"], payload
    assert "linux=False" in payload["error_message"]
    assert h.power == [] and h.launched == []


def test_usb_power_echo_parsing() -> None:
    echo = ff._usb_power_echo(
        "STP_USB_POWER_OK serial=S1 location=1-5.1 port=3 action=off")
    assert echo == {"serial": "S1", "hub_location": "1-5.1", "port": 3, "action": "off"}
    assert ff._usb_power_echo("garbage") is None
