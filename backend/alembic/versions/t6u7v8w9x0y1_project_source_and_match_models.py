"""ADR-0029 P2.5 — test_project.source + match_models。

P1 回填的 HONOR-MLD 等标为 SEED，工作台只展示 USER 项目。
match_models 是人工填写的型号映射（精确值，非前缀推断）。

Revision ID: t6u7v8w9x0y1
Revises:    s6t7u8v9w0x1
Create Date: 2026-08-20
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "t6u7v8w9x0y1"
down_revision = "s6t7u8v9w0x1"
branch_labels = None
depends_on = None

_SEED_KEYS = (
    "HONOR-MLD",
    "HONOR-ELA",
    "ZTE-Z258",
    "ODM-DAM",
    "TRANSSION-X110",
    "LEGACY",
)


def upgrade() -> None:
    op.add_column(
        "test_project",
        sa.Column("source", sa.String(length=16), nullable=False, server_default="USER"),
    )
    op.add_column(
        "test_project",
        sa.Column(
            "match_models",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
        ),
    )
    op.create_check_constraint(
        "ck_test_project_source",
        "test_project",
        "source IN ('USER', 'SEED')",
    )
    keys = ", ".join(f"'{key}'" for key in _SEED_KEYS)
    op.execute(sa.text(f"UPDATE test_project SET source = 'SEED' WHERE project_key IN ({keys})"))


def downgrade() -> None:
    op.drop_constraint("ck_test_project_source", "test_project", type_="check")
    op.drop_column("test_project", "match_models")
    op.drop_column("test_project", "source")
