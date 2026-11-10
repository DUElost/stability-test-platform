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
    assert ff._parse_bound_port(line) == "1-13.2.4"
    assert ff._port_mismatch_reason(line, "1-13.2.4", "S") is None
    assert "1-13.2.4" in ff._port_mismatch_reason(line, "1-13.2.2", "S")
    assert ff._parse_bound_port("com portName is: /dev/ttyACM7") is None


def test_vbus_v5_carries_slot_port_and_brom_gate(tmp_path, monkeypatch, capsys):
    h = _v5_harness(monkeypatch, tmp_path)
    gate_calls = []
    monkeypatch.setattr(v140.ff, "_gate_other_mtk",
                        lambda port, base=None, hold_pids=None:
                        gate_calls.append(hold_pids) or {"hidden": [], "errors": {}, "target_port": port})
    payload = h.run(monkeypatch, capsys)
    assert payload["success"] is True, payload
    cmd = h.launched[-1]
    assert cmd[0].endswith("flash_tool")
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
