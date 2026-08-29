# JIRA 草稿接上 test_project.jira_project_key（全局默认降为标注兜底）

Status: implemented
Class: feature

ADR-0029 项目域 P0 第三步：消除「JIRA 草稿页看到的项目键 ≠ 实际提交进 JIRA
的项目键」的双口径。草稿链路此前完全不认识 test_project，全平台一个
`RUN_REPORT_JIRA_PROJECT_KEY`（默认 "STABILITY"）。

## Decision

**1. 解析函数抽到 services 层（`backend/services/jira_project_key.py`）**

`resolve_jira_project_key`（快照口径：`plan_run.project_id` → test_project，
与 results.py 同口径）从 `dedup.py` 抽到 services 层成为全系统唯一解析入口；
`dedup.py` 保留同名委托函数（调用点与外部 import 兼容）。`runs.py` 草稿端点
直接 import services 版。

**2. 草稿端点解析快照并传入 override（`backend/api/routes/runs.py`）**

`_resolve_draft_project_key(db, run_id)`：`JobInstance.plan_run_id` →
services 解析 → 传入 `build_jira_draft(project_key_override=...)`。POST
`/runs/{id}/jira-draft` 与 `/cached` 两个端点都接。无 plan_run / 未归属 /
项目未配 jira 键 → None（回落模板/全局默认）。

**3. `build_jira_draft` 加 override + 来源标注（`report_service.py`）**

优先级：plan_run 快照 > 模板显式配置 > 全局默认。`extra` 新增
`project_key_source`（plan_run_project / template / global_default）与
`project_key_global_default`——「静默用 STABILITY」从此可见（缺配置不再隐身，
但**不阻断**：best-effort + 标注是 G18 自动草稿策略的范畴）。

判定「template」的细节：默认模板 `_default_jira_template()` 自带
`project_key: REPORT_JIRA_PROJECT_KEY`，所以「显式配置」= 模板值 ≠ 默认值；
否则即 global_default（首次实现按「key 存在与否」判定导致永远 template，
已修正并有测试锁定）。

## Alternatives

- **report_service 直接查 DB**：`build_jira_draft` 定位是 stateless
  & DB-free（两端点共用、可缓存），由有 db 的端点解析后传入，职责边界不变。
- **草稿反查 jira_run 表**：jira_run 当前为空且是提单链路记录，草稿在其
  之前发生；直接解析 plan_run 快照最简且同源。
- **缺键时阻断草稿**：与提单 best-effort 定位一致，草稿也是旁路功能，
  标注可见即可，硬门禁留给 G18。

## Verification

- unit：`test_run_report.py` 3 新测试（override 生效 + source 标注 /
  默认回落标注 global_default / 模板显式键标 template）。
- 端点：`test_runs.py::TestRunJiraDraftProjectKey` 2 新测试（项目键进草稿 /
  无项目 → STABILITY + 标注）。
- 命令：38 passed（test_run_report + test_runs + test_dedup_jira_endpoints）；
  ruff 全过。
- 既有语义保持：`build_jira_draft` 无 override 时行为与原来一致（默认值
  不变，仅 extra 多两个标注字段）。

## Revisit

P1 规则表落地后 plan_run.project_id 数据变实，草稿的 project_key 将出现
真实项目键；若 G18 自动草稿策略落地，「缺配置可见」升级为「缺配置告警」。
