"""seed gpu_finish v1.0.2 — JUnit FAILURES 计入（假成功修复）

Revision ID: v5w4x3y2z1a0
Revises: u6v5w4x3y2z1
Create Date: 2026-09-11

Data migration (issue #774 run 356 实证):

1. Ensure gpu_finish v1.0.2 exists, deactivate v1.0.1.

Behavioral delta:
- JUnit FAILURES 时 am instrument 退出码仍为 0 → failed_rounds=0 → job
  COMPLETED（假成功：run 356 的 523 台「完成 10 轮」实际未执行测试）。
- v1.0.2：parse 增加 junit_failed_rounds（FAILURES 文本计数，上限 rounds_done）；
  final_status 在 junit_failed_rounds>0 或 failed_rounds>0 时标 TEST_FAILED。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from alembic import op
from sqlalchemy import text

revision = "v5w4x3y2z1a0"
down_revision = "u6v5w4x3y2z1"
branch_labels = None
depends_on = None

VERSIONS = [
    {
        "name": "gpu_finish", "ver": "1.0.2",
        "sha": "55b9058d2c89c9b70cf2459b5b7419dc8127f24175bb2e62c46aa34eb3226b3c",
        "desc": "GPU 收尾 — v1.0.1 + JUnit FAILURES 计入（假成功修复，#774 实证）",
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
                text("UPDATE script SET is_active = true, updated_at = :now WHERE name = :name AND version = :ver"),
                {"name": v["name"], "ver": v["ver"], "now": now},
            )
        for old in v["deactivate"]:
            conn.execute(
                text("UPDATE script SET is_active = false, updated_at = :now WHERE name = :name AND version = :ver"),
                {"name": v["name"], "ver": old, "now": now},
            )


def downgrade() -> None:
    conn = op.get_bind()
    now = datetime.now(timezone.utc)
    for v in VERSIONS:
        conn.execute(
            text("UPDATE script SET is_active = false, updated_at = :now WHERE name = :name AND version = :ver"),
            {"name": v["name"], "ver": v["ver"], "now": now},
        )
        for old in v["deactivate"]:
            conn.execute(
                text("UPDATE script SET is_active = true, updated_at = :now WHERE name = :name AND version = :ver"),
                {"name": v["name"], "ver": old, "now": now},
            )
