"""seed gpu_setup v1.0.2 / powercycle v1.0.1 / sleep v1.0.1 resources_dir 透传

Revision ID: e7f8a9b0c1d2
Revises: f7g8h9i0j1k2
Create Date: 2026-08-31

Data migration (资源目录透传修复):

1. Ensure gpu_setup v1.0.2 / powercycle_setup v1.0.1 / sleep_setup v1.0.1
   exist in the script table with populated param_schema/default_params
   (f4a5b6c7d8e9 precedent).
2. Deactivate gpu_setup v1.0.1 / powercycle v1.0.0 / sleep v1.0.0
   (superseded by the passthrough fix).

Behavioral delta: gpu_config/powercycle_config/sleep_config 原来只保留
业务键、丢弃 resources_dir 键 → STP_STEP_PARAMS 传的 gpu_resources_dir
等失效、回落默认路径（2026-08-31 实证：GPU 专项 variant 目录报错指向
/opt/stability-test-agent/agent/resources/gpu 默认路径）。三版在 config
返回 dict 透传 resources_dir 键（cfg > env > 默认）。

资源部署（带外，NFS 共享）：
- /mnt/stp-aee/gpu/{project}/{variant}/（Antutu APK ×3）
- /mnt/stp-aee/resources/power-cycle/AutoTestTool.apk
- /mnt/stp-aee/resources/sleep/AutoTestTool.apk
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from alembic import op
from sqlalchemy import text

revision = "e7f8a9b0c1d2"
down_revision = "f7g8h9i0j1k2"
branch_labels = None
depends_on = None

VERSIONS = [
    {
        "name": "gpu_setup", "ver": "1.0.2",
        "sha": "961f2f3929b26aae4213c879992c609bb71ae4ce88134125a74c88dce9043990",
        "desc": "GPU 部署+启动 — v1.0.1 + gpu_resources_dir 透传（config 丢键修复）",
        "deactivate": ["1.0.0", "1.0.1"],
    },
    {
        "name": "powercycle_setup", "ver": "1.0.1",
        "sha": "38cb525fd5405b0c0f08c765a8d3a3bdbcea7aadf21c0733f59fedbfd9a960e3",
        "desc": "开关机循环 — v1.0.0 + powercycle_resources_dir 透传（config 丢键修复）",
        "deactivate": ["1.0.0"],
    },
    {
        "name": "sleep_setup", "ver": "1.0.1",
        "sha": "c54ed4b371612e37af3954df07a5588a19adebc5edce74f6205b0f1557f2163e",
        "desc": "休眠唤醒循环 — v1.0.0 + sleep_resources_dir 透传（config 丢键修复）",
        "deactivate": ["1.0.0"],
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
