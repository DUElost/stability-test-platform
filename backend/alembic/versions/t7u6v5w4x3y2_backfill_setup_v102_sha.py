"""backfill setup v1.0.2 seed sha（#1276）

Revision ID: t7u6v5w4x3y2
Revises: s5t4u3v2w1x0
Create Date: 2026-09-10

o9p8q7r6s5t4 的内嵌 ``content_sha256`` 曾在合入后被原地修正（#1171）：该迁移的
``upgrade()`` 只在 INSERT 分支写 sha，已执行过它的库保留旧值 → ``script_catalog``
比对 DB 与磁盘 sha 持续报 conflict，直到人工 ``force_rebaseline``。

本迁移按「旧值 → 正确值」精确回填两个行：

- ``sleep_setup`` v1.0.2：970a0213…（旧）→ 41f40e54…
- ``powercycle_setup`` v1.0.2：29136c9c…（旧）→ f36b155b…

``WHERE content_sha256 = :old`` 保证只改「仍带错误旧值」的行：运维已
``force_rebaseline``、或磁盘脚本被有意改动的安装不受影响。
"""
from __future__ import annotations

from datetime import datetime, timezone

from alembic import op
from sqlalchemy import text

revision = "t7u6v5w4x3y2"
down_revision = "s5t4u3v2w1x0"
branch_labels = None
depends_on = None

# (script_name, version, 原地修正前的错误 sha, 正确的入口脚本 sha)
_SHA_BACKFILL = [
    (
        "sleep_setup", "1.0.2",
        "970a02133edcf75528adb74cdcf413d89f8d3a4f6f384ab3380d1c872db6db79",
        "41f40e5498e0ae822378d4c33443d8c2e9f7b008d7eab3b352d4905d19f9aea2",
    ),
    (
        "powercycle_setup", "1.0.2",
        "29136c9ce24f9dfcc90ad705988f9a5aa153de5a59d96e697ea278e59b9e3d7e",
        "f36b155bffe1e3faed11ef3286a49492ac8279c4cf842d92831b6c33bf195e33",
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
    """不还原：回填是数据修正，降级把已知错误值写回会覆盖运维
    ``force_rebaseline`` 的合法值（且无法区分「本次改过的行」与「本来就正确
    的行」）。schema 无变化，故降级是 no-op。"""
    pass
