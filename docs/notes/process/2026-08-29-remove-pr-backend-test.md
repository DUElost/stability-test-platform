# 移除 PR 信息性 job pr-backend-test

Status: implemented
Class: process

## Decision

从 `ci.yml` 删除 `pr-backend-test`（PR 上跑整套 `backend/tests/`，PG
service，~7 分钟/PR）。控制面 PG 套件仅由 `main-ci-backstop` 每日 dispatch
的全量 `backend-test` 兜底。

PR 仍保留：`lint`、`pr-typecheck`、`pr-compileall`、`pr-agent-tests`、
`pr-migrate-empty-db`（#510 空库迁移 required check）。

## Alternatives

- **保留信息性 job**（#281 P2 现状）：不挡合入，但每个 PR 多 ~7 分钟
  Actions 与一条并行 job，与「合入路径轻量」原则相悖。
- **升为 required check**：否决——超出 ~2 分钟注意力预算。
- **diff 影响子集**：否决——维护成本高，且夜间全量已覆盖。

## Verification

- PR CI 不再出现 `pr-backend-test` job。
- `main-ci-backstop` 仍 dispatch 全量 `backend-test`（含 `backend/tests/`）。
- 本地：`grep pr-backend-test .github/workflows/ci.yml` 无匹配。

## Revisit

若 `backend/tests/` 回归频繁在 backstop 才暴露且修复成本高，可重议
「PR 上跑 diff 相关子集」——须先证明夜间延迟不可接受。
