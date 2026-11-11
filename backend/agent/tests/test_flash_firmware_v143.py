"""flash_firmware v1.4.3（#3892，15.93 LX3 金丝雀 PlanRun 1265 / Job 206967）：sysfs 端口解析的 PCI 前缀。

真实 x86 路径
`/sys/devices/pci0000:00/0000:00:14.0/usb1/1-13/1-13.2/1-13.2.2/1-13.2.2:1.0/tty/ttyACM0`
在 USB 端口之前含两个带冒号的 PCI 段。1.4.1/1.4.2 的 `_tty_usb_port` 遇第一个冒号即
break，返回 None，导致实时核对与采样历史都为空；1.4.3 只把 `<端口>:<配置>.<接口>`
形态当作接口边界。
"""
from backend.agent.tests import test_flash_firmware_v140 as v140

ff = v140.ff
_SLOT = "1-13.2.2"


def _sysfs_tty(tmp_path, port=_SLOT, name="ttyACM0", pci=True):
    parts = port.split(".")
    chain = [parts[0]] + [".".join(parts[:i]) for i in range(2, len(parts) + 1)]
    dev = tmp_path / "devices"
    if pci:
        dev = dev / "pci0000:00" / "0000:00:14.0"
    dev = dev / "usb1"
    for seg in chain:
        dev = dev / seg
    dev = dev / f"{port}:1.0" / "tty" / name
    dev.mkdir(parents=True)
    tty = tmp_path / "class_tty"
    tty.mkdir(exist_ok=True)
    (tty / name).symlink_to(dev)
    return tty


def test_tty_usb_port_skips_pci_components(tmp_path):
    """带 `pci0000:00` 前缀时仍解析出槽位口（1.4.2 在此返回 None）。"""
    tty = _sysfs_tty(tmp_path)
    assert ff._tty_usb_port("ttyACM0", str(tty)) == _SLOT


def test_tty_usb_port_without_pci_prefix_unchanged(tmp_path):
    """不带 PCI 前缀的旧形态继续按接口目录截断。"""
    tty = _sysfs_tty(tmp_path, pci=False)
    assert ff._tty_usb_port("ttyACM0", str(tty)) == _SLOT


def test_tty_usb_port_missing_node_returns_none(tmp_path):
    tty = _sysfs_tty(tmp_path)
    assert ff._tty_usb_port("ttyACM9", str(tty)) is None


def test_history_remembers_vanished_node_with_pci_prefix(tmp_path):
    """采样历史在带 PCI 前缀时也能记住已消失的节点。"""
    tty = _sysfs_tty(tmp_path)
    hist = ff._TtyPortHistory(base=str(tty))
    hist.sample()
    (tty / "ttyACM0").unlink()
    assert ff._tty_usb_port("ttyACM0", str(tty)) is None
    hist.sample()
    assert hist.ports("ttyACM0") == {_SLOT}
    assert hist.snapshot() == {"ttyACM0": [_SLOT]}
