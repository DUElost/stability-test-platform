"""SQLite WAL cache for Agent-side persistence.

Tables:
  step_trace_cache      — step traces before Redis XADD; acked after confirmation
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
        self._db_path: Optional[str] = None
        self._lock = threading.RLock()
        self._thread_local = threading.local()
        self._connections: Dict[int, sqlite3.Connection] = {}
        self._closed = False

    @property
    def _conn(self) -> sqlite3.Connection:
        return self._get_conn()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def initialize(self, db_path: str) -> None:
        with self._lock:
            self._db_path = db_path
            self._closed = False
            conn = self._get_conn()
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
                fencing_token TEXT    NOT NULL DEFAULT '',
                trace_event_id TEXT   NOT NULL,
                attempts      INTEGER NOT NULL DEFAULT 0,
                last_error    TEXT,
                dead_letter   INTEGER NOT NULL DEFAULT 0,
                acked         INTEGER NOT NULL DEFAULT 0,
                UNIQUE(trace_event_id)
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
                device_serial TEXT,
                fencing_token TEXT    NOT NULL DEFAULT '',
                claimed_at    TEXT    NOT NULL
            );
            CREATE TABLE IF NOT EXISTS job_archive (
                job_id      INTEGER PRIMARY KEY,
                nfs_uri     TEXT    NOT NULL,
                sha256      TEXT,
                size_bytes  INTEGER,
                spilled     INTEGER NOT NULL DEFAULT 0,
                archived_at TEXT    NOT NULL
            );
        """)
        self._ensure_step_trace_schema()
        self._ensure_log_signal_outbox_schema()
        self._ensure_active_job_registry_schema()
        self._backfill_step_trace_tokens()
        conn.commit()
        logger.info(f"LocalDB initialized: {db_path}")

    def _new_connection(self) -> sqlite3.Connection:
        if not self._db_path:
            raise RuntimeError("LocalDB is not initialized")
        conn = sqlite3.connect(self._db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=FULL")
        return conn

    def _get_conn(self) -> sqlite3.Connection:
        with self._lock:
            if self._closed:
                raise RuntimeError("LocalDB is closed")
            conn = getattr(self._thread_local, "conn", None)
            if conn is None:
                conn = self._new_connection()
                self._thread_local.conn = conn
                self._connections[threading.get_ident()] = conn
            return conn

    def _ensure_step_trace_schema(self) -> None:
        columns = {
            row["name"]
            for row in self._conn.execute("PRAGMA table_info(step_trace_cache)").fetchall()
        }
        if "fencing_token" not in columns:
            self._conn.execute(
                "ALTER TABLE step_trace_cache "
                "ADD COLUMN fencing_token TEXT NOT NULL DEFAULT ''"
            )
        # 审计 Agent #6: step_trace_cache 需要 attempts/last_error/dead_letter 三列以承载死信。
        # Why: 上游 step_trace_uploader 持续 5xx 时旧实现会无限重试,挤占 buffer。
        # How to apply: idempotent ALTER TABLE,与 fencing_token 同套路;get_unacked_traces 过滤 dead_letter。
        if "attempts" not in columns:
            self._conn.execute(
                "ALTER TABLE step_trace_cache "
                "ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0"
            )
        if "last_error" not in columns:
            self._conn.execute(
                "ALTER TABLE step_trace_cache "
                "ADD COLUMN last_error TEXT"
            )
        if "dead_letter" not in columns:
            self._conn.execute(
                "ALTER TABLE step_trace_cache "
                "ADD COLUMN dead_letter INTEGER NOT NULL DEFAULT 0"
            )
        if "trace_event_id" not in columns:
            self._conn.execute(
                "ALTER TABLE step_trace_cache "
                "ADD COLUMN trace_event_id TEXT NOT NULL DEFAULT ''"
            )
        self._conn.execute(
            "UPDATE step_trace_cache "
            "SET trace_event_id = 'legacy:' || id "
            "WHERE trace_event_id = ''"
        )
        self._rebuild_legacy_step_trace_unique_constraint()

    def _rebuild_legacy_step_trace_unique_constraint(self) -> None:
        """Replace the legacy per-step uniqueness with event-id uniqueness."""
        row = self._conn.execute(
            "SELECT sql FROM sqlite_master "
            "WHERE type='table' AND name='step_trace_cache'"
        ).fetchone()
        table_sql = (row["sql"] if row else "") or ""
        normalized = "".join(table_sql.lower().split())
        if "unique(job_id,step_id,event_type)" not in normalized:
            return

        self._conn.executescript(
            """
            CREATE TABLE step_trace_cache_v2 (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id         INTEGER NOT NULL,
                step_id        TEXT    NOT NULL,
                stage          TEXT    NOT NULL,
                event_type     TEXT    NOT NULL,
                status         TEXT    NOT NULL,
                output         TEXT,
                error_message  TEXT,
                original_ts    TEXT    NOT NULL,
                fencing_token  TEXT    NOT NULL DEFAULT '',
                trace_event_id TEXT    NOT NULL,
                attempts       INTEGER NOT NULL DEFAULT 0,
                last_error     TEXT,
                dead_letter    INTEGER NOT NULL DEFAULT 0,
                acked          INTEGER NOT NULL DEFAULT 0,
                UNIQUE(trace_event_id)
            );
            INSERT INTO step_trace_cache_v2 (
                id, job_id, step_id, stage, event_type, status, output,
                error_message, original_ts, fencing_token, trace_event_id,
                attempts, last_error, dead_letter, acked
            )
            SELECT
                id, job_id, step_id, stage, event_type, status, output,
                error_message, original_ts, fencing_token, trace_event_id,
                attempts, last_error, dead_letter, acked
            FROM step_trace_cache;
            DROP TABLE step_trace_cache;
            ALTER TABLE step_trace_cache_v2 RENAME TO step_trace_cache;
            """
        )

    def _ensure_log_signal_outbox_schema(self) -> None:
        """log_signal_outbox 增列 dead_letter (#9):与 step_trace_cache 同套路。

        Why: OutboxDrainer 整批 POST 失败时只 bump_attempts,旧条目永不下台,
             长跑下 batch 名额被反复重试的死条目挤占,新 signal 推不出去。
        How to apply: idempotent ALTER + DEFAULT 0,兼容已部署 Agent;
             get_pending_log_signals 加 dead_letter=0 过滤,prune SQL 显式排除。
        """
        columns = {
            row["name"]
            for row in self._conn.execute(
                "PRAGMA table_info(log_signal_outbox)"
            ).fetchall()
        }
        if "dead_letter" not in columns:
            self._conn.execute(
                "ALTER TABLE log_signal_outbox "
                "ADD COLUMN dead_letter INTEGER NOT NULL DEFAULT 0"
            )

    def _ensure_active_job_registry_schema(self) -> None:
        """兼容旧 agent 本地库：为 active_job_registry 补齐 device_serial。"""
        columns = {
            row["name"]
            for row in self._conn.execute("PRAGMA table_info(active_job_registry)").fetchall()
        }
        if "device_serial" not in columns:
            self._conn.execute(
                "ALTER TABLE active_job_registry "
                "ADD COLUMN device_serial TEXT"
            )

    def _backfill_step_trace_tokens(self) -> None:
        self._conn.execute(
            """
            UPDATE step_trace_cache
            SET fencing_token = (
                SELECT active_job_registry.fencing_token
                FROM active_job_registry
                WHERE active_job_registry.job_id = step_trace_cache.job_id
            )
            WHERE fencing_token = ''
              AND EXISTS (
                SELECT 1
                FROM active_job_registry
                WHERE active_job_registry.job_id = step_trace_cache.job_id
                  AND active_job_registry.fencing_token <> ''
              )
            """
        )

    def close(self) -> None:
        with self._lock:
            for conn in self._connections.values():
                conn.close()
            self._connections.clear()
            self._thread_local = threading.local()
            self._closed = True

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
        fencing_token: str = "",
        trace_event_id: str = "",
    ) -> int:
        """Insert (or ignore duplicate) step trace. Returns row id."""
        ts = (original_ts or datetime.now(timezone.utc)).isoformat()
        stable_event_id = trace_event_id or (
            f"legacy:{job_id}:{stage}:{step_id}:{event_type}"
        )
        with self._lock:
            with self._conn:
                cursor = self._conn.execute(
                    """
                    INSERT OR IGNORE INTO step_trace_cache
                    (job_id, step_id, stage, event_type, status, output,
                     error_message, original_ts, fencing_token, trace_event_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        job_id,
                        step_id,
                        stage,
                        event_type,
                        status,
                        output,
                        error_message,
                        ts,
                        fencing_token,
                        stable_event_id,
                    ),
                )
                if cursor.rowcount > 0:
                    return cursor.lastrowid
                row = self._conn.execute(
                    "SELECT id FROM step_trace_cache WHERE trace_event_id = ?",
                    (stable_event_id,),
                ).fetchone()
                return int(row["id"]) if row else 0

    def mark_acked(self, trace_id: int) -> None:
        with self._lock:
            with self._conn:
                self._conn.execute(
                    "UPDATE step_trace_cache SET acked=1 WHERE id=?", (trace_id,)
                )

    def get_unacked_traces(self, after_id: int = 0) -> list:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM step_trace_cache "
                "WHERE id > ? AND acked = 0 AND dead_letter = 0 "
                "ORDER BY original_ts ASC",
                (after_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def bump_step_trace_attempt(self, trace_id: int, error: str) -> int:
        """+1 attempts, set last_error, return new attempts count.

        审计 Agent #6: 与 ``bump_terminal_attempt`` / ``bump_log_signal_attempt`` 同口径。
        Why: 死信判定需要看新 attempts;原子两步走避免漂移。
        """
        with self._lock:
            with self._conn:
                self._conn.execute(
                    "UPDATE step_trace_cache "
                    "SET attempts = attempts + 1, last_error = ? WHERE id = ?",
                    (error[:500] if error else None, trace_id),
                )
                row = self._conn.execute(
                    "SELECT attempts FROM step_trace_cache WHERE id = ?",
                    (trace_id,),
                ).fetchone()
                return int(row["attempts"]) if row else 0

    def mark_step_trace_dead_letter(self, trace_id: int, error: str) -> None:
        """标记 trace 为死信(不再尝试上传)。

        审计 Agent #6: dead_letter=1 表示永久失败;保留行用于审计而不删除。
        """
        with self._lock:
            with self._conn:
                self._conn.execute(
                    "UPDATE step_trace_cache "
                    "SET dead_letter = 1, last_error = ? WHERE id = ?",
                    (error[:500] if error else None, trace_id),
                )

    def get_step_trace_dead_letters(self, limit: int = 100) -> List[Dict[str, Any]]:
        """返回死信样本,供 heartbeat / 监控查询。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, job_id, step_id, event_type, attempts, last_error "
                "FROM step_trace_cache WHERE dead_letter = 1 "
                "ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def prune_acked_step_traces(self, keep_recent: int = 2000) -> int:
        """删除旧的 acked=1 且 dead_letter=0 条目,保留最近 keep_recent 条。

        Why: 长跑 Agent 上 step_trace_cache 只增不删 → SQLite 文件膨胀 + 全表扫描劣化。
             prune_acked_terminals / prune_acked_log_signals 已覆盖另两张表,这张漏了。
        How to apply: 与上两者同套路,但死信行(dead_letter=1)即使 acked 也保留供审计。
        """
        with self._lock:
            with self._conn:
                cur = self._conn.execute(
                    "DELETE FROM step_trace_cache "
                    "WHERE acked = 1 AND dead_letter = 0 "
                    "AND id NOT IN (SELECT id FROM step_trace_cache "
                    "WHERE acked = 1 AND dead_letter = 0 ORDER BY id DESC LIMIT ?)",
                    (keep_recent,),
                )
                return cur.rowcount

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
        """Persist the first terminal fact; conflicting replays are rejected."""
        now = datetime.now(timezone.utc).isoformat()
        raw = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        with self._lock:
            with self._conn:
                existing = self._conn.execute(
                    "SELECT id, payload FROM job_terminal_outbox WHERE job_id = ?",
                    (job_id,),
                ).fetchone()
                if existing is not None:
                    try:
                        same_payload = json.loads(existing["payload"]) == payload
                    except (TypeError, ValueError, json.JSONDecodeError):
                        same_payload = existing["payload"] == raw
                    if not same_payload:
                        raise ValueError(
                            f"conflicting terminal payload for job {job_id}"
                        )
                    return int(existing["id"])
                cur = self._conn.execute(
                    """
                    INSERT INTO job_terminal_outbox
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

    def count_pending_terminals(self) -> int:
        """Count un-acked terminal outbox rows (backlog depth)."""
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS c FROM job_terminal_outbox WHERE acked = 0"
            ).fetchone()
        return int(row["c"]) if row else 0

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
        """取未 ack 且非死信的 log_signal;按 id 升序(≈ 入队时间)。

        #9: dead_letter=1 行不再被取出,避免持续失败的旧条目挤占 batch 名额。
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, job_id, seq_no, envelope, attempts "
                "FROM log_signal_outbox WHERE acked = 0 AND dead_letter = 0 "
                "ORDER BY id ASC LIMIT ?",
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

    def count_pending_log_signals(self) -> int:
        """Count un-acked, non-dead-letter log_signal outbox rows."""
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS c FROM log_signal_outbox "
                "WHERE acked = 0 AND dead_letter = 0"
            ).fetchone()
        return int(row["c"]) if row else 0

    def ack_log_signal(self, row_id: int) -> None:
        with self._lock:
            with self._conn:
                self._conn.execute(
                    "UPDATE log_signal_outbox SET acked = 1 WHERE id = ?",
                    (row_id,),
                )

    def bump_log_signal_attempt(self, row_id: int, error: str) -> int:
        """累计 attempts 并返回新值。#9: 返回值便于上游判断是否到死信阈值。"""
        with self._lock:
            with self._conn:
                self._conn.execute(
                    "UPDATE log_signal_outbox SET attempts = attempts + 1, "
                    "last_error = ? WHERE id = ?",
                    (error[:500] if error else None, row_id),
                )
                row = self._conn.execute(
                    "SELECT attempts FROM log_signal_outbox WHERE id = ?",
                    (row_id,),
                ).fetchone()
        return int(row["attempts"]) if row else 0

    def mark_log_signal_dead_letter(self, row_id: int, error: str) -> None:
        """#9: 标记死信,从此 get_pending_log_signals 不再取出;保留供审计。"""
        with self._lock:
            with self._conn:
                self._conn.execute(
                    "UPDATE log_signal_outbox SET dead_letter = 1, "
                    "last_error = ? WHERE id = ?",
                    (error[:500] if error else None, row_id),
                )

    def get_log_signal_dead_letters(self, limit: int = 100) -> List[Dict[str, Any]]:
        """#9: 审计读取死信清单 — 仅供运维/告警面板,不参与重试。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, job_id, seq_no, envelope, attempts, last_error "
                "FROM log_signal_outbox WHERE dead_letter = 1 "
                "ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        result: List[Dict[str, Any]] = []
        for row in rows:
            result.append({
                "id":         row["id"],
                "job_id":     row["job_id"],
                "seq_no":     row["seq_no"],
                "envelope":   json.loads(row["envelope"]),
                "attempts":   row["attempts"],
                "last_error": row["last_error"],
            })
        return result

    def prune_acked_log_signals(self, keep_recent: int = 1000) -> int:
        """删除旧的 acked=1 且 dead_letter=0 条目,保留最近 keep_recent 条。

        Guards: 对每个 job_id 至少保留该 job 下 seq_no 最大的一行（无论是否
        在 keep_recent 窗口内），防止 prune 后 MAX(seq_no) 回退导致重启丢信号
        （见 B3：prune+重启叠加场景）。
        """
        with self._lock:
            with self._conn:
                # Sub-select ids that are NOT the max-seq row for their job.
                # Those can safely be pruned when they fall outside keep_recent.
                cur = self._conn.execute(
                    "DELETE FROM log_signal_outbox "
                    "WHERE acked = 1 AND dead_letter = 0 "
                    "AND id NOT IN (SELECT id FROM log_signal_outbox "
                    "WHERE acked = 1 AND dead_letter = 0 ORDER BY id DESC LIMIT ?) "
                    "AND id NOT IN ("
                    "  SELECT MAX(id) FROM log_signal_outbox "
                    "  WHERE acked = 1 AND dead_letter = 0 "
                    "  GROUP BY job_id"
                    ")",
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
    # job_archive — ADR-0025 Sprint 2: LogArchiver 归档标记
    # ------------------------------------------------------------------

    def mark_job_archived(
        self,
        job_id: int,
        *,
        nfs_uri: str,
        sha256: Optional[str] = None,
        size_bytes: Optional[int] = None,
        spilled: bool = False,
    ) -> None:
        """标记某 Job 的运行日志已归档到 NFS（LogArchiver prune 本地前调用）。"""
        with self._lock:
            with self._conn:
                self._conn.execute(
                    "INSERT OR REPLACE INTO job_archive "
                    "(job_id, nfs_uri, sha256, size_bytes, spilled, archived_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        int(job_id),
                        nfs_uri,
                        sha256,
                        int(size_bytes) if size_bytes is not None else None,
                        1 if spilled else 0,
                        datetime.now(timezone.utc).isoformat(),
                    ),
                )

    def is_job_archived(self, job_id: int) -> bool:
        """该 Job 是否已归档（幂等：避免重复归档同一 Job）。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM job_archive WHERE job_id = ?", (int(job_id),)
            ).fetchone()
        return row is not None

    def count_archived_jobs(self) -> int:
        """已归档 Job 数（供 archive-status 端点 / 心跳指标）。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS c FROM job_archive"
            ).fetchone()
        return int(row["c"]) if row else 0

    def count_spilled_jobs(self) -> int:
        """因磁盘溢出而归档的 Job 数。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS c FROM job_archive WHERE spilled = 1"
            ).fetchone()
        return int(row["c"]) if row else 0

    # ------------------------------------------------------------------
    # active_job_registry — ADR-0019 Phase 3a: crash recovery persistence
    # ------------------------------------------------------------------

    def save_active_job(
        self,
        job_id: int,
        device_id: int,
        fencing_token: str,
        device_serial: str = "",
    ) -> None:
        """Persist active job for crash recovery. Claim 时调用."""
        with self._lock:
            with self._conn:
                self._conn.execute(
                    "INSERT OR REPLACE INTO active_job_registry "
                    "(job_id, device_id, device_serial, fencing_token, claimed_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        job_id,
                        device_id,
                        device_serial,
                        fencing_token,
                        datetime.now(timezone.utc).isoformat(),
                    ),
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
                "SELECT job_id, device_id, device_serial, fencing_token FROM active_job_registry "
                "ORDER BY claimed_at ASC"
            ).fetchall()
        return [
            {
                "job_id": r["job_id"],
                "device_id": r["device_id"],
                "device_serial": r["device_serial"] or "",
                "fencing_token": r["fencing_token"],
            }
            for r in rows
        ]

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
