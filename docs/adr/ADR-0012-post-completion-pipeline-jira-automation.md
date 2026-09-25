# ADR-0012: 后处理流水线到 JIRA 自动提交演进
- 状态：Accepted（第 1 层已实现；2026-09-25 裁决第 2–3 层：第 2 层改判为「S/A 级 PlanRun 自动生成提单清单」，Accepted 待实施；第 3 层无人值守建单不采纳——见「第 2–3 层裁决」节）
- 优先级：P2
- 目标里程碑：M3（完整闭环）
- 日期：2026-02-18
- 更新日期：2026-09-25（第 2–3 层裁决，owner 授权 Claude 裁决）；2026-09-19（交付出口分层裁定）
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

## 第 2–3 层裁决（2026-09-25，owner 授权 Claude 裁决）

原第 2–3 层写于 2026-02，前提是「平台在 draft 之上做提单决策并自己建单」。2026-09-19 的交付出口裁定已确定
**唯一建单路径是 extract 材料包 + JiraRun + 厂商工具**，per-Job draft 只是预览层。第 2–3 层须在这一前提下重判：

- **第 2 层——改判为「S/A 级 PlanRun 自动生成提单清单」，Accepted，待实施。**
  决策点放在 PlanRun 级的 JiraRun 入口，不放在 per-Job draft 上（避免重开双出口）。
  策略：PlanRun 完结、extract 材料包就绪、本 run 出现 **S 或 A** 级风险事件（ADR-0045 词表与判定源）时，平台自动发起
  `stage=upload_list` 的 JiraRun，只生成提单清单供人复核；**B 及以下只保留材料包**，由人决定是否发起。
  解析不到 `jira_project_key`（ADR-0029 G17）时不自动发起，改为可见告警。
  实施前置：实证厂商工具 `upload_list` 阶段对 JIRA **无写入**（ADR-0053 §1.1-6 记录厂商工具行为尚未验证）；
  若有写入，本层降级为「完结时提示可提单」，不自动发起。
  这是原「建议提单 / 自动提单 / 仅草稿」三级策略（落地第二步）的收敛形态：S/A = 自动出清单，其余 = 仅材料包。
- **第 3 层——无人值守建单（非 dry-run `create`）不采纳。** 建单保持人工触发：由操作者在复核清单后发起，
  JiraRun 记录发起人、reporter 与 issue keys。已具备与仍需补齐的部分分别归属：
  - 回写 issue key：JiraRun 已按 run 解析并持久化 `issue_keys`；精确到「每次故障发生 ↔ issue key」的关联
    归 [ADR-0053 D7](./ADR-0053-center-storage-event-dedup.md) 的长期记录；
  - 幂等去重：平台侧按「故障发生」去重（ADR-0053 D7：同一发生不重复进入提单输入）；
    同签名多次发生是否并单属厂商工具 / JIRA 侧；
  - 失败重试（原落地第三步）：不另建重试队列。建单是人工动作，失败由操作者重跑；
    自动 `upload_list` 失败只记 JiraRun FAILED 并可见，不自动重试（厂商工具幂等性未证实）。
  - **复议触发器**：自动清单上线满 30 天，人工建单时对清单「原样转正」比例 ≥ 95% 且期间无误报工单返工，
    再议无人值守 `create`。

## 落地与后续动作

- ~~第一步~~（✅ 已完成）：固化草稿字段规范与去重键模型（设备、版本、错误指纹）。
- ~~第一步补充~~（✅ 已完成）：实现 `post_completion.py` 后处理流水线与前端 IssueTrackerPage。
- 第二步：~~新增”建议提单/自动提单/仅草稿”三级策略~~ → 2026-09-25 收敛为「S/A 级自动出提单清单」（见第 2–3 层裁决），待实施。
- 第三步：~~引入提单审计与失败重试队列~~ → 2026-09-25 裁决：审计由 JiraRun 承担，不另建重试队列；无人值守建单不采纳（见第 2–3 层裁决）。

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
