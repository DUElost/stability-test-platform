"""Control-plane view of the script catalog version.

The Agent caches the catalog it fetched from ``GET /api/v1/scripts?is_active=1``
and reports a digest of it on every heartbeat. For the heartbeat to be able to
say "your copy is stale", the control plane must compute the digest **the same
way** over its own current catalog — otherwise the two numbers are not
comparable.

Before this existed, ``agent_api`` compared the agent's reported digest against
*the previous value reported by that same agent*, so it only ever noticed the
agent changing, never the server. Publishing a new script version therefore
never reached a running Agent, and the mistake surfaced much later as
``ScriptVersionMismatch: cached=[...], required='2.0.0'`` when a job tried to
run the new version — with a manual restart of every Agent as the only cure.

Keep :func:`compute_script_catalog_version` byte-for-byte equivalent to
``ScriptRegistry._compute_version`` (``backend/agent/registry/script_registry.py``):
same field order, same filtering, same md5-prefix length. The parity test lives
in ``backend/tests/services/test_script_catalog_version.py``.
"""

from __future__ import annotations

import hashlib
import json

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from backend.core.legacy_aee import LEGACY_AEE_SCRIPT_NAMES
from backend.models.script import Script

_ACTIVE_SCRIPTS = select(Script.name, Script.version, Script.content_sha256).where(
    Script.is_active.is_(True)
)


def catalog_digest(entries: list[tuple[str, str, str]]) -> str:
    """Digest ``[(name, version, content_sha256), ...]``.

    Mirrors ``ScriptRegistry._compute_version``.
    """
    return hashlib.md5(json.dumps(sorted(entries)).encode()).hexdigest()[:12]


def _digest_rows(rows) -> str:
    return catalog_digest([
        (r.name, r.version, r.content_sha256 or "")
        for r in rows
        if r.name not in LEGACY_AEE_SCRIPT_NAMES
    ])


def compute_script_catalog_version(db: Session) -> str:
    """Digest of every active, non-legacy script row."""
    return _digest_rows(db.execute(_ACTIVE_SCRIPTS).all())


async def compute_script_catalog_version_async(db: AsyncSession) -> str:
    """Async twin of :func:`compute_script_catalog_version` (heartbeat path)."""
    result = await db.execute(_ACTIVE_SCRIPTS)
    return _digest_rows(result.all())
