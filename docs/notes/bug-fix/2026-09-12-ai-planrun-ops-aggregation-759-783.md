# AI 助手 PlanRun ops 审计/假成功修复 + 聚合口径裁定（#759 #783）

Status: implemented
Class: bug-fix

## Decision

### #759 AI 助手 PlanRun ops 工具收尾（代码修复）

1. **retry-dispatch 审计缺 user_id**
   `run_retry_plan_run_dispatch` 增 `requester_user_id` 并通过
   `retry_plan_run_dispatch(audit_user_id=...)` 透传到
   `record_audit(user_id=...)`；orchestrator 调用点补传 `requester_user_id`。
   其余 4 个 ops 工具本已透传，retry 此前是唯一缺口。

2. **archive 主循环不可用时假成功**
   `_schedule_emit_agent_control` 返回 `bool`（是否实际入队；主循环 None/closed
   或 `run_coroutine_threadsafe` 抛错 → False）。`run_trigger_plan_run_archive`
   逐主机记录 `dispatch_failed`，**任一 ONLINE 主机未入队即抛 RuntimeError**
   （不再返回「已触发」），审计 `details.dispatch_failed` 留证。

### #783 聚合口径两处裁定（文档/代码注释，不改语义）

对照 ADR-0028 与既有行为，裁定两处为**有意口径**并就地成文（避免「待裁定」
长期悬空）：

1. **DLE 计数 = distinct 事件产物路径**（`log_observation._rows_from_device_log_events`）：
   `COUNT(DISTINCT remote_path/local_path)` 是「事件产物」计数，同路径多信号指
   同一产物、不得重复计入风险桶；`_classify_subtype` 阈值语义 = distinct
   artifacts，非事件发生次数。docstring 已写明。
2. **`aborted > 0 → FAILED`**（`plan_run_aggregation._resolve_plan_run_status`）：
   abort 属操作者可归因，代表运行未完成预期覆盖，部分 abort 不得呈现为
   SUCCESS/PARTIAL_SUCCESS；`abort_requested` 另行污染。docstring 已写明。

两处**未改代码语义**——它们是需要产品裁决的口径，擅自放宽（如 aborted 只按
failed_only/total）会掩盖未完成覆盖。以文档收口并留 Revisit。

影响面：`backend/services/ai_assistant/{plan_run_ops,orchestrator}.py`、
`backend/services/precheck/runner.py`、`backend/services/{log_observation,plan_run_aggregation}.py`
+ `backend/tests/services/test_ai_plan_run_ops.py`。

## Alternatives

- #759 archive：仅记 WARNING 返回部分成功——AI 仍会向用户宣称「已触发」，未解决
  假成功；抛错让模型看到失败并如实汇报。
- #783：直接改代码放宽 aborted/计数——无产品裁决依据，且可能掩盖覆盖缺口；选择
  成文 + Revisit，待产品明确再动。

## Verification

- `backend/tests/services/test_ai_plan_run_ops.py`：archive 审计带 user_id；
  下发失败抛错且审计留 `dispatch_failed`；retry-dispatch 透传 requester_user_id。
  9 passed。
- 相邻：`test_plan_run_dispatch_retry.py` / `test_log_observation.py` /
  `test_plan_run_aggregation_shared.py` / `test_plan_precheck.py` 全绿（23+53 passed）。
- `ruff check` 变更文件通过。

## Revisit

- #783：若产品要求「部分 abort 仍可 SUCCESS/PARTIAL_SUCCESS」或「阈值按事件
  次数而非 distinct 产物」，需 ADR/产品裁决后改代码，并同步本 Note 与文档。
