"""Host-level AEE extraction concurrency (#740).

Each JobSession runs its own Reconciler thread; without a process-wide gate,
20+ devices crashing together hammer mechanical HDD with concurrent
``adb pull`` / mobilelog / bugreport writes. EventUploader already limits
upload concurrency; this module mirrors that for the edge pull path.
"""

from __future__ import annotations

import logging
import os
import threading
from contextlib import contextmanager
from typing import Iterator, Optional

logger = logging.getLogger(__name__)

_DEFAULT_MAX_CONCURRENT_PULLS = 2

_lock = threading.Lock()
_sem: Optional[threading.Semaphore] = None
_limit: int = _DEFAULT_MAX_CONCURRENT_PULLS


def _parse_limit() -> int:
    raw = (os.getenv("STP_AEE_MAX_CONCURRENT_PULLS") or "").strip()
    if not raw:
        return _DEFAULT_MAX_CONCURRENT_PULLS
    try:
        value = int(raw)
    except ValueError:
        logger.warning(
            "aee_extraction_slot_invalid_limit value=%r falling back to %d",
            raw, _DEFAULT_MAX_CONCURRENT_PULLS,
        )
        return _DEFAULT_MAX_CONCURRENT_PULLS
    if value < 1:
        logger.warning(
            "aee_extraction_slot_non_positive value=%r falling back to %d",
            raw, _DEFAULT_MAX_CONCURRENT_PULLS,
        )
        return _DEFAULT_MAX_CONCURRENT_PULLS
    return value


def get_extraction_limit() -> int:
    """Current configured host-level pull concurrency (lazy-init)."""
    _ensure_sem()
    return _limit


def _ensure_sem() -> threading.Semaphore:
    global _sem, _limit
    with _lock:
        if _sem is None:
            _limit = _parse_limit()
            _sem = threading.Semaphore(_limit)
            logger.info("aee_extraction_slot_initialized limit=%d", _limit)
        return _sem


def reset_extraction_slot_for_tests() -> None:
    """Drop the process semaphore so the next acquire re-reads env."""
    global _sem, _limit
    with _lock:
        _sem = None
        _limit = _DEFAULT_MAX_CONCURRENT_PULLS


@contextmanager
def host_extraction_slot(*, purpose: str = "pull") -> Iterator[None]:
    """Acquire a host-wide AEE extraction slot (blocking).

    Hold across ``adb pull`` + correlated mobilelog/bugreport writes for one
    event so concurrent devices queue instead of thrashing the HDD.
    """
    sem = _ensure_sem()
    logger.debug("aee_extraction_slot_wait purpose=%s limit=%d", purpose, _limit)
    sem.acquire()
    logger.debug("aee_extraction_slot_acquired purpose=%s", purpose)
    try:
        yield
    finally:
        sem.release()
        logger.debug("aee_extraction_slot_released purpose=%s", purpose)


__all__ = [
    "get_extraction_limit",
    "host_extraction_slot",
    "reset_extraction_slot_for_tests",
]
