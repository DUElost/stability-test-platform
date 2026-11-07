"""stp-agent-priv `usb-power` 切电窄面（#3892 / ADR-0063 D1–D2 / ADR-0037 D8）。

守什么：

- **调用方只给 serial**：槽位只来自 root 属主映射文件；未登记 serial 被拒，
  解析层不存在 `--location` / `--port` / 任何 uhubctl 选项；`allow_abbrev=False`；
- **映射文件整份校验**：schema、字段集合、serial / hub_location / port 取值，
  serial 重复或槽位重复时整份拒绝；文件与父目录必须可信属主、非 group/other 可写，
  文件不跟随 symlink；
- **delay 只对 cycle 有效**，取值 1..30；
- **执行面固定**：`uhubctl -l <loc> -p <port> -a <action> [-d <n>]`，环境清空，
  uhubctl 本体必须可信属主；Hub 位置必须解析为 `/sys/devices` 下同名真实目录；
  非零退出 / 超时一律 exit 3 + `STP_USB_POWER_*` 标记。
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT / "backend/agent/stp_agent_priv.py"
SERIAL = "MTK0001"


@pytest.fixture
def wrapper(monkeypatch):
    spec = importlib.util.spec_from_file_location("stp_agent_priv_usb_power", WRAPPER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "_require_root", lambda: None)
    monkeypatch.setattr(module, "USB_POWER_TRUSTED_UID", os.getuid())
    return module


def _slots_doc(*entries):
    return {"schema": 1, "slots": [
        {"serial": s, "hub_location": loc, "port": port} for s, loc, port in entries
    ]}


@pytest.fixture
def host(wrapper, monkeypatch, tmp_path):
    """假主机：映射文件、内核同形 sysfs（bus 侧符号链接）、记录 argv 与环境的 uhubctl 桩。"""
    conf_dir = tmp_path / "etc-stp-agent"
    conf_dir.mkdir(mode=0o755)
    slots = conf_dir / "usb-power-slots.json"
    slots.write_text(json.dumps(_slots_doc((SERIAL, "1-1.4", 3))), encoding="utf-8")
    slots.chmod(0o644)

    devices_root = tmp_path / "sysdev"
    real_hub = devices_root / "pci0000:00" / "usb1" / "1-1" / "1-1.4"
    real_hub.mkdir(parents=True)
    bus = tmp_path / "bususb"
    bus.mkdir()
    (bus / "1-1.4").symlink_to(real_hub)

    stub = tmp_path / "uhubctl"
    stub.write_text(
        "#!/bin/sh\n"
        'printf "%s\\n" "$@" > "$0.argv"\n'
        'env > "$0.env"\n'
        'exit "${STUB_RC:-0}"\n',
        encoding="utf-8",
    )
    stub.chmod(0o755)

    monkeypatch.setattr(wrapper, "USB_POWER_SLOTS_PATH", str(slots))
    monkeypatch.setattr(wrapper, "USB_SYSFS_BASE", str(bus))
    monkeypatch.setattr(wrapper, "_SYSFS_DEVICES_ROOT", str(devices_root))
    monkeypatch.setattr(wrapper, "UHUBCTL_BIN", str(stub))
    return SimpleNamespace(slots=slots, conf_dir=conf_dir, stub=stub, bus=bus,
                           devices_root=devices_root)


def _args(serial=SERIAL, action="cycle", delay=12):
    return SimpleNamespace(serial=serial, action=action, delay=delay)


def _argv(stub: Path) -> list[str]:
    return (stub.parent / (stub.name + ".argv")).read_text(encoding="utf-8").splitlines()


# ── 解析层 ───────────────────────────────────────────────────────────────


def test_parser_wires_only_serial_action_delay(wrapper):
    parser = wrapper._build_parser()
    ns = parser.parse_args(["usb-power", "--serial", SERIAL, "--action", "cycle", "--delay", "12"])
    assert (ns.command, ns.serial, ns.action, ns.delay) == ("usb-power", SERIAL, "cycle", 12)
    ns = parser.parse_args(["usb-power", "--serial", SERIAL, "--action", "off"])
    assert ns.delay is None


@pytest.mark.parametrize("argv", [
    ["usb-power", "--serial", SERIAL],
    ["usb-power", "--action", "on"],
    ["usb-power", "--serial", SERIAL, "--action", "toggle"],
    ["usb-power", "--serial", SERIAL, "--action", "on", "--location", "1-1"],
    ["usb-power", "--serial", SERIAL, "--action", "on", "--port", "3"],
    ["usb-power", "--ser", SERIAL, "--action", "on"],
    ["usb-power", "--serial", SERIAL, "--action", "cycle", "--del", "3"],
    ["usb-power", "--serial", SERIAL, "--action", "cycle", "--delay", "1.5"],
])
def test_parser_rejects_everything_else(wrapper, argv):
    with pytest.raises(SystemExit) as excinfo:
        wrapper._build_parser().parse_args(argv)
    assert excinfo.value.code == 2


def test_contract_and_capabilities_include_usb_power(wrapper, capsys):
    assert wrapper._validate_parser_contract(wrapper._build_parser()) == []
    assert "usb-power" in wrapper._SUBCOMMAND_CONTRACT
    wrapper.cmd_capabilities(None, None)
    assert "usb-power" in capsys.readouterr().out.splitlines()


# ── 纯函数校验 ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("bad", ["", "a" * 65, "AB C", "AB/C", "AB\n", "AB;id", "$(id)", None, 1])
def test_serial_validation_rejects(wrapper, bad):
    with pytest.raises(wrapper.PrivError):
        wrapper.validate_usb_power_serial(bad)


@pytest.mark.parametrize("action,delay", [
    ("on", 5), ("off", 5), ("cycle", 0), ("cycle", 31), ("cycle", -1), ("cycle", True),
    ("reset", None),
])
def test_request_validation_rejects(wrapper, action, delay):
    with pytest.raises(wrapper.PrivError):
        wrapper.validate_usb_power_request(action, delay)


@pytest.mark.parametrize("action,delay", [("on", None), ("off", None), ("cycle", None),
                                          ("cycle", 1), ("cycle", 30)])
def test_request_validation_accepts(wrapper, action, delay):
    assert wrapper.validate_usb_power_request(action, delay) == (action, delay)


@pytest.mark.parametrize("doc", [
    [],
    {"schema": 2, "slots": []},
    {"schema": 1},
    {"schema": 1, "slots": {}},
    {"schema": 1, "slots": [{"serial": SERIAL, "hub_location": "1-1.4"}]},
    {"schema": 1, "slots": [{"serial": SERIAL, "hub_location": "1-1.4", "port": 3, "x": 1}]},
    _slots_doc(("bad serial", "1-1.4", 3)),
    _slots_doc((SERIAL, "../1-1", 3)),
    _slots_doc((SERIAL, "1-1.4", 0)),
    _slots_doc((SERIAL, "1-1.4", 17)),
    _slots_doc((SERIAL, "1-1.4", "3")),
    _slots_doc((SERIAL, "1-1.4", True)),
    _slots_doc((SERIAL, "1-1.4", 3), (SERIAL, "1-1.4", 4)),
    _slots_doc((SERIAL, "1-1.4", 3), ("OTHER", "1-1.4", 3)),
])
def test_slots_file_is_rejected_as_a_whole(wrapper, doc):
    with pytest.raises(wrapper.PrivError):
        wrapper.parse_usb_power_slots(json.dumps(doc))


def test_slots_file_parses(wrapper):
    doc = _slots_doc((SERIAL, "1-1.4", 3), ("OTHER", "1-1.4", 4), ("THIRD", "2-1", 1))
    assert wrapper.parse_usb_power_slots(json.dumps(doc)) == {
        SERIAL: ("1-1.4", 3), "OTHER": ("1-1.4", 4), "THIRD": ("2-1", 1),
    }


def test_argv_is_fixed(wrapper):
    assert wrapper.build_usb_power_argv("1-1.4", 3, "off", None) == [
        wrapper.UHUBCTL_BIN, "-l", "1-1.4", "-p", "3", "-a", "off"]
    assert wrapper.build_usb_power_argv("1-1.4", 3, "cycle", 12) == [
        wrapper.UHUBCTL_BIN, "-l", "1-1.4", "-p", "3", "-a", "cycle", "-d", "12"]


# ── 执行面 ───────────────────────────────────────────────────────────────


def test_cycle_runs_fixed_argv_with_clean_env(wrapper, host, capsys, monkeypatch):
    monkeypatch.setenv("LEAK_ME", "1")
    assert wrapper.cmd_usb_power(_args(), None) == 0
    assert _argv(host.stub) == ["-l", "1-1.4", "-p", "3", "-a", "cycle", "-d", "12"]
    env = (host.stub.parent / "uhubctl.env").read_text(encoding="utf-8")
    assert "LEAK_ME" not in env
    assert "STP_USB_POWER_OK serial=MTK0001 location=1-1.4 port=3 action=cycle" in capsys.readouterr().out


def test_unregistered_serial_is_rejected_before_exec(wrapper, host):
    with pytest.raises(wrapper.PrivError, match="no slot"):
        wrapper.cmd_usb_power(_args(serial="NOTLISTED"), None)
    assert not (host.stub.parent / "uhubctl.argv").exists()


def test_slots_file_symlink_is_not_followed(wrapper, host, tmp_path):
    real = tmp_path / "elsewhere.json"
    real.write_text(host.slots.read_text(encoding="utf-8"), encoding="utf-8")
    host.slots.unlink()
    host.slots.symlink_to(real)
    with pytest.raises(wrapper.PrivError, match="cannot open"):
        wrapper.cmd_usb_power(_args(), None)


@pytest.mark.parametrize("mode", [0o664, 0o646])
def test_writable_slots_file_is_rejected(wrapper, host, mode):
    host.slots.chmod(mode)
    with pytest.raises(wrapper.PrivError, match="group/other writable"):
        wrapper.cmd_usb_power(_args(), None)


def test_writable_slots_dir_is_rejected(wrapper, host):
    host.conf_dir.chmod(0o777)
    with pytest.raises(wrapper.PrivError, match="slots dir"):
        wrapper.cmd_usb_power(_args(), None)


def test_untrusted_owner_is_rejected(wrapper, host, monkeypatch):
    monkeypatch.setattr(wrapper, "USB_POWER_TRUSTED_UID", os.getuid() + 1)
    with pytest.raises(wrapper.PrivError, match="owned by uid"):
        wrapper.cmd_usb_power(_args(), None)


def test_writable_uhubctl_is_rejected(wrapper, host):
    host.stub.chmod(0o777)
    with pytest.raises(wrapper.PrivError, match="group/other writable"):
        wrapper.cmd_usb_power(_args(), None)
    assert not (host.stub.parent / "uhubctl.argv").exists()


def test_hub_location_must_resolve_into_kernel_device_tree(wrapper, host, tmp_path):
    (host.bus / "1-1.4").unlink()
    fake = tmp_path / "fake" / "1-1.4"
    fake.mkdir(parents=True)
    (host.bus / "1-1.4").symlink_to(fake)
    with pytest.raises(wrapper.PrivError, match="kernel sysfs"):
        wrapper.cmd_usb_power(_args(), None)


def test_nonzero_exit_reports_failure(wrapper, host, monkeypatch, capsys):
    # 桩从 STUB_RC 读退出码；wrapper 清空了环境，所以改写桩本身。
    host.stub.write_text("#!/bin/sh\necho 'No compatible devices' >&2\nexit 1\n", encoding="utf-8")
    assert wrapper.cmd_usb_power(_args(action="off", delay=None), None) == 3
    assert "STP_USB_POWER_FAILED rc=1" in capsys.readouterr().err


def test_timeout_reports_failure(wrapper, host, monkeypatch, capsys):
    def boom(*_a, **_k):
        raise subprocess.TimeoutExpired(cmd="uhubctl", timeout=60)

    monkeypatch.setattr(wrapper.subprocess, "run", boom)
    assert wrapper.cmd_usb_power(_args(), None) == 3
    assert "STP_USB_POWER_TIMEOUT seconds=60" in capsys.readouterr().err


def test_main_maps_rejection_to_exit_2(wrapper, host, monkeypatch, capsys):
    monkeypatch.setattr(wrapper, "_load_conf", lambda _p: {})
    assert wrapper.main(["usb-power", "--serial", "NOTLISTED", "--action", "on"]) == 2
    assert "STP_AGENT_PRIV_ERROR" in capsys.readouterr().err


# ── 下发链与 wrapper 同源（ADR-0063 D1）──────────────────────────────────


def test_provisioning_playbook_writes_the_path_and_shape_the_wrapper_reads(wrapper):
    import yaml

    playbook = ROOT / "tools/ansible/playbooks/ensure_usb_power_slots.yml"
    plays = yaml.safe_load(playbook.read_text(encoding="utf-8"))
    play = plays[0]
    assert play["vars"]["stp_usb_power_slots_path"] == wrapper.USB_POWER_SLOTS_PATH
    assert play["vars"]["stp_usb_power_slots_dir"] == os.path.dirname(wrapper.USB_POWER_SLOTS_PATH)
    tasks = {t["name"]: t for t in play["tasks"]}
    write = tasks["Write slot mapping file (#3892)"]["ansible.builtin.copy"]
    assert (write["owner"], write["group"], write["mode"]) == ("root", "root", "0644")
    assert "'schema': 1" in write["content"]
    folder = tasks["Ensure slot mapping directory is root-owned (#3892)"]["ansible.builtin.file"]
    assert (folder["owner"], folder["mode"]) == ("root", "0755")
    # 渲染形态与 wrapper 解析器一致：Ansible 的 to_nice_json 等价于 json.dumps。
    rendered = json.dumps({"schema": 1, "slots": [
        {"serial": SERIAL, "hub_location": "1-1.4", "port": 3}]}, indent=4)
    assert wrapper.parse_usb_power_slots(rendered) == {SERIAL: ("1-1.4", 3)}
