"""seed sleep_check v1.0.4 — 离线豁免分支 dead_streak 键归一（#1693）

Revision ID: y8z7a6b5c4d3
Revises: x9y8z7a6b5c4
Create Date: 2026-09-13

Data migration (#1693 回归修复):

1. Ensure sleep_check v1.0.4 exists, deactivate v1.0.3（停用前查 plan_step
   引用，被引用即 RuntimeError——治理模板内嵌，见下方 _raise_if_any_version_referenced）。

Behavioral delta:
- v1.0.3 的 #814 离线豁免分支（设备离线时不累计 dead_streak）对分支体
  ``pass`` 跳过写入——新 Job 首拍（v1.0.2 的 job_id 重置后 state 仅含
  job_id）遇设备离线时，其后对 ``state["dead_streak"]`` 的无条件读取抛
  KeyError，被 main() 捕获报 failure——离线反而必判失败，豁免分支自爆
  （2026-09-13 变更审计 HIGH，/tmp 对照实跑复现，v1.0.2 正常）。
- v1.0.4：离线分支同样把 dead_streak 归一入 state（保持现值、不累计），
  保证判死比较前键必然在场。其余判定（存活清零 / 在线累计 / 宽限判死 /
  完成检测）与 v1.0.3 一致。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from alembic import op
from sqlalchemy import text

revision = "y8z7a6b5c4d3"
down_revision = "x9y8z7a6b5c4"
branch_labels = None
depends_on = None

VERSIONS = [
    {
        "name": "sleep_check", "ver": "1.0.4",
        "sha": "7cd0935b48e97657ffc366292e2731ea4f4416f4235ba1a05873a6fe0f66a5be",
        "desc": "Sleep 轮询 — v1.0.3 + 离线豁免分支 dead_streak 键归一（#1693：新 Job 首拍离线不再 KeyError 判失败）",
        "deactivate": ["1.0.3"],
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
