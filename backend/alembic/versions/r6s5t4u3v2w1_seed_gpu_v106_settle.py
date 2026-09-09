"""seed gpu_setup v1.0.6（reboot settle）+ gpu_check v1.0.5（FAILURES 归因）

Revision ID: r6s5t4u3v2w1
Revises: q7r6s5t4u3v2
Create Date: 2026-09-09

Data migration (issue #774 第二轮实证 run 353):

1. Ensure gpu_setup v1.0.6 exists, deactivate v1.0.5.
2. Ensure gpu_check v1.0.5 exists, deactivate v1.0.4.

Behavioral delta:
- v1.0.5（gpu_setup）pre_reboot 清残留有效（already registered/NPE 未再现）
  但 boot_completed=1 后立即 instrument 时 Antutu 3D/Unity 首启失败
  （AssertionError: antutu app start test）——589 台并发更甚。
- gpu_setup v1.0.6：boot 后 settle（STP_GPU_REBOOT_SETTLE_SECONDS，默认 60s）。
- gpu_check v1.0.5：判定识别 FAILURES!!!（测试执行但 JUnit 失败）→ failed
  归因（区别于 no-tests/crashed——修复 #746 类误归因）。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from alembic import op
from sqlalchemy import text

revision = "r6s5t4u3v2w1"
down_revision = "q7r6s5t4u3v2"
branch_labels = None
depends_on = None

VERSIONS = [
    {
        "name": "gpu_setup", "ver": "1.0.6",
        "sha": "e82720f938603e7f4b2d14531cf8fdff5448a54fb85589150b1d4a23d700a32b",
        "desc": "GPU 部署 — v1.0.5 + reboot 后 settle 等待（#774 run 353：boot 后 Antutu 首启失败）",
        "deactivate": ["1.0.5"],
    },
    {
        "name": "gpu_check", "ver": "1.0.5",
        "sha": "90f7064e7d28e719e0b84aca2ee5b87edc89927def202a3e4ce521ab66c543df",
        "desc": "GPU 轮询 — v1.0.4 + FAILURES 归因（测试失败非空跑，#774/#746）",
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
