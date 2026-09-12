"""seed powercycle_finish v1.0.4 — #830 stop→pull 撞重启窗口重拉

Revision ID: x9y8z7a6b5c4
Revises: q3r4s5t6u7v8
Create Date: 2026-09-13

Data migration (issue #830 teardown 竞态):

Ensure powercycle_finish v1.0.4 exists (stop→pull 之间撞设备重启窗口时，
等设备重新就绪后重拉，最多 collect_attempts 次；离线诊断与「文件缺失」
分离), deactivate v1.0.3.

种子迁移治理（#942 裁决 A）：对已存在版本行的写操作与停用前，内嵌
``_raise_if_any_version_referenced`` 做 plan_step 引用检查（守卫实现复制
自 backend/services/script_seed_governance.py，复制日期 2026-09-13；
迁移自包含，不 import 服务层）。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from alembic import op
from sqlalchemy import text

revision = "x9y8z7a6b5c4"
down_revision = "q3r4s5t6u7v8"
branch_labels = None
depends_on = None

VERSIONS = [
    {
        "name": "powercycle_finish", "ver": "1.0.4",
        "sha": "c0eebf857a88a174f4d668e54b048aeb50c78beb1a3e76f6b8a5204b4ab05b4f",
        "desc": "开关机收尾 — v1.0.3 + stop→pull 撞重启窗口重拉（#830）",
        "deactivate": ["1.0.3"],
    },
]


def _count_plan_step_references(
    conn: Any, *, script_name: str, script_version: str
) -> int:
    """plan_step 中引用该 (script_name, script_version) 的步骤数。"""
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
    """批量形态（deactivate 列表场景）：任一版本被引用即失败并列出全部。"""
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
            # 写已存在行（ensure active）→ 先过引用检查（#942）
            _raise_if_any_version_referenced(
                conn, script_name=v["name"], versions=[v["ver"]],
            )
            conn.execute(
                text(
                    "UPDATE script SET is_active = true, updated_at = :now "
                    "WHERE name = :name AND version = :ver"
                ),
                {"name": v["name"], "ver": v["ver"], "now": now},
            )
        # 停用旧版本 → 先过引用检查（#942）
        _raise_if_any_version_referenced(
            conn, script_name=v["name"], versions=list(v["deactivate"]),
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
        _raise_if_any_version_referenced(
            conn, script_name=v["name"], versions=[v["ver"]],
        )
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
