"""Refresh token blacklist service.

PG-backed jti blacklist with idempotent inserts (ON CONFLICT DO NOTHING) and a
cleanup hook for APScheduler. The companion model is RevokedRefreshToken; see
the module docstring there for the why.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.models.token_blacklist import RevokedRefreshToken

logger = logging.getLogger(__name__)


def is_revoked(db: Session, jti: str) -> bool:
    """Return True if the given jti is on the blacklist."""
    if not jti:
        return False
    return db.query(RevokedRefreshToken.jti).filter(
        RevokedRefreshToken.jti == jti
    ).first() is not None


def revoke(
    db: Session,
    *,
    jti: str,
    expires_at: datetime,
    reason: Optional[str] = None,
) -> bool:
    """Insert a jti into the blacklist; idempotent on duplicate jti.

    Returns True if a row was inserted, False if the jti was already blacklisted.
    """
    if not jti:
        return False

    now = datetime.now(timezone.utc)
    # PG path uses ON CONFLICT for idempotency without surfacing IntegrityError.
    # RETURNING jti reliably tells us whether the row was inserted (rowcount
    # semantics on ON CONFLICT DO NOTHING are driver-dependent).
    dialect = db.bind.dialect.name if db.bind is not None else ""
    if dialect == "postgresql":
        stmt = pg_insert(RevokedRefreshToken).values(
            jti=jti,
            revoked_at=now,
            expires_at=expires_at,
            reason=reason,
        ).on_conflict_do_nothing(index_elements=["jti"]).returning(RevokedRefreshToken.jti)
        result = db.execute(stmt)
        inserted = result.first() is not None
        db.commit()
        return inserted

    # Generic fallback (SQLite tests) — try insert, swallow PK collision.
    try:
        db.add(RevokedRefreshToken(
            jti=jti,
            revoked_at=now,
            expires_at=expires_at,
            reason=reason,
        ))
        db.commit()
        return True
    except IntegrityError:
        db.rollback()
        return False


def cleanup_expired(db: Session, *, now: Optional[datetime] = None) -> int:
    """Delete rows whose expires_at has passed.

    Returns the number of rows deleted. Intended to be wired into APScheduler.
    """
    cutoff = now or datetime.now(timezone.utc)
    result = db.execute(
        delete(RevokedRefreshToken).where(RevokedRefreshToken.expires_at < cutoff)
    )
    db.commit()
    deleted = result.rowcount or 0
    if deleted:
        logger.info("revoked_refresh_token_cleanup deleted=%d cutoff=%s", deleted, cutoff)
    return deleted
