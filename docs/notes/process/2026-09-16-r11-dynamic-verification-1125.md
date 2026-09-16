# R11 动态验证：实时通信/任务队列与通知归属套件隔离回归升「已完成」

Status: implemented
Class: process

## Decision

- 按 `PROJECT_REVIEW_PLAN.md` §5.1 转态条件，对 R11（#1125）做与 R01–R10 同口径的
  **隔离环境动态验证**（`unset TEST_DATABASE_URL`；控制面 realtime/SAQ/通知走
  testcontainers；前端 Vitest 覆盖 Socket/LiveConsole）。
- **基线**：`babf27ac`（验证开始时 worktree HEAD = 当时 `origin/main`；R07–R10
  已合入 #2337）。
- **结果**：归属套件 **297 passed / 0 failed**（pytest + Vitest；按唯一文件分批）；
  容器巡检无本区残留。
- 同步把 §5 / §5.1 的 R11 行升「已完成」（与 R01–R10 并列；其余 4 区仍待验证）。

### F 项 ↔ 运行断言

| ID | Issue | 套件 | 条数 |
|---|---|---|---:|
| F01/F02/F04 | #1110/#1111/#1085 | `test_saq_scan_pipeline.py` | 33 |
| F04/F16 | #1085/#1123 | `test_saq_tasks.py` | 34 |
| F03 | #1112 | `test_dashboard_subscribe.py` + useSocketIO（部分） | 32+ |
| F05 | #1113 | sid_registry + p3_3_multi_instance | 24 |
| F06/F07/F10/F17 | #1114/#1115/#1118/#1124 | `test_run_console.py` | 22 |
| F06 API | #1114 | `test_dedup_jira_endpoints.py` | 35 |
| F08 | #1116 | `LiveConsole.test.tsx` | 6 |
| F09/F12 | #1117/#1120 | notification service/delivery/api | 90 |
| F11 | #1119 | `useSocketIO.test.ts` | 8 |
| F13 | #1121 | `test_socketio_client_transport.py` | 1 |
| F14 | #890 | `tests/test_leader_election.py` | 7 |
| F15 | #1122 | `test_thread_pool.py`（+ notif timeout 含于 F09） | 5 |

## Alternatives

- **多实例真部署故障注入**：否决为本区升态门槛——F06/F13/F14 以契约与边界提示
  / fail-closed 单测收口；真多实例交 R14。
- **浏览器 E2E 握手刷新**：否决——Vitest 覆盖 F08/F11 重连与鉴权恢复。

## Verification

| 批 | 结果 |
|---|---|
| saq_scan_pipeline | 33 passed |
| saq_tasks | 34 passed |
| dashboard_subscribe | 32 passed |
| sid_registry + multi_instance | 24 passed |
| run_console | 22 passed |
| dedup_jira | 35 passed |
| notification ×3 | 90 passed |
| socketio_client_transport | 1 passed |
| leader_election | 7 passed |
| thread_pool | 5 passed |
| useSocketIO (Vitest) | 8 passed |
| LiveConsole (Vitest) | 6 passed |
| **合计** | **297 passed** |
| 容器巡检 | 疑似残留 0 |

## Revisit

- R11「已完成」不含多实例生产故障注入与浏览器目视；方法能力边界见总纲 §3 第 8 条。
- F06/F13 多实例边界若升级为全功能路由，另开 ADR，不重开本台账。
- 下一区建议 R12（#1201，前端架构与交互体验）。
