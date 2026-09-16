# R03 动态验证：数据模型与迁移归属套件隔离回归升「已完成」

Status: implemented
Class: process

## Decision

- 按 `PROJECT_REVIEW_PLAN.md` §5.1 转态条件，对 R03（#945）做与 R01/R02 同口径的
  **隔离环境动态验证**：`unset TEST_DATABASE_URL` → testcontainers Postgres。
- **基线**：`7456277d`（验证开始时 worktree HEAD；含当日 main 快进与 R01 文档叠层）。
- **结果**：归属套件 **75 passed / 0 failed**；`check_test_containers.py` 零残留。
- 同步把 §5 / §5.1 的 R03 行从「待验证」升「已完成」（与 R01/R02 并列；其余 12 区
  仍待验证）。

### F 项 ↔ 运行断言

| ID | Issue | 套件 | 条数 |
|---|---|---|---:|
| F01 / F12 | #934 / #944 | `backend/tests/test_schema_sync_guard.py` | 6 |
| F02 | #935 | `backend/tests/migration/test_sentinel_downgrade_935.py` | 1 |
| F03 | #936 | `backend/tests/scheduler/test_retention_cleanup.py` | 21 |
| F04 | #937 | `TestUserHardDeleteGuards` + `TestHostHardDeleteGuards` + pool 409 | 7 |
| F05 | #938 | `test_plans_api.py -k step_key` | 3 |
| F06 | #939 | project `explicit_null` + suite `put_rejects_explicit_null_is_active` | 2 |
| F07 | #940 | `backend/tests/api/test_agent_log_query.py` | 5 |
| F08 | #941 | URL export + leases unique + lease_manager + abort_reaper | 22 |
| F09 | #889 | 文档项（与 R01-F09 重合） | — |
| F10 | #942 | `tests/test_script_seed_governance.py` | 6 |
| F11 | #943 | `TestSuiteListBatching` | 2 |

## Alternatives

- **整文件跑 `test_plans_api` / `test_project_routes` / `test_hosts`**：否决。会把非
  R03 归属断言算进本区；改用类 / `-k` / 单测节点精确选中。
- **跳过设计风险 F10–F12**：否决。台账把它们登记为 R03 范围；既有回归已可运行，
  应一并取得证据。
- **真库 alembic upgrade 对生产配置的 E2E（#934 原场景手工探针）**：否决为本区升态
  门槛——`test_schema_sync_guard` 已锁 ambient URL 写回与成对白名单语义；活体探针
  属运维只读诊断，另授权。

## Verification

分批命令（均 `TESTING=1 JWT_SECRET_KEY=test-secret`，且 `unset TEST_DATABASE_URL`）：

| 批 | 结果 |
|---|---|
| schema_sync_guard | 6 passed |
| sentinel_downgrade_935 | 1 passed |
| retention_cleanup | 21 passed |
| hard-delete guards（user/host/pool） | 7 passed |
| plans `-k step_key` | 3 passed |
| project `-k explicit_null` + suite null | 2 passed |
| SuiteListBatching | 2 passed |
| agent_log_query | 5 passed |
| URL export + lease 三组 | 22 passed |
| script_seed_governance | 6 passed |
| **合计** | **75 passed** |
| 容器巡检 | 零残留 |

## Revisit

- R03「已完成」= 约定范围关键结论取得隔离运行证据；不含真机 SSH、生产库迁移演练
  或 #708 触发条件类噪音面的现场复跑。
- F09 无运行断言（文档）；若设计文档再漂移，走文档门禁，不重开本台账。
- 下一区建议 R04（#961）或主链 R06（#996）。
