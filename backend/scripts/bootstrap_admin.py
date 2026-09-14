"""Controlled first-administrator bootstrap (multi-site P1/I3).

Creates the first administrator **exactly once**: if any administrator already
exists the action is skipped; a conflicting ordinary user is reported for
manual resolution; existing accounts are never reset or elevated.  The password
is read from the environment only and never appears in argv.

Exit codes: 0 ok (created / already present), 2 invalid input, 3 username
conflict, 1 runtime failure.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_BYTES = 72
MAX_USERNAME_LENGTH = 32


def _emit(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False))


def main() -> int:
    username = os.environ.get("STP_INITIAL_ADMIN_USER", "").strip()
    password = os.environ.get("STP_INITIAL_ADMIN_PASSWORD", "")
    if not username or len(username) > MAX_USERNAME_LENGTH:
        _emit({"status": "invalid", "detail": "username"})
        return 2
    if len(password) < MIN_PASSWORD_LENGTH or len(password.encode("utf-8")) > MAX_PASSWORD_BYTES:
        _emit({"status": "invalid", "detail": "password"})
        return 2

    try:
        from backend.core.audit import record_audit
        from backend.core.database import SessionLocal
        from backend.core.security import get_password_hash
        from backend.models.user import User
    except Exception:
        _emit({"status": "error", "detail": "runtime"})
        return 1

    db = SessionLocal()
    try:
        admins = db.query(User).filter(User.role == "admin").count()
        if admins > 0:
            _emit({"status": "exists", "admins": admins})
            return 0
        existing = db.query(User).filter(User.username == username).first()
        if existing is not None:
            _emit({"status": "conflict"})
            return 3
        user = User(
            username=username,
            hashed_password=get_password_hash(password),
            role="admin",
            is_active="Y",
            token_version=1,
        )
        db.add(user)
        db.flush()
        record_audit(
            db,
            action="initial_admin_created",
            resource_type="user",
            resource_id=user.id,
            username=user.username,
            user_id=user.id,
            details={"bootstrap": "site-install"},
            strict=True,
        )
        db.commit()
        _emit({"status": "created"})
        return 0
    except Exception:
        db.rollback()
        _emit({"status": "error"})
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
