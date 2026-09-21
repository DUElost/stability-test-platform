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
    REASON_MAINTENANCE,
    REASON_NOT_WHITELISTED,
    REASON_TICKS_SHORT,
    REASON_TREE_NOT_EMPTY,
    RebindFuse,
    decide,
    env_enabled,
    evaluate_gate,
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
        usb_device_count=2,
        usb_root_hub_count=2,
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
