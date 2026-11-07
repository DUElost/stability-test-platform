"""Scan report device evidence shared by Agent validation and shadow ingestion.

The published tool treats these values as unknown device IDs. They do not
replace the authoritative serial of a mapped DLE.
"""
from __future__ import annotations

UNKNOWN_DEVICE_ID_VALUES = frozenset({
    "none",
    "undefined",
    "unknown_device_id",
    "discard_device_id",
})


def normalize_device_id(value: object) -> str | None:
    """Return stripped serial evidence, or None for empty/unknown tool values."""
    text = str(value).strip()
    if not text or text.lower() in UNKNOWN_DEVICE_ID_VALUES:
        return None
    return text
