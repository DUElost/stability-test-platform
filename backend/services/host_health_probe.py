"""#2983：控制面 host 健康探针——纯解析 / 对账层（第一切片）。

本模块**不**做 SSH、不读凭据、不调度。它把探针原始文本折成事实，并与
agent 自报的 ``extra.health.reasons`` 对账——这是「第四条通道」里可离线预演
的那一截（issue 验收：.102 journal 回放命中；.90/.91 判空柜不判失明）。

SSH 采集与 cron 接线是后续切片；签名词表与 Agent 侧
``backend.agent.kernel_usb_faults`` 同源（同字面量，避免双源漂移）。
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Optional, Sequence, Set

from backend.agent.kernel_usb_faults import (
    parse_kernel_usb_faults,
)

# 拓扑判定词表（进对账信号；后续告警规则按此建）。
TOPOLOGY_OK = "ok"
TOPOLOGY_BLIND = "hub_present_phones_zero"  # hub 级联在树、手机/外设归零 → 该盲
TOPOLOGY_EMPTY_CABINET = "root_hub_only"  # 仅 root hub、无级联 → 机柜未接线（.90/.91）
TOPOLOGY_UNKNOWN = "unknown"

# 常见 USB hub 级联 VID（Realtek 等机柜级联）；root hub 是 Linux 1d6b。
_ROOT_HUB_VID = "1d6b"
_CASCADE_HUB_VIDS = frozenset({"0bda", "2109", "05e3", "1a40"})  # Realtek / VIA / Genesys / Terminus


class ProbeVerdict(str, Enum):
    """单轮探针相对 agent 自报的对账结论（连续 N 轮才转红由调用方做）。"""

    ALIGNED = "aligned"
    AGENT_MUTE = "agent_mute"  # 探针见故障、自报无对应 reason
    PROBE_QUIET = "probe_quiet"  # 自报有故障、探针本轮未见（单轮不当罪）
    TOPOLOGY_MISMATCH = "topology_mismatch"


@dataclass(frozen=True)
class JournalProbeFacts:
    hc_dead_seen: bool
    link_errors: int
    cable_suspect: int
    lines: int


@dataclass(frozen=True)
class TopologyFacts:
    """lsusb / sysfs 摘要折成的拓扑事实。"""

    root_hub_count: int
    cascade_hub_count: int
    peripheral_count: int  # 非 hub、非 root 的节点（手机等）
    classification: str


@dataclass(frozen=True)
class ProbeRound:
    journal: JournalProbeFacts
    topology: TopologyFacts


@dataclass(frozen=True)
class ReconcileResult:
    verdict: ProbeVerdict
    signals: tuple[str, ...]


def parse_journal_probe(lines: Iterable[str]) -> JournalProbeFacts:
    """内核 journal 行 → 探针事实（复用 Agent 纯解析，保证签名一致）。"""
    faults = parse_kernel_usb_faults(lines)
    return JournalProbeFacts(
        hc_dead_seen=faults.hc_dead_seen,
        link_errors=faults.link_errors,
        cable_suspect=faults.cable_suspect,
        lines=faults.lines,
    )


def classify_lsusb_topology(lsusb_lines: Iterable[str]) -> TopologyFacts:
    """``lsusb`` 文本 → 拓扑分类（#2983 §3：盲 vs 空柜）。

    行形如 ``Bus 001 Device 002: ID 1d6b:0002 Linux Foundation 2.0 root hub``。
    解析失败的行忽略；全空 → UNKNOWN。
    """
    root = cascade = peripheral = 0
    for raw in lsusb_lines:
        line = (raw or "").strip()
        if not line:
            continue
        vid = _vid_from_lsusb(line)
        if vid is None:
            continue
        lower = line.lower()
        if vid == _ROOT_HUB_VID or "root hub" in lower:
            root += 1
            continue
        if vid in _CASCADE_HUB_VIDS or "hub" in lower:
            cascade += 1
            continue
        peripheral += 1

    total = root + cascade + peripheral
    if total == 0:
        classification = TOPOLOGY_UNKNOWN
    elif cascade > 0 and peripheral == 0:
        classification = TOPOLOGY_BLIND
    elif root > 0 and cascade == 0 and peripheral == 0:
        classification = TOPOLOGY_EMPTY_CABINET
    else:
        classification = TOPOLOGY_OK

    return TopologyFacts(
        root_hub_count=root,
        cascade_hub_count=cascade,
        peripheral_count=peripheral,
        classification=classification,
    )


def _vid_from_lsusb(line: str) -> Optional[str]:
    # ``ID abcd:1234`` —— 取 VID
    marker = " ID "
    idx = line.find(marker)
    if idx < 0:
        return None
    rest = line[idx + len(marker) :].strip()
    if len(rest) < 4 or ":" not in rest[:9]:
        return None
    vid = rest.split(":", 1)[0].strip().lower()
    return vid if len(vid) == 4 and all(c in "0123456789abcdef" for c in vid) else None


def reconcile_agent_health(
    agent_reasons: Sequence[str] | Set[str],
    round_: ProbeRound,
    *,
    link_error_threshold: int = 20,
) -> ReconcileResult:
    """探针一轮 vs agent ``health.reasons`` 对账。

    - 探针见 HC died / 拓扑失明，而 agent 无 ``usb_host_controller_dead`` /
      ``usb_tree_empty`` → ``AGENT_MUTE``（医生哑了）；
    - 拓扑空柜不升为失明信号（.90/.91）；
    - agent 自报故障而探针本轮安静 → ``PROBE_QUIET``（单轮不当罪，供连续窗吸收）。
    """
    reasons = {str(r) for r in agent_reasons}
    signals: list[str] = []
    journal = round_.journal
    topo = round_.topology

    probe_hc = journal.hc_dead_seen
    probe_blind = topo.classification == TOPOLOGY_BLIND
    probe_link = journal.link_errors >= link_error_threshold
    probe_fault = probe_hc or probe_blind or probe_link

    agent_usb_fault = bool(
        reasons
        & {
            "usb_host_controller_dead",
            "usb_tree_empty",
            "usb_link_degraded",
        }
    )

    if topo.classification == TOPOLOGY_EMPTY_CABINET:
        # 空柜不是失明——即使 agent 报了 usb_tree_empty 也不升 AGENT_MUTE
        if "usb_tree_empty" in reasons:
            signals.append("empty_cabinet_not_blind")
        return ReconcileResult(ProbeVerdict.ALIGNED, tuple(signals))

    if probe_fault and not agent_usb_fault:
        if probe_hc:
            signals.append("probe_hc_dead")
        if probe_blind:
            signals.append("probe_topology_blind")
        if probe_link:
            signals.append("probe_link_degraded")
        return ReconcileResult(ProbeVerdict.AGENT_MUTE, tuple(signals))

    if agent_usb_fault and not probe_fault:
        signals.append("agent_reported_usb_fault")
        return ReconcileResult(ProbeVerdict.PROBE_QUIET, tuple(signals))

    if probe_blind and "usb_tree_empty" not in reasons and agent_usb_fault:
        signals.append("topology_vs_reason")
        return ReconcileResult(ProbeVerdict.TOPOLOGY_MISMATCH, tuple(signals))

    return ReconcileResult(ProbeVerdict.ALIGNED, tuple(signals))


def consecutive_strike_open(
    recent_verdicts: Sequence[ProbeVerdict],
    *,
    need: int = 2,
    strike_on: frozenset[ProbeVerdict] = frozenset({ProbeVerdict.AGENT_MUTE}),
) -> bool:
    """连续 N 轮命中才转红（SSH 抖动 / 单轮安静不定罪）。"""
    if need < 1 or len(recent_verdicts) < need:
        return False
    window = recent_verdicts[-need:]
    return all(v in strike_on for v in window)
