"""rechain 后重放 dd44 passthrough entry-script sha 回填（#1717）

Revision ID: f6a5b4c3d2e1
Revises: z7a6b5c4d3e2
Create Date: 2026-09-13

``8c4d5f47`` 为解双 head 把 seed 迁移 p9q8r7s6t5u4 的 ``down_revision`` 从
cc33dd44ee55 改为 dd44ee55ff66（链变为 cc33→dd44→p9q8→q3r4→x9y8）。对在
rechain 前把 p9q8 当 head 应用过的库（2026-09-12 09:55Z–12:49Z 窗口），
``alembic_version`` 已停在 p9q8——rechain 后 dd44 成为其**祖先**，
``upgrade head`` 只会补 q3r4/x9y8，不会再执行 dd44 的定向回填：e7f8 误写入
的 _lib sha 永久留存，入闸 verify / script_catalog 持续 conflict。

本迁移以 ``(name, version)`` 为键、``WHERE content_sha256 = :old`` 重放同一
回填：新链库的行已正确（不命中 :old，0 行更新）；受损库自愈。迁移自包含，
回填表与 dd44ee55ff66（复制日期 2026-09-13）逐字一致。
"""
from __future__ import annotations

from datetime import datetime, timezone

from alembic import op
from sqlalchemy import text

revision = "f6a5b4c3d2e1"
down_revision = "z7a6b5c4d3e2"
branch_labels = None
depends_on = None

# (script_name, version, _lib.py sha 误写入值, 入口脚本 sha) —— 与 dd44ee55ff66 一致
_SHA_BACKFILL = [
    (
        "gpu_setup", "1.0.2",
        "961f2f3929b26aae4213c879992c609bb71ae4ce88134125a74c88dce9043990",
        "a654a624a197dcbdfa626dbfd478114273a19253da779ff03fbae13f540748b1",
    ),
    (
        "powercycle_setup", "1.0.1",
        "38cb525fd5405b0c0f08c765a8d3a3bdbcea7aadf21c0733f59fedbfd9a960e3",
        "29136c9ce24f9dfcc90ad705988f9a5aa153de5a59d96e697ea278e59b9e3d7e",
    ),
    (
        "sleep_setup", "1.0.1",
        "c54ed4b371612e37af3954df07a5588a19adebc5edce74f6205b0f1557f2163e",
        "970a02133edcf75528adb74cdcf413d89f8d3a4f6f384ab3380d1c872db6db79",
    ),
]


def upgrade() -> None:
    conn = op.get_bind()
    now = datetime.now(timezone.utc)
    for name, ver, old_sha, new_sha in _SHA_BACKFILL:
        conn.execute(
            text(
                "UPDATE script SET content_sha256 = :new, updated_at = :now "
                "WHERE name = :name AND version = :ver AND content_sha256 = :old"
            ),
            {"name": name, "ver": ver, "old": old_sha, "new": new_sha, "now": now},
        )


def downgrade() -> None:
    """不还原：与 dd44ee55ff66 同理——回填是数据修正，降级写回错误值会覆盖
    运维 force_rebaseline 的合法值。"""
    pass
