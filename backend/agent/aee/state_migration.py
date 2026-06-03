"""AEE state namespace migration helpers."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

LEGACY_PATROL_STATE_PREFIX = "scan_aee"
WATCHER_AEE_STATE_PREFIX = "watcher:aee"
_AEE_TYPES = {"aee_exp", "vendor_aee_exp"}
_STATE_KINDS = {"processed_entries", "pending_pull"}


def migrate_legacy_aee_state_keys(db_path: str, *, dry_run: bool = False) -> Dict[str, Any]:
    """Migrate agent_state rows from scan_aee:* to watcher:aee:*.

    Old rows are kept in place so the migration is reversible during the M3
    compatibility window.
    """
    path = Path(db_path)
    summary: Dict[str, Any] = {
        "db_path": str(path),
        "dry_run": dry_run,
        "legacy_rows_seen": 0,
        "processed_entries_migrated": 0,
        "pending_pull_migrated": 0,
        "skipped": 0,
        "errors": [],
    }
    conn = sqlite3.connect(path)
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS agent_state (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        rows = conn.execute(
            "SELECT key, value FROM agent_state WHERE key LIKE ?",
            (f"{LEGACY_PATROL_STATE_PREFIX}:%",),
        ).fetchall()
        for old_key, old_value in rows:
            summary["legacy_rows_seen"] += 1
            parsed = _parse_legacy_key(str(old_key))
            if parsed is None:
                summary["skipped"] += 1
                continue
            serial, aee_type, kind = parsed
            new_key = _state_key(WATCHER_AEE_STATE_PREFIX, serial, aee_type, kind)
            row = conn.execute("SELECT value FROM agent_state WHERE key=?", (new_key,)).fetchone()
            new_value = str(row[0]) if row else _default_value(kind)
            merged = _merge_state_value(kind, str(old_value), new_value)
            if merged is None:
                summary["errors"].append({"key": old_key, "reason": "invalid_json"})
                continue
            if merged == new_value:
                summary["skipped"] += 1
                continue
            if not dry_run:
                conn.execute(
                    "INSERT OR REPLACE INTO agent_state (key, value) VALUES (?, ?)",
                    (new_key, merged),
                )
            if kind == "processed_entries":
                summary["processed_entries_migrated"] += 1
            else:
                summary["pending_pull_migrated"] += 1
        if not dry_run:
            conn.commit()
    finally:
        conn.close()
    return summary


def migrate_legacy_aee_state_store(
    state_store: Any,
    *,
    serial: str,
    aee_types: Iterable[str],
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Migrate one device's in-process state store view."""
    summary: Dict[str, Any] = {
        "dry_run": dry_run,
        "processed_entries_migrated": 0,
        "pending_pull_migrated": 0,
        "skipped": 0,
        "errors": [],
    }
    for aee_type in aee_types:
        if aee_type not in _AEE_TYPES:
            summary["skipped"] += 1
            continue
        for kind in sorted(_STATE_KINDS):
            old_key = _state_key(LEGACY_PATROL_STATE_PREFIX, serial, aee_type, kind)
            new_key = _state_key(WATCHER_AEE_STATE_PREFIX, serial, aee_type, kind)
            old_value = state_store.get_state(old_key, _default_value(kind))
            new_value = state_store.get_state(new_key, _default_value(kind))
            merged = _merge_state_value(kind, old_value, new_value)
            if merged is None:
                summary["errors"].append({"key": old_key, "reason": "invalid_json"})
                continue
            if merged == new_value:
                summary["skipped"] += 1
                continue
            if not dry_run:
                state_store.set_state(new_key, merged)
            if kind == "processed_entries":
                summary["processed_entries_migrated"] += 1
            else:
                summary["pending_pull_migrated"] += 1
    return summary


def _parse_legacy_key(key: str) -> Optional[tuple[str, str, str]]:
    parts = key.split(":")
    if len(parts) != 4 or parts[0] != LEGACY_PATROL_STATE_PREFIX:
        return None
    serial, aee_type, kind = parts[1], parts[2], parts[3]
    if not serial or aee_type not in _AEE_TYPES or kind not in _STATE_KINDS:
        return None
    return serial, aee_type, kind


def _state_key(prefix: str, serial: str, aee_type: str, kind: str) -> str:
    return f"{prefix}:{serial}:{aee_type}:{kind}"


def _default_value(kind: str) -> str:
    return "[]" if kind == "processed_entries" else "{}"


def _merge_state_value(kind: str, old_value: str, new_value: str) -> Optional[str]:
    if kind == "processed_entries":
        old_items = _load_json_list(old_value)
        new_items = _load_json_list(new_value)
        if old_items is None or new_items is None:
            return None
        return json.dumps(sorted(new_items | old_items))
    old_items = _load_json_dict(old_value)
    new_items = _load_json_dict(new_value)
    if old_items is None or new_items is None:
        return None
    return json.dumps({**old_items, **new_items}, sort_keys=True)


def _load_json_list(raw: str) -> Optional[set[str]]:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, list):
        return None
    return {str(item) for item in data}


def _load_json_dict(raw: str) -> Optional[dict[str, Any]]:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    return dict(data)
