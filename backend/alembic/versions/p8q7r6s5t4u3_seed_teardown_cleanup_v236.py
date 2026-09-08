"""seed monkey_setup v2.3.6 + powercycle_finish v1.0.3 + sleep_finish v1.0.2 — #894 清理完整化

Revision ID: p8q7r6s5t4u3
Revises: o9p8q7r6s5t4
Create Date: 2026-09-08

Data migration (issue #894 teardown 清理完整化):

1. Ensure monkey_setup v2.3.6 exists (att_clean step——monkey 启动前清
   AutoTestTool 残留，防 boot 自启叠加), deactivate v2.3.5.
2. Ensure powercycle_finish v1.0.3 exists (停测后回读验证 prefs running=false),
   deactivate v1.0.2.
3. Ensure sleep_finish v1.0.2 exists (同款验证), deactivate v1.0.1.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from alembic import op
from sqlalchemy import text

revision = "p8q7r6s5t4u3"
down_revision = "o9p8q7r6s5t4"
branch_labels = None
depends_on = None

VERSIONS = [
    {
        "name": "monkey_setup", "ver": "2.3.6",
        "sha": "28d763bb8f50c7d52a11be421b8c783850dcb8faebded880267b71d48525c7c1",
        "desc": "Monkey 部署 — v2.3.5 + att_clean 步骤（#894：清 AutoTestTool 残留防叠加）",
        "deactivate": ["2.3.5"],
    },
    {
        "name": "powercycle_finish", "ver": "1.0.3",
        "sha": "aae11bcf9ac4a95b4b6c2cd54f07b993c38e4f1bb8c7f25b62699b3b34c06f10",
        "desc": "开关机收尾 — v1.0.2 + 停测后回读验证 prefs running=false（#894）",
        "deactivate": ["1.0.2"],
    },
    {
        "name": "sleep_finish", "ver": "1.0.2",
        "sha": "ff4fb1a3231ea925ccd0053571d07b57c323f34385b0bc50c357604a1bc75a26",
        "desc": "休眠唤醒收尾 — v1.0.1 + 停测后回读验证 prefs running=false（#894）",
        "deactivate": ["1.0.1"],
    },
]


def upgrade() -> None:
    conn = op.get_bind()
    now = datetime.now(timezone.utc)

    for v in VERSIONS:
        row = conn.execute(
            text("SELECT id FROM script WHERE name = :name AND version = :ver"),
            {"name": v["name"], "ver": v["ver"]},
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
                    "name": v["name"], "display": v["name"], "cat": "device",
                    "stype": "python", "ver": v["ver"],
                    "nfs": (f"/opt/stability-test-agent/agent/scripts/"
                            f"{v['name']}/v{v['ver']}/{v['name']}.py"),
                    "sha": v["sha"],
                    "pschema": json.dumps({}), "dparams": json.dumps({}),
                    "desc": v["desc"], "now": now,
                },
            )
        else:
            conn.execute(
                text(
                    "UPDATE script SET is_active = true, updated_at = :now "
                    "WHERE name = :name AND version = :ver"
                ),
                {"name": v["name"], "ver": v["ver"], "now": now},
            )
        for old in v["deactivate"]:
            conn.execute(
                text(
                    "UPDATE script SET is_active = false, updated_at = :now "
                    "WHERE name = :name AND version = :ver"
                ),
                {"name": v["name"], "ver": old, "now": now},
            )


def downgrade() -> None:
    conn = op.get_bind()
    now = datetime.now(timezone.utc)

    for v in VERSIONS:
        conn.execute(
            text(
                "UPDATE script SET is_active = false, updated_at = :now "
                "WHERE name = :name AND version = :ver"
            ),
            {"name": v["name"], "ver": v["ver"], "now": now},
        )
        for old in v["deactivate"]:
            conn.execute(
                text(
                    "UPDATE script SET is_active = true, updated_at = :now "
                    "WHERE name = :name AND version = :ver"
                ),
                {"name": v["name"], "ver": old, "now": now},
            )
