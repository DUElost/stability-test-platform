"""seed gpu_check v1.0.10 — protobuf 字段值前缀判定（#1695）

Revision ID: a3b2c1d0e9f8
Revises: z7a6b5c4d3e2
Create Date: 2026-09-13

Data migration (#1695 回归修复):

1. Ensure gpu_check v1.0.10 exists, deactivate v1.0.9（停用前查 plan_step
   引用，被引用即 RuntimeError——治理模板内嵌，见下方 _raise_if_any_version_referenced）。

Behavioral delta:
- v1.0.8（#746）起 protobuf `test_result` 按长度前缀取值后对值做**全等**
  比较（``value == b"true"``）。真机形态 ``test_result\x12\x05true\x0f``
  （2026-09-01 实证，length 第 5 字节是下一字段的 tag 字节 \x0f、非空白）
  经 ``bytes.strip()`` 仍是 ``b"true\x0f"`` → 解析恒 None → verdict
  ``no-tests`` → 正常完成被判「空跑」失败（PASS→FAIL 回归；v1.0.7 的
  64B 窗口扫描对同一样本判 ok）。
- v1.0.10：值判定改 ``startswith``——长度前缀仍在，#746 的「最后一条为
  准 + 不被后文 true 子串干扰」语义保持。其余判定与 v1.0.9 一致。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from alembic import op
from sqlalchemy import text

revision = "a3b2c1d0e9f8"
down_revision = "z7a6b5c4d3e2"
branch_labels = None
depends_on = None

VERSIONS = [
    {
        "name": "gpu_check", "ver": "1.0.10",
        "sha": "317ff0c75231d00f9efbe68dd7e296b54b95120e21f69353396ffef51e52c8da",
        "desc": "GPU 轮询 — v1.0.9 + protobuf 字段值前缀判定（#1695：真机 length 多含 tag 字节不再判空跑）",
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
