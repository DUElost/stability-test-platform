"""add users table and phase 2-5 models

Revision ID: b0f805bf6cee
Revises: 001_add_device_monitoring
Create Date: 2026-02-10 17:17:56.042445

Populates the previously empty users migration and adds all models
introduced in Phases 2-5: device_metric_snapshots, task_templates,
deployments, tool_categories, tools, workflows, workflow_steps,
notification_channels, alert_rules, audit_logs, task_schedules,
log_artifacts, plus new columns on tasks and task_runs.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b0f805bf6cee'
down_revision: Union[str, None] = '001_add_device_monitoring'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── Users table ──
    op.create_table(
        'users',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('username', sa.String(128), nullable=False),
        sa.Column('hashed_password', sa.String(256), nullable=False),
        sa.Column('role', sa.String(32), nullable=False, server_default='user'),
        sa.Column('is_active', sa.String(1), nullable=False, server_default='Y'),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('last_login', sa.DateTime(), nullable=True),
    )
    op.create_index('ix_users_username', 'users', ['username'], unique=True)

    # ── Device metric snapshots ──
    op.create_table(
        'device_metric_snapshots',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('device_id', sa.Integer(), sa.ForeignKey('devices.id'), nullable=False),
        sa.Column('timestamp', sa.DateTime(), nullable=False),
        sa.Column('battery_level', sa.Integer(), nullable=True),
        sa.Column('temperature', sa.Integer(), nullable=True),
        sa.Column('network_latency', sa.Float(), nullable=True),
        sa.Column('cpu_usage', sa.Float(), nullable=True),
        sa.Column('mem_used', sa.BigInteger(), nullable=True),
    )
    op.create_index('ix_dms_device_ts', 'device_metric_snapshots', ['device_id', 'timestamp'])

    # ── Task templates ──
    op.create_table(
        'task_templates',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('name', sa.String(128), nullable=False),
        sa.Column('type', sa.String(32), nullable=False),
        sa.Column('description', sa.String(256), nullable=True),
        sa.Column('default_params', sa.JSON(), nullable=True),
        sa.Column('enabled', sa.Boolean(), server_default='1'),
        sa.Column('created_at', sa.DateTime(), nullable=False),
    )
    op.create_index('ix_task_templates_name', 'task_templates', ['name'], unique=True)

    # ── Tool categories ──
    op.create_table(
        'tool_categories',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('name', sa.String(64), nullable=False),
        sa.Column('description', sa.String(256), nullable=True),
        sa.Column('icon', sa.String(32), nullable=True),
        sa.Column('order', sa.Integer(), server_default='0'),
        sa.Column('enabled', sa.Boolean(), server_default='1'),
        sa.Column('created_at', sa.DateTime(), nullable=False),
    )
    op.create_index('ix_tool_categories_name', 'tool_categories', ['name'], unique=True)

    # ── Tools ──
    op.create_table(
        'tools',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('category_id', sa.Integer(), sa.ForeignKey('tool_categories.id'), nullable=False),
        sa.Column('name', sa.String(128), nullable=False),
        sa.Column('description', sa.String(256), nullable=True),
        sa.Column('script_path', sa.String(512), nullable=False),
        sa.Column('script_class', sa.String(128), nullable=True),
        sa.Column('script_type', sa.String(16), server_default='python'),
        sa.Column('default_params', sa.JSON(), nullable=True),
        sa.Column('param_schema', sa.JSON(), nullable=True),
        sa.Column('timeout', sa.Integer(), server_default='3600'),
        sa.Column('need_device', sa.Boolean(), server_default='1'),
        sa.Column('enabled', sa.Boolean(), server_default='1'),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
    )
    op.create_index('ix_tools_category_id', 'tools', ['category_id'])

    # ── Deployments ──
    op.create_table(
        'deployments',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('host_id', sa.Integer(), sa.ForeignKey('hosts.id'), nullable=False),
        sa.Column('status', sa.String(32), nullable=False, server_default='PENDING'),
        sa.Column('install_path', sa.String(256), server_default='/opt/stability-test-agent'),
        sa.Column('started_at', sa.DateTime(), nullable=False),
        sa.Column('finished_at', sa.DateTime(), nullable=True),
        sa.Column('logs', sa.Text(), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
    )
    op.create_index('ix_deployments_host_id', 'deployments', ['host_id'])

    # ── Workflows ──
    op.create_table(
        'workflows',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('name', sa.String(128), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('status', sa.String(32), nullable=False, server_default='DRAFT'),
        sa.Column('is_template', sa.Boolean(), server_default='0'),
        sa.Column('created_by', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('started_at', sa.DateTime(), nullable=True),
        sa.Column('finished_at', sa.DateTime(), nullable=True),
    )

    # ── Workflow steps ──
    op.create_table(
        'workflow_steps',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('workflow_id', sa.Integer(), sa.ForeignKey('workflows.id'), nullable=False),
        sa.Column('order', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(128), nullable=False),
        sa.Column('tool_id', sa.Integer(), sa.ForeignKey('tools.id'), nullable=True),
        sa.Column('task_type', sa.String(64), nullable=True),
        sa.Column('params', sa.JSON(), nullable=True),
        sa.Column('target_device_id', sa.Integer(), sa.ForeignKey('devices.id'), nullable=True),
        sa.Column('status', sa.String(32), nullable=False, server_default='PENDING'),
        sa.Column('task_run_id', sa.Integer(), sa.ForeignKey('task_runs.id'), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('started_at', sa.DateTime(), nullable=True),
        sa.Column('finished_at', sa.DateTime(), nullable=True),
    )
    op.create_index('ix_ws_task_run_id', 'workflow_steps', ['task_run_id'])

    # ── Notification channels ──
    op.create_table(
        'notification_channels',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('name', sa.String(128), nullable=False),
        sa.Column('type', sa.String(32), nullable=False),
        sa.Column('config', sa.JSON(), nullable=True),
        sa.Column('enabled', sa.Boolean(), server_default='1'),
        sa.Column('created_at', sa.DateTime(), nullable=False),
    )

    # ── Alert rules ──
    op.create_table(
        'alert_rules',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('name', sa.String(128), nullable=False),
        sa.Column('event_type', sa.String(32), nullable=False),
        sa.Column('channel_id', sa.Integer(), sa.ForeignKey('notification_channels.id'), nullable=False),
        sa.Column('filters', sa.JSON(), nullable=True),
        sa.Column('enabled', sa.Boolean(), server_default='1'),
        sa.Column('created_at', sa.DateTime(), nullable=False),
    )

    # ── Audit logs ──
    op.create_table(
        'audit_logs',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('username', sa.String(128), nullable=True),
        sa.Column('action', sa.String(64), nullable=False),
        sa.Column('resource_type', sa.String(64), nullable=False),
        sa.Column('resource_id', sa.Integer(), nullable=True),
        sa.Column('details', sa.JSON(), nullable=True),
        sa.Column('ip_address', sa.String(64), nullable=True),
        sa.Column('timestamp', sa.DateTime(), nullable=False),
    )
    op.create_index('ix_audit_user_ts', 'audit_logs', ['user_id', 'timestamp'])
    op.create_index('ix_audit_resource', 'audit_logs', ['resource_type', 'resource_id'])

    # ── Task schedules ──
    op.create_table(
        'task_schedules',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('name', sa.String(128), nullable=False),
        sa.Column('cron_expression', sa.String(128), nullable=False),
        sa.Column('task_template_id', sa.Integer(), sa.ForeignKey('task_templates.id'), nullable=True),
        sa.Column('tool_id', sa.Integer(), sa.ForeignKey('tools.id'), nullable=True),
        sa.Column('task_type', sa.String(32), nullable=False),
        sa.Column('params', sa.JSON(), nullable=True),
        sa.Column('target_device_id', sa.Integer(), sa.ForeignKey('devices.id'), nullable=True),
        sa.Column('enabled', sa.Boolean(), server_default='1'),
        sa.Column('last_run_at', sa.DateTime(), nullable=True),
        sa.Column('next_run_at', sa.DateTime(), nullable=True),
        sa.Column('created_by', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
    )
    op.create_index('ix_sched_enabled_next', 'task_schedules', ['enabled', 'next_run_at'])

    # ── Log artifacts ──
    op.create_table(
        'log_artifacts',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('run_id', sa.Integer(), sa.ForeignKey('task_runs.id'), nullable=False),
        sa.Column('storage_uri', sa.String(512), nullable=False),
        sa.Column('size_bytes', sa.BigInteger(), nullable=True),
        sa.Column('checksum', sa.String(128), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
    )
    op.create_index('ix_log_artifacts_run_id', 'log_artifacts', ['run_id'])

    # ── New columns on existing tables ──

    # devices.network_latency
    op.add_column('devices', sa.Column('network_latency', sa.Float(), nullable=True))

    # tasks: tool support + distributed + template
    op.add_column('tasks', sa.Column('tool_id', sa.Integer(), nullable=True))
    op.add_column('tasks', sa.Column('tool_snapshot', sa.JSON(), nullable=True))
    op.add_column('tasks', sa.Column('group_id', sa.String(32), nullable=True))
    op.add_column('tasks', sa.Column('is_distributed', sa.Boolean(), server_default='0'))
    op.add_column('tasks', sa.Column('template_id', sa.Integer(), nullable=True))
    op.create_index('ix_tasks_group_id', 'tasks', ['group_id'])

    # task_runs: progress + post-processing
    op.add_column('task_runs', sa.Column('group_id', sa.String(32), nullable=True))
    op.add_column('task_runs', sa.Column('progress', sa.Integer(), server_default='0'))
    op.add_column('task_runs', sa.Column('progress_message', sa.String(256), nullable=True))
    op.add_column('task_runs', sa.Column('last_heartbeat_at', sa.DateTime(), nullable=True))
    op.add_column('task_runs', sa.Column('error_code', sa.String(64), nullable=True))
    op.add_column('task_runs', sa.Column('report_json', sa.JSON(), nullable=True))
    op.add_column('task_runs', sa.Column('jira_draft_json', sa.JSON(), nullable=True))
    op.add_column('task_runs', sa.Column('post_processed_at', sa.DateTime(), nullable=True))
    op.create_index('ix_task_runs_group_id', 'task_runs', ['group_id'])


def downgrade() -> None:
    # ── Drop new columns ──
    op.drop_index('ix_task_runs_group_id', table_name='task_runs')
    op.drop_column('task_runs', 'post_processed_at')
    op.drop_column('task_runs', 'jira_draft_json')
    op.drop_column('task_runs', 'report_json')
    op.drop_column('task_runs', 'error_code')
    op.drop_column('task_runs', 'last_heartbeat_at')
    op.drop_column('task_runs', 'progress_message')
    op.drop_column('task_runs', 'progress')
    op.drop_column('task_runs', 'group_id')

    op.drop_index('ix_tasks_group_id', table_name='tasks')
    op.drop_column('tasks', 'template_id')
    op.drop_column('tasks', 'is_distributed')
    op.drop_column('tasks', 'group_id')
    op.drop_column('tasks', 'tool_snapshot')
    op.drop_column('tasks', 'tool_id')

    op.drop_column('devices', 'network_latency')

    # ── Drop new tables (reverse order of creation) ──
    op.drop_index('ix_log_artifacts_run_id', table_name='log_artifacts')
    op.drop_table('log_artifacts')

    op.drop_index('ix_sched_enabled_next', table_name='task_schedules')
    op.drop_table('task_schedules')

    op.drop_index('ix_audit_resource', table_name='audit_logs')
    op.drop_index('ix_audit_user_ts', table_name='audit_logs')
    op.drop_table('audit_logs')

    op.drop_table('alert_rules')
    op.drop_table('notification_channels')

    op.drop_index('ix_ws_task_run_id', table_name='workflow_steps')
    op.drop_table('workflow_steps')
    op.drop_table('workflows')

    op.drop_index('ix_deployments_host_id', table_name='deployments')
    op.drop_table('deployments')

    op.drop_index('ix_tools_category_id', table_name='tools')
    op.drop_table('tools')

    op.drop_index('ix_tool_categories_name', table_name='tool_categories')
    op.drop_table('tool_categories')

    op.drop_index('ix_task_templates_name', table_name='task_templates')
    op.drop_table('task_templates')

    op.drop_index('ix_dms_device_ts', table_name='device_metric_snapshots')
    op.drop_table('device_metric_snapshots')

    op.drop_index('ix_users_username', table_name='users')
    op.drop_table('users')
