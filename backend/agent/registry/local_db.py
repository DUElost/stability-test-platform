"""SQLite WAL cache for Agent-side persistence.

Tables:
  step_trace_cache      — step traces before Redis XADD; acked after confirmation
  tool_cache            — local copy of the server-side tool catalog
  script_cache          — local copy of the server-side script catalog
  agent_state           — key/value store for last_ack_id and other scalars
  job_terminal_outbox   — terminal-state payloads; retried until server ACKs
  log_signal_outbox     — per-job log_signal envelopes; (job_id, seq_no) idempotent key
  watcher_state         — per-watcher lifecycle state; supports cross-restart recovery

All writes are wrapped in transactions and protected by a threading lock.
WAL mode + FULL synchronous ensures durability without blocking readers.
"""

import json
import logging
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

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
            CREATE TABLE IF NOT EXISTS script_cache (
                cache_key       TEXT    PRIMARY KEY,
                script_id       INTEGER NOT NULL,
                name            TEXT    NOT NULL,
                version         TEXT    NOT NULL,
                script_type     TEXT    NOT NULL,
                nfs_path        TEXT    NOT NULL,
                content_sha256  TEXT    NOT NULL,
                updated_at      TEXT    NOT NULL
            );
            CREATE TABLE IF NOT EXISTS agent_state (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS job_terminal_outbox (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id      INTEGER NOT NULL UNIQUE,
                payload     TEXT    NOT NULL,
                created_at  TEXT    NOT NULL,
                attempts    INTEGER NOT NULL DEFAULT 0,
                last_error  TEXT,
                acked       INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS log_signal_outbox (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id      INTEGER NOT NULL,
                seq_no      INTEGER NOT NULL,
                envelope    TEXT    NOT NULL,
                created_at  TEXT    NOT NULL,
                attempts    INTEGER NOT NULL DEFAULT 0,
                last_error  TEXT,
                acked       INTEGER NOT NULL DEFAULT 0,
                UNIQUE(job_id, seq_no)
            );
            CREATE INDEX IF NOT EXISTS idx_log_signal_outbox_pending
                ON log_signal_outbox(acked, id);
            CREATE TABLE IF NOT EXISTS watcher_state (
                watcher_id    TEXT    PRIMARY KEY,
                job_id        INTEGER NOT NULL,
                serial        TEXT    NOT NULL,
                host_id       TEXT    NOT NULL,
                state         TEXT    NOT NULL,
                capability    TEXT,
                started_at    TEXT    NOT NULL,
                stopped_at    TEXT,
                last_error    TEXT,
                last_seq_no   INTEGER NOT NULL DEFAULT 0,
                updated_at    TEXT    NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_watcher_state_active
                ON watcher_state(state);
            CREATE TABLE IF NOT EXISTS active_job_registry (
                job_id        INTEGER PRIMARY KEY,
                device_id     INTEGER NOT NULL,
                fencing_token TEXT    NOT NULL DEFAULT '',
                claimed_at    TEXT    NOT NULL
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
        ts = (original_ts or datetime.now(timezone.utc)).isoformat()
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
        now = datetime.now(timezone.utc).isoformat()
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
        now = datetime.now(timezone.utc).isoformat()
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
    # script_cache
    # ------------------------------------------------------------------

    def save_script_cache(self, scripts: dict) -> None:
        """Bulk-replace script cache entries."""
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            with self._conn:
                self._conn.execute("DELETE FROM script_cache")
                for cache_key, entry in scripts.items():
                    self._conn.execute(
                        """
                        INSERT OR REPLACE INTO script_cache
                        (cache_key, script_id, name, version, script_type, nfs_path,
                         content_sha256, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            cache_key,
                            entry.get("script_id", 0),
                            entry.get("name", ""),
                            entry.get("version", ""),
                            entry.get("script_type", ""),
                            entry.get("nfs_path", ""),
                            entry.get("content_sha256", ""),
                            now,
                        ),
                    )

    def update_script_cache(self, cache_key: str, entry: dict) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            with self._conn:
                self._conn.execute(
                    """
                    INSERT OR REPLACE INTO script_cache
                    (cache_key, script_id, name, version, script_type, nfs_path,
                     content_sha256, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        cache_key,
                        entry.get("script_id", 0),
                        entry.get("name", ""),
                        entry.get("version", ""),
                        entry.get("script_type", ""),
                        entry.get("nfs_path", ""),
                        entry.get("content_sha256", ""),
                        now,
                    ),
                )

    def load_script_cache(self) -> dict:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM script_cache").fetchall()
        return {
            row["cache_key"]: {
                "script_id": row["script_id"],
                "name": row["name"],
                "version": row["version"],
                "script_type": row["script_type"],
                "nfs_path": row["nfs_path"],
                "content_sha256": row["content_sha256"],
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

    # ------------------------------------------------------------------
    # job_terminal_outbox
    # ------------------------------------------------------------------

    def enqueue_terminal(self, job_id: int, payload: Dict[str, Any]) -> int:
        """Persist a terminal-state payload. Idempotent per job_id (REPLACE)."""
        now = datetime.now(timezone.utc).isoformat()
        raw = json.dumps(payload, ensure_ascii=False)
        with self._lock:
            with self._conn:
                cur = self._conn.execute(
                    """
                    INSERT OR REPLACE INTO job_terminal_outbox
                    (job_id, payload, created_at, attempts, last_error, acked)
                    VALUES (?, ?, ?, 0, NULL, 0)
                    """,
                    (job_id, raw, now),
                )
                return cur.lastrowid

    def get_pending_terminals(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Return un-acked outbox entries ordered by creation time."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, job_id, payload, attempts FROM job_terminal_outbox "
                "WHERE acked = 0 ORDER BY created_at ASC LIMIT ?",
                (limit,),
            ).fetchall()
        result = []
        for row in rows:
            result.append({
                "id": row["id"],
                "job_id": row["job_id"],
                "payload": json.loads(row["payload"]),
                "attempts": row["attempts"],
            })
        return result

    def ack_terminal(self, job_id: int) -> None:
        with self._lock:
            with self._conn:
                self._conn.execute(
                    "UPDATE job_terminal_outbox SET acked = 1 WHERE job_id = ?",
                    (job_id,),
                )

    def bump_terminal_attempt(self, job_id: int, error: str) -> None:
        with self._lock:
            with self._conn:
                self._conn.execute(
                    "UPDATE job_terminal_outbox SET attempts = attempts + 1, "
                    "last_error = ? WHERE job_id = ?",
                    (error, job_id),
                )

    def prune_acked_terminals(self, keep_recent: int = 100) -> int:
        """Delete old acked entries, keeping the most recent ones."""
        with self._lock:
            with self._conn:
                cur = self._conn.execute(
                    "DELETE FROM job_terminal_outbox WHERE acked = 1 "
                    "AND id NOT IN (SELECT id FROM job_terminal_outbox "
                    "WHERE acked = 1 ORDER BY id DESC LIMIT ?)",
                    (keep_recent,),
                )
                return cur.rowcount

    # ------------------------------------------------------------------
    # log_signal_outbox — Watcher 采集的异常信号，批量推送到后端
    # 幂等键: (job_id, seq_no)，与后端 job_log_signal 表约束一致
    # ------------------------------------------------------------------

    def next_log_signal_seq_no(self, job_id: int) -> int:
        """返回该 job 下一个可用 seq_no（即 MAX(seq_no)+1；空则返回 1）。

        Agent 崩溃/重启后，SignalEmitter 用此方法恢复单调 seq_no，避免冲突。
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT COALESCE(MAX(seq_no), 0) AS m FROM log_signal_outbox WHERE job_id = ?",
                (job_id,),
            ).fetchone()
        return int(row["m"]) + 1 if row else 1

    def enqueue_log_signal(
        self,
        job_id: int,
        seq_no: int,
        envelope: Dict[str, Any],
    ) -> Optional[int]:
        """持久化一条 log_signal envelope。

        幂等：INSERT OR IGNORE，重复的 (job_id, seq_no) 返回 None。
        envelope 以 JSON 文本存储，取出后反序列化。
        """
        now = datetime.now(timezone.utc).isoformat()
        raw = json.dumps(envelope, ensure_ascii=False)
        with self._lock:
            with self._conn:
                cur = self._conn.execute(
                    """
                    INSERT OR IGNORE INTO log_signal_outbox
                    (job_id, seq_no, envelope, created_at, attempts, last_error, acked)
                    VALUES (?, ?, ?, ?, 0, NULL, 0)
                    """,
                    (job_id, seq_no, raw, now),
                )
                # rowcount=0 表示 (job_id, seq_no) 冲突（已存在）
                return cur.lastrowid if cur.rowcount > 0 else None

    def get_pending_log_signals(self, limit: int = 50) -> List[Dict[str, Any]]:
        """取未 ack 的 log_signal；按 id 升序（≈ 入队时间）。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, job_id, seq_no, envelope, attempts "
                "FROM log_signal_outbox WHERE acked = 0 ORDER BY id ASC LIMIT ?",
                (limit,),
            ).fetchall()
        result: List[Dict[str, Any]] = []
        for row in rows:
            result.append({
                "id":       row["id"],
                "job_id":   row["job_id"],
                "seq_no":   row["seq_no"],
                "envelope": json.loads(row["envelope"]),
                "attempts": row["attempts"],
            })
        return result

    def ack_log_signal(self, row_id: int) -> None:
        with self._lock:
            with self._conn:
                self._conn.execute(
                    "UPDATE log_signal_outbox SET acked = 1 WHERE id = ?",
                    (row_id,),
                )

    def bump_log_signal_attempt(self, row_id: int, error: str) -> None:
        with self._lock:
            with self._conn:
                self._conn.execute(
                    "UPDATE log_signal_outbox SET attempts = attempts + 1, "
                    "last_error = ? WHERE id = ?",
                    (error[:500] if error else None, row_id),
                )

    def prune_acked_log_signals(self, keep_recent: int = 1000) -> int:
        """删除旧的 acked=1 条目，保留最近 keep_recent 条。"""
        with self._lock:
            with self._conn:
                cur = self._conn.execute(
                    "DELETE FROM log_signal_outbox WHERE acked = 1 "
                    "AND id NOT IN (SELECT id FROM log_signal_outbox "
                    "WHERE acked = 1 ORDER BY id DESC LIMIT ?)",
                    (keep_recent,),
                )
                return cur.rowcount

    # ------------------------------------------------------------------
    # watcher_state — per-watcher 生命周期状态
    # 支持 Agent 重启后重建 (本轮仅建表 + 基础 CRUD，reconcile 留到阶段 5)
    # ------------------------------------------------------------------

    def upsert_watcher_state(
        self,
        *,
        watcher_id: str,
        job_id: int,
        serial: str,
        host_id: str,
        state: str,                          # active | stopping | stopped | failed
        capability: Optional[str] = None,
        started_at: Optional[datetime] = None,
        stopped_at: Optional[datetime] = None,
        last_error: Optional[str] = None,
    ) -> None:
        """插入或全量覆写一条 watcher_state。首次启动时调用。"""
        now = datetime.now(timezone.utc).isoformat()
        started = (started_at or datetime.now(timezone.utc)).isoformat()
        stopped = stopped_at.isoformat() if stopped_at else None
        with self._lock:
            with self._conn:
                self._conn.execute(
                    """
                    INSERT INTO watcher_state
                    (watcher_id, job_id, serial, host_id, state, capability,
                     started_at, stopped_at, last_error, last_seq_no, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
                    ON CONFLICT(watcher_id) DO UPDATE SET
                        job_id=excluded.job_id,
                        serial=excluded.serial,
                        host_id=excluded.host_id,
                        state=excluded.state,
                        capability=excluded.capability,
                        started_at=excluded.started_at,
                        stopped_at=excluded.stopped_at,
                        last_error=excluded.last_error,
                        updated_at=excluded.updated_at
                    """,
                    (watcher_id, job_id, serial, host_id, state, capability,
                     started, stopped, last_error, now),
                )

    def update_watcher_state(
        self,
        watcher_id: str,
        *,
        state: Optional[str] = None,
        capability: Optional[str] = None,
        stopped_at: Optional[datetime] = None,
        last_error: Optional[str] = None,
    ) -> None:
        """增量更新非空字段。未指定字段保持不变。"""
        fields: List[str] = []
        values: List[Any] = []
        if state is not None:
            fields.append("state = ?"); values.append(state)
        if capability is not None:
            fields.append("capability = ?"); values.append(capability)
        if stopped_at is not None:
            fields.append("stopped_at = ?"); values.append(stopped_at.isoformat())
        if last_error is not None:
            fields.append("last_error = ?"); values.append(last_error[:500])
        if not fields:
            return
        fields.append("updated_at = ?"); values.append(datetime.now(timezone.utc).isoformat())
        values.append(watcher_id)
        with self._lock:
            with self._conn:
                self._conn.execute(
                    f"UPDATE watcher_state SET {', '.join(fields)} WHERE watcher_id = ?",
                    tuple(values),
                )

    def bump_watcher_last_seq(self, watcher_id: str, seq_no: int) -> None:
        """单调递增 last_seq_no（仅当 seq_no 更大才更新）。"""
        with self._lock:
            with self._conn:
                self._conn.execute(
                    "UPDATE watcher_state SET last_seq_no = ?, updated_at = ? "
                    "WHERE watcher_id = ? AND last_seq_no < ?",
                    (seq_no, datetime.now(timezone.utc).isoformat(), watcher_id, seq_no),
                )

    def get_watcher_state(self, watcher_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM watcher_state WHERE watcher_id = ?",
                (watcher_id,),
            ).fetchone()
        return dict(row) if row else None

    def list_active_watcher_states(self) -> List[Dict[str, Any]]:
        """返回 state='active' 的所有 watcher_state，用于 Agent 重启后 reconcile。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM watcher_state WHERE state = 'active' ORDER BY started_at ASC"
            ).fetchall()
        return [dict(row) for row in rows]

    # ------------------------------------------------------------------
    # active_job_registry — ADR-0019 Phase 3a: crash recovery persistence
    # ------------------------------------------------------------------

    def save_active_job(self, job_id: int, device_id: int, fencing_token: str) -> None:
        """Persist active job for crash recovery. Claim 时调用."""
        with self._lock:
            with self._conn:
                self._conn.execute(
                    "INSERT OR REPLACE INTO active_job_registry (job_id, device_id, fencing_token, claimed_at) "
                    "VALUES (?, ?, ?, ?)",
                    (job_id, device_id, fencing_token, datetime.now(timezone.utc).isoformat()),
                )

    def delete_active_job(self, job_id: int) -> None:
        """Remove job from active registry. Complete/abort 时调用."""
        with self._lock:
            with self._conn:
                self._conn.execute(
                    "DELETE FROM active_job_registry WHERE job_id = ?",
                    (job_id,),
                )

    def get_active_jobs(self) -> List[Dict[str, Any]]:
        """Return all persisted active jobs for recovery sync."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT job_id, device_id, fencing_token FROM active_job_registry "
                "ORDER BY claimed_at ASC"
            ).fetchall()
        return [{"job_id": r["job_id"], "device_id": r["device_id"], "fencing_token": r["fencing_token"]} for r in rows]

    def get_pending_outbox(self) -> List[Dict[str, Any]]:
        """Return un-acked terminal outbox entries for recovery sync.

        Thin wrapper around get_pending_terminals; extracts job_id + event_type.
        """
        terminals = self.get_pending_terminals()
        result = []
        for entry in terminals:
            try:
                payload = entry.get("payload", {}) if isinstance(entry.get("payload"), dict) else {}
                event_type = payload.get("update", {}).get("status", "RUN_COMPLETED")
            except Exception:
                event_type = "RUN_COMPLETED"
            result.append({
                "job_id": entry["job_id"],
                "event_type": event_type,
            })
        return result
