# R07 动态验证：Agent 执行引擎与运行可靠性归属套件隔离回归升「已完成」

Status: implemented
Class: process

## Decision

- 按 `PROJECT_REVIEW_PLAN.md` §5.1 转态条件，对 R07（#1015）做与 R01–R06 同口径的
  **隔离环境动态验证**（本区归属断言均在 `backend/agent/tests/`，不触生产库；
  `unset TEST_DATABASE_URL`）。
- **基线**：`f4bf8b61`（验证开始时 worktree HEAD；验证时 HEAD=`f4bf8b61`（当时 PR #2314 tip）；#2314 合入后本提交变基到 `origin/main`）。
- **结果**：归属套件 **103 passed / 0 failed**；容器巡检无本区残留。
- 同步把 §5 / §5.1 的 R07 行升「已完成」（与 R01–R06 并列；其余 8 区仍待验证）。

### F 项 ↔ 运行断言

| ID | Issue | 套件 | 条数 |
|---|---|---|---:|
| F01 | #799 | `test_lease_lost_device_safety.py` | 3 |
| F02 | #1003 | `test_pipeline_engine_process_group.py` | 14 |
| F03 | #1004 | recovery：UPLOAD_TERMINAL / 跳过 RESUME / pending outbox | 4 |
| F04 | #1005 | fencing `terminal_lost` + `test_terminal_durability.py` | 8 |
| F05 | #1006 | fencing release / superseded / stale / owned 占位补偿 | 4 |
| F06 | #1008 | `test_patrol_recovery.py` | 4 |
| F07 | #1009 | recovery sync/reconnect/heartbeat 重试保留 | 4 |
| F08 | #1010 | checkpoint cruise / time_budget | 2 |
| F09/#804 | #804 | stall seq 单调（与 F10 同文件选中） | — |
| F10 | #1011 | stall pending_cap / no_newline / long_line | 4（含 F09） |
| F11 | #762 | dead_letter + drainer metrics | 42 |
| F12 | #1012 | step5b SIGTERM / terminal_denial / cancel | 6 |
| F13 | #1013 | `test_main.py` register/claim | 2 |
| F14 | #1014 | coordinator reclaim / projection / delete_state | 4 |
| F15 | #730 | `test_heartbeat_parallel_probe.py` | 2 |

## Alternatives

- **整目录跑 `backend/agent/tests/`**：否决。非本区归属断言会稀释证据；按 issue
  Note 的精确节点 / 带引号 `-k` 分批选中（避免 shell 把 `or` 拆散）。
- **真机失租 / 真机巡航 SIGTERM**：否决为本区升态门槛——单元与集成回归已锁定
  F01–F15 契约；真机规模与故障注入仍属方法能力边界（总纲 §3 第 8 条）。

## Verification

| 批 | 结果 |
|---|---|
| F01 lease_lost | 3 passed |
| F02 process_group | 14 passed |
| F03+F07 recovery_executor 精确节点 | 8 passed |
| F04+F05 fencing + terminal_durability | 12 passed |
| F06 patrol_recovery | 4 passed |
| F08 checkpoint `-k` | 2 passed |
| F09+F10 stall `-k` | 4 passed |
| F11 dead_letter + drainer | 42 passed |
| F12 step5b `-k` | 6 passed |
| F13 main `-k` | 2 passed |
| F14 coordinator `-k` | 4 passed |
| F15 heartbeat_parallel | 2 passed |
| **合计** | **103 passed** |
| 容器巡检 | 本区无残留 |

## Revisit

- R07「已完成」不含真机失租/恢复与生产规模并发交错；方法能力边界见总纲 §3 第 8 条。
- F14 投影累计若需回收策略，另开测量单，不重开本台账。
- 下一区建议 R08（#1031，脚本库/版本与外部工具）。
