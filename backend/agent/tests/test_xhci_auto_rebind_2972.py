"""#2972：xHCI 自动 rebind 门控 / 熔断 / 动作幂等。"""
from __future__ import annotations

from pathlib import Path

import pytest

from backend.agent.xhci_auto_rebind import (
    EMPTY_TREE_TICKS_NEED,
    Action,
    GateInput,
    REASON_ACTIVE_DEVICES,
    REASON_ACTIVE_JOBS,
    REASON_DISABLED,
    REASON_FUSE_BOOT,
    REASON_HOST_LEDGER_EMPTY,
    REASON_MAINTENANCE,
    REASON_NOT_WHITELISTED,
    REASON_TICKS_SHORT,
    REASON_TREE_NOT_EMPTY,
    RebindFuse,
    decide,
    env_enabled,
    evaluate_gate,
    is_usb_tree_verifiably_empty,
    list_xhci_pci_ids,
    parse_host_whitelist,
    rebind_controllers,
    run_if_allowed,
)


def _gate(**overrides) -> GateInput:
    base = dict(
        host_id="172.21.15.63",
        enabled=True,
        whitelist=frozenset({"172.21.15.63"}),
        # 「可证为空」的形态：没有目标外设、没有被关键词排除的外设、agent 也没发现
        # 设备——这才是允许 rebind 的输入（#2972 复核）。
        usb_device_count=0,
        usb_root_hub_count=2,
        other_usb_nodes=0,
        host_ledger_device_rows=8,
        discovered_devices=0,
        empty_tree_ticks=EMPTY_TREE_TICKS_NEED,
        active_jobs=0,
        active_devices=0,
        in_maintenance=False,
    )
    base.update(overrides)
    return GateInput(**base)


@pytest.mark.parametrize(
    "field,value,reason",
    [
        ("enabled", False, REASON_DISABLED),
        ("whitelist", frozenset(), REASON_NOT_WHITELISTED),
        ("discovered_devices", 1, REASON_TREE_NOT_EMPTY),
        ("usb_device_count", 16, REASON_TREE_NOT_EMPTY),
        # 「2 台非 ADB 模式的手机」（L4 拓扑）在旧谓词 usb_device_count <= root_hub_count
        # 下被判成空树并放行（#2972 复核①）——这是回归钉子。
        ("usb_device_count", 2, REASON_TREE_NOT_EMPTY),
        # 被 `_NON_TARGET_USB_KEYWORDS` 排除的外设（USB 归档盘 / 网卡）在旧谓词里
        # 完全不可见，rebind 会把它们从总线拔掉。
        ("other_usb_nodes", 1, REASON_TREE_NOT_EMPTY),
        # root hub 一个都读不到 = lsusb 没采到，那是「未知」不是「空」。
        ("usb_root_hub_count", 0, REASON_TREE_NOT_EMPTY),
        # 空柜 / 闲置机：其它条件全绿，但没有「本应有设备」的证据（#2967 合取）。
        ("host_ledger_device_rows", 0, REASON_HOST_LEDGER_EMPTY),
        ("empty_tree_ticks", EMPTY_TREE_TICKS_NEED - 1, REASON_TICKS_SHORT),
        ("active_jobs", 1, REASON_ACTIVE_JOBS),
        ("active_devices", 2, REASON_ACTIVE_DEVICES),
        ("in_maintenance", True, REASON_MAINTENANCE),
    ],
)
def test_gate_each_condition_alone_blocks(field, value, reason):
    decision = evaluate_gate(_gate(**{field: value}))
    assert decision.allowed is False
    assert decision.block_reason == reason


def test_gate_all_green_allows():
    assert evaluate_gate(_gate()).allowed is True


def test_two_non_adb_phones_are_not_a_verifiably_empty_tree():
    """#2972 复核①：`<=` 把非空树读成空树——动作门控要求**可证**为空。"""
    assert is_usb_tree_verifiably_empty(2, 2, 0, 0) is False
    assert is_usb_tree_verifiably_empty(0, 2, 0, 0) is True
    assert is_usb_tree_verifiably_empty(0, 2, 1, 0) is False
    assert is_usb_tree_verifiably_empty(0, 0, 0, 0) is False
    assert is_usb_tree_verifiably_empty(None, None, 0, 0) is False


def test_destructive_action_requires_host_to_have_devices():
    """#2972 复核②：空柜/闲置机不得触发 rebind（无故障证据却消耗熔断额度）。"""
    decision = evaluate_gate(_gate(host_ledger_device_rows=0))
    assert decision.block_reason == REASON_HOST_LEDGER_EMPTY


def test_env_enabled_exact_one_only():
    assert env_enabled("1") is True
    assert env_enabled("0") is False
    assert env_enabled("true") is False
    assert env_enabled("") is False


def test_whitelist_parse():
    assert parse_host_whitelist("a, b ,") == frozenset({"a", "b"})
    assert parse_host_whitelist("") == frozenset()


def test_fuse_retry_then_hardware_suspect():
    fuse = RebindFuse()
    assert decide(evaluate_gate(_gate()), fuse)[0] == Action.REBIND
    fuse.record_attempt(now=1000.0)
    assert fuse.record_still_empty() == Action.REBIND  # 第 1 次失败，还可再试
    assert fuse.record_still_empty() == Action.STOP_HARDWARE_SUSPECT
    action, reason = decide(evaluate_gate(_gate()), fuse, now=1001.0)
    assert action == Action.STOP_HARDWARE_SUSPECT
    assert reason is not None


def test_fuse_boot_limit_needs_human():
    fuse = RebindFuse()
    fuse.record_attempt(now=1.0)
    fuse.record_attempt(now=2.0)
    action, reason = decide(evaluate_gate(_gate()), fuse, now=3.0)
    assert action == Action.STOP_NEED_HUMAN
    assert reason == REASON_FUSE_BOOT


def test_fuse_recovered_clears_episode_failures():
    fuse = RebindFuse()
    fuse.record_attempt(now=10.0)
    assert fuse.record_still_empty() == Action.REBIND
    fuse.record_recovered()
    assert fuse.episode_failures == 0
    assert decide(evaluate_gate(_gate()), fuse, now=11.0)[0] == Action.REBIND


def test_list_xhci_pci_ids_skips_control_files(tmp_path: Path):
    (tmp_path / "unbind").write_text("")
    (tmp_path / "bind").write_text("")
    (tmp_path / "module").mkdir()
    (tmp_path / "0000:00:14.0").mkdir()
    (tmp_path / "0000:3c:00.0").mkdir()
    assert list_xhci_pci_ids(tmp_path) == ["0000:00:14.0", "0000:3c:00.0"]


def test_rebind_controllers_writes_unbind_then_bind(tmp_path: Path):
    (tmp_path / "unbind").write_text("")
    (tmp_path / "bind").write_text("")
    writes: list[tuple[str, str]] = []

    def _write(path: Path, payload: str) -> None:
        writes.append((path.name, payload))

    results = rebind_controllers(
        ["0000:00:14.0"], sysfs_dir=tmp_path, write=_write,
    )
    assert results == [("0000:00:14.0", "ok")]
    assert writes == [("unbind", "0000:00:14.0"), ("bind", "0000:00:14.0")]


def test_run_if_allowed_idempotent_noop_when_gated():
    fuse = RebindFuse()
    action, reason, results = run_if_allowed(
        _gate(enabled=False), fuse, sysfs_dir="/nope",
    )
    assert action == Action.NOOP
    assert reason == REASON_DISABLED
    assert results == []
    assert fuse.boot_attempts == 0
