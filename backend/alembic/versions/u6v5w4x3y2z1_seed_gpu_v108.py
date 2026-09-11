"""seed gpu_setup v1.0.8 — Antutu 首启弹窗清理（#774 run 356/357 根因）

Revision ID: u6v5w4x3y2z1
Revises: t7u6v5w4x3y2
Create Date: 2026-09-11

Data migration (run 356/357 根因修复):

1. Ensure gpu_setup v1.0.8 exists, deactivate v1.0.7/v1.0.6.

Behavioral delta:
- run 356/357 实证：Antutu 3D 启动弹「Security Warning: Please enable
  development settings first!」阻塞在 TransferActivity，测试 UiAutomator
  未能点击 → 「antutu app start」检测超时 81s → tearDown AssertionError。
  手动点掉弹窗后 instrument OK (1 test)（根因确证）。
- v1.0.8：prepare_device 后 dismiss_antutu_dialogs()——预热启动 Antutu
  → uiautomator dump 按文本（OK/确定/知道了/允许）找坐标 → 点击 →
  force-stop（覆盖任意包的阻塞弹窗）。含 v1.0.7 全部修复。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from alembic import op
from sqlalchemy import text

revision = "u6v5w4x3y2z1"
down_revision = "t7u6v5w4x3y2"
branch_labels = None
depends_on = None

VERSIONS = [
    {
        "name": "gpu_setup", "ver": "1.0.8",
        "sha": "da0c5c141b9d7f5386a927a733d6cc5c3704a4b9b00209bf94d5d0b570c85f56",
        "desc": "GPU 部署 — v1.0.7 + Antutu 首启弹窗清理（#774 run 356/357 根因）",
        "deactivate": ["1.0.7", "1.0.6"],
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
            text("UPDATE script SET is_active = false, updated_at = :now WHERE name = :name AND version = :ver"),
            {"name": v["name"], "ver": v["ver"], "now": now},
        )
        for old in v["deactivate"]:
            conn.execute(
                text("UPDATE script SET is_active = true, updated_at = :now WHERE name = :name AND version = :ver"),
                {"name": v["name"], "ver": old, "now": now},
            )
