"""merge 并行迁移链（双 head 修复）

Revision ID: f29aef122ec3
Revises: m8n9o0p1q2r3, r6s5t4u3v2w1
Create Date: 2026-09-09 17:36:56.750866

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f29aef122ec3'
down_revision: Union[str, None] = ('m8n9o0p1q2r3', 'r6s5t4u3v2w1')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
