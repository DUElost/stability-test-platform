"""Bugreport export — aligned with export_bugreport_for_timestamp."""

from __future__ import annotations

import logging
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Dict, Optional

from .paths import resolve_bugreport_subdir
from .timestamp import format_timestamp_for_filename

logger = logging.getLogger(__name__)

_cooldown_lock = threading.Lock()
_last_export_ts: Dict[str, float] = {}


def _is_bugreport_in_cooldown(
    serial: str,
    *,
    normalized_type: str,
    cooldown_seconds: int,
) -> bool:
    if cooldown_seconds <= 0 or not normalized_type:
        return False
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
            return True
    return False


def _mark_bugreport_exported(serial: str) -> None:
    with _cooldown_lock:
        _last_export_ts[serial] = time.time()


def _terminate_process(proc) -> None:
    try:
        proc.terminate()
    except Exception:
        pass
    try:
        proc.wait(timeout=0.2)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass
        try:
            proc.wait(timeout=0.2)
        except Exception:
            pass
    try:
        proc.communicate(timeout=0.2)
    except Exception:
        pass


def _run_bugreport_interruptibly(
    argv: list[str],
    *,
    timeout_seconds: int,
    stop_event: Optional[threading.Event],
):
    if stop_event is None:
        return subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    if stop_event.is_set():
        raise subprocess.TimeoutExpired(argv, timeout_seconds)

    proc = subprocess.Popen(
        argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    deadline = time.monotonic() + max(float(timeout_seconds), 0.0)
    while True:
        if stop_event.is_set():
            _terminate_process(proc)
            raise subprocess.TimeoutExpired(argv, timeout_seconds)
        rc = proc.poll()
        if rc is not None:
            stdout, stderr = proc.communicate(timeout=0.2)
            return subprocess.CompletedProcess(
                args=argv, returncode=rc, stdout=stdout, stderr=stderr,
            )
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            _terminate_process(proc)
            raise subprocess.TimeoutExpired(argv, timeout_seconds)
        stop_event.wait(min(0.1, remaining))


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
    stop_event: Optional[threading.Event] = None,
) -> bool:
    """Export adb bugreport zip named `{formatted_ts}_bugreport.zip`.

    ADR-0018 2026-06-18: output_dir 由调用方传入事件目录(local_target_dir),
    bugreport 落在 output_dir/bugreport/(或 correlated_bugreports/,由 env 控制)。
    """
    if not enabled:
        return False

    cooldown_types = cooldown_event_types or {"ANR", "CRASH"}
    normalized_type = (event_type or "").strip().upper()
    if (
        normalized_type in cooldown_types
        and _is_bugreport_in_cooldown(
            serial,
            normalized_type=normalized_type,
            cooldown_seconds=cooldown_seconds,
        )
    ):
        return False

    bugreport_dir = output_dir / resolve_bugreport_subdir()
    bugreport_dir.mkdir(parents=True, exist_ok=True)

    formatted_ts = format_timestamp_for_filename(timestamp_str)
    final_path = bugreport_dir / f"{formatted_ts}_bugreport.zip"
    if final_path.exists():
        logger.info("bugreport_exists path=%s", final_path.name)
        if normalized_type in cooldown_types:
            _mark_bugreport_exported(serial)
        return True

    temp_path = Path(str(final_path) + temp_suffix)
    if temp_path.exists():
        try:
            temp_path.unlink()
        except OSError:
            pass

    try:
        result = _run_bugreport_interruptibly(
            [adb_path, "-s", serial, "bugreport", str(temp_path)],
            timeout_seconds=timeout_seconds,
            stop_event=stop_event,
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

    if normalized_type in cooldown_types:
        _mark_bugreport_exported(serial)
    logger.info("bugreport_exported serial=%s path=%s", serial, final_path.name)
    return True


def _safe_unlink(path: Path) -> None:
    try:
        if path.exists():
            path.unlink()
    except OSError:
        pass
