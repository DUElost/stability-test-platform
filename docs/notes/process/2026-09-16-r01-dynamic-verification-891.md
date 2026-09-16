# R01 动态验证：硬契约归属套件隔离回归升「已完成」

Status: implemented
Class: process

## Decision

- 按 `PROJECT_REVIEW_PLAN.md` §5.1 转态条件，对 R01（#891）做与 R02 同口径的
  **隔离环境动态验证**：`unset TEST_DATABASE_URL` → testcontainers Postgres；
  Agent / 根目录门禁与控制面分跑，禁止混用 fixture。
- **基线**：`b5ff1625`（验证开始时 worktree HEAD = 当时 `origin/main`）。
- **结果**：归属套件 **69 passed / 0 failed**；`check_test_containers.py` 零残留。
- 同步把 §5 / §5.1 的 R01 行从「待验证」升「已完成」，并修正口径说明（R01 与 R02
  并列已完成，其余 13 区仍待验证）。
- 同会话关闭 [#1737](https://github.com/DUElost/stability-test-platform/issues/1737)
  （台账审计已复核五项验收满足、关闭动作遗漏）；`test_run_console_registry.py`
  复跑 **19 passed**——关闭证据，不计入 R01 套件计数。

### F 项 ↔ 运行断言

| ID | Issue | 套件 | 条数 |
|---|---|---|---:|
| F01 / F07 | #881 / #887 | `backend/tests/realtime/test_agent_sid_registry.py` | 9 |
| F02 | #882 | `backend/tests/api/test_plans_api.py::TestPlanSpecialtyOptional` | 5 |
| F03 | #883 | `backend/tests/api/test_main_lifespan.py` | 4 |
| F04 | #884 | `tests/test_env_source_testing_gate.py` + `test_production_env_source.py` + `test_run_with_backend_env.py` | 16 |
| F05 | #885 | `backend/tests/api/test_health_saq.py` | 13 |
| F06 | #886 | `backend/tests/core/test_redis_url.py` | 2 |
| F08 | #888 | `tests/test_script_version_immutability_gate.py` | 8 |
| F09 | #889 | 文档项（表名单数例外写入设计文档） | —（无运行断言） |
| F10 | #890 | `tests/test_leader_election.py` + `backend/agent/tests/test_leader_election.py` | 12 |

## Alternatives

- **只在 #891 评论、不回写总纲**：否决。§5.1 是仓库内检索面；只写 issue 评论会重蹈
  「只写在 issue 里 = 放弃」纪律缺口。
- **把 `test_plans_api.py` 全量 70 条算进 R01**：否决。R01-F02 的归属断言是
  `TestPlanSpecialtyOptional`；全量会把 Plan API 其它契约算进本区动态验证。
- **真多实例 / 真 Redis 故障注入一并做**：否决。F01/F07/F10 的关键结论已由现有
  单元与门禁覆盖；真多副本 rollout 属 Epic #720 载体，不是本区升「已完成」的门槛。

## Verification

| 批 | 命令 | 结果 |
|---|---|---|
| 控制面 | `TESTING=1 JWT_SECRET_KEY=test-secret python -m pytest -q` + sid registry / lifespan / health_saq / redis_url / `TestPlanSpecialtyOptional` | **33 passed** |
| 根门禁 | `python -m pytest -q` + env_source_testing_gate / script_version_immutability_gate / leader_election | **17 passed** |
| Agent | `python -m pytest -q backend/agent/tests/test_leader_election.py` | **5 passed** |
| F04 补充 | `test_production_env_source.py` + `test_run_with_backend_env.py` | **14 passed** |
| 容器巡检 | `python tools/dev/check_test_containers.py` | 零残留 |
| #1737 | `python -m pytest backend/tests/services/test_run_console_registry.py -q` | **19 passed**（关单证据） |

## Revisit

- R01「已完成」= 约定范围关键结论取得隔离运行证据；不覆盖真多副本 e2e、生产流量
  或 Epic #720 多实例 rollout。
- F09 无运行断言属文档交付性质；若设计文档后续漂移，用文档门禁 / 人工 diff 追踪，
  不重开本台账。
- 下一区动态验证建议继续按编号或主链风险选区（R03 / R06 等），并先 `declare`。
