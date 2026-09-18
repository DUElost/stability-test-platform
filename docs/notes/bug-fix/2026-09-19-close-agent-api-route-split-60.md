# #60 关单：Route Split 由 service 下沉替代达成

Status: implemented
Class: bug-fix

## Decision

关闭 [#60](https://github.com/DUElost/stability-test-platform/issues/60)：原「四路由
文件拆分」不做；痛点（胖 `agent_api`）已由 #1520 垂直下沉 + god-files 棘轮达成。
同步把 `docs/architecture/non-adr20-followups.md` 的 Route Split 段标为 Closed /
superseded，避免读者误以为仍有三千行待拆。

## Alternatives

- **仍按原文拆四文件再关单**：弃——零用户收益、契约面大，成本前提已消失；
- **只关单不改文档**：弃——Acceptance 要求标记该章节完成/删除。

## Verification

- `agent_api.py` 实测约 391 行；内联 `BaseModel` = 0；`services/agent_*.py` ≈ 19
- Issue #60 关单评论 + 本文档 Status 回写

## Revisit

- #84（内联 schema 外迁）实质已完成，可另议关单；
- 若将来要按 claims/runtime/ingest/control 分路由文件，另开可验收新单。
