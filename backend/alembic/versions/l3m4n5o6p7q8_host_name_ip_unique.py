"""host.name / host.ip 补唯一约束（#101）。

Revision ID: l3m4n5o6p7q8
Revises: k6l7m8n9o0p1
Create Date: 2026-08-04

#101：host 重名/重 IP 均可落库。name 与 hostname 在所有写入路径恒同值
（hostname 已唯一），显式化；ip 唯一是必须的——同一物理机登记两行会让
心跳/容量/租约按 id 结算分叉（heartbeat 已按 IP 查找 host）。

迁移前必须先核查存量重复行：

    SELECT ip, count(*) FROM host WHERE ip IS NOT NULL GROUP BY ip HAVING count(*) > 1;
    SELECT name, count(*) FROM host WHERE name IS NOT NULL GROUP BY name HAVING count(*) > 1;

PostgreSQL 唯一索引允许多个 NULL，可空列直接可加。
"""

from alembic import op

revision = "l3m4n5o6p7q8"
down_revision = "k6l7m8n9o0p1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_unique_constraint("uq_host_name", "host", ["name"])
    op.create_unique_constraint("uq_host_ip", "host", ["ip"])


def downgrade() -> None:
    op.drop_constraint("uq_host_ip", "host", type_="unique")
    op.drop_constraint("uq_host_name", "host", type_="unique")
