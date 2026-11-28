"""Agent capability contract shared by heartbeat and Socket.IO handshake.

Capabilities are explicit, positive assertions of the running Agent build.
The control plane treats the most recent heartbeat as authoritative: a
heartbeat that omits this field withdraws every previously reported
capability for that host.
"""

from __future__ import annotations

from typing import Iterable

SCAN_ROUND_V1 = "scan_round_v1"
FIRMWARE_CACHE_V1 = "firmware_cache_v1"

# ADR-0065 / B12：能力常量预登记。U1 只登记词表；Agent 心跳上报由 U5 完备后开启，
# 不得提前并入 AGENT_CAPABILITIES（避免半成品协商）。
FINISHING_GUARD_V2 = "finishing_guard_v2"
ATOMIC_COMMIT = "atomic_commit"
FORCE_ABORT = "force_abort"

AGENT_CAPABILITIES: tuple[str, ...] = (SCAN_ROUND_V1, FIRMWARE_CACHE_V1)


def finishing_guard_capability_bundle() -> tuple[str, ...]:
    """Control-plane / script negotiation bundle (B12 U4)."""
    return (FINISHING_GUARD_V2, ATOMIC_COMMIT, FORCE_ABORT)


def normalize_capabilities(raw: object) -> tuple[str, ...]:
    """Return a stable, duplicate-free tuple of non-empty capability names.

    去除首尾空白并丢弃空串：心跳与 Socket.IO 握手都走这一个函数，两侧词表必须
    一致，否则「心跳写成了 A、握手留着 ' A '」会让证据与连接永远对不上。
    非字符串、标量与映射一律视为无能力（fail closed）。
    """
    if not isinstance(raw, Iterable) or isinstance(raw, (str, bytes, dict)):
        return ()
    return tuple(sorted({
        item.strip() for item in raw if isinstance(item, str) and item.strip()
    }))
