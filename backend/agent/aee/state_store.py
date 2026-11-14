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

    def list_states(
        self, prefix: str, limit: int = 200, value: Optional[str] = None,
        after_key: str = "",
    ) -> list[tuple[str, str]]:
        """按 key 前缀有界列出 (key, value)，用于重启后恢复持久意图（#3957）。

        用 substr 比较而不是 LIKE：key 里的下划线在 LIKE 中是通配符。
        value 过滤在 SQL 内完成——已清除的意图（value="0"）不会挤占上限。

        ``after_key`` 是 keyset 游标：只返回 ``key > after_key`` 的行。恢复循环
        每轮把游标推进到本轮最后一条，越过「已耗尽 / 本循环无法处理」的意图，
        从而保证一批固定不变的意图不会永久占满有界窗口（B8 G6 第 3 轮）。
        """
        sql = (
            "SELECT key, value FROM agent_state "
            "WHERE substr(key, 1, ?) = ?"
        )
        params: list = [len(prefix), prefix]
        if value is not None:
            sql += " AND value = ?"
            params.append(value)
        if after_key:
            sql += " AND key > ?"
            params.append(after_key)
        sql += " ORDER BY key LIMIT ?"
        params.append(int(limit))
        with self._lock:
            conn = sqlite3.connect(self._db_path, timeout=5.0)
            try:
                rows = conn.execute(sql, params).fetchall()
                return [(row[0], row[1]) for row in rows]
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
