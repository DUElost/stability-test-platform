"""flash_firmware v1.4.1（#3892 推广）：路由表补 MLD-NX3。"""
import importlib.util
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "flash_firmware" / "flash_firmware.py"


def _load():
    spec = importlib.util.spec_from_file_location("flash_firmware_v141", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_nx3_routes_to_mld_family_both_spellings():
    ff = _load()
    assert ff._MODEL_FAMILY_ROUTES["MLD_NX3"] == "MLD"
    assert ff._MODEL_FAMILY_ROUTES["MLD-NX3"] == "MLD"


def test_existing_routes_unchanged():
    ff = _load()
    for model in ("MLD_LX2", "MLD-LX2", "MLD_LX3", "MLD-LX3", "MLD_NX2", "MLD-NX2"):
        assert ff._MODEL_FAMILY_ROUTES[model] == "MLD"
    for model in ("ELA_LX2", "ELA-LX2", "ELA_LX3", "ELA-LX3"):
        assert ff._MODEL_FAMILY_ROUTES[model] == "ELA"


# ── v1.4.1：vbus_cycle 允许 V5，V5 同样带 -p（15.93 实测，#3892 comment 6764214206）──

import json  # noqa: E402

import pytest  # noqa: E402

from backend.agent.tests import test_flash_firmware_v140 as v140  # noqa: E402

_V5_MANIFEST = {
    "family": "MLD",
    "version": "V552AA-HONOR-LX2-16-261010V143",
    "version_prop": "ro.build.version.incremental",
    "scatter_file": "MT6789_Android_scatter.txt",
    "da_file": "DA_BR.bin",
    "models": ["MLD_LX2", "MLD-LX2"],
}


def _v5_harness(monkeypatch, tmp_path, params=None):
    h = v140.Harness(monkeypatch, tmp_path, params or {})
    fw = tmp_path / _V5_MANIFEST["version"]
    fw.mkdir()
    for name in (_V5_MANIFEST["scatter_file"], _V5_MANIFEST["da_file"]):
        (fw / name).write_text("x", encoding="utf-8")
    (fw / "manifest.json").write_text(json.dumps(_V5_MANIFEST), encoding="utf-8")
    tool = tmp_path / "v5tool"
    tool.mkdir()
    exe = tool / "flash_tool"
    exe.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    exe.chmod(0o755)
    monkeypatch.setenv("STP_FLASH_TOOL_DIR", str(tool))
    # v1.4.2：V5 vbus 经 stdbuf 启动、后台采样 tty；假件固定两者，不依赖宿主环境。
    which = v140.ff._shutil_which
    monkeypatch.setattr(v140.ff, "_shutil_which",
                        lambda cmd: "/usr/bin/stdbuf" if cmd == "stdbuf" else which(cmd))
    monkeypatch.setattr(v140.ff, "_list_acm_ttys", lambda base=None: [])
    monkeypatch.setenv("STP_STEP_PARAMS", json.dumps(
        {"firmware_dir": str(fw), "trigger_mode": "vbus_cycle", **(params or {})}))
    return h


def test_argv_v5_port_only_via_explicit_vbus_kwarg():
    ff = _load()
    base = ["/t/flash_tool", "-c", "firmware-upgrade", "-d", "/fw/DA.bin", "-s", "/fw/s.txt", "-b", "auto"]
    kw = dict(da_file="/fw/DA.bin", scatter_file="/fw/s.txt")
    assert ff._build_flash_argv("/t/flash_tool", "v5", "firmware-upgrade", "auto", port="1-10", **kw) == base
    assert ff._build_flash_argv("/t/flash_tool", "v5", "firmware-upgrade", "auto",
                                v5_port="1-13.2.4", **kw) == base + ["-p", "1-13.2.4"]


def test_v5_portname_echo_resolves_through_sys_class_tty(tmp_path):
    ff = _load()
    dev = tmp_path / "devices" / "usb1" / "1-13" / "1-13.2" / "1-13.2.4" / "1-13.2.4:1.0" / "tty" / "ttyACM0"
    dev.mkdir(parents=True)
    tty = tmp_path / "class_tty"
    tty.mkdir()
    (tty / "ttyACM0").symlink_to(dev)
    assert ff._tty_usb_port("ttyACM0", str(tty)) == "1-13.2.4"
    assert ff._tty_usb_port("ttyACM9", str(tty)) is None


def test_v5_echo_mismatch_aborts_and_match_passes(tmp_path, monkeypatch):
    ff = _load()
    monkeypatch.setattr(ff, "_tty_usb_port", lambda name, base=None: {"ttyACM0": "1-13.2.4"}.get(name))
    line = "com portName is: /dev/ttyACM0"
    assert ff._parse_bound_port(line, v5_echo=True) == "1-13.2.4"
    assert ff._port_mismatch_reason(line, "1-13.2.4", "S", v5_echo=True) is None
    assert "1-13.2.4" in ff._port_mismatch_reason(line, "1-13.2.2", "S", v5_echo=True)


def test_v5_echo_unresolvable_tty_aborts_in_vbus_mode(monkeypatch):
    # 复核 #3917 B1：回显了 tty 却解析不出口 = 无法核对，必须止损而不是当普通日志放过。
    ff = _load()
    monkeypatch.setattr(ff, "_tty_usb_port", lambda name, base=None: None)
    reason = ff._port_mismatch_reason("com portName is: /dev/ttyACM7", "1-13.2.4", "S",
                                      v5_echo=True)
    assert reason and "cannot be resolved" in reason
    assert ff._parse_bound_port("com portName is: /dev/ttyACM7", v5_echo=True) is None


def test_v5_echo_ignored_outside_vbus(monkeypatch):
    # 复核 #3917 B3：非 vbus（v5_echo=False）的 V5 不继承回显规则，旧行为不变。
    ff = _load()
    monkeypatch.setattr(ff, "_tty_usb_port", lambda name, base=None: {"ttyACM0": "1-13.2.4"}.get(name))
    for line in ("com portName is: /dev/ttyACM0", "com portName is: /dev/ttyACM7"):
        assert ff._parse_bound_port(line) is None
        assert ff._port_mismatch_reason(line, "1-13.2.2", "S") is None
    # V6 回显照旧
    assert ff._parse_bound_port(
        "scan device succeed, symbolic_link: COM:///dev/ttyACM1@/dev/ttyACM1@1-8") == "1-8"


_SLOT_PORT = "1-5.1.3"


def _feed_tool_lines(h, monkeypatch, lines, tty_map):
    """让假工具把 lines 逐行交给脚本的 abort_check，与真循环一样命中即止损。"""
    monkeypatch.setattr(v140.ff, "_tty_usb_port", lambda name, base=None: tty_map.get(name))
    inner = h.launch

    def launch(cmd, *a, **k):
        check = k.get("abort_check")
        if check is not None:
            # 真工具在上电（on_running）之后才回显绑定口。
            k["on_running"]()
            k = dict(k, on_running=lambda: None)
            for line in lines:
                reason = check(line)
                if reason:
                    h.launched.append(cmd)
                    h.launch_kwargs.append(k)
                    raise v140.ff.FlashToolAborted(reason)
        return inner(cmd, *a, **k)

    h.launch = launch


_V5_BOUND_LINE = "com portName is: /dev/ttyACM0"


def test_vbus_v5_carries_slot_port_and_brom_gate(tmp_path, monkeypatch, capsys):
    h = _v5_harness(monkeypatch, tmp_path)
    _feed_tool_lines(h, monkeypatch, [_V5_BOUND_LINE], {"ttyACM0": _SLOT_PORT})
    gate_calls = []
    monkeypatch.setattr(v140.ff, "_gate_other_mtk",
                        lambda port, base=None, hold_pids=None, before_write=None:
                        gate_calls.append(hold_pids) or {"hidden": [], "errors": {}, "target_port": port})
    payload = h.run(monkeypatch, capsys)
    assert payload["success"] is True, payload
    cmd = h.launched[-1]
    assert cmd[:3] == ["/usr/bin/stdbuf", "-oL", "-eL"], "v1.4.2：V5 vbus 按行缓冲"
    assert cmd[3].endswith("flash_tool")
    assert cmd[-2:] == ["-p", "1-5.1.3"]
    assert h.power == ["off", "on"]
    assert gate_calls and gate_calls[0] == v140.ff._BROM_GATE_PIDS, "V5 vbus 门控只压可刷态口"
    assert h.launch_kwargs[-1].get("abort_check") is not None


@pytest.mark.parametrize("params", [{"require_port_binding": False}])
def test_vbus_v5_still_refuses_when_binding_off(tmp_path, monkeypatch, capsys, params):
    h = _v5_harness(monkeypatch, tmp_path, params)
    payload = h.run(monkeypatch, capsys)
    assert payload["success"] is False
    assert "requires port binding" in payload["error_message"]
    assert h.power == [] and h.launched == []


@pytest.mark.parametrize("lines,tty_map,needle", [
    # 工具退出前从未回显绑定口
    (["BROM connected", "All command exec done!"], {}, "without a verifiable bound-port echo"),
    # 回显了 tty，但节点已消失，解析不出口
    ([_V5_BOUND_LINE], {}, "cannot be resolved"),
    # 回显口是邻机
    ([_V5_BOUND_LINE], {"ttyACM0": "1-5.1.2"}, "!= target port"),
])
def test_vbus_v5_without_trusted_binding_fails_and_isolates(
        tmp_path, monkeypatch, capsys, lines, tty_map, needle):
    h = _v5_harness(monkeypatch, tmp_path, {"max_attempts": 3})
    _feed_tool_lines(h, monkeypatch, lines, tty_map)
    payload = h.run(monkeypatch, capsys)
    assert payload["success"] is False, payload
    assert needle in payload["error_message"]
    assert len(h.launched) == 1, "不得重试"
    assert h.power == ["off", "on", "off"], "结算时断电隔离"
    assert payload["metrics"]["aborted"] is not None


@pytest.mark.parametrize("params,env", [
    ({"gate_pid_scope": "full"}, None),
    ({}, "full"),
    ({"gate_pid_scope": "brmo"}, None),
])
def test_vbus_forces_brom_gate_scope(tmp_path, monkeypatch, capsys, params, env):
    # 复核 #3917 B2：参数、环境变量、拼错的值都不能让 vbus 用 full 压制 201c 邻机。
    if env:
        monkeypatch.setenv("STP_FLASH_GATE_PID_SCOPE", env)
    h = _v5_harness(monkeypatch, tmp_path, {"max_attempts": 2, **params})
    _feed_tool_lines(h, monkeypatch, [_V5_BOUND_LINE], {"ttyACM0": _SLOT_PORT})
    h.launch_script = ["fail", "pass"]
    gate_calls = []
    monkeypatch.setattr(v140.ff, "_gate_other_mtk",
                        lambda port, base=None, hold_pids=None, before_write=None:
                        gate_calls.append(hold_pids) or {"hidden": [], "errors": {}, "target_port": port})
    payload = h.run(monkeypatch, capsys)
    assert payload["success"] is True, payload
    assert payload["metrics"]["identity"]["gate_pid_scope"] == "brom"
    brom_gates = [c for c in gate_calls if c != v140.ff._BROM_STAGE_PIDS]
    # 初始门控 + 重试轮 regate 都是 brom 口径
    assert len(brom_gates) == 2 and all(c == v140.ff._BROM_GATE_PIDS for c in brom_gates)


def test_vbus_v6_scope_override_also_forced(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("STP_FLASH_GATE_PID_SCOPE", "full")
    h = v140.Harness(monkeypatch, tmp_path, {})
    payload = h.run(monkeypatch, capsys)
    assert payload["success"] is True, payload
    assert payload["metrics"]["identity"]["gate_pid_scope"] == "brom"


# ── 复核 #3917 第 2 轮 B1-R2：sysfs 在同一行的多次查询之间变化 ──────────────────

def _feed_with_tty_sequence(h, monkeypatch, lines, answers):
    """第 i 行回显的**首次** tty 查询返回 answers[i]，之后的查询都返回槽位口
    （模拟链接在同一行的几次查询之间恢复到目标口）。"""
    pending = []
    calls = []

    def _tty(name, base=None):
        calls.append(name)
        return pending.pop() if pending else _SLOT_PORT

    monkeypatch.setattr(v140.ff, "_tty_usb_port", _tty)
    inner = h.launch

    def launch(cmd, *a, **k):
        check = k.get("abort_check")
        k["on_running"]()
        k = dict(k, on_running=lambda: None)
        for line, first in zip(lines, answers, strict=True):
            pending[:] = [first]
            reason = check(line)
            if reason:
                h.launched.append(cmd)
                h.launch_kwargs.append(k)
                raise v140.ff.FlashToolAborted(reason)
        return inner(cmd, *a, **k)

    h.launch = launch
    return calls


@pytest.mark.parametrize("lines,answers,needle", [
    # 唯一一条回显：首查是邻口，之后链接变成目标口 → 立即按错口止损
    ([_V5_BOUND_LINE], ["1-5.1.4"], "!= target port"),
    # 先有正确回显，第二条首查解析不出，之后恢复 → 立即止损，不得成功
    ([_V5_BOUND_LINE, _V5_BOUND_LINE], [_SLOT_PORT, None], "cannot be resolved"),
    # 先有正确回显，第二条首查是邻口，之后恢复 → 立即止损，不得成功
    ([_V5_BOUND_LINE, _V5_BOUND_LINE], [_SLOT_PORT, "1-5.1.4"], "!= target port"),
])
def test_vbus_v5_each_echo_line_resolved_once(tmp_path, monkeypatch, capsys,
                                              lines, answers, needle):
    h = _v5_harness(monkeypatch, tmp_path, {"max_attempts": 3})
    calls = _feed_with_tty_sequence(h, monkeypatch, lines, answers)
    payload = h.run(monkeypatch, capsys)
    assert payload["success"] is False, payload
    assert needle in payload["error_message"]
    assert payload["metrics"]["attempts"][-1]["outcome"] == "aborted-port-mismatch"
    assert len(h.launched) == 1, "不得重试"
    assert h.power == ["off", "on", "off"], "结算时断电隔离"
    assert len(calls) == len(lines), "每行只解析一次 tty"


def test_check_bound_echo_single_lookup(monkeypatch):
    ff = _load()
    calls = []
    monkeypatch.setattr(ff, "_tty_usb_port",
                        lambda name, base=None: calls.append(name) or "1-5.1.4")
    bound, reason = ff._check_bound_echo(_V5_BOUND_LINE, _SLOT_PORT, "S", v5_echo=True)
    assert bound == "1-5.1.4" and "!= target port" in reason
    assert calls == ["ttyACM0"]
    assert ff._check_bound_echo("BROM connected", _SLOT_PORT, "S", v5_echo=True) == (None, None)
