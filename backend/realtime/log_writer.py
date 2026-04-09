"""
Async log file writer — persists SocketIO log lines to disk.

File layout:  {LOG_BASE_DIR}/jobs/{job_id}/console.log
Uses asyncio file I/O to avoid blocking the event loop.
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Dict

logger = logging.getLogger(__name__)

LOG_BASE_DIR = Path(os.getenv("LOG_BASE_DIR", "data/logs"))

_locks: Dict[int, asyncio.Lock] = {}


def _get_lock(job_id: int) -> asyncio.Lock:
    if job_id not in _locks:
        _locks[job_id] = asyncio.Lock()
    return _locks[job_id]


def _log_path(job_id: int) -> Path:
    return LOG_BASE_DIR / "jobs" / str(job_id) / "console.log"


async def append_log_line(
    job_id: int,
    line: str,
    level: str = "INFO",
    ts: str = "",
    step_id: str = "",
) -> None:
    """Append a single log line to the job's console.log file."""
    path = _log_path(job_id)
    path.parent.mkdir(parents=True, exist_ok=True)

    prefix = f"{ts} [{level}]" if ts else f"[{level}]"
    if step_id:
        prefix = f"{prefix} [{step_id}]"
    formatted = f"{prefix} {line}\n"

    lock = _get_lock(job_id)
    async with lock:
        await asyncio.to_thread(_write_line, path, formatted)


def _write_line(path: Path, text: str) -> None:
    with open(path, "a", encoding="utf-8") as f:
        f.write(text)


async def read_job_logs(job_id: int, tail: int = 200) -> list[str]:
    """Read the last N lines from a job's console.log (for replay on connect)."""
    path = _log_path(job_id)
    if not path.exists():
        return []
    try:
        lines = await asyncio.to_thread(_read_tail, path, tail)
        return lines
    except Exception:
        logger.debug("read_job_logs_failed job_id=%d", job_id, exc_info=True)
        return []


def _read_tail(path: Path, n: int) -> list[str]:
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        all_lines = f.readlines()
    return all_lines[-n:] if len(all_lines) > n else all_lines
