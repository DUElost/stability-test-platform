# God-module 垂直切片：plan_run summary / artifacts / result views（#1520）

Status: implemented
Class: bug-fix

## Decision

叠在 #2564（archive）之上切读侧尾部装配（避开 #2565 catalog list/detail）：

| Service | 覆盖 |
|---|---|
| `plan_run_summary.py` | `GET .../summary` |
| `plan_run_job_artifacts.py` | `GET .../jobs/{id}/artifacts` |
| `plan_run_result_views.py` | `GET .../log-events` + `.../test-case-results` |

下载仍走既有 `job_artifact_download` 薄壳。

`plan_runs.py` **1097 → 989**（本刀约 -108；相对 archive tip）。

## Alternatives

- **再抢 catalog list/detail**：弃——#2565 已 OPEN；
- **只迁 summary**：弃——同属读侧尾部，一并下沉更省冲突轮次。

## Verification

- 服务直测：`test_plan_run_summary_artifacts`；
- API：read_api_auth + aggregation 相关（47 passed）；
- `ruff` + `check:quick`（11 gates；god-files plan_runs 989/2419）通过。

## Revisit

- catalog / archive 合入后评估残余（export 已薄；`_plan_run_out` 若仍在路由则归 catalog）；
- Issue #1520 保持 OPEN；`Refs #1520`。

## Merge conflict vs main（#2565 landed）

#2565 合入后本分支 `mergeable_state=dirty`。冲突仅
`backend/api/routes/plan_runs.py` 模型 import 块：本刀仍带 list/detail 时代的
`Device`/`Plan`/`StepTrace`/`PRECHECK_*`，main 带 artifacts 仍在路由时的
`JobArtifact`/`JobInstance`。两边 ownership 下沉后路由均不再引用——删冲突块、
去掉多余 `func`/`select`，保留 `plan_run_catalog` + `plan_run_summary` /
`plan_run_job_artifacts` / `plan_run_result_views` 双切片接线。
