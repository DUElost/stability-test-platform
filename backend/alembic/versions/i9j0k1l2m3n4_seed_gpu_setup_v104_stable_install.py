"""seed gpu_setup v1.0.4 — 大 APK 稳定安装（push + pm install）

Revision ID: i9j0k1l2m3n4
Revises: h8i9j0k1l2m3
Create Date: 2026-08-31

Data migration (GPU 安装稳定性):

1. Ensure gpu_setup v1.0.4 exists in the script table.
2. Deactivate gpu_setup v1.0.2.

Behavioral delta（2026-08-31 真机实证，.92）:
- Lite APK（Antutu_3D_Lite_10.2.9.apk）378MB——adb install 流式安装
  不稳定（2/3 失败，Streamed Install 未完成）。
- v1.0.4：install 改 push 到设备本地 + pm install -r -g -t -d（设备侧
  安装，不经 adb 流式通道）；失败自动重试一次。
- Lite 路由保持 v1.0.2 语义（test_id 002 动态 + Lite androidTest）。
"""
from __future__ import annotations

from datetime import datetime, timezone

from alembic import op
from sqlalchemy import text

revision = "i9j0k1l2m3n4"
down_revision = "h8i9j0k1l2m3"
branch_labels = None
depends_on = None

_SHA = "a654a624a197dcbdfa626dbfd478114273a19253da779ff03fbae13f540748b1"


def upgrade() -> None:
    conn = op.get_bind()
    now = datetime.now(timezone.utc)
    row = conn.execute(
        text("SELECT id FROM script WHERE name='gpu_setup' AND version='1.0.4'")
    ).fetchone()
    if row is None:
        conn.execute(
            text(
                "INSERT INTO script (name, display_name, category, script_type, version, "
                "nfs_path, content_sha256, param_schema, default_params, is_active, "
                "description, created_at, updated_at) "
                "VALUES ('gpu_setup','gpu_setup','device','python','1.0.4',"
                "'/opt/stability-test-agent/agent/scripts/gpu_setup/v1.0.4/gpu_setup.py',"
                ":sha, '{}'::jsonb, '{}'::jsonb, true, "
                "'GPU 部署+启动 — v1.0.2 + 大 APK 稳定安装（push+pm install）', :now, :now)"
            ),
            {"sha": _SHA, "now": now},
        )
    conn.execute(
        text("UPDATE script SET is_active=false, updated_at=:now "
             "WHERE name='gpu_setup' AND version='1.0.2'"),
        {"now": now},
    )


def downgrade() -> None:
    conn = op.get_bind()
    now = datetime.now(timezone.utc)
    conn.execute(
        text("UPDATE script SET is_active=false, updated_at=:now "
             "WHERE name='gpu_setup' AND version='1.0.4'"),
        {"now": now},
    )
    conn.execute(
        text("UPDATE script SET is_active=true, updated_at=:now "
             "WHERE name='gpu_setup' AND version='1.0.2'"),
        {"now": now},
    )
