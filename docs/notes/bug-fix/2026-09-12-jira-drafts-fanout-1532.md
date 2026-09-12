# Agent Note: 草稿列表扇出收敛为单次聚合读（#1532）

Status: implemented
Class: bug-fix
Issue: #1532

## Decision

把「问题追踪 → 草稿列表」的取数从**调用方自算扇出**改为**一次聚合读**：

1. 后端新增只读端点 `GET /api/v1/runs/jira-drafts?limit=N`，返回
   `{job_id, plan_run_id, draft, ended_at, post_processed_at}`，按
   `post_processed_at` 倒序、`1 ≤ limit ≤ 200`；仅返回「已 post-process 且草稿非空」
   的 Job。
2. 前端 `IssueTrackerPage` 用 `api.runs.listRecentJiraDrafts(50)` 单请求消费，
   保留 `enabled: tab === 'drafts'` 懒加载；行内 `plan_run_id` 用于跳转
   PlanRun 详情，`job_id` 仅作 React key。

**为什么必须动到后端**：#818 合入后的实现每次进「草稿列表」会执行
`planRuns.list(0,50)` → 逐 Run `listJobs` → 逐 Job `getCachedJiraDraft`，
且内层 `break` 只在**命中草稿**时触发。草稿是稀疏数据，无草稿的 Run 会把该
Run 的**全部** Job 逐个请求一遍并 404——请求数 = `1 + 50 + Σ(每个 Run 首个有草稿
Job 的序号，无草稿则 = 该 Run 全部 Job)`。单体 Run 可达设备数量级（平台目标
276 台），最坏是万级**串行** 404，比 #818 修复前的 50 次更差。前端单侧无法消除：
要么拿不到跨 Run 的批量草稿读，要么只能继续扇出。

**「已落草稿」的判据**用 `post_processed_at IS NOT NULL` + 取值真值，而不是
`jira_draft_json IS NOT NULL`：SQLAlchemy 的 JSONB 默认 `none_as_null=False`，
Python `None` 落库是 JSON `null` 而非 SQL NULL，`IS NOT NULL` 对「有时间戳、
无草稿」的行同样成立（此陷阱由本单测试 `test_excludes_not_post_processed_and_draft_less_jobs`
钉住）。

## Alternatives

- **沿用「PlanRun 列表 → listJobs → 逐 Job 取草稿」，仅加并发与短路**：否决。
  并发能压时长但压不掉请求数；无草稿的 Run 仍要探测全部 Job，而「有没有草稿」
  只有取到才知道——根因是缺少跨 Run 的批量读，不是并发度。
- **给 `/plan-runs/{id}/jobs` 的 `JobInstanceOut` 加 `jira_draft` 字段**：否决。
  该响应已被多处列表消费，塞入整份草稿会放大无关调用的载荷；且仍要
  `1 + N` 次请求（逐 Run 列 jobs），扇出只降一阶。
- **前端只保留「最近 N 条 PlanRun」并逐条请求**：同第一项，未消除扇出。

## Verification

- 后端：`venv/bin/python -m pytest backend/tests/api/test_runs_jira_drafts_list.py
  backend/tests/api/test_read_api_auth.py -q` → 79 passed（含新端点 4 例：id 域
  独立性、缓存语义排除、倒序与 limit、limit 越界 422；以及新端点的 401 鉴权回归）。
- 前端：`npx vitest run src/pages/issues/IssueTrackerPage.test.tsx` → 8 passed。
  其中两例直接锁回归：断言 `planRuns.list` / `planRuns.listJobs` /
  `runs.getCachedJiraDraft` **一次都不被调用**（扇出不存在）；断言跳转用的是
  `plan_run_id` 而非 `job_id`。
- 门禁：`python scripts/run_gates.py check:quick` → 7 gates 全绿
  （ruff / eslint / tsc / knip / compileall / gov-surface / ai-work）。

## Revisit

- 若草稿量增长到需要分页/筛选（按项目、按时间窗），当前 `limit` 单参数不够；
  届时在端点上加 `project_key` / `since` 等过滤，而不是回到前端扇出。
- 列表以 Job 为行单位（一个 PlanRun 可能有多个 Job 有草稿）；若要「按 PlanRun
  聚合成一行」，应在后端分组而不是前端再聚合。
