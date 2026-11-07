"""G4 shadow merge result, independent of coverage and selection.

Revision ID: b8g4a719c203
Revises: b8g1c4a7d2e9
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = 'b8g4a719c203'
down_revision = 'b8g1c4a7d2e9'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('scan_round', sa.Column('merge_status', sa.String(16), nullable=True))
    op.add_column('scan_round', sa.Column('merge_summary', postgresql.JSONB(), nullable=True))
    op.add_column('scan_round', sa.Column('merged_at', sa.DateTime(timezone=True), nullable=True))


def downgrade():
    op.drop_column('scan_round', 'merged_at')
    op.drop_column('scan_round', 'merge_summary')
    op.drop_column('scan_round', 'merge_status')
