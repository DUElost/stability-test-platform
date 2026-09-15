"""seed monkey_teardown v1.0.2 + gpu_finish v1.0.4 — #894 teardown 清理完整化

Revision ID: j7k8l9m0n1o2
Revises: i6j7k8l9m0n1
Create Date: 2026-09-15

Data migration (issue #894 链式衔接设备清理):

1. Ensure monkey_teardown v1.0.2 exists（设备端资源完整删除 + 逐项回读验证，
   cleanup 默认开启；aimwd 看门狗纳入停测清单），deactivate v1.0.1。
   v1.0.0 仍被 plan_step 引用，保持 active（不在此停用）。
2. Ensure gpu_finish v1.0.4 exists（结果落盘后删 /sdcard/Auto 循环脚本 +
   回读验证；test_log.txt 保留——原始日志未进中心存储），deactivate v1.0.0。

种子迁移治理（#942 裁决 A）：对已存在版本行的写操作与停用前，内嵌
``_raise_if_any_version_referenced`` 做 plan_step 引用检查（守卫实现复制
自 backend/services/script_seed_governance.py，复制日期 2026-09-15；
迁移自包含，不 import 服务层）。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from alembic import op
from sqlalchemy import text

revision = "j7k8l9m0n1o2"
down_revision = "i6j7k8l9m0n1"
branch_labels = None
depends_on = None

VERSIONS = [
    {
        "name": "monkey_teardown", "ver": "1.0.2",
        "sha": "8d8fd0f0086d7eb016f0a8489dd54494a80500ab6e95a2f9a43d75e2df9b38df",
        "desc": "Monkey 收尾 — v1.0.1 + 设备端资源完整删除与回读验证（#894）",
        "deactivate": ["1.0.1"],
    },
    {
        "name": "gpu_finish", "ver": "1.0.4",
        "sha": "a03c27aceeedde5ac1602d50c55fd9920c0ef10fbe7920a296f87366b0ce9e4c",
        "desc": "GPU 收尾 — v1.0.3 + 删设备端循环脚本并回读验证（#894）",
        "deactivate": ["1.0.0"],
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
