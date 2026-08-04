"""script.support_files_manifest — companion module drift detection (#145)

Tracks sha256 of non-entry script files in each version directory (e.g. _adb.py)
so scan/precheck can detect NFS-side edits that leave the entry hash unchanged.

Revision ID: k6l7m8n9o0p1
Revises: j5k6l7m8n9o0
Create Date: 2026-08-04
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "k6l7m8n9o0p1"
down_revision = "j5k6l7m8n9o0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "script",
        sa.Column(
            "support_files_manifest",
            JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("script", "support_files_manifest")
