# #2030 第 3 条：告警聚合标签校验恢复（`sum by (...)` 不再免检）+ adr-0026 文档漂移同步

Status: implemented
Class: bug-fix

## Decision

#1927 收敛 `stability_plan_run_counter_drift_total` 标签（去 `plan_run_id`、告警改
`sum by (mode)`）时，为让结构层解析器不把 `sum`/`by`/`mode` 误认成指标名/标签，
`_AGG_PREFIX_RE` 把聚合前缀整体剥掉（`tests/test_prometheus_alerts_contract.py:39-42`）
——副作用是 **`by (...)` 里的标签不再被任何断言校验**：「指标改了标签、告警没跟」
只有 promtool 场景层能拦，而 CI runner 无 promtool（该用例显式 skip）。

决定：新增 `_AGG_CLAUSE_RE` + `_aggregation_clauses()`，对每个
`sum|... by|without (labels) (inner)` 子句提取聚合标签与内层指标名，断言
`labels ⊆ 内层指标的注册表标签集`
（`test_alert_aggregation_labels_match_metric_registry`）。定位不到内层指标的
形态（如未来出现嵌套聚合）**显式报 problem 转红**，提示更新解析器——不静默放过。
配套自证用例 `test_aggregation_parser_reads_labels_and_inner_metric` 防解析退化
空跑；集合级断言「至少解析出一个聚合子句」。

同时同步权威运维文档 `docs/operations/adr-0026-admission-and-scale-gray-rollout.md`
§6.2 两行漂移（#1927 未跟进）：指标 `{plan_run_id, mode}` → `{mode}`；告警
`increase(...)` → `sum by (mode) (increase(...))`。

**影响面**：`tests/test_prometheus_alerts_contract.py`（+2 用例）；文档两行；
无产品行为变更。`.test.yml` 场景与 Grafana 面板此前已随 #1927 同步，本次核对一致。

## Alternatives

- **删掉 `_AGG_PREFIX_RE`、让 token 扫描直接覆盖聚合标签**：`sum`/`by`/`mode`
  会被当作指标名/标签选择器产生误报（#1927 修的就是这个）。否决。
- **只靠 promtool 场景层拦**：CI runner 无 promtool（显式 skip）——漂移只在装了
  promtool 的机器被拦，等于「取决于跑测机器」的盲区。否决。
- **聚合标签与指标的对应关系用「同表达式全部指标取并集」**：过松（`by (x)` 可能
  与式内别的指标无关）；本仓库形态固定（by 子句紧跟内层指标），按首个非函数
  token 定位更严格。否决。

## Verification

- `python -m pytest tests/test_prometheus_alerts_contract.py -q` → **5 passed**；
- **逐点 mutation**（改告警 yml、仅一处）：
  1. 改回 `sum by (plan_run_id)`（#1927 之前的旧标签）→ 新用例 **FAILED**
     （`['plan_run_id'] 不在 ... 标签集 ['mode']`）；同 mutation 下
     `test_alert_selectors_match_metric_registry`（旧结构层）**passed**——证明
     旧断言确实放行、加固必要。本机场景层（promtool 可用）也 FAILED，但 CI 无
     promtool 时该用例 skip，加固价值正是把拦截前移到恒跑层；
  2. 改为嵌套 `sum by (mode) (sum by (host_id) (...))`（解析器未覆盖形态）→
     新用例 **FAILED**，problem 明示「未定位到内层指标（inner='sum'）」；
- 同族回归：`test_prometheus_alerts_contract` + `test_grafana_dashboard_contract`
  + `test_automerge_queue_alerts` → **27 passed**；
- 两处 mutation 恢复后均复绿；`check:quick` 全绿。

## Revisit

- 本单只处理 #2030 第 3 条；第 4 条（部署身份枚举含不部署文件）未动，issue 保持 open。
- `docs/notes/feature/2026-09-13-counter-drift-metrics-77.md` 保留当时事实
  （`{plan_run_id, mode}`）——#1927 的 Note 已记录变更、本单同步了权威 runbook，
  历史 Note 未改写；若后续形成「历史 Note 标注现行形态」的统一口径再批量处理。
