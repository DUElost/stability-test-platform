"""SQLite persistence for patrol cycle checkpoints (crash recovery)."""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

_MAX_SQLITE_RETRIES = 3
_RETRY_BACKOFF_SECONDS = 0.05
_sleep: Callable[[float], None] = time.sleep


@dataclass(frozen=True)
class PatrolCycleCheckpointRow:
    job_id: str
    checkpoint: dict[str, Any]


class PatrolCycleCheckpointStoreRecoverableError(Exception):
    """Transient SQLite failure after retries (caller may retry later)."""


class PatrolCycleCheckpointStore:
    """Persists patrol cycle checkpoints in a dedicated SQLite file."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = str(db_path)
        self._initialized = False

    def initialize(self) -> None:
        if self._initialized:
            return
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS patrol_cycle_checkpoint (
                    job_id TEXT PRIMARY KEY,
                    checkpoint_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    claimed_at REAL
                )
                """
            )
            conn.commit()
        self._initialized = True

    def save(self, job_id: str, checkpoint: dict[str, Any] | None) -> None:
        self.initialize()
        if checkpoint is None:
            self.drop(job_id)
            return
        payload = json.dumps(checkpoint, ensure_ascii=False)
        now = datetime.now(timezone.utc).isoformat()
        self._execute_with_retry(
            lambda conn: conn.execute(
                """
                INSERT INTO patrol_cycle_checkpoint (job_id, checkpoint_json, updated_at, claimed_at)
                VALUES (?, ?, ?, NULL)
                ON CONFLICT(job_id) DO UPDATE SET
                    checkpoint_json = excluded.checkpoint_json,
                    updated_at = excluded.updated_at,
                    claimed_at = NULL
                """,
                (job_id, payload, now),
            ),
            operation="save",
            job_id=job_id,
        )

    def drop(self, job_id: str) -> None:
        self.initialize()
        self._execute_with_retry(
            lambda conn: conn.execute(
                "DELETE FROM patrol_cycle_checkpoint WHERE job_id = ?",
                (job_id,),
            ),
            operation="drop",
            job_id=job_id,
        )

    def get_for_recovery(self, job_id: str) -> PatrolCycleCheckpointRow | None:
        self.initialize()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT job_id, checkpoint_json FROM patrol_cycle_checkpoint WHERE job_id = ?",
                (job_id,),
            ).fetchone()
        if row is None:
            return None
        return self._row_to_checkpoint(row[0], row[1])

    def list_for_recovery(self) -> list[PatrolCycleCheckpointRow]:
        self.initialize()
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT job_id, checkpoint_json FROM patrol_cycle_checkpoint ORDER BY updated_at"
            ).fetchall()
        out: list[PatrolCycleCheckpointRow] = []
        for job_id, checkpoint_json in rows:
            parsed = self._row_to_checkpoint(job_id, checkpoint_json)
            if parsed is not None:
                out.append(parsed)
        return out

    def claim_pending_batch(self, batch_size: int = 50) -> list[PatrolCycleCheckpointRow]:
        """Atomically claim up to batch_size unclaimed rows for recovery replay."""
        self.initialize()
        if batch_size <= 0:
            return []
        for attempt in range(_MAX_SQLITE_RETRIES):
            try:
                return self._claim_batch_once(batch_size)
            except sqlite3.OperationalError as exc:
                if attempt + 1 >= _MAX_SQLITE_RETRIES:
                    raise PatrolCycleCheckpointStoreRecoverableError(
                        f"claim_pending_batch failed after retries: {exc}"
                    ) from exc
                _sleep(_RETRY_BACKOFF_SECONDS * (attempt + 1))
        return []

    def _claim_batch_once(self, batch_size: int) -> list[PatrolCycleCheckpointRow]:
        claimed_at = time.time()
        claimed: list[PatrolCycleCheckpointRow] = []
        with self._connect() as conn:
            pending = conn.execute(
                """
                SELECT job_id, checkpoint_json
                FROM patrol_cycle_checkpoint
                WHERE claimed_at IS NULL
                ORDER BY updated_at
                LIMIT ?
                """,
                (batch_size,),
            ).fetchall()
            for job_id, checkpoint_json in pending:
                if self._claim_one(conn, job_id, claimed_at) != 1:
                    continue
                parsed = self._row_to_checkpoint(job_id, checkpoint_json)
                if parsed is not None:
                    claimed.append(parsed)
            conn.commit()
        return claimed

    @staticmethod
    def _claim_one(conn: sqlite3.Connection, job_id: str, claimed_at: float) -> int:
        cursor = conn.execute(
            """
            UPDATE patrol_cycle_checkpoint
            SET claimed_at = ?
            WHERE job_id = ? AND claimed_at IS NULL
            """,
            (claimed_at, job_id),
        )
        return cursor.rowcount

    def _row_to_checkpoint(
        self, job_id: str, checkpoint_json: str
    ) -> PatrolCycleCheckpointRow | None:
        try:
            payload = json.loads(checkpoint_json)
        except json.JSONDecodeError:
            logger.warning(
                "patrol_checkpoint_corrupt_json job_id=%s", job_id, exc_info=True
            )
            return None
        if not isinstance(payload, dict):
            logger.warning(
                "patrol_checkpoint_invalid_payload job_id=%s type=%s",
                job_id,
                type(payload).__name__,
            )
            return None
        return PatrolCycleCheckpointRow(job_id=job_id, checkpoint=payload)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._db_path, timeout=5)

    def _execute_with_retry(
        self,
        fn: Callable[[sqlite3.Connection], Any],
        *,
        operation: str,
        job_id: str,
    ) -> None:
        for attempt in range(_MAX_SQLITE_RETRIES):
            try:
                with self._connect() as conn:
                    fn(conn)
                    conn.commit()
                return
            except sqlite3.OperationalError as exc:
                if attempt + 1 >= _MAX_SQLITE_RETRIES:
                    raise PatrolCycleCheckpointStoreRecoverableError(
                        f"{operation} failed for job_id={job_id} after retries: {exc}"
                    ) from exc
                _sleep(_RETRY_BACKOFF_SECONDS * (attempt + 1))
