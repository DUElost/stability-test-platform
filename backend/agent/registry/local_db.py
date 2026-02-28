"""SQLite WAL cache for Agent-side StepTrace persistence and tool catalog.

Provides three tables:
  step_trace_cache — persists step traces before Redis XADD; acked after confirmation
  tool_cache       — local copy of the server-side tool catalog
  agent_state      — key/value store for last_ack_id and other scalars

All writes are wrapped in transactions and protected by a threading lock.
WAL mode + FULL synchronous ensures durability without blocking readers.
"""

import logging
import sqlite3
import threading
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


class LocalDB:
    """Thread-safe SQLite WAL wrapper. Call initialize() before any other method."""

    def __init__(self) -> None:
        self._conn: Optional[sqlite3.Connection] = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def initialize(self, db_path: str) -> None:
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=FULL")
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS step_trace_cache (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id        INTEGER NOT NULL,
                step_id       TEXT    NOT NULL,
                stage         TEXT    NOT NULL,
                event_type    TEXT    NOT NULL,
                status        TEXT    NOT NULL,
                output        TEXT,
                error_message TEXT,
                original_ts   TEXT    NOT NULL,
                acked         INTEGER NOT NULL DEFAULT 0,
                UNIQUE(job_id, step_id, event_type)
            );
            CREATE TABLE IF NOT EXISTS tool_cache (
                tool_id      INTEGER PRIMARY KEY,
                version      TEXT    NOT NULL,
                script_path  TEXT    NOT NULL,
                script_class TEXT    NOT NULL,
                updated_at   TEXT    NOT NULL
            );
            CREATE TABLE IF NOT EXISTS agent_state (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
        """)
        self._conn.commit()
        logger.info(f"LocalDB initialized: {db_path}")

    def close(self) -> None:
        with self._lock:
            if self._conn:
                self._conn.close()
                self._conn = None

    # ------------------------------------------------------------------
    # step_trace_cache
    # ------------------------------------------------------------------

    def save_step_trace(
        self,
        job_id: int,
        step_id: str,
        stage: str,
        event_type: str,
        status: str,
        output: Optional[str] = None,
        error_message: Optional[str] = None,
        original_ts: Optional[datetime] = None,
    ) -> int:
        """Insert (or ignore duplicate) step trace. Returns row id."""
        ts = (original_ts or datetime.utcnow()).isoformat()
        with self._lock:
            with self._conn:
                cursor = self._conn.execute(
                    """
                    INSERT OR IGNORE INTO step_trace_cache
                    (job_id, step_id, stage, event_type, status, output, error_message, original_ts)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (job_id, step_id, stage, event_type, status, output, error_message, ts),
                )
                return cursor.lastrowid

    def mark_acked(self, trace_id: int) -> None:
        with self._lock:
            with self._conn:
                self._conn.execute(
                    "UPDATE step_trace_cache SET acked=1 WHERE id=?", (trace_id,)
                )

    def get_unacked_traces(self, after_id: int = 0) -> list:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM step_trace_cache WHERE id > ? AND acked=0 ORDER BY original_ts ASC",
                (after_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    # ------------------------------------------------------------------
    # tool_cache
    # ------------------------------------------------------------------

    def save_tool_cache(self, tools: dict) -> None:
        """Bulk-replace tool cache entries."""
        now = datetime.utcnow().isoformat()
        with self._lock:
            with self._conn:
                for tool_id, entry in tools.items():
                    self._conn.execute(
                        """
                        INSERT OR REPLACE INTO tool_cache
                        (tool_id, version, script_path, script_class, updated_at)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            tool_id,
                            entry.get("version", ""),
                            entry.get("script_path", ""),
                            entry.get("script_class", ""),
                            now,
                        ),
                    )

    def update_tool_cache(self, tool_id: int, entry: dict) -> None:
        now = datetime.utcnow().isoformat()
        with self._lock:
            with self._conn:
                self._conn.execute(
                    """
                    INSERT OR REPLACE INTO tool_cache
                    (tool_id, version, script_path, script_class, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        tool_id,
                        entry.get("version", ""),
                        entry.get("script_path", ""),
                        entry.get("script_class", ""),
                        now,
                    ),
                )

    def load_tool_cache(self) -> dict:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM tool_cache").fetchall()
        return {
            row["tool_id"]: {
                "version": row["version"],
                "script_path": row["script_path"],
                "script_class": row["script_class"],
            }
            for row in rows
        }

    # ------------------------------------------------------------------
    # agent_state
    # ------------------------------------------------------------------

    def get_state(self, key: str, default: str = "") -> str:
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM agent_state WHERE key=?", (key,)
            ).fetchone()
        return row["value"] if row else default

    def set_state(self, key: str, value: str) -> None:
        with self._lock:
            with self._conn:
                self._conn.execute(
                    "INSERT OR REPLACE INTO agent_state (key, value) VALUES (?, ?)",
                    (key, value),
                )
