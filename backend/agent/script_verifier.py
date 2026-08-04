"""Agent-side script content verification (ADR-0021).

Pure helpers used by the SocketIO ``verify_scripts`` RPC handler.  Splitting
them out of ``socketio_client.py`` keeps the handler thin and makes the verification
logic unit-testable without spinning up SocketIO.
"""

from __future__ import annotations

import hashlib
import logging
import os
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

logger = logging.getLogger(__name__)

_HASH_CHUNK_BYTES = 65536


def hash_local_script_file(path: Optional[str]) -> Optional[str]:
    """Compute sha256 of a local file.

    Returns ``None`` if the path is empty, the file does not exist, or it
    cannot be read.  Never raises — failures map to ``None`` so the caller
    can flag ``ok=False`` / ``exists=False`` deterministically.
    """
    if not path:
        return None

    try:
        h = hashlib.sha256()
        with open(path, "rb") as fp:
            while True:
                chunk = fp.read(_HASH_CHUNK_BYTES)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest()
    except (FileNotFoundError, IsADirectoryError, NotADirectoryError):
        return None
    except PermissionError as exc:
        logger.warning("hash_local_script_permission_denied path=%s: %s", path, exc)
        return None
    except OSError as exc:
        logger.warning("hash_local_script_io_error path=%s: %s", path, exc)
        return None


def _verify_support_files(
    nfs_path: str,
    support_files: dict,
) -> tuple[bool, str | None]:
    if not support_files:
        return True, None
    base_dir = os.path.dirname(nfs_path or "")
    if not base_dir:
        return False, "support_file_base_missing"
    for fname, expected_sha in sorted(support_files.items()):
        actual = hash_local_script_file(os.path.join(base_dir, fname))
        if actual != expected_sha:
            return False, f"support_file_mismatch:{fname}"
    return True, None


def verify_scripts_payload(
    expected: Iterable[dict],
    *,
    host_id: str,
    agent_version: Optional[str] = None,
) -> dict:
    """Build the ack payload for the ``verify_scripts`` RPC.

    ``expected`` is an iterable of dicts with the keys ``name``, ``version``,
    ``nfs_path``, ``sha256`` (server-side authority).  The returned payload
    matches the schema defined in ADR-0021 §D10.
    """
    results: list[dict[str, Any]] = []
    for entry in expected or []:
        name = entry.get("name") or ""
        version = entry.get("version") or ""
        expected_sha = entry.get("sha256") or ""
        nfs_path = entry.get("nfs_path") or ""

        actual_sha = hash_local_script_file(nfs_path)
        exists = actual_sha is not None
        support_files = entry.get("support_files") or {}
        support_ok, support_err = _verify_support_files(nfs_path, support_files)
        entry_ok = exists and actual_sha == expected_sha
        ok = entry_ok and support_ok
        if ok:
            error = None
        elif not exists:
            error = "file_missing_or_unreadable"
        elif not entry_ok:
            error = "sha_mismatch"
        else:
            error = support_err

        results.append(
            {
                "name": name,
                "version": version,
                "expected_sha": expected_sha,
                "actual_sha": actual_sha,
                "exists": exists,
                "ok": ok,
                "error": error,
            }
        )

    return {
        "host_id": str(host_id),
        "agent_version": agent_version
        or os.getenv("STP_AGENT_VERSION", "unknown"),
        "results": results,
        "checked_at": datetime.now(timezone.utc).isoformat() + "Z",
    }
