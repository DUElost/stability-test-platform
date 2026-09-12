"""seed fill_storage v1.0.2 — 回读核验假失败修复（#1554）

Revision ID: p9q8r7s6t5u4
Revises: dd44ee55ff66
Create Date: 2026-09-12

Data migration（#1554，审计批次 audit-2026-09-12）：

1. 登记 fill_storage v1.0.2（自包含、不依赖 scan_script_root 的执行时机，
   沿用 b8c9d0e1f2a3 / c0d1e2f3a4b5 的既有惯例）；
2. 停用 v1.0.1 与 v1.0.0。

Behavioral delta vs v1.0.1：

v1.0.1 新增的「回读 df 核验」叠加两处向下取整，使**正好灌到目标**几乎必然被
算成 target-1 个百分点 → 误报 ``fill insufficient``：

- ``blocks = need_kb // block_size_kb``（floor）少写至多 block_size_kb-1 KB；
- ``actual_pct = used_after * 100 // total_after``（floor）再截一次。

以 total_kb=119473921 / target=60% 实测：target_used=71684352，
dd 实写 70004*1024=71684096 KB，回读 71684096*100//119473921 = 59 →
``fill insufficient: 59% < target 60%``。要判成功需 target_pct*total 恰好整除
**且** need_kb 恰好是 block_size_kb 的整数倍，真实 /data 容量几乎不满足。

v1.0.2 改法：块数向上取整；核验改为整数**绝对量**比较（``used_after >= target_used``），
不再重算被截断的百分比。

为何两个旧版本都停用（不留回滚位）：v1.0.0 是**假成功**（dd 返回码不检查、
无回读核验），v1.0.1 是**假失败**（本条）。两者各带一种正确性缺陷，都不是可用的
回滚目标；需要回退时按 ADR-0020 走新版本表达，不要复活旧版本。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from alembic import op
from sqlalchemy import text

revision = "p9q8r7s6t5u4"
down_revision = "dd44ee55ff66"
branch_labels = None
depends_on = None

# ── fill_storage v1.0.2 metadata ────────────────────────────────────────────

PARAM_SCHEMA = {
    "target_percentage": {
        "type": "integer",
        "required": False,
        "label": "目标占用率(%)",
        "description": "把 /data 填充到多少百分比（默认 60）",
    },
    "block_size_kb": {
        "type": "integer",
        "required": False,
        "label": "块大小(KB)",
        "description": "dd bs 的 KB 数（默认 1024）",
    },
    "fill_path": {
        "type": "string",
        "required": False,
        "label": "填充文件路径",
        "description": "设备上的填充文件路径（默认 /data/local/tmp/fill.bin）",
    },
}

DEFAULT_PARAMS = {"target_percentage": 60}

VERSIONS = [
    {
        "name": "fill_storage", "ver": "1.0.2",
        "sha": "0c9c44f77481b8aa064bc6d0a7baf330723f27a79cff2e453b8c6dd58ab7277f",
        "desc": "存储填充 — v1.0.1 + 回读核验假失败修复（向上取整 + 绝对量比较，#1554）",
        "deactivate": ["1.0.1", "1.0.0"],
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
                    "pschema": json.dumps(PARAM_SCHEMA, ensure_ascii=False),
                    "dparams": json.dumps(DEFAULT_PARAMS),
                    "desc": v["desc"], "now": now,
                },
            )
        else:
            conn.execute(
                text("UPDATE script SET is_active = true, updated_at = :now WHERE name = :name AND version = :ver"),
                {"name": v["name"], "ver": v["ver"], "now": now},
            )
        for old in v["deactivate"]:
            conn.execute(
                text("UPDATE script SET is_active = false, updated_at = :now WHERE name = :name AND version = :ver"),
                {"name": v["name"], "ver": old, "now": now},
            )


def downgrade() -> None:
    conn = op.get_bind()
    now = datetime.now(timezone.utc)
    for v in VERSIONS:
        conn.execute(
            text("UPDATE script SET is_active = false, updated_at = :now WHERE name = :name AND version = :ver"),
            {"name": v["name"], "ver": v["ver"], "now": now},
        )
        for old in v["deactivate"]:
            conn.execute(
                text("UPDATE script SET is_active = true, updated_at = :now WHERE name = :name AND version = :ver"),
                {"name": v["name"], "ver": old, "now": now},
            )
