"""seed gpu_setup v1.0.10 — 安装失败重试只卸当前 APK 包（#755）

Revision ID: y0z1a2b3c4d5
Revises: x9y8z7a6b5c4
Create Date: 2026-09-13

Data migration:

1. Ensure gpu_setup v1.0.10 exists, deactivate v1.0.9.

Behavioral delta:
- v1.0.7 已修 NameError + pm uninstall 双包名一次调用（Android 只收一个）。
- v1.0.7–v1.0.9 重试前仍无条件卸 FULL+LITE——同批先装成功的包会被清掉。
- v1.0.10：按 APK 文件名映射只卸当前失败包（#755 建议收口）。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from alembic import op
from sqlalchemy import text

revision = "y0z1a2b3c4d5"
down_revision = "x9y8z7a6b5c4"
branch_labels = None
depends_on = None

VERSIONS = [
    {
        "name": "gpu_setup", "ver": "1.0.10",
        "sha": "c83a46953e5f990b1d9e911a86e5a528465a5367b452e43c4b598de079382dd0",
        "desc": "GPU 部署 — v1.0.9 + 安装重试只卸当前失败 APK 对应包（#755）",
        "deactivate": ["1.0.9"],
    },
]


def _raise_if_any_version_referenced(
    conn: Any, script_name: str, versions: list[str]
) -> None:
    """内嵌自 backend/services/script_seed_governance.py（#942 裁决：迁移
    自包含、不 import 服务层）。停用仍被 plan_step 引用的版本即失败。"""
    blocked: list[str] = []
    for ver in versions:
        n = int(
            conn.execute(
                text(
                    "SELECT COUNT(*) FROM plan_step "
                    "WHERE script_name = :name AND script_version = :ver"
                ),
                {"name": script_name, "ver": ver},
            ).scalar_one()
        )
        if n > 0:
            blocked.append(f"{ver} ×{n}")
    if blocked:
        raise RuntimeError(
            "seed migration aborted: 待停用版本仍被 plan_step 引用："
            f"{script_name} {', '.join(blocked)}。"
            "请把引用的 plan_step 重指到新版本后重跑迁移。"
        )


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
        # #2055：停用既有版本前必须确认没有 plan_step 引用它（#942 裁决 A 的
        # 「禁止无引用检查的 is_active=false」）——否则被引用的版本被静默下线，
        # 相关 plan 到 precheck 才失败。与兄弟 seed 同实现（自包含内嵌）。
        if v["deactivate"]:
            _raise_if_any_version_referenced(conn, v["name"], v["deactivate"])
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
