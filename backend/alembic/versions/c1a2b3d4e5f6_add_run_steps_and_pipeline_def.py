"""add run_steps table and pipeline_def column

Revision ID: c1a2b3d4e5f6
Revises: b0f805bf6cee
Create Date: 2026-02-23

Adds:
- tasks.pipeline_def JSONB nullable column for pipeline definitions
- run_steps table for tracking pipeline step execution within a TaskRun
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c1a2b3d4e5f6'
down_revision: Union[str, None] = 'b0f805bf6cee'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add pipeline_def column to tasks table
    op.add_column('tasks', sa.Column('pipeline_def', sa.JSON(), nullable=True))

    # Create run_steps table
    op.create_table(
        'run_steps',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('run_id', sa.Integer(), sa.ForeignKey('task_runs.id', ondelete='CASCADE'), nullable=False),
        sa.Column('phase', sa.String(64), nullable=False),
        sa.Column('step_order', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(128), nullable=False),
        sa.Column('action', sa.String(256), nullable=False),
        sa.Column('params', sa.JSON(), server_default='{}'),
        sa.Column('status', sa.String(16), nullable=False, server_default='PENDING'),
        sa.Column('started_at', sa.DateTime(), nullable=True),
        sa.Column('finished_at', sa.DateTime(), nullable=True),
        sa.Column('exit_code', sa.Integer(), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('log_line_count', sa.Integer(), server_default='0'),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )

    op.create_index('ix_rs_run_id', 'run_steps', ['run_id'])
    op.create_index('ix_rs_run_status', 'run_steps', ['run_id', 'status'])


def downgrade() -> None:
    op.drop_index('ix_rs_run_status', table_name='run_steps')
    op.drop_index('ix_rs_run_id', table_name='run_steps')
    op.drop_table('run_steps')
    op.drop_column('tasks', 'pipeline_def')
