"""DEV ONLY: initialize the Docker Compose database from current ORM metadata.

The historical Alembic chain assumes some pre-ADR tables already exist, so it
cannot bootstrap an empty development database. This script is intentionally for
non-production Compose/dev use: it creates the current schema and optionally
upserts a local admin account.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def _refuse_production() -> None:
    if os.getenv("ENV", "").strip().lower() == "production":
        raise SystemExit("Refusing to initialize dev DB when ENV=production")


def _import_models() -> None:
    import backend.models.action_template  # noqa: F401
    import backend.models.audit  # noqa: F401
    import backend.models.device_lease  # noqa: F401
    import backend.models.host  # noqa: F401
    import backend.models.jira_run  # noqa: F401
    import backend.models.job  # noqa: F401
    import backend.models.notification  # noqa: F401
    import backend.models.plan  # noqa: F401
    import backend.models.plan_migration_audit  # noqa: F401
    import backend.models.plan_run  # noqa: F401
    import backend.models.plan_run_artifact  # noqa: F401
    import backend.models.resource_pool  # noqa: F401
    import backend.models.schedule  # noqa: F401
    import backend.models.script  # noqa: F401
    import backend.models.token_blacklist  # noqa: F401
    import backend.models.user  # noqa: F401


def _upsert_admin() -> None:
    username = os.getenv("STP_ADMIN_USER", "admin").strip()
    password = os.getenv("STP_ADMIN_PASSWORD", "").strip()
    if not username or not password:
        print("dev_db_admin_skipped reason=missing STP_ADMIN_USER/STP_ADMIN_PASSWORD")
        return

    from backend.core.database import SessionLocal
    from backend.core.security import get_password_hash
    from backend.models.user import User

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.username == username).first()
        if user is None:
            user = User(
                username=username,
                hashed_password=get_password_hash(password),
                role="admin",
                is_active="Y",
            )
            db.add(user)
            action = "created"
        else:
            user.hashed_password = get_password_hash(password)
            user.role = "admin"
            user.is_active = "Y"
            action = "updated"
        db.commit()
        print(f"dev_db_admin_{action} username={username!r}")
    finally:
        db.close()


def main() -> int:
    _refuse_production()
    _import_models()

    from backend.core.database import Base, engine

    Base.metadata.create_all(bind=engine)
    print("dev_db_schema_ready")
    _upsert_admin()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
