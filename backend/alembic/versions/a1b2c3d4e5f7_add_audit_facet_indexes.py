"""audit_logs facets 支撑索引（#2694）

Revision ID: a1b2c3d4e5f7
Revises: e5f6a7b8c9d0
Create Date: 2026-09-18

Schema migration（增列索引，additive）：

`audit_logs` 原先只有 `ix_audit_user_ts(user_id, timestamp)` 与
`ix_audit_resource(resource_type, resource_id)`。而 #2629 的 facets 端点
（`backend/api/routes/audit.py::get_audit_filter_facets`）每次进入 `/audit` 都要做
`group_by(action)` 聚合与默认 `order_by(timestamp desc)` 列表排序：

- `action` **无索引** ⇒ 聚合走全表扫；生产实测该表 **266,882 行**、86+ 种 action；
- `timestamp` **无单列索引** ⇒ 不带 user 过滤时走不到 `ix_audit_user_ts`（前导列是
  `user_id`），退化为全表排序。

补两个索引（均为 additive，不改列、不回填）：

- `ix_audit_action_ts(action, timestamp)`：供 action 聚合/精确筛（前导列），
  并让「按 action 过滤 + 时间倒序」走索引序免排序；
- `ix_audit_ts(timestamp)`：供列表默认排序。

**不变式**：本迁移只加索引，不触碰任何行——可安全地在生产库在线执行
（`CREATE INDEX` 非 CONCURRENTLY，在 26 万行 / 74 MB 量级上是亚秒级；
若将来表显著增大，应改用 `CONCURRENTLY` 的独立迁移）。
"""
from __future__ import annotations

from alembic import op

revision = "a1b2c3d4e5f7"
down_revision = "e5f6a7b8c9d0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_audit_action_ts", "audit_logs", ["action", "timestamp"])
    op.create_index("ix_audit_ts", "audit_logs", ["timestamp"])


def downgrade() -> None:
    op.drop_index("ix_audit_ts", table_name="audit_logs")
    op.drop_index("ix_audit_action_ts", table_name="audit_logs")
