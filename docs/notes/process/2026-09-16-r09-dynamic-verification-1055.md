# R09 动态验证：设备日志采集与异常事件归属套件隔离回归升「已完成」

Status: implemented
Class: process

## Decision

- 按 `PROJECT_REVIEW_PLAN.md` §5.1 转态条件，对 R09（#1055）做与 R01–R08 同口径的
  **隔离环境动态验证**（`unset TEST_DATABASE_URL`；控制面 DLE/信号 API 走
  testcontainers，Agent 采集链单测不触生产库）。
- **基线**：`2f4421cf`（验证开始时 worktree HEAD；叠在 R07–R08 文档分支 /
  PR #2337 tip 之上）。
- **结果**：归属套件 **315 passed / 0 failed**（已扣除 `idempotent` 与全文件
  `test_agent_device_log_events` 的 1 条重复）；容器巡检见 Verification。
- 同步把 §5 / §5.1 的 R09 行升「已完成」（与 R01–R08 并列；其余 6 区仍待验证）。
- 台账内跨区交接项 R10-F01（#1054）本轮一并跑观测套件；正式升态仍归 R10。

### F 项 ↔ 运行断言

| ID | Issue | 套件 | 条数 |
|---|---|---|---:|
| F01/R01 | #1042/#1051 | `test_dle_register_outbox_1042.py` + DLE create 幂等 | 6 |
| F02 | #1043 | unisoc_reconciler + job_session + emitter + platform_collector | 84 |
| F03 | #1044 | `test_aee_processor.py` + `test_aee_reconciler.py` | 70 |
| F04/F07 | #806 | `test_watcher_contracts.py`（自停/rollback source；与 F02/F03 分批） | 3 |
| F05 | #1048 | outbox dead_letter + watcher/log_signal/dual_write | 110 |
| F06/R02 | #795/#1052 | `test_agent_device_log_events.py`（含迟到 plan_run 保留） | 12 |
| F08 | #1049 | `test_device_watcher.py` | 17 |
| F09 | #1050 | 文档项（规格同步） | — |
| R03 | #1053 | `test_puller.py` NfsQuota | 3 |
| R10-F01 | #1054 | `test_log_observation.py`（交接预跑） | 11 |

## Alternatives

- **整目录 `backend/agent/tests/`**：否决。按台账 F 项与 Agent Note 精确套件选中。
- **真机 inotifyd 掉线 / AEE 自关闭注入**：否决为本区升态门槛——单测已锁定契约。

## Verification

| 批 | 结果 |
|---|---|
| F01 register outbox | 5 passed |
| F01 idempotent（计入 F01/R01；与全文件去重） | 1 → 并入全文件 |
| F02 UNISOC 链 | 84 passed |
| F03 pull/hash pending | 70 passed |
| F04 watcher contracts | 3 passed |
| F05 dead_letter + 控制面信号 | 110 passed |
| F06/R02 DLE API 全文件 | 12 passed |
| F08 inotifyd 监督 | 17 passed |
| R03 nfs_quota | 3 passed |
| #1054 observation | 11 passed |
| **合计（去重后）** | **315 passed** |

## Revisit

- R09「已完成」不含真机采集故障注入与生产规模；方法能力边界见总纲 §3 第 8 条。
- #1054 消费侧正式升态随 R10；本区只登记交接预跑证据。
- 下一区建议 R10（#1086，扫描/上传/存储与结果后处理）。
