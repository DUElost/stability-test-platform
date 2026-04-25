"""Script Registry — maps script name/version to NFS script metadata."""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from dataclasses import dataclass
from typing import Dict

import requests

from .local_db import LocalDB

logger = logging.getLogger(__name__)


@dataclass
class ScriptEntry:
    script_id: int
    name: str
    version: str
    script_type: str
    nfs_path: str
    content_sha256: str


class ScriptNotFoundLocally(Exception):
    def __init__(self, name: str, version: str = "") -> None:
        suffix = f" version {version}" if version else ""
        super().__init__(f"Script {name}{suffix} not found in local registry")
        self.name = name
        self.version = version


class ScriptVersionMismatch(Exception):
    def __init__(self, name: str, cached_versions: list[str], required: str) -> None:
        super().__init__(
            f"Script {name} version mismatch: cached={cached_versions!r}, required={required!r}"
        )
        self.name = name
        self.cached_versions = cached_versions
        self.required_version = required


class ScriptRegistry:
    """Thread-safe script catalog. Server is source of truth; SQLite is fallback."""

    def __init__(self, db: LocalDB, api_url: str, agent_secret: str = "") -> None:
        self._db = db
        self._api_url = api_url.rstrip("/")
        self._agent_secret = agent_secret
        self._cache: Dict[str, ScriptEntry] = {}
        self._version: str = ""
        self._lock = threading.RLock()

    @property
    def version(self) -> str:
        return self._version

    def initialize(self) -> None:
        try:
            self._load_from_server()
            with self._lock:
                count = len(self._cache)
            logger.info("ScriptRegistry loaded %d scripts from server (version=%s)", count, self._version)
        except Exception as exc:
            logger.warning("ScriptRegistry server load failed: %s — loading from SQLite cache", exc)
            self._load_from_sqlite()

    def _load_from_server(self) -> None:
        resp = requests.get(
            f"{self._api_url}/api/v1/scripts",
            headers=self._auth_headers(),
            params={"is_active": True},
            timeout=10,
        )
        resp.raise_for_status()
        payload = resp.json()
        scripts = payload.get("data", payload) if isinstance(payload, dict) else payload

        with self._lock:
            self._cache.clear()
            for item in scripts or []:
                name = item.get("name", "")
                version = item.get("version", "")
                if not name or not version:
                    continue
                entry = ScriptEntry(
                    script_id=int(item.get("id", item.get("script_id", 0))),
                    name=name,
                    version=version,
                    script_type=item.get("script_type", ""),
                    nfs_path=item.get("nfs_path", ""),
                    content_sha256=item.get("content_sha256", ""),
                )
                self._cache[self._key(name, version)] = entry
            self._version = self._compute_version()

        self._db.save_script_cache({
            key: {
                "script_id": entry.script_id,
                "name": entry.name,
                "version": entry.version,
                "script_type": entry.script_type,
                "nfs_path": entry.nfs_path,
                "content_sha256": entry.content_sha256,
            }
            for key, entry in self._cache.items()
        })

    def _load_from_sqlite(self) -> None:
        cached = self._db.load_script_cache()
        with self._lock:
            self._cache.clear()
            for key, item in cached.items():
                self._cache[key] = ScriptEntry(
                    script_id=int(item.get("script_id", 0)),
                    name=item.get("name", ""),
                    version=item.get("version", ""),
                    script_type=item.get("script_type", ""),
                    nfs_path=item.get("nfs_path", ""),
                    content_sha256=item.get("content_sha256", ""),
                )
            self._version = self._compute_version()

    def resolve(self, name: str, required_version: str) -> ScriptEntry:
        key = self._key(name, required_version)
        with self._lock:
            entry = self._cache.get(key)
            cached_versions = [
                item.version for item in self._cache.values() if item.name == name
            ]
        if entry is not None:
            return entry
        if cached_versions:
            raise ScriptVersionMismatch(name, sorted(cached_versions), required_version)
        raise ScriptNotFoundLocally(name, required_version)

    def resolve_latest(self, name: str) -> ScriptEntry:
        with self._lock:
            matches = [item for item in self._cache.values() if item.name == name]
        if not matches:
            raise ScriptNotFoundLocally(name)
        return sorted(matches, key=lambda item: self._version_tuple(item.version))[-1]

    def _compute_version(self) -> str:
        catalog = sorted(
            (entry.name, entry.version, entry.content_sha256)
            for entry in self._cache.values()
        )
        return hashlib.md5(json.dumps(catalog).encode()).hexdigest()[:12]

    def _auth_headers(self) -> dict:
        if self._agent_secret:
            return {"X-Agent-Secret": self._agent_secret}
        return {}

    @staticmethod
    def _key(name: str, version: str) -> str:
        return f"{name}::{version}"

    @staticmethod
    def _version_tuple(version: str) -> tuple[int, ...]:
        parts = []
        for part in version.split("."):
            try:
                parts.append(int(part))
            except ValueError:
                parts.append(0)
        return tuple(parts)
