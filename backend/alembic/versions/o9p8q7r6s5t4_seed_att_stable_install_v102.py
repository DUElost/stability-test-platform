"""seed sleep_setup v1.0.2 + powercycle_setup v1.0.2 — AutoTestTool 稳定安装

Revision ID: l2m3n4o5p6q7
Revises: k1l2m3n4o5p6
Create Date: 2026-09-08

Data migration (issue #775):

1. Ensure sleep_setup v1.0.2 + powercycle_setup v1.0.2 exist in the script table.
2. Deactivate v1.0.1 for both.

Behavioral delta（2026-09-05 全量 484 台实证，#775）:
- v1.0.1 的 adb install -r（流式）在 UNISOC 设备上不稳定——31/223 台
  Performing Streamed Install 失败。
- v1.0.2：install_apk 改 push + pm install -r -g -t -d（设备侧安装，
  同 gpu_setup v1.0.4 _install_apk_stable），失败重试一次。
- 入口脚本 sha 不变（仅 _lib.py 内部安装逻辑变更）。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from alembic import op
from sqlalchemy import text

revision = "o9p8q7r6s5t4"
down_revision = "n4o5p6q7r8s9"
branch_labels = None
depends_on = None

VERSIONS = [
    {
        "name": "sleep_setup", "ver": "1.0.2",
        "sha": "970a02133edcf75528adb74cdcf413d89f8d3a4f6f384ab3380d1c872db6db79",
        "desc": "休眠唤醒部署 — v1.0.1 + AutoTestTool push+pm 稳定安装（#775）",
        "deactivate": ["1.0.1"],
    },
    {
        "name": "powercycle_setup", "ver": "1.0.2",
        "sha": "29136c9ce24f9dfcc90ad705988f9a5aa153de5a59d96e697ea278e59b9e3d7e",
        "desc": "开关机部署 — v1.0.1 + AutoTestTool push+pm 稳定安装（#775）",
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
