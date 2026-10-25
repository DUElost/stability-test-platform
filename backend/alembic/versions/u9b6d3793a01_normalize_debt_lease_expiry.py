"""Normalize far-future lease expiry for timezone-safe decoding (B6-U9)."""

from alembic import op

revision = "u9b6d3793a01"
down_revision = "u8b6d3752a01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "UPDATE device_leases "
        "SET expires_at = TIMESTAMPTZ '9999-01-01 00:00:00+00' "
        "WHERE expires_at > TIMESTAMPTZ '9999-01-01 00:00:00+00'"
    )


def downgrade() -> None:
    # Restoring the old sentinel would make historical rows undecodable again.
    pass
