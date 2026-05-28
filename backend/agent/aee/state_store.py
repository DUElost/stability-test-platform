"""Lightweight agent_state access for patrol scripts (subprocess context)."""

from __future__ import annotations

import os
import sqlite3
import threading
from typing import Optional


class ScriptStateStore:
    """Minimal get/set on agent_state table — same keys as LocalDB."""

    def __init__(self, db_path: Optional[str] = None) -> None:
        path = db_path or os.getenv("STP_AGENT_STATE_DB", "")
        if not path:
            raise ValueError("STP_AGENT_STATE_DB is not set")
        self._db_path = path
        self._lock = threading.Lock()

    def get_state(self, key: str, default: str = "") -> str:
        with self._lock:
            conn = sqlite3.connect(self._db_path, timeout=5.0)
            try:
                row = conn.execute(
                    "SELECT value FROM agent_state WHERE key=?", (key,)
                ).fetchone()
                return row[0] if row else default
            finally:
                conn.close()

    def set_state(self, key: str, value: str) -> None:
        with self._lock:
            conn = sqlite3.connect(self._db_path, timeout=5.0)
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO agent_state (key, value) VALUES (?, ?)",
                    (key, value),
                )
                conn.commit()
            finally:
                conn.close()
