"""Agent protocol version gate — rollout-safe defaults.

``STP_AGENT_MIN_VERSION`` unset or blank disables claim-time enforcement so a
control-plane deploy does not brick old Agents still on the queue.  Ops enable
the gate explicitly after fleet upgrade.
"""

from __future__ import annotations

import os
import re
from typing import Tuple


def resolve_agent_min_version() -> str:
    return (os.getenv("STP_AGENT_MIN_VERSION") or "").strip()


def agent_version_gate_enabled() -> bool:
    return bool(resolve_agent_min_version())


def _version_tuple(version: str) -> Tuple[int, ...]:
    parts = re.findall(r"\d+", version or "")
    if not parts:
        return (0,)
    return tuple(int(p) for p in parts)


def agent_version_is_supported(agent_version: str, minimum: str) -> bool:
    if not minimum:
        return True
    try:
        agent = _version_tuple(agent_version)
        floor = _version_tuple(minimum)
    except ValueError:
        return False
    length = max(len(agent), len(floor))
    agent_padded = agent + (0,) * (length - len(agent))
    floor_padded = floor + (0,) * (length - len(floor))
    return agent_padded >= floor_padded
