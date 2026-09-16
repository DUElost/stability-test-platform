# R13 动态验证：平台 AI 助手归属套件隔离回归升「已完成」

Status: implemented
Class: process

## Decision

- 按 `PROJECT_REVIEW_PLAN.md` §5.1 转态条件，对 R13（#1230）做与 R01–R12 同口径的
  **隔离环境动态验证**（`unset TEST_DATABASE_URL`；助手 API 走 testcontainers；
  前端 Vitest）。
- **基线**：`a26cb688`（验证开始时 worktree HEAD = 当时 `origin/main`；R11–R12
  已合入 #2355）。
- **结果**：归属套件 **177 passed / 0 failed**（唯一文件分批；已扣除 saq `-k`
  重叠、t2b 重复计入、LogPanel 在 assistant/ 与 #823 批之间的重复）。
- 同步把 §5 / §5.1 的 R13 行升「已完成」（与 R01–R12 并列；其余 2 区仍待验证）。

### F 项 ↔ 运行断言

| ID | Issue | 套件 | 条数 |
|---|---|---|---:|
| F01–F12 主面 | #1213–#1224 等 | `test_ai_assistant_endpoints.py` | 60 |
| F01 | #1213 | `test_ai_authz.py` | 5 |
| F02 | #1214 | redaction + notification webhook 脱敏 | 11 |
| F04 | #1216 | `test_saq_tasks.py`（enqueue/dedup/required） | 9 |
| F06 | #1218 | `test_ai_tools.py` + `test_t2b_allowlist.py` | 23 |
| F05/F13–F15/R01 | #1217/#823/#1225–#1227 | assistant 页 + settings + #823 四文件 | — |
| R02 | #1228 | `test_run_console.py` | 22 |
| F13/#823 | #823 | LogPanel/PlanRunLogs/Devices/HostHotUpdate（LogPanel 已去重） | 27 |
| 前端助手 | #1217/#1226/#1229 等 | `pages/assistant/` + settings | 20 |

## Alternatives

- **整目录 `backend/tests/services`（768）**：否决。非本区断言稀释证据。
- **浏览器审批闭环目视**：否决为本区升态门槛——Vitest + API 回归已锁定契约。

## Verification

| 批 | 结果 |
|---|---|
| ai_assistant_endpoints | 60 passed |
| ai_authz | 5 passed |
| redaction + notif 脱敏 | 11 passed |
| saq enqueue/dedup（去重后） | 9 passed |
| ai_tools + t2b | 23 passed |
| run_console（#1228） | 22 passed |
| pages/assistant + settings | 20 passed |
| #823 四文件（扣 LogPanel 重复） | 27 passed |
| **合计（去重后）** | **177 passed** |
| 容器巡检 | 零残留 |

## Revisit

- R13「已完成」不含浏览器审批目视与多 worker 取消路由；方法能力边界见总纲 §3
  第 8 条。
- 下一区建议 R14（#1266，部署/运维与可观测性）。
