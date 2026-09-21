# ADR-0048：执行状态语义 v2——移除 run 级测试通过率判定

- 状态：**Accepted** v1.1（2026-09-20 owner 重议 [#2982](https://github.com/DUElost/stability-test-platform/issues/2982)：恢复 PARTIAL_SUCCESS 真实产出与通过率展示；阈值轴废止的 v1.0 内核不变。v1.0 = 2026-09-18 裁决，三个分叉点逐项确认，见 §3）
- 优先级：P1（周期回归链已因阈值判定在生产随机断链：run 428 失败率 5.2% 恰好越过 5% 线判 FAILED，链中段终止）
- 目标里程碑：M7
- 日期：2026-09-18（v1.1 修订：2026-09-20）
- 决策者：owner（平台负责人）
- 标签：状态机, PlanRun, failure_threshold, PARTIAL_SUCCESS, 通过率, #815, #783, #2734, #2982
- 关联：[#2734](https://github.com/DUElost/stability-test-platform/issues/2734)（v1.0 实施载体）
  / [#2982](https://github.com/DUElost/stability-test-platform/issues/2982)（v1.1 实施载体）
  / [`07-execution-protocol.md`](../design/07-execution-protocol.md) §2（#815「PlanRun 绿 ≠ 测试结论」——本 ADR 是其落实）
  / [ADR-0022](./ADR-0022-patrol-heartbeat-aggregation.md) D8（阈值含退避语义，被本 ADR 废止）
  / [ADR-0026](./ADR-0026-plan-execution-scaling.md) §failed_only/aborted 推导（判定轴被本 ADR 收窄）
  / ADR-0020 §链式触发（v1.0 声明 SUCCESS/PARTIAL 集合为存量兼容；v1.1 恢复为常态产出集）
- 版本记录：
  - v1.0（2026-09-18）裁决并即时生效：D1–D3 全部落定（移除 run 级通过率阈值判定轴）。
  - v1.1（2026-09-20）实际使用反馈重议（#2982）：D1 补三态分支（黄=有设备失败，
    **永不因设备失败判红**的内核保留）；D2 恢复 PARTIAL_SUCCESS 真实产出；
    D3 通过率作为纯展示派生回归（列表页通过率列 + Dashboard 通过率趋势图）。
    `failure_threshold` 判定轴、后端 `pass_rate` 字段与 histogram 维持废止不回灌。

## 1. 背景：通过率轴不属于稳定性平台的执行链

本平台的目的是给设备施加高压与典型用户场景，**设备死机、重启、掉线是正常且预期的
现象**；对测试通过率无验收要求。现行 `plan.failure_threshold`（默认 0.05）把
「设备失败台数占比」混入 PlanRun 终态判定，造成三类系统性问题：

1. **链随机断**：常态 fleet 在 568 台规模下 init 噪声失败率 4.6–5.2%（2026-09-18
   实测 run 426=2.8%、427=4.6%、428=5.2%），阈值判定使链在中段因一次正常抖动判
   FAILED、`TRIGGERABLE_TERMINAL_STATUSES` 拒绝续链——周期回归的完整性无保证；
2. **告警信噪比**：`RUN_FAILED` 通知混入设备级失败噪声，稀释了「调度层真故障」
   这一 run 级红应有的全部含义；
3. **文档-实现矛盾**：#815 已在 `07-execution-protocol.md` 明确 run 状态描述
   执行链结果而非测试结论，但阈值分支使 run 状态事实上成为「批次测试结论」——
   两个轴叠在同一字段上。

判定主体唯一：`plan_run_aggregation.py::_resolve_plan_run_status`（阈值分支
`failed_only / total <= failure_threshold → PARTIAL_SUCCESS` + #1591-④ 里程碑
豁免分支）。除 `plan`/`plan_run` 两表列、API 校验、快照组装、展示链外，无其他
判定者。

### v1.1 重议动因（2026-09-20，#2982）

v1.0 上线后的实际使用反馈：plan-runs 列表页的「通过率」显示、以及与它关联的
「部分成功」状态显示承载了必要的浏览信息量，红绿二值使「完成但有设备失败」在
一眼视图上不可区分。复核结论：v1.0 修复的是「越线判红→断链」这条**轴**，
三态显示本身不是断链根因——黄色 PARTIAL 属于 `TRIGGERABLE_TERMINAL_STATUSES`
（不断链）、不属于通知红侧（不触发 RUN_FAILED）。owner 据此对三个分叉点逐项
重议（裁决记录见 [#2982](https://github.com/DUElost/stability-test-platform/issues/2982)）：
① 语义层次=恢复 PARTIAL_SUCCESS 真实产出（无阈值
线）；② 通过率数字=前端派生（后端字段不回归）；③ 显示范围=run 列表页 +
Dashboard 趋势图。

## 2. 决策

### D1 终态语义：完成不判红，abort 才红（v1.1 三态，owner 确认）

```
全部 job 落终态（COMPLETED/FAILED/ABORTED，UNKNOWN 不落终态——不变）
  ├─ aborted > 0 或 abort_requested → FAILED           （保留 #783 裁决：人工中止=未覆盖计划）
  ├─ failed_only > 0 → PARTIAL_SUCCESS                 （v1.1：完成但有设备失败=黄；台数多少都不断链、不判红）
  └─ 其余 → SUCCESS                                    （完成且无设备失败=绿）
```

- **「设备失败永不判红」内核不变**（v1.0 裁决保留）：黄由 `failed_only > 0` 的
  二值事实判定，**不是** `failed_only / total <= failure_threshold` 比例线——
  原断链根因是「越线判红→`TRIGGERABLE` 拒绝续链」，阈值轴维持废止，判定输入
  只有 `failed_only/aborted/abort_requested` 三个计数；
- 红/黄/绿仍是**执行链**语义而非测试结论（#815 立场不变）：黄=「链完整跑完、
  过程有设备失败」，红=「链未完整跑完（abort/派发故障）」；测试结果判定仍归
  结果层（`test_case_result`/报告）；
- 设备级失败是**数据事实**：`failed_job_count`、job 状态、`result_summary.failed`、
  StepTrace/日志/产物全链保留，供「哪些设备在什么场景下死机」这一稳定性平台核心
  问题消费；
- #1591-④ 里程碑豁免不回潮——其作用对象（阈值判红）不存在；
- 链触发 `TRIGGERABLE_TERMINAL_STATUSES = {SUCCESS, PARTIAL_SUCCESS}`（v1.0 即
  保留）：v1.1 起为常态产出集，黄色 run 正常续链。

### D2 PARTIAL_SUCCESS：恢复真实产出（v1.1 修订，owner 确认）

- v1.0「保留枚举与全部消费面、停止产出」的设计使 v1.1 的恢复为**零迁移、
  零消费面改动**：枚举值、DB 原生 enum、状态机迁移边、终态集合字面量、
  retention/auto_archive/dedup_scan/recycler/广播等消费面在 v1.0 原样保留，
  历史行与新行为同一心智模型；
- 存量历史行仍零改写（v1.0 理由继续成立：事后改写历史状态=编造事实）；
- 不重新引入任何阈值参数或豁免参数（防回潮测试钉住判定函数签名，见实施 PR）。

### D3 展示面：通过率回归为纯派生显示（v1.1 修订，owner 确认）

v1.1 恢复（全部是**派生显示**，不新增任何判定）：
- run 列表页「通过率」列：前端由 `result_summary.completed / total` 派生
  （整数百分比，非终态行显示「—」），与 v1.0 的「失败设备」列**并存**；
- Dashboard「运行通过率趋势 (30d)」图：恢复端点
  `GET /stats/plan-run-pass-rate-trend`（口径=按日对终态 run 的
  `completed/total` 求日均），与 v1.0 的「失败设备数趋势」「按 plan 失败设备数
  排行」**双口径并存**；
- 黄色「部分成功」徽标与状态筛选 tab：前端消费面 v1.0 即保留，随 D2 恢复直接
  生效。

维持废止（v1.0 裁决，v1.1 明确**不回灌**）：
- `failure_threshold` 判定轴整体：`plan`/`plan_run` 两表列（保持 drop）、API
  Create/Update 字段、Plan 编辑表单「失败阈值」输入、run 详情「失败阈值」meta、
  watcher `exceeded` 标志——判红轴不回来；
- 后端通过率字段：`result_summary.pass_rate` 键、`PlanRunJobsSummaryOut.pass_rate`、
  `ChainNodeOut.pass_rate`、`stability_plan_run_pass_rate` histogram——数字唯一
  权威源=`completed/total`，不造第二份可漂移的副本（逐键形状测试钉住）；
- `plan_run_watcher_summary` 的 `abnormal_rate` 仅作事实指标。

本轮未纳入（Revisit，见 §5）：导出 markdown「Pass rate」行、run 详情/链侧栏的
通过率 meta、PARTIAL 的通知文案区分。

## 3. 方案对比（裁决时逐条过）

v1.0（2026-09-18）：

| 候选 | 结论 |
|---|---|
| 任一设备失败即 run FAILED | 否决：与「无通过率要求」矛盾，链断得比今天更频繁 |
| 取消三态只留完成态 | 否决：abort→FAILED 是 #783 有效裁决；状态机/retention/归档门控重做成本与收益不匹配 |
| drop PARTIAL_SUCCESS 枚举（含迁移） | 否决：存量行改判=编造历史，DB enum 迁移成本放大 |
| 阈值放宽到 0.08–0.1 | 否决：治标；噪声基线随 fleet 组成漂移，本质是轴错不是数错 |

v1.1（2026-09-20，owner 分叉点①的三个候选）：

| 候选 | 结论 |
|---|---|
| 仅展示层派生黄徽标（DB 终态仍二值） | 否决：显示语义与数据脱钩，链/归档/监控仍无法区分「完成有失败」 |
| 三态产出 + 恢复阈值线（黄线分绿黄） | 否决：阈值即 v1.0 判定的「轴错不是数错」，且需回灌已 drop 的列/表单 |
| **三态产出、无阈值线（failed_only>0 即黄）** ← v1.1 裁决 | 采纳。已知代价：568 台常态噪声失败率 4.6–5.2%，多数轮次将为黄，黄色区分度有限——接受为恢复三态显示的固有代价 |

## 4. 影响与不做的事

- 在飞 run：部署重启后由新聚合接管，无状态改写；历史 run 零迁移；
- 通知二分 `RUN_COMPLETED/RUN_FAILED` 结构不动，`RUN_FAILED` 自本 ADR 起只表示
  执行链故障（abort/派发失败），不再被设备噪声触发；
- 不动：#783 abort 语义、UNKNOWN 不落终态、scan/upload/merge 管线（其门控只判
  `status==FAILED`，与新语义自洽）、Jira/报告事实字段、#2651 收缩准入的比例上限
  问题（正交，另案）；
- `docs/operations/new-specialty-onboarding-runbook.md`「失败率熔断线」表述、
  `honor-flash-runbook` 示例 JSON 同步删除阈值字段。

v1.1 增量影响：

- PARTIAL 归通知 `RUN_COMPLETED` 侧（与 v1.0 前同形），模板文案不含失败台数——
  黄/绿通知区分列为 Revisit；
- `auto_archive`（`{SUCCESS, PARTIAL_SUCCESS}`）与链续跑自 v1.1 起重新覆盖黄色
  新行——即 v1.0 前的行为面，属预期；
- 风险看板 `results.py` 的 `success_runs` 仅计 `status==SUCCESS`（历史口径，
  v1.0 前即如此），黄色 run 不计入该 KPI——维持不动，列为 Revisit。

## 5. 重议触发条件

- 「批次成败门控」（如出厂判定）若出现，判据仍应在**结果层**
  （`test_case_result`/报告）建立——v1.1 边界声明：`PARTIAL_SUCCESS` 是显示/
  归档/链语义，不是成败门控（不断链、不判红、不告警）；任何「失败率越线→
  FAILED/断链」的回归都须重开 ADR 裁决；
- v1.0 的「存量行清零后收口消费面字面量」条目失效——v1.1 起 PARTIAL 为活跃
  词汇，消费面无噪音可言；
- abort→FAILED 若与三态语义产生新冲突（如 abort 前的设备失败），随 #783
  复审一并裁决；
- Revisit 面（v1.1 未纳入，轻量单即可，无需重开本 ADR）：导出 markdown
  「Pass rate」行、run 详情/链侧栏通过率 meta、通知文案黄绿区分。
