"""seed gpu_check v1.0.3 — 空跑显式失败判定

Revision ID: h8i9j0k1l2m3
Revises: g7h8i9j0k1l2
Create Date: 2026-08-31

Data migration (GPU 轮询空跑判定):

1. Ensure gpu_check v1.0.3 exists in the script table.
2. Deactivate gpu_check v1.0.2.

Behavioral delta（2026-08-31 真机实证，.92）:
- am instrument 方法名不匹配时返回 OK (0 tests) 假成功、手机静置
  （空跑）——v1.0.2 的 _run_finished 只看 GPU_RUN_END（假完成）。
- v1.0.3：_run_finished 区分 OK (N tests) N>0（真实完成）vs
  OK (0 tests)（空跑显式失败——gpu_check 返回失败）。

注：gpu_setup v1.0.3（曾错误统一 Full）已废弃——Lite 路由真实有效
（Lite androidTest 含 _002 方法；NFS 资源已修正 Lite 版）。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from alembic import op
from sqlalchemy import text

revision = "h8i9j0k1l2m3"
down_revision = "g7h8i9j0k1l2"
branch_labels = None
depends_on = None

VERSIONS = [
    {
        "name": "gpu_check", "ver": "1.0.3",
        "sha": "0c0a50daf45e2d6bbe3971a056ba4434139e46efb951d4f290479b0096f8dd30",
        "desc": "GPU 轮询 — v1.0.2 + OK (N tests) 真实完成判定（0 tests 空跑显式失败）",
        "deactivate": ["1.0.2"],
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
