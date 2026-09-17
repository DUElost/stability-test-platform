# 风险口径收口：四项逐条落点（#2419）

Status: implemented
Class: bug-fix

## Decision

issue 的四项建议逐条落点——**1/2/3 已由合入的 #2436（#2365 的实现）结构消除，本单补第 4 项并逐条留证**：

**① 列表侧与报告页同源 —— 已完成（#2436，已合入）。**
`/results/summary` 的 `recent_runs[].risk_level` 与 `risk_distribution` 现在都走
`log_observation.aggregate_risk_levels_by_job`（DLE 权威 + 未链接信号，与报告页
`aggregate_risk_summary_from_signals` 同一判据同一数据源），`_parse_risk_level` 与
`log_summary` 解析整条删除。用例带**双向诱饵**：`jobs[0]` 有 S 级信号 + `risk=LOW`
快照 → 必须判 high（活链赢）；`jobs[2]` 只有 `risk=MEDIUM` 快照无信号 → 必须仍
unknown（死字段不参与）。

**② 词表收敛 —— 已完成（#2436）。**
判定词表**唯一源**是 `log_observation` 的 S/A/B（`_subtype_levels`/`_worst_level`）。
对外展示只经**一处显式映射** `_RISK_LABEL_BY_LEVEL`（`results.py`：S/A/B →
HIGH/MEDIUM/LOW，供结果页 `StatusBadge`）；报告面直接用 S/A/B（前端
`RISK_RATING_TEXT` 上色）。issue 说的「两套」正是 `_parse_risk_level` 那套解析词表——
它已不存在，所以不再有第二个判据源。

**③ 「无数据」与「解析失败」的区分 —— 由结构消除达成（#2436）。**
原来 UNKNOWN 同时承担两种语义；解析器删除后**不存在解析失败这条路径**，UNKNOWN
只剩一个含义：**该 job 没有任何异常事件**（不是「低风险」，也不是「没查到」）。
覆盖率另有观测面 `stability_risk_jobs_by_level{level}`（每次 `/results/summary` 刷新）。
UI 文案仍显示「未知」——要与新语义一致应改成「无异常记录」，属渲染面，本单不动。

**④ 死面板处置 —— 本单删（issue 给的两个选项里选「删」）。**

| 删除项 | 位置 |
|---|---|
| 报告页「汇总指标」面板 | `frontend/src/pages/runs/RunReportPage.tsx` |
| Markdown 导出的 `## Summary Metrics` 小节 | `backend/api/routes/runs.py::_report_to_markdown` |
| Markdown 导出里恒为 `0` 的 `restart_count` 行 | 同上（`counts.restart_count` 无生产者） |
| JIRA 描述里的 `h2. Summary Metrics` 小节 | `backend/services/report_service.py::build_jira_draft` |

数据源（RUN_COMPLETE 快照的 `log_summary`）自 ADR-0025 起**没有任何生产者**
（`backend/agent/pipeline_runner.py:106` 硬编码 `None`；#2365 实测 15118 条快照 0 条带
`risk=`，报告侧抽样 0/40 非空），面板与导出小节在生产里永远是空/`N/A`——是噪声，且
「看起来有答案」。**不补写入侧**：那需要一个产品决策（哪些指标、谁来产），不该由一条
口径 bug 顺手决定；真要恢复，应从活链派生并同时补一条非空断言（见 Revisit）。

**明确保留（按 issue 的最小范围）**：`summary_metrics` 字段仍在报告 API 与
`build_risk_alerts` 里——它是写入侧的入口，且 `RESTART_FREQUENT` 规则本就有
`counts.restart_count` 回落路径；删字段是契约变更，不在本单。`build_risk_alerts` 的
S/A 触发阈值按 issue 明示**不动**。

## Alternatives

- **A. 给 `log_summary` 补生产者**：不做。要么让 Agent 重新上送运行摘要（协议/产品决策，
  且与 ADR-0025「运行日志不再上送」的字面冲突需要先澄清），要么由平台从活链派生指标
  （得先定「哪些指标有意义」）——两者都不是一条口径 bug 该顺手决定的事。
- **B. 只删前端面板、留导出小节**：否决。同一份死数据在导出里继续打印 `- N/A`，
  issue 点名的「噪声」还在。
- **C. 连 `summary_metrics` 字段与 `RESTART_FREQUENT` 规则一起删**：本单不做。字段是契约
  的一部分（`schemas/run.py` 与前端 `types.ts` 同步），且告警规则属 issue 明示的
  「不在范围」区。留待有生产者时再统一收口。
- **D. 把 `unknown` 桶改名 `no_data`**：不做。API 词表改名会牵动前端图例与既有消费方，
  收益只是文案更准；语义澄清改在 UI 文案（更小、无契约影响），已记入 Revisit。

## Verification

- **红绿差分（先证伪再实现）**：
  - 后端负断言在基线实现上红，且失败信息正好复现 issue 描述的两处噪声——
    `AssertionError: 'Summary Metrics' unexpectedly found in '# Run Report - 101 … ##
    Summary Metrics\n- restarts: 2 …'` 与 `'…\n## Risk Summary\n…\n- restart_count: 0\n…'`
    （后者即「恒为 0 却像数据」的那一行）；换回新实现 12 passed。
  - 前端守卫在基线实现上红（`does not render a summary-metrics panel even when the API
    sends values`：`1 failed | 4 passed`），新实现 5 passed。
- **测试**：`backend/tests/api/test_run_report.py` + `test_runs.py` → 12 passed；
  前端全量 `vitest run`（118 files）→ **942 passed**；`npx tsc --noEmit`、
  `eslint --max-warnings 0` 通过；`check:quick` → 10 gates OK。
- **只读**：本单未新增生产查询；引用的 0/40、15118/0 两组数字来自 #2419/#2365 的只读实测
  （GET / SELECT，未写入）。

## Revisit

- **「汇总指标」的写入侧仍是空的**：本单只删了它的三个展示面。若将来要这个面板，正确形状是
  **从活链派生**（例如风险 counts 的 events_total / aee_entries、patrol 统计），并配一条
  「非空」断言；**不要**恢复 `log_summary` 文本解析——那正是本单与 #2365 一起拆掉的死链。
- **`log_summary` 字段本身仍在协议/模型里**：`RunOut.log_summary`、Agent payload 的
  `log_summary: None`、`StepTrace.output.update.log_summary` 都保留（AGENTS.md：已发布
  脚本版本与既有协议不因清理而改）。若哪天真要移除，属协议级变更，单独走。
- **UI 文案**：「未知」现在等于「无异常记录」。改文案（`StatusBadge` 的 RISK.UNKNOWN
  或结果页图例）是渲染面，等有需要时单独做；`tests/test_status_vocabulary_drift.py`
  对拍的是键集，改文案不影响它。
- **告警列表为 0 而风险摘要非空**（`build_risk_alerts` 仅 S/A 触发）按 issue 明示不在本单，
  它是否要改成「B 级也给提示」是独立的产品判断。
