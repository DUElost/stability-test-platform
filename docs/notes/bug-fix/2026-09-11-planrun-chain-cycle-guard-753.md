# PlanRun /chain 读路径环与深度护栏（#753）

Status: implemented
Class: bug-fix

## Decision

`GET /plan-runs/{id}/chain` 展开未触发 `next_plan_id` 时增加 `seen_plans`（含已存在 PlanRun 的 `plan_id`）与 `MAX_CHAIN_DEPTH`（与写路径 `plans.py` 对齐）。命中已访问节点或超深即断链截断，避免坏数据 2+ 环挂死只读 GET。

## Alternatives

- **遇环返回 500/422**：读端点对历史脏数据应降级展示而非拒绝整页；否决。
- **截断 + 复用写侧深度常量**（采纳）。

## Verification

- `python -m pytest backend/tests/api/test_plan_run_aggregation_endpoints.py::TestChainEndpoint -q`
- `python scripts/run_gates.py check:quick`

## Revisit

若产品要在 UI 明示「链因环截断」，可在 `PlanChainOut` 增 `truncated` 字段；当前行为与「断链截断」一致且无契约扩展。
