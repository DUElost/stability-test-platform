# 指标生产者守卫扩到全指标面：10 条无生产者定义逐条判读与删除（#2287）

Status: implemented
Class: testing

- 日期：2026-09-16
- 上游：`#2237`（告警面守卫，`b913b3f2`）、`#2286`/PR #2313（分析器修正与豁免派生化的前置）

## Decision

### 1. 10 条「有定义、无生产者」逐条判读 → 全部删除定义

判据不是「看起来没人用」，而是两条**可核事实**：

- **全史无写入调用**：对每个标识符跑 `git log -S"<ident>.inc" / ".set" / ".observe" --all`，
  命中为空——即这些定义自引入以来**从未**被写过，不是「曾经有生产者后来被删」；
- **零消费者**：指标名在 `backend/`（非定义处）、`deploy/prometheus/`、
  `docs/grafana/`、`tests/` 全域零命中——没有告警、没有面板、没有文档契约引用
  （仅 `docs/notes/testing/2026-09-16-alert-metric-producer-guard-2237.md` 的存量清单提及）。

| 指标 | 引入 | 判读 |
|---|---|---|
| `stability_task_dispatch_total{status}` | `c82a2a07` 2026-02-11 首版指标集成 | 删除——派发语义今日由 `dispatch_gate_runs_total{check,outcome}`（活）+ `saq_tasks_total{task_name,status}`（活）承担 |
| `stability_task_dispatch_errors_total{error_type}` | 同上 | 删除——`error_type` 的取值域（`device_unavailable`/`host_capacity`/`lock_failed`）今日无对应实现，属首版脚手架的想象面 |
| `stability_device_lease_conflicts_total` | `ff4f407b` 2026-05-03 租约指标批 | 删除——**已被活指标取代**：`claim_lease_failed_total`（`backend/api/routes/agent_api.py:516` 真实写入）就是「抢租约失败」这一件事 |
| `stability_device_lease_duration_seconds` | 同上 | 删除——同批的 `acquired`/`released` 有 `record_*` 门面并接线，本条没有；租约时长今日由租约表/UI 承担 |
| `stability_task_run_duration_seconds{task_type}` | `c82a2a07` | 删除——SAQ 侧 `saq_task_duration_seconds`（活）已在，任务级时长在 DB/UI |
| `stability_host_heartbeat_latency_seconds` | `c82a2a07` | 删除——心跳的**可行动**信号是 `host_heartbeat_missed_total{host_id}`（活）；延迟分布零消费者 |
| `stability_device_monitoring_updates_total` | `c82a2a07` | 删除——连 label 都没有、语义无对应实现，零消费者 |
| `stability_expired_active_leases{host_id}` | `b4fd7f7f` 2026-05-01 ADR-0019 Phase 4a/4b | 删除——**设计态从未实现**：引入时只定义了 gauge 与 import，从来没有 `.set()`（import 后来被 ruff 当未使用清掉）；状态转换由 `reconciler_actions{action,reason}`（活）表达 |
| `stability_unknown_jobs{reason}` | 同上 | 同上 |
| `stability_plan_run_active` | `c1aed754` 2026-05-08 ADR-0021 C5 | 删除——活跃 run 数由 UI/DB 承担，Prometheus 侧吞吐由 `plan_run_terminal_total{status}`（活）表达 |

**为什么删而不是补埋点**：「补真实埋点」要给出一条「为什么现在才接」的需求，而这 10 条
一条都拿不出（4–7 个月零消费者）。反方向的成本是真实的：只加定义不接线会被扩面后的守卫
拦住，要恢复必须先有读它的看板或告警——**恢复成本低且受守卫驱动**，留着则是持续误导
（"这个面已经有指标"正是 `#1958` 死锁四周无人发现的同款认知）。

**删除的破坏面为零**：这些序列自始至终恒为零（Counter/Gauge 无写入）或根本不出现在
`/metrics`（未实例化的子序列不产出样本）。即使仓外存在查询它们的看板，那面板显示的也是
空/零——删除不会让任何现有视图从"有数据"变成"没数据"，只会让"以为有数据"变成诚实。

### 2. 守卫扩到全指标面，且**存量红为 0**

`tests/test_alert_metric_producers.py` 的判据作用域从「告警表达式引用到的指标」扩到
`backend/` 的**全部**定义，入口是 `unproduced_definitions()`（PR #2313 建，本单与仪表板面
共用）。断言是**空集**，不是带解释的允许清单——扩面前把存量清干净，避免「第一次红灯被当
噪声忽略」。

配套两条防线：

- **反向失效守卫**：断言 `collect_definitions()` 解析出的定义数 ≥ 50——扫描根变更或解析
  整体失效时「零红」没有意义，必须先红；
- **负向对照**：合成树里只加定义、无人写入 → 判据红；补一个跨文件写入点 → 判据绿
  （红绿双向，证明断言不恒真）。

### 3. 「已埋点、零消费方」的反向轴：**裁决为不立**

`stability_api_request_duration_seconds` 有生产者、零消费方（既无告警也无面板），但**不**为它
立反向棘轮。理由不是成本而是判据性质：

- 生产者是**代码事实**（AST 可判、可红绿双向验证）；消费方是**产品决策**（要不要建面板、
  口径是什么）。把决策做成 red gate，会把「先埋点、后建面板」这一正常次序判红；
- 强制「必须有告警或面板」会逼出为过门禁而建的无意义面板——正是 PR #2313 明确拒绝做的事
  （不为未经裁决的指标固化看板资产）；
- 该实例的再评估条件已由 PR #2313 的 Note 写明（错误率口径、`histogram_quantile`、
  `$endpoint` 变量三问）。

**重议条件**：出现**第二个**「已埋点零消费方」实例，或某条指标的存在本身开始误导
（例如被当成 SLO/容量依据）。届时形态应是 **advisory 报告**（run_gates 非阻塞输出），
不是红灯。

## Alternatives

- **给 10 条逐条补埋点**：需要为每条给出「现在才接」的需求，拿不出；且其中 3 条已有活指标
  覆盖同一问题（派发/租约冲突/SAQ 时长），补了就是重复计数来源。否。
- **保留定义 + 进豁免清单**：那就是重建一张手维护清单——`#1258` 的过期豁免正是本单与
  `#2286` 要收口的形态。否。
- **只扩判据、不清存量**：守卫会背 10 条红落地，第一次红灯被当噪声忽略，守卫等于没上。否。
- **反向轴做成 advisory**：形态可接受，但当前只有 1 个实例、且它的裁决入口是产品问题而非
  治理问题；先写清重议条件，不做没有 owner 的报告。否（条件成熟再立）。

## Verification

- `python -m pytest tests/test_alert_metric_producers.py tests/test_grafana_dashboard_contract.py tests/test_prometheus_alerts_contract.py -q`
  → **15 passed**（分析器取 PR #2313 的版本；本单未合入前，main 上的分析器会把
  `stability_build` 误判为无生产者——那正是 #2313 修的假阳性，故本单必须在其后落）。
- **全指标面棘轮红向反证**：临时在 `backend/core/metrics.py` 追加
  `dead_probe_total = Counter('stability_dead_probe_total', ...)` →
  `test_every_definition_has_a_producer` **红**，报
  `backend/core/metrics.py:916 有定义、无任何写入点也无跨文件引用`；删除探针即绿。
- 删除后模块可导入、指标名不再存在：
  `python -c "from backend.core import metrics; assert not hasattr(metrics, 'plan_run_active')"` 等 10 条逐一断言通过。
- `python -m pytest backend/tests/api/test_metrics_lock_wait_gauges.py -q` → **2 passed**。
- `python scripts/run_gates.py check:quick` → **10 gates 绿**。

## Revisit

- **被删的 5 条"看起来会有用"的指标**（租约时长、任务时长、心跳延迟、活跃 run 数、宽限持锁
  租约数）若将来需要：**定义与接线同 PR** 补回，扩面后的守卫会拦住只加定义不接线的改动；
  数据面本身没有损失（租约/任务时长在 DB，可随时补算）。
- **反向轴**：重议条件见 Decision 3（第二个实例 / 出现误导），形态应是 advisory。
- **`Info` 样本名后缀**（`stability_build` ↔ `stability_build_info`）的归一由 PR #2313 落地；
  本单的扩面依赖它，若其归一被后续改动削弱，全指标面棘轮会立刻在 `stability_build` 上报红——
  即本单的红灯同时是那条判据的回归网。
