"""Agent capability contract shared by heartbeat and Socket.IO handshake.

Capabilities are explicit, positive assertions of the running Agent build.
The control plane treats the most recent heartbeat as authoritative: a
heartbeat that omits this field withdraws every previously reported
capability for that host.
"""

from __future__ import annotations

from typing import Iterable

SCAN_ROUND_V1 = "scan_round_v1"

AGENT_CAPABILITIES: tuple[str, ...] = (SCAN_ROUND_V1,)


def normalize_capabilities(raw: object) -> tuple[str, ...]:
    """Return a stable, duplicate-free tuple of non-empty string capabilities."""
    if not isinstance(raw, Iterable) or isinstance(raw, (str, bytes, dict)):
        return ()
    return tuple(sorted({item for item in raw if isinstance(item, str) and item}))
