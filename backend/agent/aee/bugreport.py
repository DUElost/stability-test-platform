"""Bugreport export — aligned with export_bugreport_for_timestamp."""

from __future__ import annotations

import logging
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Dict, Optional

from .timestamp import format_timestamp_for_filename

logger = logging.getLogger(__name__)

_cooldown_lock = threading.Lock()
_last_export_ts: Dict[str, float] = {}


def export_bugreport_for_timestamp(
    *,
    serial: str,
    timestamp_str: str,
    output_dir: Path,
    adb_path: str = "adb",
    event_type: Optional[str] = None,
    enabled: bool = True,
    cooldown_seconds: int = 300,
    cooldown_event_types: Optional[set[str]] = None,
    timeout_seconds: int = 600,
    temp_suffix: str = ".partial",
) -> bool:
    """Export adb bugreport zip named `{formatted_ts}_bugreport.zip` under bugreport/."""
    if not enabled:
        return False

    cooldown_types = cooldown_event_types or {"ANR", "CRASH"}
    normalized_type = (event_type or "").strip().upper()
    if cooldown_seconds > 0 and normalized_type in cooldown_types:
        now = time.time()
        with _cooldown_lock:
            last_ts = _last_export_ts.get(serial)
            if last_ts and (now - last_ts) < cooldown_seconds:
                logger.info(
                    "bugreport_cooldown serial=%s event=%s remaining=%ds",
                    serial,
                    normalized_type,
                    int(cooldown_seconds - (now - last_ts)),
                )
                return False
            _last_export_ts[serial] = now

    bugreport_dir = output_dir / "bugreport"
    bugreport_dir.mkdir(parents=True, exist_ok=True)

    formatted_ts = format_timestamp_for_filename(timestamp_str)
    final_path = bugreport_dir / f"{formatted_ts}_bugreport.zip"
    if final_path.exists():
        logger.info("bugreport_exists path=%s", final_path.name)
        return True

    temp_path = Path(str(final_path) + temp_suffix)
    if temp_path.exists():
        try:
            temp_path.unlink()
        except OSError:
            pass

    try:
        result = subprocess.run(
            [adb_path, "-s", serial, "bugreport", str(temp_path)],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        logger.error("bugreport_timeout serial=%s", serial)
        _safe_unlink(temp_path)
        return False

    if result.returncode != 0 or not temp_path.exists():
        logger.error(
            "bugreport_failed serial=%s rc=%s stderr=%s",
            serial,
            result.returncode,
            (result.stderr or "")[:200],
        )
        _safe_unlink(temp_path)
        return False

    try:
        os.replace(temp_path, final_path)
    except OSError as exc:
        logger.error("bugreport_rename_failed: %s", exc)
        _safe_unlink(temp_path)
        return False

    logger.info("bugreport_exported serial=%s path=%s", serial, final_path.name)
    return True


def _safe_unlink(path: Path) -> None:
    try:
        if path.exists():
            path.unlink()
    except OSError:
        pass
