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

AGENT_CAPABILITIES: tuple[str, ...] = (SCAN_ROUND_V1, FIRMWARE_CACHE_V1)


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
