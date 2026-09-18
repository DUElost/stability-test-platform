# Jira 双出口收敛：draft=预览、extract=交付（#290 / Epic #286）

Status: implemented
Class: simplification

Issue: #290（Epic #286 最后一个子任务；裁决记录写入 ADR-0012 §交付出口分层裁定）

## Decision

裁定「崩溃 → 工单材料」两条链路为分层职责而非双出口竞争，并删除真正的冗余：

- `JobInstance.jira_draft_json`（per-Job 草稿）= **预览层**：post_completion
  与报告同事务生成，仅服务人工复核（RunReportPage 预览面板 +
  IssueTrackerPage 草稿列表）。核查确认全库不存在 draft→工单自动桥。
- extract 材料包（`jira/{plan_run_id}/`）+ `/api/v1/jira` JiraRun 厂商工具
  = **唯一交付/建单路径**（merge xls → 材料包 → stability_Jira-Automation）。
- 删除 `POST /runs/{run_id}/jira-draft`（按需重建端点）：前端 `runs.ts` 只
  封装 cached / 列表两个 GET、无任何 POST 调用方；post_completion 已在终态
  持久化草稿，按需重建是无人走的冗余写入路径。`GET .../cached` 保留同款
  实时回落计算（`build_jira_draft` + `_resolve_draft_project_key`），
  项目键解析行为（ADR-0029 P0）不变。
- UI 单入口无需再收敛：问题追踪页已是一个页面三 tab
  （批量提单=交付 / 草稿列表=预览 / 历史=审计）。

## Alternatives

- **只留 PlanRun extract、删 draft 链**：否——草稿列表是一级导航日常入口
  （#1532 刚做过查询性能修复），RunReport 预览在用；删掉砍掉人工复核唯一
  结构化预览，且推翻 ADR-0012 第 1 层既有交付，而 draft 与 report 同事务
  生成边际成本≈0。
- **只留 draft、删 extract 链**：否——材料包是厂商建单工具唯一材料供给，
  删掉交付闭环断裂，ADR-0025 方案 C 归档链也依赖它。
- **维持现状不删 POST**：否——#290 验收要求「未选中路径不再被写入」，
  POST 是唯一满足该描述的死路径，留着 Epic 无法关单。

## Verification

- `backend/tests/api/test_runs.py::TestRunJiraDraftProjectKey`：两个项目键
  解析测试改走 `GET .../jira-draft/cached` 实时回落路径，断言移入
  `{data, error}` 信封（与报告端点同口径）。
- 引用面核查：POST 端点仅 `test_runs.py:183/193` 两处测试引用，无其他
  backend/frontend/docs 活引用（ADR-0008 表格为历史迁移记录，不动）；
  `JiraDraftOut` schema 保留导出（report_service 仍消费）。
- `.venv/bin/python3 -m pytest backend/tests/api/test_runs.py
  backend/tests/api/test_runs_jira_drafts_list.py
  backend/tests/api/test_read_api_auth.py -q` 全绿（结果见 PR）；
  `python scripts/run_gates.py check:quick` 通过。

## Revisit

- 第 2/3 层（提单策略引擎 / 自动提交+回写）仍 Proposed；若未来落地，
  在 draft 之上叠加决策层，需回改本 ADR 而非新开出口。
- 前端 `QueryProvider.tsx` 仍保留 `job-jira-draft` 查询键失效逻辑——
  POST 删除后 invalidation 只剩 cached GET 消费，无功能影响；若后续
  报告缓存刷新机制调整可一并审视。
