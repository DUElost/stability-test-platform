# #1890 forward 删除 action_template 表

Status: implemented
Class: bug-fix

## Decision

`action_template` 仅在 `f4a5b6c7d8e9.upgrade()` 创建、在其 `downgrade()` 删除；
#734/#1526/#1754 曾把 downgrade 里的 `drop_table` 误判为「表级删除已完成」。
本单落地 **A｜数据面**：

- 新 revision `h4i5j6k7l8m9`（`down_revision=f3a4b5c6d7e8`）在 **`upgrade()`**
  幂等 `drop_index` + `drop_table("action_template")`；
- 从 `schema_sync_baseline.json` 移除
  `remove_table|action_template` /
  `remove_index|action_template|ix_action_template_active`（表删后白名单不得
  继续兜住幽灵差异）；
- `docs/notes/README.md` 补复核纪律：验证「迁移已删除 X」须看 `upgrade()`。

未做生产只读行数核对（owner 窗口）：迁移幂等，空表/有表均可 drop；无 FK
引用（模型与路由已拆除）。

## Alternatives

- 只改正文档/账目、不 drop：幽灵表仍在生产 schema。
- 同 PR 落地 #734 要求的 2 项 CI 门禁（路径 B）：范围更大，本单 Revisit。
- 改写历史 revision `f4a5`：违反已发布迁移不可改。

## Verification

- `python -m pytest backend/tests/test_schema_sync_guard.py -q`
- 空库 / testcontainers：`alembic upgrade head` 后
  `action_template` 不在 `inspect.get_table_names()`（由 pr-migrate-empty-db
  覆盖）
- `python scripts/run_gates.py check:quick`

## Revisit

- 路径 B：`check_deprecated_endpoints_usage` + 孤立 ORM 挂载门禁；
- 部署窗口：生产 `\d action_template` 确认后 `alembic upgrade`。
