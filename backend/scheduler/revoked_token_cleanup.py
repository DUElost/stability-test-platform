"""Periodic cleanup of expired revoked refresh tokens.

APScheduler sync job. Uses SessionLocal so it doesn't share session state with
HTTP request handlers.
"""
from __future__ import annotations

import logging

from backend.core.database import SessionLocal
from backend.services.token_blacklist import cleanup_expired

logger = logging.getLogger(__name__)


def cleanup_revoked_refresh_tokens_job() -> int:
    """Delete revoked_refresh_token rows whose expires_at has passed."""
    session = SessionLocal()
    try:
        return cleanup_expired(session)
    except Exception:
        logger.exception("revoked_token_cleanup_failed")
        return 0
    finally:
        session.close()
