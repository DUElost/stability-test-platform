"""ADR-0020 terminal compatibility revision.

This revision was used by local databases during the ADR-0020 schema
alignment pass.  The actual schema changes are now folded into the earlier
ADR-0020 migrations, so this file intentionally remains a no-op while keeping
already-stamped databases on a valid Alembic revision.
"""

revision = "b5c6d7e8f9a0"
down_revision = "a4b5c6d7e8f9"
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
