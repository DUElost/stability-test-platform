# 风险分布接回活链：打标链路确认 + 覆盖率观测（#2365）

Status: implemented
Class: bug-fix

## Decision

**打标链路确认（issue 的第一问）：`/results/summary` 那条链根本不存在生产者。**

`results.py` 的 `risk_distribution` 与 `recent_runs[].risk_level` 读的是 RUN_COMPLETE 快照里
`log_summary` 的 `risk=HIGH|MEDIUM|LOW` 标记。全仓扫描：**没有任何代码写过它**——
`backend/agent/pipeline_runner.py:106` 把 `log_summary` 硬编码为 `None`（注释写明
ADR-0025 方案 C 后运行日志不再上送），其余只有转发点（`job_runner` / `api_client`）。
生产只读实测（`SELECT`，DSN 仅经环境变量）：

| 观测 | 值 |
|---|---|
| RUN_COMPLETE 快照总数 / 含 `risk=` | **15118 / 0** |
| job 总数 / 有任一异常信号的 job | 15751 / **730**（4.6%） |
| 近 30 天终态 PlanRun / 其中有异常信号 job 的 run | 204 / **61**（29.9%） |

所以「风险分布长期只有未知」不是判据太严，是**判据指向了一个死字段**——100% unknown 是
构造结果。与之并存的活链是 `log_observation.aggregate_risk_summary`（ADR-0028：
`device_log_event` 权威 + 未链接 `job_log_signal`），`/results/risk-trend` 已经在用它。

**做法：把 summary 接回活链，并让覆盖率可观测。**

1. **逐 job 判定的集合式聚合**（`log_observation.aggregate_risk_levels_by_job`）：
   与 `aggregate_risk_summary` **同判据同数据源**（抽出 `_subtype_levels` / `_worst_level`
   两个共用函数），只是按 job 分桶。两条源查询加 `by_job=True` 参数复用同一段
   SELECT/WHERE 字面量——过滤器只有一份，不会两条链各写一份而漂移。
2. **`/results/summary` 改接**：`risk_distribution` 与 `recent_runs[].risk_level` 都由它
   计算；S/A/B → high/medium/low（`_RISK_LABEL_BY_LEVEL`），**无异常事件 → unknown**，
   且**不压成 low**——「没采到」与「查过且没风险」的区别正是覆盖率要观测的东西。
   顺带删掉 `_parse_risk_level` / `_extract_log_summary_from_snapshot`（及只服务它们的
   `_safe_json_loads`、`StepTrace` 导入）与全表快照扫描。
3. **覆盖率指标** `stability_risk_jobs_by_level{level}`（Gauge，每次 `/summary` 计算刷新）：
   `high+medium+low` = 有判定依据的 job，`unknown` = 无任何异常事件的 job。
   此前这两种状态在仪表盘上同形（都只有未知桶），现在可查询/可告警。

**前端不改**（issue 明示，渲染面保持）：桶名与 `RecentRun.risk_level` 的词表
（HIGH/MEDIUM/LOW/UNKNOWN）都与 `StatusBadge` 的 `RISK` 映射一致，无契约变化。

## Alternatives

- **A. 给 `log_summary` 补生产者（让 Agent 回填 `risk=`）**：否决。那等于把已经用
  DLE 权威判过的风险再序列化一遍文本、再解析回来——两份判据必然漂移，且 ADR-0025 之后
  运行日志本就不上送中心存储（该字段的载体已经不存在）。
- **B. 只删风险分布、不做替代**：否决。判据是活的（30 天里 61 个 run 真有异常信号），
  删掉等于把已经采到的信号也丢掉。
- **C. 把 `unknown` 记成 low**：否决。空集不是结论；这样覆盖率永远 100%，本单要的观测
  面反而没了。
- **D. 逐 job 调 `aggregate_risk_summary`**：否决。仪表盘要覆盖全部 job（生产 15k），
  逐 job 往返是 O(N) 查询；改成「两条 GROUP BY 按 job 分桶 + 一次 `ANY(:ids)`」，
  比原来的全表快照扫描还便宜。
- **E. 顺手修 report_service 里同源的 `log_summary` 消费**：**不做**，见 Revisit
  （同一死字段的另一个消费者，但属报告面，单独一单）。

## Verification

- **红绿差分**：
  - 改写后的 `/results/summary` 用例在**基线实现**上失败：`assert 0 == (0 + 1)`（high 桶
    来自死字段的诱饵快照，活链信号被忽略）；换回新实现 5 passed；
  - 覆盖率指标断言有牙：临时移除 `.set()` 循环 → `assert recorder.values == {…}` 失败，
    恢复后通过；
  - 用例内部本身带诱饵：`jobs[0]` 同时有 S 级信号与 `risk=LOW` 快照、`jobs[2]` 只有
    `risk=MEDIUM` 快照（无信号）——前者必须判 high、后者必须仍 unknown，即「活链赢、
    死字段不参与」双向钉死。
- **测试**：
  - `backend/tests/api/test_results.py` → 5 passed；`backend/tests/services/test_log_observation.py`
    → 13 passed（新增 2 例：逐 job 分桶且无信号 job 缺席；单 job 时与
    `aggregate_risk_summary` 级别相等——防两套判据）。
  - 下游消费面（`plan_run_aggregation` / `run_report` / `plan_runs_api`）→ 111 passed。
  - PR 路径 `pytest tests/ --ignore=…`（与 CI 同口径）→ **1228 passed**。
  - 指标门禁 `tests/test_alert_metric_producers.py` → 8 passed（新 Gauge 有可识别的
    生产者，不会被判成死指标）。
- **门禁**：`python scripts/run_gates.py check:quick` → `[OK] check:quick (10 gates)`。
- **生产只读**：本单结论中的三组数字全部来自 `SELECT`（DSN 只从 `.env.backend` 取、
  未打印未落盘、全程未写）。**未做**：未在界面/浏览器验证新分布（本机无隔离前端环境）。

## Revisit

- **覆盖率本身仍低**（job 级 4.6%、30 天 run 级 29.9%）：这是**采集面**问题（多少 run 真的
  产生了异常事件），不是判据问题。要把覆盖率提上去应查事件采集链路（AEE/UNIVIEW 上报、
  `job_log_signal` 落库），**不要放松分级判据**——那是把数字做好看的假动作。
- **同一死字段的另一个消费者**：`backend/services/report_service.py::parse_run_log_summary`
  仍在解析 `log_summary`（报告摘要用）。既然该字段没有生产者，报告面这部分很可能同样
  恒空——本单未动（属报告面，需先确认现场报告里这些字段的实际观感），建议另开单核实。
- **指标是请求驱动的**：`stability_risk_jobs_by_level` 只在有人打开仪表盘（`/summary`）时
  刷新；无流量窗口里它是「上次的值」而非实时值。若要做覆盖率告警，需先决定是否改成
  定时计算（另需考虑 15k job 的定期聚合成本）。
- **前端「未知」文案**：语义从「没有 risk 标记」变成「该 job 没有任何异常事件」，
  更好的措辞可能是「无异常记录」。改文案属前端渲染（本单明确不做），等有需要再单独处理。
