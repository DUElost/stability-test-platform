# R15 动态验证：测试/CI 与工程治理归属套件隔离回归升「已完成」

Status: implemented
Class: process

## Decision

- 按 `PROJECT_REVIEW_PLAN.md` §5.1 转态条件，对 R15（#1302）做与 R01–R14 同口径的
  **隔离环境动态验证**（`unset TEST_DATABASE_URL`；CI/devx/测试护栏以根目录与
  Agent 单测 + 治理脚本自证为主）。
- **基线**：`a789c99d`（验证开始时 worktree HEAD = 当时 `origin/main`；R14 已合入
  #2387）。
- **结果**：归属套件 **56 passed / 0 failed**；`check_invariant_diff.py --self-test`
  与 `check_governance_surface.py --check` 全绿；容器巡检零残留。
- 同步把 §5 / §5.1 的 R15 行升「已完成」（**R01–R15 全部完成**）。
- 验证当日 `check:quick` 曾因 main 上短暂 F811 红；**同日稍后复跑已全绿**
  （10 gates），与本区无关的瞬时红灯不计入升态阻断。

### F 项 ↔ 运行断言

| ID | Issue | 套件 | 条数 |
|---|---|---|---:|
| F01 | #1293 | `tests/test_approve_pending_workflow_runs.py` | 3 |
| F02/F06 | #1294/#1298 | `tests/test_main_ci_backstop_guards.py` | 5 |
| F03 | #1295 | `backend/agent/tests/test_env_isolation.py` | 2 |
| F04 | #1296 | `check_invariant_diff.py --self-test` | OK |
| F05 | #1297 | `tests/test_requirements_dev_lock.py` | 12 |
| F07 | #1299 | `test_ci_and_test_harness_files.py` + gov-surface | 4 |
| R01 | #1300 | `backend/tests/core/test_test_db_guard.py` | 10 |
| R02 | #1246 | `tests/test_automerge_queue_alerts.py` | 20 |

## Alternatives

- **整目录 `backend/agent/tests/`（千级）**：否决。F03 以 env 隔离护栏精确覆盖；
  全量 Agent 套件非本区升态必需。
- **顺手改无关业务代码**：否决。本 Requirement 仅文档回写；当日 F811 随后自行消失
  / 被他单收口，复跑 `check:quick` 已绿。

## Verification

| 批 | 结果 |
|---|---|
| approve_pending_workflow_runs | 3 passed |
| main_ci_backstop_guards | 5 passed |
| env_isolation | 2 passed |
| requirements_dev_lock | 12 passed |
| test_db_guard | 10 passed |
| automerge_queue_alerts | 20 passed |
| ci_and_test_harness_files | 4 passed |
| invariant-diff self-test | OK |
| gov-surface --check | OK |
| **合计（pytest）** | **56 passed** |
| check:quick | OK（10 gates；验证当日稍后复跑） |
| 容器巡检 | 零残留 |

## Revisit

- R15「已完成」完成首轮 15 区动态验证闭环；不含生产 auto-merge 真队列演练。
- 09-15 台账审计仍开放的跟进项（非本区）：#1520 God-module 分期债、#1035
  「批次」定义裁决、ADR-0037 联审 S2/S3/O4 排期。
