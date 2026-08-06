"""script.capabilities — 脚本能力元数据（#171）

PROGRESS 打戳能力不再靠控制面硬编码白名单：由脚本版本目录的
``capabilities.json`` 声明，``scan_script_root`` 登记到本列，Plan 的
``stall_seconds`` 门禁改为查询本列。新版本发布只需在版本目录放元数据文件，
不再需要改控制面代码。

迁移同时回填已知已接入 PROGRESS 的存量版本（monkey_setup v2.3.1+、
flash_firmware v1.1.0），避免升级后、首次 scan 前误拦既有计划。

Revision ID: m1n2o3p4q5r6
Revises: l3m4n5o6p7q8
Create Date: 2026-08-06
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "m1n2o3p4q5r6"
down_revision = "l3m4n5o6p7q8"
branch_labels = None
depends_on = None

_PROGRESS_VERSIONS = (
    "2.3.1", "2.3.2", "2.3.3", "2.3.4",
    "v2.3.1", "v2.3.2", "v2.3.3", "v2.3.4",
)


def upgrade() -> None:
    op.add_column(
        "script",
        sa.Column(
            "capabilities",
            JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.execute(
        "UPDATE script SET capabilities = '[\"progress_stamps\"]'::jsonb "
        "WHERE (name = 'monkey_setup' AND version IN ("
        + ", ".join(f"'{v}'" for v in _PROGRESS_VERSIONS)
        + ")) OR (name = 'flash_firmware' AND version IN ('1.1.0', 'v1.1.0'))"
    )


def downgrade() -> None:
    op.drop_column("script", "capabilities")
