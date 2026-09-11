"""seed gpu_setup v1.0.9 — 循环脚本每轮清弹窗（#774 run 359 实证）

Revision ID: w4x3y2z1a0b9
Revises: v5w4x3y2z1a0
Create Date: 2026-09-11

Data migration (run 359 全量实证):

1. Ensure gpu_setup v1.0.9 exists, deactivate v1.0.8.

Behavioral delta:
- v1.0.8 的 init 一次性 dismiss 只清当次弹窗——测试每轮自带 force-stop +
  restart Antutu 后弹窗重现（确认标记未持久化到测试启动路径）→ run 359
  仍有 121 台 JUnit 失败（470→121，改善 74% 但未清零）。
- v1.0.9：_gpu_stress_loop.sh 每轮 am instrument 前 dismiss_dialogs()
  （uiautomator dump → 按文本找 OK/确定/知道了/允许 → sed 提取坐标 →
  input tap）——把弹窗清理下沉到轮次粒度。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from alembic import op
from sqlalchemy import text

revision = "w4x3y2z1a0b9"
down_revision = "v5w4x3y2z1a0"
branch_labels = None
depends_on = None

VERSIONS = [
    {
        "name": "gpu_setup", "ver": "1.0.9",
        "sha": "da0c5c141b9d7f5386a927a733d6cc5c3704a4b9b00209bf94d5d0b570c85f56",
        "desc": "GPU 部署 — v1.0.8 + 循环脚本每轮清弹窗（#774 run 359 实证）",
        "deactivate": ["1.0.8"],
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
