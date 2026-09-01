"""seed gpu_check v1.0.4 — monitor 模式兼容判定

Revision ID: k1l2m3n4o5p6
Revises: j0k1l2m3n4o5
Create Date: 2026-09-01

Data migration (GPU 全量链执行空跑误判修复):

1. Ensure gpu_check v1.0.4 exists in the script table.
2. Deactivate gpu_check v1.0.3.

Behavioral delta（2026-09-01 全量 276 台实证）:
- v1.0.3 的 0-tests 空跑判定依赖 "OK (N tests)" 文本——但循环脚本
  `am instrument -w -m`（monitor 模式）只输出 protobuf，正常完成时
  没有 OK 文本（test_result=true + testcase_name），v1.0.3 把正常完成
  误判为 OK (0 tests) 空跑 → 全量失败。
- v1.0.4：判定顺序改为 GPU_RUN_END 缺失→running；OK 文本 N>0→ok /
  N==0→no-tests；Process crashed→crashed；protobuf test_result=true→ok。
  v1.0.3 的空跑防护（OK (0 tests)）保留。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from alembic import op
from sqlalchemy import text

revision = "k1l2m3n4o5p6"
down_revision = "j0k1l2m3n4o5"
branch_labels = None
depends_on = None

VERSIONS = [
    {
        "name": "gpu_check", "ver": "1.0.4",
        "sha": "26cc5506b3bf1ae077d279cc9112b05a9e9edbfd924e49373e73e378a630faa3",
        "desc": "GPU 轮询 — v1.0.3 + monitor 模式（-m protobuf）兼容判定（test_result=true 正常完成不再误判空跑）",
        "deactivate": ["1.0.3"],
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
