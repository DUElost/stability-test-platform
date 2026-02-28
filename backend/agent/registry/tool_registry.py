"""Tool Registry — maps tool_id to local ToolEntry (script_path + version).

Load order:
  1. Server API (full catalog) on initialize()
  2. Falls back to SQLite cache if server unreachable

resolve(tool_id, version) → ToolEntry
  Raises ToolVersionMismatch if cached version differs.
  Raises ToolNotFoundLocally if tool_id absent.

pull_tool_sync(tool_id, version) → bool
  Fetches a single tool from the server. Retries 3× with exponential backoff.
  Used by ControlListener on tool_update command and by PipelineEngine on
  ToolVersionMismatch.
"""

import hashlib
import json
import logging
import threading
import time
from dataclasses import dataclass
from typing import Dict, Optional

import requests

from .local_db import LocalDB

logger = logging.getLogger(__name__)


@dataclass
class ToolEntry:
    tool_id: int
    version: str
    script_path: str
    script_class: str


class ToolNotFoundLocally(Exception):
    def __init__(self, tool_id: int) -> None:
        super().__init__(f"Tool {tool_id} not found in local registry")
        self.tool_id = tool_id


class ToolVersionMismatch(Exception):
    def __init__(self, tool_id: int, cached: str, required: str) -> None:
        super().__init__(
            f"Tool {tool_id} version mismatch: cached={cached!r}, required={required!r}"
        )
        self.tool_id = tool_id
        self.cached_version = cached
        self.required_version = required


class ToolRegistry:
    """Thread-safe tool catalog. Server is source of truth; SQLite is fallback cache."""

    def __init__(self, db: LocalDB, api_url: str, agent_secret: str = "") -> None:
        self._db = db
        self._api_url = api_url.rstrip("/")
        self._agent_secret = agent_secret
        self._cache: Dict[int, ToolEntry] = {}
        self._version: str = ""
        self._lock = threading.RLock()

    @property
    def version(self) -> str:
        """Hash of current catalog; used in heartbeat to detect staleness."""
        return self._version

    # ------------------------------------------------------------------
    # Initialization
    # ------------------------------------------------------------------

    def initialize(self) -> None:
        """Full catalog load from server, with SQLite fallback."""
        try:
            self._load_from_server()
            with self._lock:
                count = len(self._cache)
            logger.info(f"ToolRegistry loaded {count} tools from server (version={self._version})")
        except Exception as e:
            logger.warning(f"ToolRegistry server load failed: {e} — loading from SQLite cache")
            self._load_from_sqlite()
            with self._lock:
                count = len(self._cache)
            logger.info(f"ToolRegistry loaded {count} tools from SQLite cache")

    def _load_from_server(self) -> None:
        headers = self._auth_headers()
        resp = requests.get(f"{self._api_url}/api/v1/tools", headers=headers, timeout=10)
        resp.raise_for_status()
        payload = resp.json()
        tools = payload.get("data", payload) if isinstance(payload, dict) else payload
        with self._lock:
            self._cache.clear()
            for t in (tools or []):
                tool_id = t.get("id")
                if tool_id is None:
                    continue
                self._cache[tool_id] = ToolEntry(
                    tool_id=tool_id,
                    version=t.get("version", ""),
                    script_path=t.get("script_path", ""),
                    script_class=t.get("script_class", ""),
                )
            self._version = self._compute_version()
        self._db.save_tool_cache(
            {
                tid: {
                    "version": e.version,
                    "script_path": e.script_path,
                    "script_class": e.script_class,
                }
                for tid, e in self._cache.items()
            }
        )

    def _load_from_sqlite(self) -> None:
        cached = self._db.load_tool_cache()
        with self._lock:
            self._cache.clear()
            for tool_id, data in cached.items():
                self._cache[tool_id] = ToolEntry(
                    tool_id=tool_id,
                    version=data.get("version", ""),
                    script_path=data.get("script_path", ""),
                    script_class=data.get("script_class", ""),
                )
            self._version = self._compute_version()

    def _compute_version(self) -> str:
        catalog = sorted((tid, e.version) for tid, e in self._cache.items())
        return hashlib.md5(json.dumps(catalog).encode()).hexdigest()[:12]

    # ------------------------------------------------------------------
    # Runtime resolution
    # ------------------------------------------------------------------

    def resolve(self, tool_id: int, required_version: str) -> ToolEntry:
        """Return ToolEntry. Raises ToolVersionMismatch or ToolNotFoundLocally."""
        with self._lock:
            entry = self._cache.get(tool_id)
        if entry is None:
            raise ToolNotFoundLocally(tool_id)
        if entry.version != required_version:
            raise ToolVersionMismatch(tool_id, entry.version, required_version)
        return entry

    def pull_tool_sync(self, tool_id: int, version: str) -> bool:
        """Fetch a specific tool version from server. Returns True on success."""
        headers = self._auth_headers()
        for attempt in range(3):
            try:
                resp = requests.get(
                    f"{self._api_url}/api/v1/tools/{tool_id}",
                    headers=headers,
                    timeout=10,
                )
                resp.raise_for_status()
                payload = resp.json()
                data = payload.get("data", payload) if isinstance(payload, dict) else payload
                entry = ToolEntry(
                    tool_id=tool_id,
                    version=data.get("version", version),
                    script_path=data.get("script_path", ""),
                    script_class=data.get("script_class", ""),
                )
                with self._lock:
                    self._cache[tool_id] = entry
                    self._version = self._compute_version()
                self._db.update_tool_cache(
                    tool_id,
                    {
                        "version": entry.version,
                        "script_path": entry.script_path,
                        "script_class": entry.script_class,
                    },
                )
                logger.info(f"ToolRegistry pulled tool_id={tool_id} version={version}")
                return True
            except Exception as e:
                logger.warning(f"pull_tool attempt {attempt + 1}/3 failed: {e}")
                if attempt < 2:
                    time.sleep(2 ** attempt)
        return False

    def _auth_headers(self) -> dict:
        if self._agent_secret:
            return {"X-Agent-Secret": self._agent_secret}
        return {}
