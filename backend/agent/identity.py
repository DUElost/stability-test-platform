"""Agent identity — instance ID and boot ID for recovery sync (ADR-0019 Phase 3a).

Generates a unique instance ID per process startup and reads the OS boot ID
to distinguish "agent restarted" from "OS rebooted".
"""

from __future__ import annotations

import logging
import platform
import uuid

logger = logging.getLogger(__name__)


def generate_agent_instance_id() -> str:
    """Return a unique instance ID for this agent process (uuid4 hex)."""
    return uuid.uuid4().hex


def read_boot_id() -> str:
    """Read the OS boot ID.

    Linux: reads /proc/sys/kernel/random/boot_id (survives only until reboot).
    Windows/WSL: generates a pseudo boot_id with warning (weak semantics, dev only).
    """
    if platform.system() == "Linux":
        try:
            with open("/proc/sys/kernel/random/boot_id") as f:
                bid = f.read().strip()
            if bid:
                return bid
        except (OSError, PermissionError):
            pass

    pseudo = uuid.uuid4().hex
    logger.warning(
        "Using pseudo boot_id (weak semantics, dev only): %s. "
        "Real boot_id requires Linux /proc/sys/kernel/random/boot_id.",
        pseudo,
    )
    return pseudo
