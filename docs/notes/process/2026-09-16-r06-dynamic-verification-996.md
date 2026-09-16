# R06 动态验证：调度/派发/执行状态闭环归属套件隔离回归升「已完成」

Status: implemented
Class: process

## Decision

- 按 `PROJECT_REVIEW_PLAN.md` §5.1 转态条件，对 R06（#996）做与 R01–R05 同口径的
  **隔离环境动态验证**（控制面 testcontainers PG）。
- **基线**：`9ccb90dd`（验证开始时 worktree HEAD；叠在 R04+R05 动态验证文档分支之上）。
- **结果**：归属套件 **55 passed / 0 failed**；容器巡检零残留。
- 同步把 §5 / §5.1 的 R06 行升「已完成」（与 R01–R05 并列；其余 9 区仍待验证）。

### F 项 ↔ 运行断言

| ID | Issue | 套件 | 条数 |
|---|---|---|---:|
| F01 | #986 | `test_plan_chain_trigger` + `test_job_terminalization` | 20 |
| F02 | #987 | `TestReaperCompetition` | 4 |
| F03 | #988 | abort pending count RETURNING 竞态一例 | 1 |
| F04 | #989 | abort reaper lock-reread 两例 | 2 |
| F05 | #990 | recovery_sync abort_requested 阻 RESUME | 2 |
| F06 | #991 | recycler RUNNING 超时 CAS / 心跳 | 4 |
| F07 | #992 | complete×extend 死锁回归 + signals + reconciler 锁序 | 8 |
| F08 | #993 | `test_plan_run_stuck_alignment` | 7 |
| F09 | #994 | `test_cron_overlap_policy` | 7 |
| F10 | #995 | 文档项（执行文档派发路径） | — |

## Alternatives

- **整目录跑 `backend/tests/scheduler/`**：否决。非本区归属断言会稀释证据；按
  issue Note 的精确节点 / `-k` 选中。
- **真机 abort/resume（F05 与 R07 对证面）**：否决为本区升态门槛——控制面
  recovery 守卫已由 dual_write 回归锁定；真机停止交接归 R07。

## Verification

| 批 | 结果 |
|---|---|
| chain + job_terminalization | 20 passed |
| TestReaperCompetition | 4 passed |
| abort pending count | 1 passed |
| #989 lock-reread 两例 | 2 passed |
| recovery abort guard | 2 passed |
| recycler CAS heartbeat | 4 passed |
| deadlock/signals + reconciler lock order | 8 passed |
| stuck alignment | 7 passed |
| cron overlap | 7 passed |
| **合计** | **55 passed** |
| 容器巡检 | 零残留 |

## Revisit

- R06「已完成」不含真机停止/恢复与生产规模并发交错；方法能力边界见总纲 §3 第 8 条。
- F10 若权威执行文档再漂移，走文档门禁，不重开本台账。
- 下一区建议 R07（#1015，Agent 执行引擎对证）。
