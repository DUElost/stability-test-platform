#!/usr/bin/env python3
"""Assert production DB ``alembic_version`` matches code head (#1882).

Usage (from repo root)::

    ./venv/bin/python tools/dev/check_alembic_at_head.py

Resolves ``DATABASE_URL`` from ambient env first, then repo-root
``.env.backend``, then ``.env``. When ``DATABASE_URL`` is not configured,
prints a WARN and exits 0 (dev machines without DB). When configured,
compares ``SELECT version_num FROM alembic_version`` to the single alembic
head from ``backend/alembic.ini``.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import psycopg

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

def _load_schema_revision_helpers():
    import importlib.util

    mod_path = _REPO_ROOT / "backend" / "core" / "schema_revision.py"
    spec = importlib.util.spec_from_file_location("stp_schema_revision", mod_path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_schema_revision = _load_schema_revision_helpers()
code_head_revision = _schema_revision.code_head_revision
is_schema_at_head = _schema_revision.is_schema_at_head


def _load_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        k, _, v = line.partition("=")
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def _database_url() -> str | None:
    url = (os.getenv("DATABASE_URL") or "").strip()
    if not url:
        env = _load_env_file(_REPO_ROOT / ".env.backend")
        url = (env.get("DATABASE_URL") or "").strip()
        if not url:
            env = _load_env_file(_REPO_ROOT / ".env")
            url = (env.get("DATABASE_URL") or "").strip()
    if not url:
        return None
    return re.sub(r"^postgresql\+[^:]+://", "postgresql://", url, count=1)


def main() -> None:
    url = _database_url()
    if not url:
        print(
            "check_alembic_at_head: WARN —— DATABASE_URL 未配置，跳过 schema 对齐检查",
            file=sys.stderr,
        )
        raise SystemExit(0)

    try:
        head = code_head_revision()
    except RuntimeError as exc:
        print(f"check_alembic_at_head: FAIL —— {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    try:
        with psycopg.connect(url) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT version_num FROM alembic_version LIMIT 1")
                row = cur.fetchone()
                db_revision = row[0] if row else None
    except Exception as exc:
        print(
            f"check_alembic_at_head: FAIL —— 无法读取 alembic_version: {exc}",
            file=sys.stderr,
        )
        raise SystemExit(1) from exc

    if not is_schema_at_head(db_revision, head):
        print(
            "check_alembic_at_head: FAIL —— "
            f"alembic_version={db_revision!r} != code head {head!r}",
            file=sys.stderr,
        )
        print(
            "  先执行：cd backend && python -m alembic upgrade head",
            file=sys.stderr,
        )
        raise SystemExit(1)

    print(f"check_alembic_at_head: OK —— alembic_version={db_revision!r} == head {head!r}")


if __name__ == "__main__":
    main()
