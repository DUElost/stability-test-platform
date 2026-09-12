"""backfill e7f8 passthrough setup entry-script sha（#751）

Revision ID: dd44ee55ff66
Revises: cc33dd44ee55
Create Date: 2026-09-12

e7f8a9b0c1d2 曾把 gpu_setup v1.0.2 / powercycle_setup v1.0.1 / sleep_setup
v1.0.1 的 ``content_sha256`` 写成同目录 ``_lib.py`` 的哈希（入口脚本语义应为
``{name}.py``）。全新空库在原地修正后 INSERT 正确；已执行过错误版本的库仍
保留旧值 → 入闸 verify / script_catalog conflict。

本迁移按「旧 _lib sha → 入口脚本 sha」精确回填；``WHERE content_sha256 = :old``
只碰仍带错误值的行（已 force_rebaseline 或磁盘有意改动的安装不受影响）。
"""
from __future__ import annotations

from datetime import datetime, timezone

from alembic import op
from sqlalchemy import text

revision = "dd44ee55ff66"
down_revision = "cc33dd44ee55"
branch_labels = None
depends_on = None

# (script_name, version, _lib.py sha 误写入值, 入口脚本 sha)
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
    """不还原：与 t7u6v5w4x3y2 同理——回填是数据修正，降级写回错误值会覆盖
    运维 force_rebaseline 的合法值。"""
    pass
