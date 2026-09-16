# 告警面「有定义、无生产者」守卫：把「追两跳」固化成断言（#2237）

Status: implemented
Class: testing

## Decision

新增恒跑结构守卫 `tests/test_alert_metric_producers.py`：**告警表达式引用的每个
`stability_*` 指标，必须在非测试代码里存在可达的写入点**，否则 PR 红灯。同类轴在
仪表板面早已存在（#1258 的 `UNPRODUCED_METRICS`，
`tests/test_grafana_dashboard_contract.py`），告警面此前零覆盖——而「引用死指标的告警」
比「引用死指标的面板」更危险：面板不响只是没人看，告警不响是**出事时没人被叫醒**。

判据（AST，不用 grep）：

1. 扫 `backend/**/*.py`（排除 `tests/`、`backend/agent/scripts/`、alembic、resources），
   把 `ident = Counter("stability_x", ...)`（含 `... if PROMETHEUS_AVAILABLE else _Mock()`
   的三元形态）映射成 指标名 ↔ 标识符；
2. **写入点** = mutator 调用链（`inc/dec/observe/set/labels/remove/delete`）里出现该
   标识符，且 `_chain()` 必须穿过中间的 `Call` 节点——`x.labels(...).inc()` 的 AST 是
   `Attribute(value=Call(...))`，只走 Attribute/Name 会整体丢掉最常见的一类埋点
   （写第一版时就是这样漏掉 CSRF 埋点的）；
3. 三种真实导入形态都要认：直接导入 `claim_lease_failed_total.inc()`
   （`backend/api/routes/agent_api.py:516`）、模块属性 `metrics.unlinked_fixable_total.inc()`
   （`backend/services/log_observation.py:278`）、**别名导入**
   `saq_queue_depth as saq_queue_depth_gauge` 后 `.labels(...).set(...)`
   （`backend/scheduler/app_scheduler.py:24,156`）；
4. **同文件写入不算生产者**，改追一跳：写入在 `backend/core/metrics.py` 的 `record_*`
   门面里时，要求该门面在别的文件有调用点。这条是本单的立单原因——#2151 期间按指标名
   grep `stability_dispatch_gate_duration_seconds` 只命中定义行
   （`backend/core/metrics.py:441`），差点把活规则判成死的；真埋点在
   `record_dispatch_gate`（`backend/core/metrics.py:693`）→
   调用点 `backend/services/precheck/runner.py:359`；
5. Histogram/Summary 的 `_bucket/_count/_sum/_created` 样本名归一到基础名
   （告警按样本名查询，代码 observe 的是基础指标），否则每条直方图告警都会误报；
6. AST 追不到的接线（框架回调、容器间接持有）进 `_MANUAL_WIRED_METRICS` 登记**可核对的
   在场证据**，而不是放宽强判据。当前一条：`stability_api_requests_total` →
   `backend/main.py` 必须有 `add_middleware(ApiRequestMetricsMiddleware)`
   （写入点在 `ApiRequestMetricsMiddleware.dispatch` 里，AST 找不到调用者）。

判据边界（写进模块 docstring，避免被当成全量可达性证明）：只追一跳，且终点是「代码里
存在调用点」；「该调用点运行时是否真会被走到」（分支永假、任务未注册）不由本文件判定。

## Alternatives

- **正则 grep 指标名**：否决——正是它把人带进「差点误判死规则」的坑（埋点在 helper 里，
  指标名不出现在调用点），且别名导入完全看不见。
- **「跨文件出现标识符即算生产者」**（v1 写法，实测被采用过一版）：否决——`import` 了
  却从不写入也算生产者，等于放行「定义了、门面也没写」的一半死法；本判据要求
  mutator 调用链，弱引用只做报错线索。
- **把间接写入也自动识别（数据流分析）**：不做。`_FLEET_GAUGES` 那种
  `for ..., gauge, ... in _FLEET_GAUGES: gauge.set(...)` 需要值流分析，代价与收益不
  对称；改成「强判据 + 可登记锚点」，登记时必须留下能在 AST 里复查的证据。
- **只在 #2151 的 Note 里记一句「记得人工核对」**：否决——人工核对不是断言，下一个人
  不会重跑那两跳。
- **顺手清掉 13 个「有定义无写入点」的非告警指标**：不做，超范围。本单只保证
  *告警引用的* 指标可达；其余属指标面清理（见 Revisit）。

## Verification

基线：`pytest tests/test_alert_metric_producers.py -q` → **4 passed**（全仓扫描
313 个非测试文件、73 个指标定义，17 个告警引用指标全部拿到证据；单次 1.5s，
满足 `tests/` 的「纯离线 + 秒级」准入）。逐条证据示例：
`stability_merge_skip_tool_not_configured_total ← backend/services/dedup_scan.py:357`、
`stability_saq_queue_depth ← backend/scheduler/app_scheduler.py:156 saq_queue_depth.labels.set()`、
`stability_dispatch_gate_duration_seconds_bucket ← runner.py:359 record_dispatch_gate()`。

破坏性对照（改完即从备份还原，`git status` 已确认干净）：

- A 删跨文件直写（`dedup_scan.py` 的 `.inc()`）→ **1 failed**
- B 删 helper 调用点（`precheck/runner.py` 的 `record_dispatch_gate(...)`）→ **1 failed**
- C 破坏别名写入（`app_scheduler.py` 换成别的变量）→ **1 failed**
- D 摘中间件：整行注释掉 / 删除但保留 import → **各 1 failed**
  （D 组最初是**绿的**：锚点用子串匹配会被注释行骗过，遂改成 AST 级
  `add_middleware(<Name>)` 判定——这次是自证过程本身暴露的缺陷）
- E1 临时新增引用 `stability_device_online` 的告警 → **红**，且报「无直接写入点，但被
  跨文件引用（`backend/api/routes/metrics.py:21,45`）——疑似经容器/间接持有」，指向出口；
- E2 临时新增引用 `stability_task_dispatch_total`（真·零引用）的告警 → **红**，
  报「有定义、无任何写入点也无跨文件引用」。
- `pytest tests/ -q` 与 `python scripts/run_gates.py check:quick` 见 PR 正文（最终 head 数字）。

顺带查实（不在本单改）：`backend/core/metrics.py` 里 `record_api_request` 的 docstring
仍写「#1258 deferred：生产者（路径模板化的请求中间件）尚未落地」，而中间件其实已挂载
（`backend/main.py:374`，`backend/core/request_metrics.py:113` 有写入点）——连带
`tests/test_grafana_dashboard_contract.py` 的 `UNPRODUCED_METRICS` 5 条豁免已失去
成立条件。属「叙述与代码漂移」，另记 Revisit。

## Revisit

- **13 个「有定义、查无写入点」的非告警指标**（`stability_task_dispatch_total`、
  `stability_device_lease_conflicts_total`、`stability_plan_run_active`、
  `stability_unknown_jobs`、`stability_expired_active_leases` 等）：其中 2 个
  （`stability_device_online` / `stability_host_online`）经 `_FLEET_GAUGES` 间接写入，
  是真生产者；其余需逐个判读后**补埋点或删除定义**，本守卫不覆盖（它们不在告警里）。
  下一批若要把轴从「告警面」扩到「全指标面」，先把这 13 条清干净，否则守卫一落地就背 11 条红。
- **#1258 的 `UNPRODUCED_METRICS` 已过期**（生产者已落地、面板未恢复）：撤豁免或恢复面板
  属仪表板面收口，与 `record_api_request` 的 docstring 一起改，别只删清单。
- 若将来有人要给 `_FLEET_GAUGES` 这类间接写入的指标加告警：守卫会先红并给出线索，
  正确出口是扩展 `_MANUAL_WIRED_METRICS` 的锚点形态（当前只能表达「某调用的实参」），
  不是放宽 mutator 判据。
