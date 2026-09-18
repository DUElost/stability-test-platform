# ownership TBD 补锚 + LINK_TREES docs/adr（#2546）

Status: proposed
Class: architecture

## Decision

1. **TBD 升实锚**：`project-taxonomy` / `multi-case-suite` / `platform-ai-assistant` / `agent-host-identity` / `schema-alembic-only` / `audit-log` / `site-delivery` 改为 `path :: 节标题` 实锚（命中恰 1）。新增 `frontend-feature-expansion` 仍 TBD（触发：触碰 ADR-0013 或前端 IA 大改）。不做全量 Inventory。
2. **`LINK_TREES += docs/adr`**：S2 断链检查覆盖 ADR 树；修 ADR-0044/0045 指向不存在的 `ADR-0025-run-console-and-command-execution.md`（0044→设计 RunConsole 文；0045→去掉幽灵链并注明判定真源在正文）。不改其它 ADR 决策正文。

## Alternatives

- 等「触碰才补」拖延 TBD → 用户下令本轮可落地即补。
- 全量扫 ADR 散文补归属域字段 → 假红面过大，拒。
- LINK_TREES 只加 README → 拦不住关联行幽灵文件。

## Verification

- `python3 tools/dev/check_governance_surface.py --self-test`
- `python3 tools/dev/check_governance_surface.py --check`（S1–S15；含 docs/adr 断链）
- 人工：ownership 表仅剩 `frontend-feature-expansion` 为 TBD

## Revisit

- 补 ADR-0013 实锚；Y1/Y2 关单时复核 `audit-log` / specialty。
- 若 ADR 树新增大量相对链，靠 LINK_TREES 持续拦幽灵。
