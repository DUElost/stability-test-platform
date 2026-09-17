# God-module 垂直切片：plan_run archive 下沉 service（#1520）

Status: implemented
Class: bug-fix

## Decision

叠在 #2560（chain）之上切 `POST /plan-runs/{id}/archive`：抽到
`backend/services/plan_run_archive.py`（archive_now + scan_now + 审计）。

路由退化为 `ok(await archive_plan_run_logs(...))`。
API 测试 patch 点迁到 service 模块（`emit_agent_control`）。

避开在窗 `plan_run-catalog`（list/detail）冲突面。

`plan_runs.py` **1153 → 1097**（本刀约 -56；相对 chain tip）。

## Alternatives

- **先切 list/detail**：弃——与在窗 catalog Execution 抢同一装配面；
- **顺手迁 summary/artifacts**：弃——另一条读侧垂直线。

## Verification

- 服务直测：`test_plan_run_archive`；
- API：archive endpoint + aggregation archive（14 passed）；
- `ruff` + `check:quick`（11 gates；god-files plan_runs 1097/2419）通过。

## Revisit

- **下一刀**：等 catalog 出窗后切 list/detail；或 summary / log-events / artifacts；
- Issue #1520 保持 OPEN；`Refs #1520`。
