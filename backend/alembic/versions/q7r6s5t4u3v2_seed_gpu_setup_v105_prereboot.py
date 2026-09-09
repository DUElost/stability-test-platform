"""seed gpu_setup v1.0.5 — 启动前重启清 UiAutomation 残留（#774 全量兼容）

Revision ID: q7r6s5t4u3v2
Revises: p8q7r6s5t4u3
Create Date: 2026-09-08

Data migration (issue #774):

1. Ensure gpu_setup v1.0.5 exists, deactivate v1.0.4.

Behavioral delta（2026-09-01 全量实证 273/276 崩溃 + 2026-09-08 复测）:
- am instrument 崩溃根因 = 设备累积 UiAutomation/系统残留（already registered /
  BaseTestCase UiDevice NPE）；设备重启后恢复可跑（AYCGNX6728006263 实证）。
- v1.0.5：setup 启动前 reboot 设备 + 等待 boot_completed（最长 180s），
  清残留后测试可全量执行。pre_reboot 参数可关（默认 true）。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from alembic import op
from sqlalchemy import text

revision = "q7r6s5t4u3v2"
down_revision = "p8q7r6s5t4u3"
branch_labels = None
depends_on = None

VERSIONS = [
    {
        "name": "gpu_setup", "ver": "1.0.5",
        "sha": "d3dd84a0c70c2928e3647d68492fb0944c987932194729d62aace22efbe59d26",
        "desc": "GPU 部署 — v1.0.4 + 启动前重启清 UiAutomation 残留（#774 全量兼容）",
        "deactivate": ["1.0.4"],
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
