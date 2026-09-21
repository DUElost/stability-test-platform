"""#2983：控制面 host 健康探针——解析/对账纯函数。"""
from __future__ import annotations

from backend.services.host_health_probe import (
    ProbeRound,
    ProbeVerdict,
    TOPOLOGY_BLIND,
    TOPOLOGY_EMPTY_CABINET,
    TOPOLOGY_OK,
    classify_lsusb_topology,
    consecutive_strike_open,
    parse_journal_probe,
    reconcile_agent_health,
)

# .102 死亡定式（与 kernel_usb_faults / issue 同字面）
_HC_DIED_JOURNAL = [
    "xhci_hcd 0000:00:14.0: xHCI host not responding to stop endpoint command",
    "xhci_hcd 0000:00:14.0: xHCI host controller not responding, assume dead",
    "xhci_hcd 0000:00:14.0: HC died; cleaning up",
]

_LSUSB_BLIND = [
    "Bus 001 Device 001: ID 1d6b:0002 Linux Foundation 2.0 root hub",
    "Bus 001 Device 002: ID 0bda:5411 Realtek Semiconductor Corp. Hub",
    "Bus 001 Device 003: ID 0bda:5411 Realtek Semiconductor Corp. Hub",
]

_LSUSB_EMPTY_CABINET = [
    "Bus 001 Device 001: ID 1d6b:0002 Linux Foundation 2.0 root hub",
    "Bus 002 Device 001: ID 1d6b:0003 Linux Foundation 3.0 root hub",
]

_LSUSB_OK = [
    "Bus 001 Device 001: ID 1d6b:0002 Linux Foundation 2.0 root hub",
    "Bus 001 Device 002: ID 0bda:5411 Realtek Semiconductor Corp. Hub",
    "Bus 001 Device 010: ID 18d1:4ee7 Google Inc. Nexus/Pixel Device",
]


def test_parse_journal_probe_hc_died_replay():
    facts = parse_journal_probe(_HC_DIED_JOURNAL)
    assert facts.hc_dead_seen is True
    assert facts.lines == 3


def test_classify_topology_blind_vs_empty_cabinet():
    blind = classify_lsusb_topology(_LSUSB_BLIND)
    assert blind.classification == TOPOLOGY_BLIND
    assert blind.cascade_hub_count >= 1
    assert blind.peripheral_count == 0

    empty = classify_lsusb_topology(_LSUSB_EMPTY_CABINET)
    assert empty.classification == TOPOLOGY_EMPTY_CABINET
    assert empty.cascade_hub_count == 0

    ok = classify_lsusb_topology(_LSUSB_OK)
    assert ok.classification == TOPOLOGY_OK
    assert ok.peripheral_count == 1


def test_reconcile_agent_mute_on_hc_died_when_agent_healthy():
    round_ = ProbeRound(
        journal=parse_journal_probe(_HC_DIED_JOURNAL),
        topology=classify_lsusb_topology(_LSUSB_BLIND),
    )
    result = reconcile_agent_health([], round_)
    assert result.verdict == ProbeVerdict.AGENT_MUTE
    assert "probe_hc_dead" in result.signals


def test_reconcile_empty_cabinet_not_blind():
    round_ = ProbeRound(
        journal=parse_journal_probe(["unrelated kernel line"]),
        topology=classify_lsusb_topology(_LSUSB_EMPTY_CABINET),
    )
    result = reconcile_agent_health(["usb_tree_empty"], round_)
    assert result.verdict == ProbeVerdict.ALIGNED
    assert "empty_cabinet_not_blind" in result.signals


def test_consecutive_strike_requires_n_rounds():
    assert consecutive_strike_open([ProbeVerdict.AGENT_MUTE], need=2) is False
    assert (
        consecutive_strike_open(
            [ProbeVerdict.ALIGNED, ProbeVerdict.AGENT_MUTE, ProbeVerdict.AGENT_MUTE],
            need=2,
        )
        is True
    )
    assert (
        consecutive_strike_open(
            [ProbeVerdict.AGENT_MUTE, ProbeVerdict.PROBE_QUIET],
            need=2,
        )
        is False
    )
