# Agent Note: PlanRun/Job id 域错位修复（#818）

Status: implemented
Class: bug-fix
Issue: #818

## Decision

1. **IssueTracker 草稿列表**：`getCachedJiraDraft` 的 path 参数是 JobInstance.id；对每个 PlanRun 先 `listJobs`，再按 job id 取草稿。查询加 `enabled: tab === 'drafts'`，避免进页即发 50 次请求。
2. **项目详情 S 级清单**：`s_runs.run_id` 是 PlanRun.id，点击跳转 `/execution/plan-runs/{id}`（不再误用 `/runs/{id}/report` Job 报表路由）。

## Alternatives

- 后端 risk-trend 增加 `job_id` 仍跳 Job 报表：与 issue 修复方向（plan-run 详情）不一致，且 S 级清单语义是 run 级。
- 后端新增 plan-run 维度 jira-draft 聚合端点：可行但超出两处 UI 错位的最小修复面。

## Verification

- `npm run test -- IssueTrackerPage.test.tsx ProjectDetailPage.test.tsx`（worktree `/tmp/stp-818`）
- `python scripts/run_gates.py check:quick`（提交前）

## Revisit

- 多 job PlanRun 仅展示首个有草稿的 job；若需 per-job 草稿行可后续扩展。
