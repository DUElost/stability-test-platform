"""Host primary-key helpers — readable IDs derived from node IP."""

from __future__ import annotations

import ipaddress
import uuid
from typing import Callable, Optional


def ip_to_host_id(ip: Optional[str]) -> Optional[str]:
    """Map IPv4 ``172.21.9.6`` → ``172-21-9-6``.

    Returns ``None`` when *ip* is missing or not a usable IPv4 address so
    callers can fall back to an opaque id. IPv6 is intentionally not mapped
    to this dotted style.
    """
    if not ip or not str(ip).strip():
        return None
    try:
        addr = ipaddress.ip_address(str(ip).strip())
    except ValueError:
        return None
    if not isinstance(addr, ipaddress.IPv4Address):
        return None
    return str(addr).replace(".", "-")


def allocate_host_id(
    ip: Optional[str] = None,
    *,
    exists: Optional[Callable[[str], bool]] = None,
) -> str:
    """Allocate a unique host id, preferring the IP-derived form.

    *exists* returns True when a candidate id is already taken. On collision,
    a short suffix is appended (``172-21-9-6-a1b2``). Without a usable IPv4,
    falls back to ``auto-<12 hex>``.
    """
    base = ip_to_host_id(ip)
    if base:
        if exists is None or not exists(base):
            return base
        for _ in range(8):
            alt = f"{base}-{uuid.uuid4().hex[:4]}"
            if len(alt) > 64:
                break
            if exists is None or not exists(alt):
                return alt
    return f"auto-{uuid.uuid4().hex[:12]}"
