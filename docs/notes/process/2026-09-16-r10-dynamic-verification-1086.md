# R10 动态验证：扫描/上传/存储与结果后处理归属套件隔离回归升「已完成」

Status: implemented
Class: process

## Decision

- 按 `PROJECT_REVIEW_PLAN.md` §5.1 转态条件，对 R10（#1086）做与 R01–R09 同口径的
  **隔离环境动态验证**（`unset TEST_DATABASE_URL`；控制面 scan/merge/JIRA 走
  testcontainers，Agent upload/scan 单测不触生产库/中心存储）。
- **基线**：`703773b7`（验证开始时 worktree HEAD；叠在 R07–R09 文档分支 /
  PR #2337 tip 之上）。
- **结果**：归属套件 **295 passed / 0 failed**（按唯一文件分批，避免跨 F 项
  重复计数）；容器巡检零残留。
- 同步把 §5 / §5.1 的 R10 行升「已完成」（与 R01–R09 并列；其余 5 区仍待验证）。
- R09 交接 #1054 本区再跑 `test_log_observation`（与 F06 同文件，计入一次）。

### F 项 ↔ 运行断言

| ID | Issue | 套件 | 条数 |
|---|---|---|---:|
| F01 | #1070 | `test_dedup_extract.py` | 25 |
| F02 | #1071 | scan_scope + dedup_scan_merge + scan_runner + saq_scan_pipeline（分文件） | — |
| F03/F05/F09 | #1072/#1074/#1077 | `test_dedup_scan_merge.py`（含 F02 屏障） | 54 |
| F02 补 | #1071 | `test_plan_run_scan_scope.py` + `test_scan_runner.py` + `test_saq_scan_pipeline.py` | 74 |
| F04/F08/F15 | #1073/#800/#1083 | `test_event_uploader.py` | 24 |
| F06+#1054 | #1075/#1054 | `test_log_observation.py` | 11 |
| F07 | #1076 | ingest + post_completion（部分） | 11 |
| F10 | #1078 | 含于 `test_scan_runner.py` | — |
| F11/F17 | #1079/#1085 | `test_saq_tasks.py` | 34 |
| F12 | #1080 | `test_plan_run_export.py` | 1 |
| F13 | #1081 | 文档项 | — |
| F14 | #1082 | 含于 `test_post_completion.py` | — |
| F09 API | #1077 | `test_dedup_scan_endpoints.py` | 26 |
| F16 | #1084 | `test_dedup_jira_endpoints.py` | 35 |

## Alternatives

- **整目录 `backend/tests/services/` + 全 Agent scan**：否决。按台账 F 项唯一文件
  选中，避免重复计数稀释证据。
- **隔离真中心存储 / 真机 prune**：否决为本区升态门槛——单测已锁定契约；F15
  完整性证据由 uploader 回归覆盖，非生产注入。

## Verification

| 批 | 结果 |
|---|---|
| F01 extract | 25 passed |
| F02 scan_scope | 10 passed |
| dedup_scan_merge（F02/03/05/09） | 54 passed |
| scan_runner（F02/F10） | 31 passed |
| saq_scan_pipeline（F02/F17） | 33 passed |
| event_uploader（F04/F08/F15） | 24 passed |
| log_observation（F06/#1054） | 11 passed |
| ingest + post_completion（F07/F14） | 11 passed |
| dedup_scan_endpoints（F09） | 26 passed |
| saq_tasks（F11/F17） | 34 passed |
| plan_run_export（F12） | 1 passed |
| dedup_jira（F16） | 35 passed |
| **合计** | **295 passed** |
| 容器巡检 | 零残留 |

## Revisit

- R10「已完成」不含真机中心存储故障注入与生产规模 prune；方法能力边界见总纲
  §3 第 8 条。
- F13 文档项若再漂移，走文档门禁，不重开本台账。
- 下一区建议 R11（#1125，实时通信与异步任务基础设施）。
