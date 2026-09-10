"""seed gpu_setup v1.0.7 — wait 超时捕获 + #755 安装健壮性

Revision ID: s5t4u3v2w1x0
Revises: f29aef122ec3
Create Date: 2026-09-10

Data migration (run 355 全量复验实证):

1. Ensure gpu_setup v1.0.7 exists, deactivate v1.0.6.

Behavioral delta:
- v1.0.6 settle 修复验证成功（run 355：561/592 完成 10 轮，run 353 为 487 台空跑）。
- v1.0.7 修两个 init 健壮性缺陷（run 355 实证）：
  1. reboot 后 wait-for-device 60s 超时未捕获 → init 失败（2 台）——捕获后继续循环。
  2. _install_apk_stable push 全失败时 out 未初始化（NameError，2 台，issue #755）
     + pm uninstall 双包名（Android 仅收一个）拆两次调用。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from alembic import op
from sqlalchemy import text

revision = "s5t4u3v2w1x0"
down_revision = "f29aef122ec3"
branch_labels = None
depends_on = None

VERSIONS = [
    {
        "name": "gpu_setup", "ver": "1.0.7",
        "sha": "744c61592b108b0b5ac84f29eb162099d49590808c93ede5cd1fbe91bec2796d",
        "desc": "GPU 部署 — v1.0.6 + wait 超时捕获 + #755 安装健壮性（out 初始化/双包名拆解）",
        "deactivate": ["1.0.6"],
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
