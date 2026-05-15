"""Migrate legacy Host SSH secrets out of host.extra into secure columns.

Usage:
    python -m backend.scripts.migrate_host_ssh_credentials --dry-run
    python -m backend.scripts.migrate_host_ssh_credentials --host-id <id>
"""

from __future__ import annotations

import argparse
import json
from typing import Sequence

from backend.core.database import SessionLocal
from backend.core.ssh_security import SshSecurityConfigError, resolve_host_ssh_credentials
from backend.models.host import Host


def migrate_legacy_host_ssh_credentials(
    db,
    *,
    dry_run: bool = False,
    host_ids: Sequence[str] | None = None,
    limit: int | None = None,
) -> dict[str, object]:
    savepoint = db.begin_nested() if dry_run else None
    query = db.query(Host).order_by(Host.id)
    if host_ids:
        query = query.filter(Host.id.in_([str(item) for item in host_ids]))
    if limit and limit > 0:
        query = query.limit(limit)

    hosts = query.all()
    summary: dict[str, object] = {
        "dry_run": dry_run,
        "scanned": len(hosts),
        "changed": 0,
        "passwords_migrated": 0,
        "key_paths_migrated": 0,
        "changed_host_ids": [],
    }

    for host in hosts:
        extra = dict(host.extra or {})
        had_legacy_password = bool(str(extra.get("ssh_password", "") or "").strip())
        had_legacy_key_path = bool(str(extra.get("ssh_key_path", "") or "").strip())

        _creds, migrated = resolve_host_ssh_credentials(host, inventory_lookup=None)
        if not migrated:
            continue

        summary["changed"] = int(summary["changed"]) + 1
        if had_legacy_password:
            summary["passwords_migrated"] = int(summary["passwords_migrated"]) + 1
        if had_legacy_key_path:
            summary["key_paths_migrated"] = int(summary["key_paths_migrated"]) + 1
        changed_host_ids = summary["changed_host_ids"]
        assert isinstance(changed_host_ids, list)
        changed_host_ids.append(host.id)

    if dry_run:
        if savepoint is not None:
            savepoint.rollback()
        db.expire_all()
    else:
        db.commit()
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Report changes without committing them")
    parser.add_argument("--host-id", dest="host_ids", action="append", help="Only migrate the specified host id; can be repeated")
    parser.add_argument("--limit", type=int, default=0, help="Maximum number of hosts to scan (0 = no limit)")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        with SessionLocal() as db:
            summary = migrate_legacy_host_ssh_credentials(
                db,
                dry_run=args.dry_run,
                host_ids=args.host_ids,
                limit=args.limit or None,
            )
    except SshSecurityConfigError as exc:
        print(f"ERROR: {exc}")
        return 2
    except Exception as exc:
        print(f"ERROR: migration failed: {exc}")
        return 1

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
