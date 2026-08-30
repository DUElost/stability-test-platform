"""seed aee_prepare v1.0.0 params

Revision ID: g5a6b7c8d9e0
Revises: u3v4w5x6y7z8
Create Date: 2026-08-30

Data migration (荣耀测试前 AEE/日志配置准备 · ensure 型):

1. Ensure aee_prepare v1.0.0 exists in the script table with populated
   param_schema and default_params (g1h2i3j4k5l6 precedent).

编排语义：刷机/测试 Plan 中排在 flash/oobe 之后的设备级前置步骤——设置
persist.vendor.mtk.aee.mode=3（V71 user_root 固件出厂 4 时 AEE EE 引擎
不产出 db 事件，2026-08-30 真机实证）+ 启动 debug.loggerui 日志服务
（mobilelog 采集）+ monkey 进程诊断。无 STP_STEP_PARAMS 参数。

nfs_path 按平台约定写**绝对路径**
（/opt/stability-test-agent/agent/scripts/...，与 scan 写入一致）。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from alembic import op
from sqlalchemy import text

revision = "g5a6b7c8d9e0"
down_revision = "u3v4w5x6y7z8"
branch_labels = None
depends_on = None

PARAM_SCHEMA: dict = {}
DEFAULT_PARAMS: dict = {}

# sha256 of aee_prepare.py v1.0.0
_CONTENT_SHA256 = "da95e5b00bcf239a728c88d721fb45132d304ad3af5692c80453e3ec8e7526a7"


def upgrade() -> None:
    conn = op.get_bind()
    now = datetime.now(timezone.utc)

    row = conn.execute(
        text("SELECT id FROM script WHERE name = :name AND version = :ver"),
        {"name": "aee_prepare", "ver": "1.0.0"},
    ).fetchone()

    if row is None:
        conn.execute(
            text(
                "INSERT INTO script "
                "(name, display_name, category, script_type, version, nfs_path, "
                " content_sha256, param_schema, default_params, is_active, "
                " description, created_at, updated_at) "
                "VALUES (:name, :display, :cat, :stype, :ver, :nfs, "
                " :sha, CAST(:pschema AS jsonb), CAST(:dparams AS jsonb), true, "
                " :desc, :now, :now)"
            ),
            {
                "name": "aee_prepare",
                "display": "aee_prepare",
                "cat": "device",
                "stype": "python",
                "ver": "1.0.0",
                "nfs": ("/opt/stability-test-agent/agent/scripts/"
                        "aee_prepare/v1.0.0/aee_prepare.py"),
                "sha": _CONTENT_SHA256,
                "pschema": json.dumps(PARAM_SCHEMA),
                "dparams": json.dumps(DEFAULT_PARAMS),
                "desc": (
                    "荣耀测试前 AEE/日志配置准备：aee.mode=3 + "
                    "debug.loggerui 日志服务（mobilelog 采集）"
                ),
                "now": now,
            },
        )
    else:
        conn.execute(
            text(
                "UPDATE script SET default_params = CAST(:dparams AS jsonb), "
                " param_schema = CAST(:pschema AS jsonb), "
                " is_active = true, updated_at = :now "
                "WHERE name = :name AND version = :ver"
            ),
            {
                "name": "aee_prepare",
                "ver": "1.0.0",
                "dparams": json.dumps(DEFAULT_PARAMS),
                "pschema": json.dumps(PARAM_SCHEMA),
                "now": now,
            },
        )


def downgrade() -> None:
    conn = op.get_bind()
    now = datetime.now(timezone.utc)

    conn.execute(
        text(
            "UPDATE script SET default_params = '{}'::jsonb, "
            " param_schema = '{}'::jsonb, is_active = false, updated_at = :now "
            "WHERE name = 'aee_prepare' AND version = '1.0.0'"
        ),
        {"now": now},
    )
