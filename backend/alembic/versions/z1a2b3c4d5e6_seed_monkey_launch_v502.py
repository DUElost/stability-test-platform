"""seed monkey_launch v5.0.2 — aimwd post-check 独立窗口（#1711）

Revision ID: z1a2b3c4d5e6
Revises: z7a6b5c4d3e2
Create Date: 2026-09-13

Data migration:

Ensure monkey_launch v5.0.2 exists (aimwd 与 MonkeyTest.sh post-check 各用
独立 max_wait 窗口；v5.0.1 共用 deadline 时 sh 迟到会误判 aimwd 未运行),
deactivate v5.0.1.

种子迁移治理（#942）：停用 v5.0.1 前内嵌 plan_step 引用检查（复制自
backend/services/script_seed_governance.py，2026-09-13）。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from alembic import op
from sqlalchemy import text

revision = "z1a2b3c4d5e6"
down_revision = "y0z1a2b3c4d5"
branch_labels = None
depends_on = None

VERSIONS = [
    {
        "name": "monkey_launch",
        "ver": "5.0.2",
        "sha": "b336daef1907a573e8be0d77c3835489cae85db94c2afa68c9afb36ae43a1799",
        "desc": "Monkey 启动 — v5.0.1 + aimwd post-check 独立窗口（#1711）",
        "deactivate": ["5.0.1"],
    },
]


def _count_plan_step_references(
    conn: Any, *, script_name: str, script_version: str
) -> int:
    return int(
        conn.execute(
            text(
                "SELECT COUNT(*) FROM plan_step "
                "WHERE script_name = :name AND script_version = :ver"
            ),
            {"name": script_name, "ver": script_version},
        ).scalar_one()
    )


def _raise_if_any_version_referenced(
    conn: Any, *, script_name: str, versions: list[str]
) -> None:
    blocked: list[tuple[str, int]] = []
    for ver in versions:
        n = _count_plan_step_references(
            conn, script_name=script_name, script_version=ver
        )
        if n > 0:
            blocked.append((ver, n))
    if blocked:
        detail = ", ".join(f"{script_name} {v} ×{n}" for v, n in blocked)
        raise RuntimeError(
            f"seed migration aborted: 待停用版本仍被 plan_step 引用：{detail}。"
            "请重指 plan_step 到在用版本后重跑迁移。"
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
                    "name": v["name"],
                    "display": v["name"],
                    "cat": "device",
                    "stype": "python",
                    "ver": v["ver"],
                    "nfs": (
                        f"/opt/stability-test-agent/agent/scripts/"
                        f"{v['name']}/v{v['ver']}/{v['name']}.py"
                    ),
                    "sha": v["sha"],
                    "pschema": json.dumps({}),
                    "dparams": json.dumps({}),
                    "desc": v["desc"],
                    "now": now,
                },
            )
        _raise_if_any_version_referenced(
            conn, script_name=v["name"], versions=list(v["deactivate"]),
        )
        for old_ver in v["deactivate"]:
            conn.execute(
                text(
                    "UPDATE script SET is_active = false, updated_at = :now "
                    "WHERE name = :name AND version = :ver"
                ),
                {"name": v["name"], "ver": old_ver, "now": now},
            )


def downgrade() -> None:
    conn = op.get_bind()
    now = datetime.now(timezone.utc)
    for v in VERSIONS:
        conn.execute(
            text("DELETE FROM script WHERE name = :name AND version = :ver"),
            {"name": v["name"], "ver": v["ver"]},
        )
        for old_ver in v["deactivate"]:
            conn.execute(
                text(
                    "UPDATE script SET is_active = true, updated_at = :now "
                    "WHERE name = :name AND version = :ver"
                ),
                {"name": v["name"], "ver": old_ver, "now": now},
            )
