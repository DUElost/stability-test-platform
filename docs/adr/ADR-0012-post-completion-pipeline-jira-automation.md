# ADR-0012: 后处理流水线到 JIRA 自动提交演进
- 状态：Accepted（第 1 层已实现，第 2-3 层 Proposed）
- 优先级：P2
- 目标里程碑：M3（完整闭环）
- 日期：2026-02-18
- 更新日期：2026-09-19
- 决策者：平台研发组
- 标签：后处理, 报告, JIRA, 自动化闭环

## 背景

平台愿景要求专项执行后自动衔接”结果收取 -> 报告生成 -> JIRA 提交 -> 测试报告产出”。
当前已实现 run 终态后自动生成 `RunReport` 与 `JIRA Draft`，但尚未进入可控自动提单阶段。

## 决策

将后处理能力分三层推进：

- 第 1 层（✅ 已实现）：终态触发报告与 JIRA 草稿缓存。
- 第 2 层（拟建设）：引入”提单策略引擎”（按风险等级、失败类型、去重规则决策是否提单）。
- 第 3 层（拟建设）：JIRA 自动提交 + 回写 issue key + 幂等去重（同一问题不重复建单）。

自动提交默认以”可回滚、可审计、可人工复核”为前提，不做无保护直推。

## 备选方案与权衡

- 方案 A：长期仅停留在草稿，人工提单。
  - 优点：风险低。
  - 缺点：闭环效率低，无法规模化。
- 方案 B：直接全自动提单。
  - 优点：效率高。
  - 缺点：误报会产生大量噪声工单。
- 方案 C：分层推进（当前提案）。
  - 优点：在风险可控前提下逐步自动化。
  - 缺点：需要多阶段建设与规则治理。

## 影响

- 正向影响：更接近项目北极星闭环，减少人工操作。第 1 层已实现自动报告生成与 JIRA 草稿缓存，前端可通过 IssueTrackerPage 查看。
- 代价：需要处理鉴权、速率限制、幂等、去重与回写一致性（第 2-3 层）。

## 交付出口分层裁定（2026-09-19，#290 收口）

「崩溃 → 工单材料」的两条链路**不在同一层、不构成双出口竞争**，书面裁定如下：

- **`JobInstance.jira_draft_json`（per-Job 草稿）= 结构化预览层。**
  post_completion 在 Job 终态与报告同事务生成，仅服务人工复核
  （RunReportPage 预览面板、IssueTrackerPage 草稿列表）。全库不存在
  「draft → 工单」自动桥；第 2/3 层策略引擎（若落地）是在 draft 之上叠加
  提单决策，不构成新交付出口。
- **extract 材料包 + `/api/v1/jira` JiraRun = 唯一交付/建单路径。**
  merge Result xls（PlanRunArtifact）→ `jira/{plan_run_id}/`（事件目录 +
  报表，ADR-0025 方案 C）→ stability_Jira-Automation 厂商工具建单。
- **配套清理**：删除无前端调用方的 `POST /runs/{run_id}/jira-draft`
  （按需重建端点）——post_completion 已在终态持久化草稿，`GET .../cached`
  保留同款实时回落计算，项目键解析行为（ADR-0029 P0）不变。

## 落地与后续动作

- ~~第一步~~（✅ 已完成）：固化草稿字段规范与去重键模型（设备、版本、错误指纹）。
- ~~第一步补充~~（✅ 已完成）：实现 `post_completion.py` 后处理流水线与前端 IssueTrackerPage。
- 第二步：新增”建议提单/自动提单/仅草稿”三级策略。
- 第三步：引入提单审计与失败重试队列。

## 关联实现/文档

### 后端
- `backend/services/post_completion.py` - 任务完成后处理流水线
- `backend/services/report_service.py` - 报告生成与 JIRA Draft 构建
- ~~`backend/api/routes/tasks.py`~~ — ~~`/runs/{run_id}/jira-draft/cached` API~~ → 已迁移至 `backend/api/routes/runs.py`（双轨合并 Wave 7）

### 前端
- `frontend/src/pages/issues/IssueTrackerPage.tsx` - 问题追踪页面
- ~~`frontend/src/pages/task-runs/TaskRunsPage.tsx`~~ — ~~任务实例页面~~ → 已迁移至 Plan 体系对应页面

### 数据库
- `JobInstance.jira_draft_json` - JIRA 草稿缓存字段（原 `TaskRun.jira_draft_json`，随 ADR-0020 迁移）
- `JobInstance.post_processed_at` - 后处理完成时间戳（原 `TaskRun.post_processed_at`，同上）

> ⚠️ **2026-06-12 勘误**：原关联实现引用 `tasks.py` 和 `TaskRun` 模型——两者分别于双轨合并 Wave 7（拆分为 `runs.py` + `logs.py`）和 ADR-0020（`WorkflowRun` → `PlanRun` / `JobInstance`）中迁移/删除，此处同步更新。

- `docs/project-vision.md`
