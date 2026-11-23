"""stp-agent-priv `xhci-rebind` 无目标参数主控恢复窄面契约与边界测试（#2972 / ADR-0037 D9）。

守什么：
1. 解析层：无目标参数、allow_abbrev=False、进入 _SUBCOMMAND_CONTRACT 与 capabilities；
2. sysfs 安全边界（exit 2 + STP_AGENT_PRIV_ERROR）：
   - 非 root 拒绝；
   - 驱动目录 /sys/bus/pci/drivers/xhci_hcd 不得为 symlink（逐级 O_NOFOLLOW | O_DIRECTORY）；
   - 属性文件 unbind / bind 必须为常规文件且以 O_WRONLY | O_NOFOLLOW 打开；
   - BDF 条目必须匹配 PCI BDF 正则且为符号链接，realpath 落于 /sys/devices/ 下且末段等于 BDF；
   - 不经 shell 或 subprocess；
3. 正常路径（exit 0 + STP_XHCI_REBIND_OK）：先写 unbind、等固定间隔、再写 bind，
   并核验驱动目录 BDF 链接重现且 O_RDONLY | O_NOFOLLOW 读 enable != 0；
4. 失败 / 部分成功 / 超时 / incident §6.1 probe -110（exit 3 + STP_XHCI_REBIND_* 标记）。
"""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT / "backend/agent/stp_agent_priv.py"


@pytest.fixture
def wrapper(monkeypatch):
    spec = importlib.util.spec_from_file_location("stp_agent_priv_xhci_rebind", WRAPPER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "_require_root", lambda: None)
    monkeypatch.setattr(module, "XHCI_REBIND_DELAY_SECONDS", 0.0)
    return module


def _make_fake_sysfs(wrapper, monkeypatch, tmp_path: Path, bdfs=("0000:00:14.0",), enable="1"):
    devices_root = tmp_path / "sysdev"
    driver_dir = tmp_path / "xhci_hcd"
    driver_dir.mkdir(parents=True)
    (driver_dir / "unbind").write_text("", encoding="utf-8")
    (driver_dir / "bind").write_text("", encoding="utf-8")
    (driver_dir / "module").symlink_to(tmp_path)  # 非 BDF 条目应被跳过

    pci_dirs: dict[str, Path] = {}
    for bdf in bdfs:
        pci_dir = devices_root / "pci0000:00" / bdf
        pci_dir.mkdir(parents=True)
        (pci_dir / "enable").write_text(f"{enable}\n", encoding="utf-8")
        (driver_dir / bdf).symlink_to(pci_dir)
        pci_dirs[bdf] = pci_dir

    monkeypatch.setattr(wrapper, "XHCI_SYSFS_BASE", str(driver_dir))
    monkeypatch.setattr(wrapper, "_SYSFS_DEVICES_ROOT", str(devices_root))
    return SimpleNamespace(driver_dir=driver_dir, devices_root=devices_root, pci_dirs=pci_dirs)


# ── 解析层 & 能力声明 ────────────────────────────────────────────────────────


def test_parser_accepts_only_bare_xhci_rebind(wrapper):
    parser = wrapper._build_parser()
    ns = parser.parse_args(["xhci-rebind"])
    assert ns.command == "xhci-rebind"
    assert wrapper._validate_parser_contract(parser) == []
    # D9.1：显式锁定子解析器 allow_abbrev=False（避免日后新增选项时被前缀缩写绕过）
    subparsers = next(
        action.choices
        for action in parser._actions
        if isinstance(getattr(action, "choices", None), dict) and action.choices
    )
    assert subparsers["xhci-rebind"].allow_abbrev is False


@pytest.mark.parametrize("argv", [
    ["xhci-rebind", "0000:00:14.0"],
    ["xhci-rebind", "--bdf", "0000:00:14.0"],
    ["xhci-rebind", "--sysfs", "/tmp"],
    ["xhci-rebind", "--timeout", "10"],
])
def test_parser_rejects_any_arguments(wrapper, argv):
    with pytest.raises(SystemExit) as exc:
        wrapper._build_parser().parse_args(argv)
    assert exc.value.code == 2


def test_capabilities_lists_xhci_rebind(wrapper, capsys):
    assert wrapper.cmd_capabilities(None, None) == 0
    assert "xhci-rebind" in capsys.readouterr().out.splitlines()


# ── 安全边界（exit 2 / PrivError）────────────────────────────────────────────


def test_non_root_is_rejected_with_exit_2(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location("stp_agent_priv_xhci_nonroot", WRAPPER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module.os, "geteuid", lambda: 1000)
    with pytest.raises(module.PrivError, match="root"):
        module.cmd_xhci_rebind(SimpleNamespace(), {})


def test_driver_dir_symlink_rejected(wrapper, monkeypatch, tmp_path):
    real_dir = tmp_path / "real_xhci"
    real_dir.mkdir()
    link_dir = tmp_path / "link_xhci"
    link_dir.symlink_to(real_dir)
    monkeypatch.setattr(wrapper, "XHCI_SYSFS_BASE", str(link_dir))
    with pytest.raises(wrapper.PrivError):
        wrapper.cmd_xhci_rebind(SimpleNamespace(), {})


@pytest.mark.parametrize("attr", ["unbind", "bind"])
def test_attribute_symlink_rejected(wrapper, monkeypatch, tmp_path, attr):
    fake = _make_fake_sysfs(wrapper, monkeypatch, tmp_path)
    target = tmp_path / "evil_file"
    target.write_text("", encoding="utf-8")
    (fake.driver_dir / attr).unlink()
    (fake.driver_dir / attr).symlink_to(target)
    with pytest.raises(wrapper.PrivError, match="regular file"):
        wrapper.cmd_xhci_rebind(SimpleNamespace(), {})


def test_bdf_entry_not_symlink_rejected(wrapper, monkeypatch, tmp_path):
    fake = _make_fake_sysfs(wrapper, monkeypatch, tmp_path, bdfs=())
    (fake.driver_dir / "0000:00:14.0").mkdir()
    with pytest.raises(wrapper.PrivError, match="symlink"):
        wrapper.cmd_xhci_rebind(SimpleNamespace(), {})


def test_bdf_symlink_outside_sys_devices_rejected(wrapper, monkeypatch, tmp_path):
    fake = _make_fake_sysfs(wrapper, monkeypatch, tmp_path, bdfs=())
    evil_dir = tmp_path / "outside" / "0000:00:14.0"
    evil_dir.mkdir(parents=True)
    (fake.driver_dir / "0000:00:14.0").symlink_to(evil_dir)
    with pytest.raises(wrapper.PrivError, match="does not resolve"):
        wrapper.cmd_xhci_rebind(SimpleNamespace(), {})


def test_bdf_symlink_mismatched_basename_rejected(wrapper, monkeypatch, tmp_path):
    fake = _make_fake_sysfs(wrapper, monkeypatch, tmp_path, bdfs=())
    other = fake.devices_root / "pci0000:00" / "0000:00:1f.0"
    other.mkdir(parents=True)
    (fake.driver_dir / "0000:00:14.0").symlink_to(other)
    with pytest.raises(wrapper.PrivError, match="does not resolve"):
        wrapper.cmd_xhci_rebind(SimpleNamespace(), {})


# ── 正常路径与不经 shell ──────────────────────────────────────────────────────


def test_happy_path_writes_unbind_delay_bind_and_verifies_enable(
    wrapper, monkeypatch, tmp_path, capsys,
):
    _make_fake_sysfs(wrapper, monkeypatch, tmp_path, bdfs=("0000:00:14.0", "0000:3c:00.0"))
    events: list[tuple[str, object]] = []

    orig_write = wrapper._write_sysfs_attr_at

    def recording_write(driver_fd, name, payload):
        events.append((name, payload))
        return orig_write(driver_fd, name, payload)

    monkeypatch.setattr(wrapper, "_write_sysfs_attr_at", recording_write)
    monkeypatch.setattr(wrapper, "XHCI_REBIND_DELAY_SECONDS", 2.0)
    monkeypatch.setattr(wrapper.time, "sleep", lambda s: events.append(("sleep", s)))
    # 确认不调用 subprocess（不经 shell/外部命令）
    monkeypatch.setattr(
        wrapper.subprocess, "run",
        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("subprocess must not be called")),
    )
    monkeypatch.setattr(
        wrapper.subprocess, "Popen",
        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("Popen must not be called")),
    )

    rc = wrapper.cmd_xhci_rebind(SimpleNamespace(), {})
    assert rc == 0
    out = capsys.readouterr().out
    assert "STP_XHCI_REBIND_OK count=2 bdfs=0000:00:14.0,0000:3c:00.0" in out
    assert events == [
        ("unbind", "0000:00:14.0"),
        ("sleep", 2.0),
        ("bind", "0000:00:14.0"),
        ("unbind", "0000:3c:00.0"),
        ("sleep", 2.0),
        ("bind", "0000:3c:00.0"),
    ]


# ── 失败 / 部分成功 / 超时 / incident §6.1 probe -110 ──────────────────────────


def test_no_pci_ids_returns_exit_3_with_marker(wrapper, monkeypatch, tmp_path, capsys):
    _make_fake_sysfs(wrapper, monkeypatch, tmp_path, bdfs=())
    rc = wrapper.cmd_xhci_rebind(SimpleNamespace(), {})
    assert rc == 3
    err = capsys.readouterr().err
    assert wrapper.XHCI_MARKER_NO_PCI_IDS in err


def test_probe_failed_enable_zero_returns_exit_3_probe_marker(wrapper, monkeypatch, tmp_path, capsys):
    _make_fake_sysfs(wrapper, monkeypatch, tmp_path, bdfs=("0000:00:14.0",), enable="0")
    rc = wrapper.cmd_xhci_rebind(SimpleNamespace(), {})
    assert rc == 3
    err = capsys.readouterr().err
    assert wrapper.XHCI_MARKER_PROBE_FAILED in err
    summary = json.loads(err.split("summary=", 1)[1])
    assert summary[0]["status"] == "probe_failed"
    assert summary[0]["detail"] == "enable_is_0"


def test_probe_failed_missing_driver_link_after_bind(wrapper, monkeypatch, tmp_path, capsys):
    fake = _make_fake_sysfs(wrapper, monkeypatch, tmp_path, bdfs=("0000:00:14.0",), enable="1")
    orig_write = wrapper._write_sysfs_attr_at

    def drop_link_on_unbind(driver_fd, name, payload):
        orig_write(driver_fd, name, payload)
        if name == "unbind":
            (fake.driver_dir / payload).unlink()

    monkeypatch.setattr(wrapper, "_write_sysfs_attr_at", drop_link_on_unbind)
    rc = wrapper.cmd_xhci_rebind(SimpleNamespace(), {})
    assert rc == 3
    err = capsys.readouterr().err
    assert wrapper.XHCI_MARKER_PROBE_FAILED in err
    assert "driver_link_missing" in err


def test_partial_success_returns_exit_3_partial_marker(wrapper, monkeypatch, tmp_path, capsys):
    _make_fake_sysfs(wrapper, monkeypatch, tmp_path, bdfs=("0000:00:14.0", "0000:3c:00.0"))
    orig_write = wrapper._write_sysfs_attr_at

    def fail_second_unbind(driver_fd, name, payload):
        if payload == "0000:3c:00.0" and name == "unbind":
            raise OSError("busy")
        return orig_write(driver_fd, name, payload)

    monkeypatch.setattr(wrapper, "_write_sysfs_attr_at", fail_second_unbind)
    rc = wrapper.cmd_xhci_rebind(SimpleNamespace(), {})
    assert rc == 3
    err = capsys.readouterr().err
    assert wrapper.XHCI_MARKER_PARTIAL in err
    summary = json.loads(err.split("summary=", 1)[1])
    assert [s["status"] for s in summary] == ["ok", "write_failed"]


def test_all_write_failed_returns_exit_3_write_failed_marker(wrapper, monkeypatch, tmp_path, capsys):
    _make_fake_sysfs(wrapper, monkeypatch, tmp_path, bdfs=("0000:00:14.0",))
    monkeypatch.setattr(
        wrapper, "_write_sysfs_attr_at",
        lambda *a, **kw: (_ for _ in ()).throw(OSError("io error")),
    )
    rc = wrapper.cmd_xhci_rebind(SimpleNamespace(), {})
    assert rc == 3
    err = capsys.readouterr().err
    assert wrapper.XHCI_MARKER_WRITE_FAILED in err


def test_timeout_during_blocking_probe_returns_exit_3_timeout_marker(
    wrapper, monkeypatch, tmp_path, capsys,
):
    _make_fake_sysfs(wrapper, monkeypatch, tmp_path, bdfs=("0000:00:14.0", "0000:3c:00.0"))
    monkeypatch.setattr(wrapper, "XHCI_REBIND_TIMEOUT_SECONDS", 0.05)

    def blocking_write(driver_fd, name, payload):
        if name == "bind":
            # 模拟内核同步 probe 阻塞，触发 SIGALRM 中断
            os.kill(os.getpid(), wrapper.signal.SIGALRM)

    monkeypatch.setattr(wrapper, "_write_sysfs_attr_at", blocking_write)
    rc = wrapper.cmd_xhci_rebind(SimpleNamespace(), {})
    assert rc == 3
    err = capsys.readouterr().err
    assert wrapper.XHCI_MARKER_TIMEOUT in err
    summary = json.loads(err.split("summary=", 1)[1])
    assert len(summary) == 2
    assert all(item["status"] == "timeout" for item in summary)
