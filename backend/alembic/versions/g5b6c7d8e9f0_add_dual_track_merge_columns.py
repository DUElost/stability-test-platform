"""add dual-track merge columns and tables

Revision ID: g5b6c7d8e9f0
Revises: f4a5b6c7d8e9
Create Date: 2026-03-26

Adds columns and tables introduced during the ADR-0008 dual-track ORM merge:
- job_instance: report_json, jira_draft_json, post_processed_at
- job_artifact table
- tool.category column
All operations are idempotent (safe to re-run).
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "g5b6c7d8e9f0"
down_revision = "f4a5b6c7d8e9"
branch_labels = None
depends_on = None


def _col_exists(conn, table: str, column: str) -> bool:
    result = conn.execute(
        sa.text(
            "SELECT EXISTS ("
            "  SELECT 1 FROM information_schema.columns "
            "  WHERE table_schema = 'public' AND table_name = :tbl AND column_name = :col"
            ")"
        ),
        {"tbl": table, "col": column},
    )
    return result.scalar()


def _table_exists(conn, table_name: str) -> bool:
    result = conn.execute(
        sa.text(
            "SELECT EXISTS ("
            "  SELECT 1 FROM information_schema.tables "
            "  WHERE table_schema = 'public' AND table_name = :tbl"
            ")"
        ),
        {"tbl": table_name},
    )
    return result.scalar()


def upgrade() -> None:
    conn = op.get_bind()

    # ── job_instance: post-completion columns ─────────────────────────────
    if not _col_exists(conn, "job_instance", "report_json"):
        op.add_column("job_instance", sa.Column("report_json", postgresql.JSONB(), nullable=True))

    if not _col_exists(conn, "job_instance", "jira_draft_json"):
        op.add_column("job_instance", sa.Column("jira_draft_json", postgresql.JSONB(), nullable=True))

    if not _col_exists(conn, "job_instance", "post_processed_at"):
        op.add_column("job_instance", sa.Column("post_processed_at", sa.DateTime(timezone=True), nullable=True))

    # ── tool.category ─────────────────────────────────────────────────────
    if not _col_exists(conn, "tool", "category"):
        op.add_column("tool", sa.Column("category", sa.String(64), nullable=True))

    # ── job_artifact table ────────────────────────────────────────────────
    if not _table_exists(conn, "job_artifact"):
        op.create_table(
            "job_artifact",
            sa.Column("id",            sa.Integer(),       primary_key=True),
            sa.Column("job_id",        sa.Integer(),       sa.ForeignKey("job_instance.id"), nullable=False),
            sa.Column("storage_uri",   sa.String(512),     nullable=False),
            sa.Column("artifact_type", sa.String(64),      nullable=False, server_default="log"),
            sa.Column("size_bytes",    sa.BigInteger()),
            sa.Column("checksum",      sa.String(128)),
            sa.Column("created_at",    sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )
        op.create_index("idx_job_artifact_job", "job_artifact", ["job_id"])


def downgrade() -> None:
    conn = op.get_bind()

    if _table_exists(conn, "job_artifact"):
        op.drop_index("idx_job_artifact_job", table_name="job_artifact")
        op.drop_table("job_artifact")

    if _col_exists(conn, "tool", "category"):
        op.drop_column("tool", "category")

    if _col_exists(conn, "job_instance", "post_processed_at"):
        op.drop_column("job_instance", "post_processed_at")
    if _col_exists(conn, "job_instance", "jira_draft_json"):
        op.drop_column("job_instance", "jira_draft_json")
    if _col_exists(conn, "job_instance", "report_json"):
        op.drop_column("job_instance", "report_json")
