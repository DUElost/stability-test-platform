"""Control-plane scheduler leader election (ADR-0027 P3-1).

Singleton APScheduler jobs (admission pump, counter reconcile, …) must run on
at most one control-plane process. Under the historical single-process
constraint this was implicit; once multi-instance is allowed, each tick
acquires a Postgres session-level advisory lock for the job name.

Behaviour:
- ``STP_SCHEDULER_LEADER_ELECTION=0`` → always leader (legacy single-process).
- ``=1`` (default) → ``pg_try_advisory_lock``; SQLite / ``TESTING=1`` /
  non-Postgres / connection failure → always leader (fail-open).
- Lock is held only for the duration of the ``leadership`` context and is
  released on exit (or when the DB session closes).
"""

from __future__ import annotations

import hashlib
import logging
import os
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import text

logger = logging.getLogger(__name__)

_FALSEY = frozenset({"0", "false", "False", "no", "NO", "off", "OFF"})


def leader_election_enabled() -> bool:
    """Default ON: mistaken multi-instance deploys still serialise singleton jobs."""
    return os.getenv("STP_SCHEDULER_LEADER_ELECTION", "1").strip() not in _FALSEY


def advisory_lock_key(job_name: str) -> int:
    """Stable signed 63-bit key derived from job name (Postgres bigint)."""
    digest = hashlib.sha256(f"stp:scheduler:leader:{job_name}".encode()).digest()
    return int.from_bytes(digest[:8], "big") & 0x7FFFFFFFFFFFFFFF


@contextmanager
def hold_scheduler_leadership(job_name: str) -> Iterator[bool]:
    """Yield True iff this process should run ``job_name`` this tick.

    When election is disabled, always yields True without touching the DB.
    """
    if not leader_election_enabled():
        yield True
        return

    # Pytest / agent suites must not depend on a live Postgres advisory lock.
    if os.getenv("TESTING") == "1":
        yield True
        return

    from backend.core.database import SessionLocal, is_sqlite_url, normalize_sync_database_url
    import backend.core.database as db_mod

    sync_url = normalize_sync_database_url(db_mod.DATABASE_URL)
    if is_sqlite_url(sync_url):
        yield True
        return
    if not sync_url.startswith("postgresql"):
        yield True
        return

    key = advisory_lock_key(job_name)
    try:
        db_cm = SessionLocal()
    except Exception:
        logger.warning(
            "scheduler_leadership_fail_open job=%s reason=session_factory",
            job_name,
            exc_info=True,
        )
        yield True
        return

    db = db_cm
    try:
        # Probe dialect without assuming connect succeeded until execute.
        bind = db.get_bind()
        if getattr(bind.dialect, "name", "") != "postgresql":
            yield True
            return
        acquired = bool(
            db.execute(
                text("SELECT pg_try_advisory_lock(:k)"),
                {"k": key},
            ).scalar()
        )
    except Exception:
        logger.warning(
            "scheduler_leadership_fail_open job=%s reason=lock_acquire",
            job_name,
            exc_info=True,
        )
        try:
            db.close()
        except Exception:
            pass
        yield True
        return

    if not acquired:
        logger.debug("scheduler_leadership_skipped job=%s", job_name)
        try:
            db.close()
        except Exception:
            pass
        yield False
        return

    try:
        yield True
    finally:
        try:
            db.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": key})
            db.commit()
        except Exception:
            logger.debug(
                "scheduler_leadership_unlock_failed job=%s",
                job_name,
                exc_info=True,
            )
        try:
            db.close()
        except Exception:
            pass
