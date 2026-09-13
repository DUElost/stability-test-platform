"""Alembic code head vs database revision helpers (#1882)."""

from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

_REPO_ROOT = Path(__file__).resolve().parents[2]
_BACKEND_DIR = _REPO_ROOT / "backend"
_ALEMBIC_INI = _BACKEND_DIR / "alembic.ini"


def code_head_revision() -> str:
    """Return the single alembic head revision from migration scripts."""
    config = Config(str(_ALEMBIC_INI))
    config.set_main_option("script_location", str(_BACKEND_DIR / "alembic"))
    script = ScriptDirectory.from_config(config)
    heads = script.get_heads()
    if len(heads) != 1:
        raise RuntimeError(f"alembic must have single head, got {heads!r}")
    return heads[0]


async def database_revision(conn: AsyncConnection) -> str | None:
    """Return ``alembic_version.version_num`` or None when the table is empty."""
    result = await conn.execute(text("SELECT version_num FROM alembic_version LIMIT 1"))
    row = result.first()
    return row[0] if row else None


def is_schema_at_head(db_revision: str | None, code_head: str) -> bool:
    """True when the database revision matches the code head."""
    return db_revision == code_head
