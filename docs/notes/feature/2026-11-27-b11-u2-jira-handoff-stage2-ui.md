# B11-U2：/issue-tracker 提单表单生成工作台与 PlanRun 快捷深链（#4199 / #4174）

Status: implemented
Class: feature

关联：[#4199](https://github.com/DUElost/stability-test-platform/issues/4199)（批次 B11 方案 v1.1）、
[#4174](https://github.com/DUElost/stability-test-platform/issues/4174)（v1.1 §4.1）、
B11-U1 冻结契约 `/api/v1/jira/handoff`。

## Decision

按批次 **B11（#4199 规划方案 v1.1）** 与 **#4174 v1.1 §4.1**，落地阶段 2（B11-U2）前端工作台与深链，零后端改动：

1. **类型与客户端对齐冻结 Schema**：在 `frontend/src/utils/api/types.ts` 登记 `HandoffPreflightIn/Out`、`HandoffBundleCreateIn`、`HandoffBundleSummaryOut`、`HandoffBuildBundleOut`、`HandoffBundleOut` 等字段；`jiraHandoff` 客户端封装全部 5 个端点，下载白名单仅 `manifest` / `preview_xls` / `review_workbook`。
2. **首 Tab + 复数深链 + 向后兼容**：`IssueTrackerPage` 新增首 Tab `handoff`（「提单表单生成」）；显式支持 `?tab=handoff&planRunIds=1,2,3`；既有 `?planRunId=` 仍落到「批量提单」并预填，不破坏原跳入行为。
3. **防误导硬约束**：工作台只展示后端预检/交付包事实；`coverage_complete=false` 禁用生成并展示缺失主机与 `unbound_rows`；`PENDING_EXPORT` 醒目标识「待导出（Path 尚未校验可达，不可直接送去提单）」；前端不自算去重计数、不拼接 15.4 路径。`DedupReportCard` 新增「生成提单表单」深链至 `/issue-tracker?tab=handoff&planRunIds=<runId>`。

## Alternatives

- **方案甲（默认落地即切到 handoff Tab）**：否决。无 `tab` 参数时保持既有「批量提单」默认，避免破坏 `?planRunId=` 与存量书签；仅 `tab=handoff` 或复数 `planRunIds` 深链进入工作台。
- **方案乙（前端用 `<a href>` 直链下载而非 blob 客户端）**：否决。统一走 `apiClient` 携带 cookie 鉴权与白名单 kind 校验，便于单测断言请求路径且与套件导出下载模式一致。

## Verification

- `cd frontend && npm run test -- --run src/pages/issues/IssueTrackerPage.test.tsx src/components/issues/HandoffWorkbench.test.tsx src/components/plan-run/DedupReportCard.test.tsx src/utils/api/jiraHandoff.test.ts` → 4 files / 34 tests passed
- `cd frontend && npm run type-check` → passed
- `.venv/bin/python scripts/run_gates.py check:quick`、`tools/dev/check-internal-ip-leak.py --check`：见 PR 正文
- 隔离预览 `stp-ui-review` Playwright 双视口核验：见 PR 正文截图路径

## Revisit

- Owner 在独立测试服 / `stp-ui-review` 确认界面与交互后再合入并部署生产前端（#4174 §4.1 / #4199 §5 步骤 2）；
- B11-U3 提供 `EXPORTED` 终稿态后，本工作台历史列表与状态横幅即可自然展示终稿区分，无需改深链契约；
- `/execution/plan-runs` 列表多选合并入口可按 #4174 §4.1 顺带补齐（本单元未做）。
