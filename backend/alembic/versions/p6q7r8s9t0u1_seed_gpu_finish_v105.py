"""seed gpu_finish v1.0.5 — #2146 清理验证补「探测不可用」态

Revision ID: p6q7r8s9t0u1
Revises: j7k8l9m0n1o2
Create Date: 2026-09-15

Data migration (issue #2146，承接 #894 三态之③):

Ensure gpu_finish v1.0.5 exists（清理验证改用 ``adb()`` 取 rc：rm rc≠0 /
探测 rc≠0 / 输出无 REMAINS 也无 CLEAN → 一律 raise，不再把「读不到」当
「干净」）。

**不在本迁移停用 v1.0.4**：它仍被 4 个 Plan 的 teardown 步骤引用
（P24/27/29/36），迁移内停用会被 #942 引用守卫 abort。退役顺序是先重指
PlanStep 到 v1.0.5、再由运维经 API 置 ``is_active=false``。

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

revision = "p6q7r8s9t0u1"
down_revision = "j7k8l9m0n1o2"
branch_labels = None
depends_on = None

VERSIONS = [
    {
        "name": "gpu_finish", "ver": "1.0.5",
        "sha": "5789894e4a19eee0134b43548c1ae14e5665e27dd8b693192eee47cfcbda350d",
        "desc": "GPU 收尾 — v1.0.4 + 清理验证覆盖「探测不可用」（rc 判定，#2146）",
        "deactivate": [],
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
