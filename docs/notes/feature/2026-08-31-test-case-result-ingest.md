# ADR-0030 P2 块 2：test_case_result 摄入与 PlanRun 展示

Status: implemented
Class: feature

## Decision

- **表** `test_case_result`：`(job_id, case_name)` 唯一，PlanRun 维度查询
- **摄入**：`post_completion` → 读 `step_trace.output.detail_uri` → NFS JSON
  `testpoints[]` 落库；已摄入的 job 幂等跳过
- **API**：`GET /plan-runs/{id}/test-case-results`
- **前端**：`TestCaseResultsCard` 挂 PlanRun 终态详情页

块 1（用例管理页）见 `2026-08-31-test-suite-management-ui.md`（PR #610）。

## Verification

- `pytest backend/tests/services/test_test_case_result_ingest.py`
- `tests/test_alembic_heads.py`
- `npm run type-check`

## Revisit

- #506 `versions_used` 增强（下一项）
- 按需摄入触发：PlanRun 终态批量 backfill（当前仅 per-job post_completion）
