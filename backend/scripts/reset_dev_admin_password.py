"""DEV ONLY — create or reset the local smoke/admin account from .env.

Reads ``STP_ADMIN_USER`` (default ``admin``) and ``STP_ADMIN_PASSWORD`` from the
repository root ``.env`` (or the current environment) and upserts that user with
``role=admin``.

Refused when ``ENV=production`` unless ``--force-dev-only`` is passed explicitly.

Import order: ``load_repo_dotenv()`` must run before ``backend.core.database`` is
imported, because ``database.py`` reads ``DATABASE_URL`` at module import time.

Usage:
    # After setting STP_ADMIN_PASSWORD (and optionally STP_ADMIN_USER) in .env:
    python backend/scripts/reset_dev_admin_password.py
    python backend/scripts/reset_dev_admin_password.py --username admin
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from backend.scripts.seed_and_smoke import load_repo_dotenv

# DATABASE_URL is bound when SessionLocal/engine are created — load .env first.
_ENV_FILE = load_repo_dotenv(_REPO_ROOT)

from backend.core.database import SessionLocal
from backend.core.security import get_password_hash
from backend.models.user import User


def _refuse_in_production(force: bool) -> None:
    if os.getenv("ENV", "").strip().lower() != "production":
        return
    if force:
        print(
            "[WARN] ENV=production — proceeding only because --force-dev-only was set.",
            file=sys.stderr,
        )
        return
    print(
        "Refusing to reset admin credentials when ENV=production. "
        "Use your normal production user-management process instead.",
        file=sys.stderr,
    )
    sys.exit(2)


def reset_dev_admin(*, username: str, password: str, dry_run: bool = False) -> str:
    """Upsert admin user. Returns action label: created | updated."""
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.username == username).first()
        hashed = get_password_hash(password)
        if user is None:
            user = User(
                username=username,
                hashed_password=hashed,
                role="admin",
                is_active="Y",
            )
            db.add(user)
            action = "created"
        else:
            user.hashed_password = hashed
            user.role = "admin"
            user.is_active = "Y"
            action = "updated"
        if dry_run:
            db.rollback()
        else:
            db.commit()
        return action
    finally:
        db.close()


def main() -> int:
    env_file = _ENV_FILE

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--username",
        default=os.getenv("STP_ADMIN_USER", "admin"),
        help="Admin username (env: STP_ADMIN_USER, default: admin)",
    )
    parser.add_argument(
        "--password",
        default=os.getenv("STP_ADMIN_PASSWORD"),
        help="Admin password (env: STP_ADMIN_PASSWORD, required unless set in env)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would happen without writing to the database",
    )
    parser.add_argument(
        "--force-dev-only",
        action="store_true",
        help="Allow running even when ENV=production (NOT for real production use)",
    )
    args = parser.parse_args()

    _refuse_in_production(args.force_dev_only)

    if not args.password:
        print(
            "Missing password: set STP_ADMIN_PASSWORD in .env or pass --password. "
            f"(checked {env_file})",
            file=sys.stderr,
        )
        return 1

    action = reset_dev_admin(
        username=args.username,
        password=args.password,
        dry_run=args.dry_run,
    )
    verb = "Would" if args.dry_run else "Did"
    print(f"{verb} {action} admin user username={args.username!r} role=admin is_active=Y")
    if args.dry_run:
        print("(dry-run — no database changes committed)")
    else:
        print(
            "You can verify with:\n"
            f"  python backend/scripts/seed_and_smoke.py --no-hot-update --no-wait "
            f"--username {args.username!r}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
