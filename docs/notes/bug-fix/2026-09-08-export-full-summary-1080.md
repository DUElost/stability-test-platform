# 导出摘要基于全量 Job，仅截断明细（#1080）

Status: implemented  
Class: bug-fix

## Decision

`build_plan_run_export` 用 SQL `GROUP BY status` 聚合**全部** Job 的
`total_jobs` / `status_counts` / `pass_rate`；devices 明细仍
`limit(_EXPORT_MAX_JOBS+1)` 截断并保留 `truncated`。timeline 改为对
PlanRun 下全部 `StepTrace` 聚合，不再被明细截断带偏。

涉及：`backend/services/plan_run_export.py`；测试见
`test_plan_run_export.py`（501 Job、尾部 FAILED）。

## Alternatives

- 提高 `_EXPORT_MAX_JOBS`：不解决「摘要=子集」语义错误。
- 摘要与明细共用截断列表：即本 bug。
- 分页导出全量明细：超出本修复范围。

## Verification

- `test_export_summary_uses_all_jobs_when_devices_truncated`
- `TESTING=1 JWT_SECRET_KEY=test-secret python -m pytest
  backend/tests/services/test_plan_run_export.py -q`

## Revisit

若 UI 需展示「截断外失败数」，可在 summary 增加 `omitted_failed`。
