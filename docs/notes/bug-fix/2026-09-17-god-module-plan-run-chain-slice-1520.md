# God-module 垂直切片：plan_run chain 下沉 service（#1520）

Status: implemented
Class: bug-fix

## Decision

从 main 切 `GET /plan-runs/{id}/chain`：抽到
`backend/services/plan_run_chain.py`：

| 符号 | 职责 |
|---|---|
| `build_plan_run_chain` | root 收集已触发节点 + next_plan_id 占位展开 |
| `chain_node_from_run` | PlanRun → ChainNodeOut |
| `MAX_CHAIN_DEPTH` | 与写侧 plans.py 对齐（#753；不反向 import routes） |

路由退化为 `_require_plan_run` + `ok(build_plan_run_chain(db, pr))`。

`plan_runs.py` **1264 → 1153**（本刀约 -111）。

## Alternatives

- **从 routes.plans import MAX_CHAIN_DEPTH**：弃——违反 #1519 services↛routes；
- **顺手迁 list/detail**：弃——共享 `_plan_run_out` 装配，另一条垂直线。

## Verification

- 服务直测：`test_plan_run_chain`；
- API：aggregation + read_api_auth（139 passed）；
- `ruff` + `check:quick`（11 gates；god-files plan_runs 1153/2419）通过。

## Revisit

- **下一刀**：list/detail 装配（`_plan_run_out` / filters）或 abort/archive/manual 簇；
- Issue #1520 保持 OPEN；`Refs #1520`。
