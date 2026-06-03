"""db_history parsing and LocalDB incremental state."""

from __future__ import annotations

import json
from typing import Any, Dict, Optional, Set


def _normalize_event_type(raw_event_type: str) -> str:
    normalized = (raw_event_type or "").strip().upper()
    if not normalized:
        return ""
    if "ANR" in normalized:
        return "ANR"
    return "CRASH"


def parse_db_history_line(line_content: str) -> Optional[Dict[str, str]]:
    try:
        parts = line_content.split(",")
        if len(parts) < 10:
            return None
        db_path = parts[0].strip()
        pkg_name = parts[8].strip()
        ts_str = parts[9].strip()
        event_type = _normalize_event_type(parts[1] if len(parts) > 1 else "")
        if not all([db_path, pkg_name, ts_str]):
            return None
        return {
            "db_path": db_path,
            "pkg_name": pkg_name,
            "timestamp": ts_str,
            "event_type": event_type,
        }
    except Exception:
        return None


def parse_vendor_db_history_line(line_content: str) -> Optional[Dict[str, str]]:
    normalized_line = (line_content or "").strip()
    if not normalized_line or normalized_line.startswith("androidboot.bootreason="):
        return None
    if not normalized_line.startswith("/data/vendor/aee_exp/db."):
        return None
    return parse_db_history_line(line_content)


def parse_effective_db_history_line(line_content: str, aee_type: str) -> Optional[Dict[str, str]]:
    if aee_type == "vendor_aee_exp":
        return parse_vendor_db_history_line(line_content)
    return parse_db_history_line(line_content)


def state_key(serial: str, aee_type: str, *, prefix: str = "scan_aee") -> str:
    return f"{prefix}:{serial}:{aee_type}:processed_entries"


def load_processed_lines(state_store: Any, key: str) -> Set[str]:
    raw = state_store.get_state(key, "[]")
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            return set(str(x) for x in data)
    except json.JSONDecodeError:
        pass
    return set()


def save_processed_lines(state_store: Any, key: str, lines: Set[str]) -> None:
    state_store.set_state(key, json.dumps(sorted(lines)))
