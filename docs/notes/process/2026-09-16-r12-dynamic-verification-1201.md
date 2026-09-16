# R12 动态验证：前端架构与交互体验归属套件隔离回归升「已完成」

Status: implemented
Class: process

## Decision

- 按 `PROJECT_REVIEW_PLAN.md` §5.1 转态条件，对 R12（#1201）做与 R01–R11 同口径的
  **隔离环境动态验证**（前端 Vitest；本区无后端/生产库依赖）。
- **基线**：`3a717123`（验证开始时 worktree HEAD；叠在 R11 文档分支 / PR #2355 tip）。
- **结果**：归属套件 **342 passed / 0 failed**（按唯一路径分批）。
- 同步把 §5 / §5.1 的 R12 行升「已完成」（与 R01–R11 并列；其余 3 区仍待验证）。
- R02（#1200）HTTP 双标签 Web Locks 为 Playwright 隔离 harness 证据（Note 已登记）；
  本区不重跑浏览器 harness，不计入 342。

### F 项 ↔ 运行断言

| ID | Issue | 套件 | 条数 |
|---|---|---|---:|
| F01/F06/F07 | #819/#1193/#1194 | plan-run + hooks/plan-run + pages/execution | 222 |
| F02/F03/F12 | #966/#967/#1198 | `pages/orchestration/` | 31 |
| F04/R01 | #1191/#1199 | api/client/timeouts 测试 | 40 |
| F05 | #1192 | `useCrossClientSync.test.ts` | 2 |
| F08 | #821 | `ErrorBoundary.test.tsx` | 5 |
| F09 | #1195 | NotificationBell（DedupReport 含于 plan-run） | 3 |
| F10 | #1196 | `NotificationsPage.test.tsx` | 36 |
| F11 | #1197 | `layouts/` | 3 |
| R02 | #1200 | Playwright 双标签 harness（Note 证据；本区不重跑） | — |

## Alternatives

- **全量 `npx vitest run`**：否决。按台账 F 项路径选中，避免稀释证据。
- **浏览器目视 / 性能验收**：否决为本区升态门槛——Vitest 已锁定契约；目视交后续。

## Verification

| 批 | 结果 |
|---|---|
| plan-run + execution（F01/F06/F07） | 222 passed |
| orchestration（F02/F03/F12） | 31 passed |
| api/timeouts（F04/R01） | 40 passed |
| useCrossClientSync（F05） | 2 passed |
| ErrorBoundary（F08） | 5 passed |
| NotificationBell（F09） | 3 passed |
| NotificationsPage（F10） | 36 passed |
| layouts（F11） | 3 passed |
| **合计** | **342 passed** |

## Revisit

- R12「已完成」不含浏览器目视与性能；方法能力边界见总纲 §3 第 8 条。
- #1200 真 internal 双标签仍 pending（Note）；机制已由隔离 harness 确认。
- 下一区建议 R13（#1230，平台 AI 助手）。
