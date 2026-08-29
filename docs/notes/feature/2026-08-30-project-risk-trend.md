# 项目级风险趋势（ADR-0029 P2-9）

Status: implemented
Class: feature

P2 第一段：项目详情页「结果」块从「最近 5 条 run 列表」（与 /results 页
重复）换成**风险趋势图**——按天 S/A/B 计数，run 级 DLE 权威聚合。

## Decision

**1. 后端 `GET /results/risk-trend?project_key=&days=`**

- 数据源：`aggregate_risk_summary`（DLE 权威计数 + 未链接信号，与 /results
  同口径）——每 run 取其全部 job 聚合，按 `plan_run.started_at` 归日
- 过滤统一经 `PlanRun.project_id` 快照（与 /summary 同口径）；project_key
  未知 404；缺省 = 全量
- 只统计终态 run（SUCCESS/PARTIAL_SUCCESS/FAILED——注意 PlanRun 枚举无
  COMPLETED）；`days` 上限 365
- 返回 `{project_key, days, buckets: [{date, S, A, B, runs}]}`

**2. 前端详情页结果块**

- recharts 堆积柱状（S/A/B 三段），x=日期 y=计数；状态语义色
  S=destructive / A=warning / B=success（项目 CSS vars，与 StatusBadge
  的 risk 映射同源）；legend + tooltip + 单轴（dataviz 规范：3 序列必有
  legend、色随实体不随排名、status 色带标签）
- 空态文案说明积累条件（「新 Run 派发完成后开始积累」——plan_run 真实
  归属数据随 P0-1 起的新派发才有）
- 删除 summaryQ（recent_runs 表格与 /results 重复）

## Alternatives

- **保留最近运行表格 + 加图**：表格有导航价值但方案明确「/results 页
  已经有了，重复」；结果块单一职责 = 趋势。
- **按事件日（detected_at）而非 run 起始日归桶**：事件日粒度更细但
  aggregate 是 run 级聚合（权威口径），拆散会破坏 S/A/B 定级的完整性。

## Verification

- `test_results.py::TestRiskTrend` 3 个：空态 / 项目过滤按天归桶（S 级
  通过 category=ANR + event_subtype=swt + nfs_path 非空构造——unlinked
  计数按 nfs_path DISTINCT、category 白名单是 AEE/VENDOR_AEE/ANR）/
  未知项目 404；5 passed + ruff 全过
- 前端：ProjectDetailPage 测试更新（标题/空态/调用参数）；tsc + 全量
  vitest + eslint 全绿
- 未做浏览器截图验证（无渲染环境）——结构与规范对齐，颜色复用现有
  StatusBadge 同源 token

## Revisit

P2-10 剩余：JIRA 提单记录块（jira_run 有数据后）、脚本「被哪些项目用过」
统计；P2-11 删抽屉。风险趋势的「无数据空图」随生产派发自然消失。
