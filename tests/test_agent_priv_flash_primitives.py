"""stp-agent-priv flash 链窄面（ADR-0037 D5 / #2133）：ensure-udev-rule / usb-authorized。

守什么：
- 端口名校验在路径拼接前拒绝任何路径语义（`..`、`/`、空白、尾随换行、注入）；
- 设备目录**允许内核 sysfs 符号链接**（`/sys/bus/usb/devices/<port>` 指向
  `/sys/devices/.../<port>`，这是内核标准布局），但解析结果必须是
  `/sys/devices` 下与端口名同名的真实目录——解析到别处/同名不符即拒；
- 属性文件（idVendor / authorized）一律 O_NOFOLLOW；sysfs 属性原地写；
- 目标须为 idVendor=0e8d；值仅 {0,1}（解析层 choices + 执行层复核）；
- udev 规则：固定路径/内容、幂等（内容一致不重写）、原子替换不跟随 symlink、
  reload 失败即拒绝、trigger 尽力而为；
- 与已发布 flash 脚本同源常量（规则行 / sysfs 根），契约不随实现漂移。

加载方式与 tests/test_agent_priv_boundary.py 同（单文件脚本，不拉起包导入）。
"""

from __future__ import annotations

import importlib.util
import os
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT / "backend/agent/stp_agent_priv.py"


@pytest.fixture
def wrapper(monkeypatch):
    spec = importlib.util.spec_from_file_location("stp_agent_priv_flash", WRAPPER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "_require_root", lambda: None)
    return module


def _fake_sysfs(tmp_path: Path, port: str, vendor: str = "0e8d",
                authorized: str = "1", *, real_name: str | None = None):
    """构造内核同形假 sysfs：bus 侧 <port> 是指向 devices 侧 <real_name> 的符号链接。"""
    devices_root = tmp_path / "sysdev"
    real_dir = devices_root / "pci0000:00" / "usb1" / (real_name or port)
    real_dir.mkdir(parents=True)
    (real_dir / "idVendor").write_text(vendor + "\n", encoding="utf-8")
    (real_dir / "authorized").write_text(authorized + "\n", encoding="utf-8")
    bus_base = tmp_path / "bususb"
    bus_base.mkdir(exist_ok=True)
    (bus_base / port).symlink_to(real_dir)
    return bus_base, devices_root, real_dir


def _patch_sysfs(wrapper, monkeypatch, bus_base: Path, devices_root: Path) -> None:
    monkeypatch.setattr(wrapper, "USB_SYSFS_BASE", str(bus_base))
    monkeypatch.setattr(wrapper, "_SYSFS_DEVICES_ROOT", str(devices_root))


# ── 端口名校验（纯函数）────────────────────────────────────────────────────


@pytest.mark.parametrize("port", ["1-1", "1-10", "2-1", "1-5.3.1", "1-5.2.4", "3-2.1.4.7"])
def test_usb_port_accepts_real_sysfs_names(wrapper, port):
    assert wrapper.validate_usb_port(port) == port


@pytest.mark.parametrize(
    "bad",
    [
        "", "..", "../..", ".", "1-1/2", "1-1/../..", "1-5.3.1/", "/1-1",
        "1-1 ", " 1-1", "1-1\n", "*", "1-1;id", "1-1$(id)", "１-１", "1_1", None, 11,
    ],
)
def test_usb_port_rejects_path_semantics_and_injection(wrapper, bad):
    with pytest.raises(wrapper.PrivError):
        wrapper.validate_usb_port(bad)


def test_kernel_device_path_predicate(wrapper, monkeypatch, tmp_path):
    devices_root = tmp_path / "sysdev"
    monkeypatch.setattr(wrapper, "_SYSFS_DEVICES_ROOT", str(devices_root))
    real = devices_root / "pci0000:00" / "usb1" / "1-5.3.1"
    real.mkdir(parents=True)
    assert wrapper.is_kernel_usb_device_path(str(real), "1-5.3.1") is True
    # 同名不符 / 不在 devices 树下 → 拒
    assert wrapper.is_kernel_usb_device_path(str(real), "1-5.3.2") is False
    assert wrapper.is_kernel_usb_device_path(str(tmp_path / "elsewhere" / "1-5.3.1"), "1-5.3.1") is False
    assert wrapper.is_kernel_usb_device_path(str(devices_root), "sysdev") is False


# ── usb-authorized：功能与拒绝面 ───────────────────────────────────────────


def test_usb_authorized_writes_through_kernel_symlink(wrapper, monkeypatch, tmp_path, capsys):
    bus_base, devices_root, real_dir = _fake_sysfs(tmp_path, "1-5.3.1", authorized="1")
    _patch_sysfs(wrapper, monkeypatch, bus_base, devices_root)

    assert wrapper.cmd_usb_authorized(
        SimpleNamespace(port="1-5.3.1", value="0"), None) == 0
    assert (real_dir / "authorized").read_text(encoding="utf-8") == "0\n"
    assert "STP_USB_AUTHORIZED_OK port=1-5.3.1 value=0" in capsys.readouterr().out

    assert wrapper.cmd_usb_authorized(
        SimpleNamespace(port="1-5.3.1", value="1"), None) == 0
    assert (real_dir / "authorized").read_text(encoding="utf-8") == "1\n"


def test_usb_authorized_rejects_non_mtk_vendor(wrapper, monkeypatch, tmp_path):
    bus_base, devices_root, real_dir = _fake_sysfs(tmp_path, "1-4", vendor="1234")
    _patch_sysfs(wrapper, monkeypatch, bus_base, devices_root)

    with pytest.raises(wrapper.PrivError) as exc:
        wrapper.cmd_usb_authorized(SimpleNamespace(port="1-4", value="0"), None)
    assert "MediaTek" in str(exc.value)
    assert (real_dir / "authorized").read_text(encoding="utf-8") == "1\n"


def test_usb_authorized_rejects_bad_value_at_execute_layer(wrapper, monkeypatch, tmp_path):
    """解析层 choices 之外，执行层复核独立生效（防跨层接线漂移）。"""
    bus_base, devices_root, real_dir = _fake_sysfs(tmp_path, "1-1")
    _patch_sysfs(wrapper, monkeypatch, bus_base, devices_root)

    with pytest.raises(wrapper.PrivError):
        wrapper.cmd_usb_authorized(SimpleNamespace(port="1-1", value="2"), None)
    with pytest.raises(wrapper.PrivError):
        wrapper.cmd_usb_authorized(SimpleNamespace(port="1-1", value="0\ntrue"), None)
    assert (real_dir / "authorized").read_text(encoding="utf-8") == "1\n"


def test_usb_authorized_rejects_missing_port(wrapper, monkeypatch, tmp_path):
    bus_base, devices_root, _ = _fake_sysfs(tmp_path, "1-1")
    _patch_sysfs(wrapper, monkeypatch, bus_base, devices_root)

    with pytest.raises(wrapper.PrivError) as exc:
        wrapper.cmd_usb_authorized(SimpleNamespace(port="1-9", value="0"), None)
    assert "does not resolve to a kernel sysfs USB device" in str(exc.value)


def test_usb_authorized_rejects_symlink_escaping_devices_root(wrapper, monkeypatch, tmp_path):
    """符号链接解析到 /sys/devices 之外（非内核设备树）→ 拒，且不写任何文件。"""
    bus_base, devices_root, _ = _fake_sysfs(tmp_path, "1-1")
    elsewhere = tmp_path / "elsewhere" / "1-2"
    elsewhere.mkdir(parents=True)
    (elsewhere / "idVendor").write_text("0e8d\n", encoding="utf-8")
    (elsewhere / "authorized").write_text("1\n", encoding="utf-8")
    (bus_base / "1-2").symlink_to(elsewhere)
    _patch_sysfs(wrapper, monkeypatch, bus_base, devices_root)

    with pytest.raises(wrapper.PrivError) as exc:
        wrapper.cmd_usb_authorized(SimpleNamespace(port="1-2", value="0"), None)
    assert "does not resolve to a kernel sysfs USB device" in str(exc.value)
    assert (elsewhere / "authorized").read_text(encoding="utf-8") == "1\n"


def test_usb_authorized_rejects_port_name_mismatch(wrapper, monkeypatch, tmp_path):
    """符号链接真相目录名与端口名不符（非该端口的设备）→ 拒。"""
    bus_base, devices_root, real_dir = _fake_sysfs(tmp_path, "1-9", real_name="1-8")
    _patch_sysfs(wrapper, monkeypatch, bus_base, devices_root)

    with pytest.raises(wrapper.PrivError):
        wrapper.cmd_usb_authorized(SimpleNamespace(port="1-9", value="0"), None)
    assert (real_dir / "authorized").read_text(encoding="utf-8") == "1\n"


def test_usb_authorized_rejects_plain_dir_outside_devices_tree(wrapper, monkeypatch, tmp_path):
    """bus 侧同名普通目录（非内核符号链接形态）→ 拒。"""
    bus_base, devices_root, _ = _fake_sysfs(tmp_path, "1-1")
    (bus_base / "1-3").mkdir()
    (bus_base / "1-3" / "idVendor").write_text("0e8d\n", encoding="utf-8")
    (bus_base / "1-3" / "authorized").write_text("1\n", encoding="utf-8")
    _patch_sysfs(wrapper, monkeypatch, bus_base, devices_root)

    with pytest.raises(wrapper.PrivError) as exc:
        wrapper.cmd_usb_authorized(SimpleNamespace(port="1-3", value="0"), None)
    assert "does not resolve" in str(exc.value)
    assert (bus_base / "1-3" / "authorized").read_text(encoding="utf-8") == "1\n"


def test_usb_authorized_rejects_symlinked_authorized_file(wrapper, monkeypatch, tmp_path):
    """authorized 本身是 symlink → O_NOFOLLOW 拒绝，且不写穿到 symlink 目标。"""
    bus_base, devices_root, real_dir = _fake_sysfs(tmp_path, "1-7")
    sentinel = tmp_path / "sentinel.txt"
    sentinel.write_text("SENTINEL\n", encoding="utf-8")
    (real_dir / "authorized").unlink()
    (real_dir / "authorized").symlink_to(sentinel)
    _patch_sysfs(wrapper, monkeypatch, bus_base, devices_root)

    with pytest.raises(wrapper.PrivError) as exc:
        wrapper.cmd_usb_authorized(SimpleNamespace(port="1-7", value="0"), None)
    assert "cannot open authorized" in str(exc.value)
    assert sentinel.read_text(encoding="utf-8") == "SENTINEL\n"


# ── sysfs 属性读取语义（#2160：st_size 恒 4096，不得据此判大小）────────────


def test_usb_authorized_reads_vendor_despite_sysfs_st_size(wrapper, monkeypatch, tmp_path, capsys):
    """sysfs 语义回归：属性 st_size=4096 而内容 5 字节——仍须可读。

    复刻内核语义：patch fstat 让 st_size 恒 4096（真实文件仍是 5 字节）。
    旧实现用 `st_size > limit` 判「input too large」，真机门控全线失败
    （#2160）；本用例在旧实现下必红。
    """
    bus_base, devices_root, real_dir = _fake_sysfs(tmp_path, "1-2")
    _patch_sysfs(wrapper, monkeypatch, bus_base, devices_root)

    real_fstat = os.fstat

    def sysfs_like_fstat(fd):
        st = real_fstat(fd)
        return SimpleNamespace(st_mode=st.st_mode, st_size=4096)

    monkeypatch.setattr(wrapper.os, "fstat", sysfs_like_fstat)

    assert wrapper.cmd_usb_authorized(
        SimpleNamespace(port="1-2", value="0"), None) == 0
    assert (real_dir / "authorized").read_text(encoding="utf-8") == "0\n"
    assert "STP_USB_AUTHORIZED_OK port=1-2 value=0" in capsys.readouterr().out


def test_read_sysfs_attr_on_real_sysfs(wrapper):
    """真实 sysfs 属性读取冒烟（st_size=4096 / 内容短）。"""
    base, name = "/sys/devices/system/cpu", "online"
    if not os.path.exists(f"{base}/{name}"):
        pytest.skip("no sysfs cpu/online in this environment")
    descriptor = os.open(base, os.O_RDONLY | os.O_DIRECTORY)
    try:
        body = wrapper._read_sysfs_attr(descriptor, name, 64)
    finally:
        os.close(descriptor)
    assert body.strip()


def test_read_sysfs_attr_rejects_empty_and_oversize(wrapper, tmp_path):
    (tmp_path / "empty").write_text("", encoding="utf-8")
    (tmp_path / "big").write_text("x" * 100, encoding="utf-8")
    descriptor = os.open(str(tmp_path), os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises(wrapper.PrivError):
            wrapper._read_sysfs_attr(descriptor, "empty", 64)
        with pytest.raises(wrapper.PrivError):
            wrapper._read_sysfs_attr(descriptor, "big", 8)
    finally:
        os.close(descriptor)


def test_read_sysfs_attr_rejects_symlink(wrapper, tmp_path):
    sentinel = tmp_path / "sentinel"
    sentinel.write_text("0e8d\n", encoding="utf-8")
    (tmp_path / "attr").symlink_to(sentinel)
    descriptor = os.open(str(tmp_path), os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises((wrapper.PrivError, OSError)):
            wrapper._read_sysfs_attr(descriptor, "attr", 64)
    finally:
        os.close(descriptor)


# ── ensure-udev-rule：固定面 / 幂等 / symlink 安全 / reload ────────────────


def _prepare_udev_env(wrapper, monkeypatch, tmp_path):
    rules_dir = tmp_path / "rules"
    fake_udevadm = tmp_path / "udevadm"
    fake_udevadm.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake_udevadm.chmod(0o755)
    monkeypatch.setattr(wrapper, "UDEV_RULE_PATH", str(rules_dir / "98-ttyacm-mtk.rules"))
    monkeypatch.setattr(wrapper, "UDEVADM_BIN", str(fake_udevadm))
    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        return 0, "", ""

    monkeypatch.setattr(wrapper, "_run", fake_run)
    return rules_dir / "98-ttyacm-mtk.rules", fake_udevadm, calls


def test_ensure_udev_rule_writes_fixed_content_and_reloads(wrapper, monkeypatch, tmp_path, capsys):
    rule_path, fake_udevadm, calls = _prepare_udev_env(wrapper, monkeypatch, tmp_path)

    assert wrapper.cmd_ensure_udev_rule(SimpleNamespace(), None) == 0
    assert rule_path.read_text(encoding="utf-8") == wrapper.UDEV_RULE_LINE
    assert stat.S_IMODE(rule_path.stat().st_mode) == 0o644
    assert calls == [
        [str(fake_udevadm), "control", "--reload"],
        [str(fake_udevadm), "trigger"],
    ]
    assert "STP_ENSURE_UDEV_RULE_OK path=%s changed=1" % rule_path in capsys.readouterr().out


def test_ensure_udev_rule_is_idempotent(wrapper, monkeypatch, tmp_path, capsys):
    rule_path, fake_udevadm, calls = _prepare_udev_env(wrapper, monkeypatch, tmp_path)

    assert wrapper.cmd_ensure_udev_rule(SimpleNamespace(), None) == 0
    first_body = rule_path.read_text(encoding="utf-8")
    capsys.readouterr()

    assert wrapper.cmd_ensure_udev_rule(SimpleNamespace(), None) == 0
    assert rule_path.read_text(encoding="utf-8") == first_body
    assert "changed=0" in capsys.readouterr().out
    assert calls.count([str(fake_udevadm), "control", "--reload"]) == 2


def test_ensure_udev_rule_rewrites_stale_content(wrapper, monkeypatch, tmp_path):
    rule_path, _, _ = _prepare_udev_env(wrapper, monkeypatch, tmp_path)
    rule_path.parent.mkdir(parents=True, exist_ok=True)
    rule_path.write_text("KERNEL==\"ttyACM*\", MODE=\"0600\"\n", encoding="utf-8")

    assert wrapper.cmd_ensure_udev_rule(SimpleNamespace(), None) == 0
    assert rule_path.read_text(encoding="utf-8") == wrapper.UDEV_RULE_LINE


def test_ensure_udev_rule_reload_failure_is_rejected(wrapper, monkeypatch, tmp_path):
    _prepare_udev_env(wrapper, monkeypatch, tmp_path)

    def failing_run(argv, **kwargs):
        return 1, "", "reload boom"

    monkeypatch.setattr(wrapper, "_run", failing_run)
    with pytest.raises(wrapper.PrivError) as exc:
        wrapper.cmd_ensure_udev_rule(SimpleNamespace(), None)
    assert "reload" in str(exc.value)


def test_ensure_udev_rule_replaces_symlink_without_following(wrapper, monkeypatch, tmp_path):
    """规则路径被换成 symlink（指向攻击者目标）→ 原子替换 symlink 本身，目标不动。"""
    rule_path, _, _ = _prepare_udev_env(wrapper, monkeypatch, tmp_path)
    rule_path.parent.mkdir(parents=True, exist_ok=True)
    sentinel = tmp_path / "passwd_like"
    sentinel.write_text("SENTINEL\n", encoding="utf-8")
    rule_path.symlink_to(sentinel)

    assert wrapper.cmd_ensure_udev_rule(SimpleNamespace(), None) == 0
    assert not rule_path.is_symlink()
    assert rule_path.read_text(encoding="utf-8") == wrapper.UDEV_RULE_LINE
    assert sentinel.read_text(encoding="utf-8") == "SENTINEL\n"


def test_flash_primitives_constants_match_published_scripts(wrapper):
    """与已发布 flash 脚本的同源常量逐字对齐（跨仓漂移即红）。"""
    preflight = (ROOT / "backend/agent/scripts/flash_preflight/v1.0.1/flash_preflight.py").read_text(
        encoding="utf-8")
    assert wrapper.UDEV_RULE_PATH in preflight
    assert wrapper.UDEV_RULE_LINE.strip() in preflight
    assert wrapper.USB_SYSFS_BASE == "/sys/bus/usb/devices"
    assert wrapper._MTK_VENDOR_ID == "0e8d"
