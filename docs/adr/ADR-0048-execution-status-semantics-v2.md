# ADR-0048：执行状态语义 v2——移除 run 级测试通过率判定

- 状态：**Accepted** v1.0（2026-09-18 owner 裁决；三个分叉点逐项确认，见 §3）
- 优先级：P1（周期回归链已因阈值判定在生产随机断链：run 428 失败率 5.2% 恰好越过 5% 线判 FAILED，链中段终止）
- 目标里程碑：M7
- 日期：2026-09-18
- 决策者：owner（平台负责人）
- 标签：状态机, PlanRun, failure_threshold, PARTIAL_SUCCESS, 通过率, #815, #783, #2734
- 关联：[#2734](https://github.com/DUElost/stability-test-platform/issues/2734)（实施载体）
  / [`07-execution-protocol.md`](../design/07-execution-protocol.md) §2（#815「PlanRun 绿 ≠ 测试结论」——本 ADR 是其落实）
  / [ADR-0022](./ADR-0022-patrol-heartbeat-aggregation.md) D8（阈值含退避语义，被本 ADR 废止）
  / [ADR-0026](./ADR-0026-plan-execution-scaling.md) §failed_only/aborted 推导（判定轴被本 ADR 收窄）
  / ADR-0020 §链式触发（SUCCESS/PARTIAL 集合的存量兼容由本 ADR 声明）
- 版本记录：v1.0（2026-09-18）裁决并即时生效：D1–D3 全部落定

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

## 2. 决策

### D1 终态语义：完成即绿，abort 才红（owner 确认）

```
全部 job 落终态（COMPLETED/FAILED/ABORTED，UNKNOWN 不落终态——不变）
  ├─ aborted > 0 或 abort_requested → FAILED   （保留 #783 裁决：人工中止=未覆盖计划）
  └─ 其余 → SUCCESS                             （无论设备失败台数）
```

- 设备级失败是**数据事实**：`failed_job_count`、job 状态、`result_summary.failed`、
  StepTrace/日志/产物全链保留，供「哪些设备在什么场景下死机」这一稳定性平台核心
  问题消费；
- #1591-④ 里程碑豁免（flash 后步骤失败不判整批红）随阈值分支一并删除——新语义下
  「设备失败批次」根本不再判红，豁免失去作用对象；
- 链触发 `TRIGGERABLE_TERMINAL_STATUSES` 保留 `{SUCCESS, PARTIAL_SUCCESS}`：前者是
  新语义常态，后者只服务存量行（见 D2）。

### D2 PARTIAL_SUCCESS：保留枚举、不再产出（owner 确认）

- 枚举值、DB 原生 enum、状态机吸收态、终态集合六处字面量、retention/auto_archive/
  dedup_scan/recycler/广播等全部消费面**原样保留**——存量历史行（retention 窗口内）
  仍可正确渲染与回收，零数据迁移；
- 聚合器从此**永不产出**新 PARTIAL_SUCCESS 行（回归测试钉住）；
- 不 drop 枚举值：给「历史行状态」做事后改写（归 SUCCESS 或 FAILED 都是编造事实），
  且 DB enum 迁移成本与风险不成比例。存量行随 retention（3 天）自然消失后，该值
  成为纯惰性词汇。

### D3 展示面：删「通过率」指标，保留失败台数事实（owner 确认）

删除（「通过率」作为指标不再存在）：
- `result_summary.pass_rate` 键、`PlanRunJobsSummaryOut.pass_rate`、
  `ChainNodeOut.pass_rate`、导出 markdown「Pass rate」行、
  `stability_plan_run_pass_rate` histogram（Prometheus 序列退役）；
- `plan.failure_threshold` / `plan_run.failure_threshold` 列（alembic drop + CHECK
  约束 + 迁移预检校验项 + schema_sync 基线）；API Create/Update 字段（
  `extra=forbid` 下前后端必须同 PR 移除）；Plan 编辑表单「失败阈值」输入、run
  详情「失败阈值」meta、前端各「通过率」列/文案；
- `plan_run_watcher_summary` 的 `abnormal_rate > failure_threshold → exceeded`
  标志（唯一残留阈值消费）。

保留/替换（同一事实换指标名）：
- Dashboard「通过率趋势 30d」→「失败设备数趋势」、「方案成功率排行」→「按 plan
  失败设备数排行」（SQL 由 completed/total 改 SUM(failed_job_count)）；
- run 列表「通过率」列 → 「失败设备」列（数据源 `failed_job_count` 已存在）；
- job 级 FAILED/ABORTED 状态、五个 run 计数器、`test_case_result` 结果层——全部
  不动（事实记录，非阈值判定）。

## 3. 方案对比（裁决时逐条过）

| 候选 | 结论 |
|---|---|
| 任一设备失败即 run FAILED | 否决：与「无通过率要求」矛盾，链断得比今天更频繁 |
| 取消三态只留完成态 | 否决：abort→FAILED 是 #783 有效裁决；状态机/retention/归档门控重做成本与收益不匹配 |
| drop PARTIAL_SUCCESS 枚举（含迁移） | 否决：存量行改判=编造历史，DB enum 迁移成本放大 |
| 阈值放宽到 0.08–0.1 | 否决：治标；噪声基线随 fleet 组成漂移，本质是轴错不是数错 |

## 4. 影响与不做的事

- 在飞 run：部署重启后由新聚合接管，无状态改写；历史 run 零迁移；
- 通知二分 `RUN_COMPLETED/RUN_FAILED` 结构不动，`RUN_FAILED` 自本 ADR 起只表示
  执行链故障（abort/派发失败），不再被设备噪声触发；
- 不动：#783 abort 语义、UNKNOWN 不落终态、scan/upload/merge 管线（其门控只判
  `status==FAILED`，与新语义自洽）、Jira/报告事实字段、#2651 收缩准入的比例上限
  问题（正交，另案）；
- `docs/operations/new-specialty-onboarding-runbook.md`「失败率熔断线」表述、
  `honor-flash-runbook` 示例 JSON 同步删除阈值字段。

## 5. 重议触发条件

- 出现需要「批次成败门控」的真实流程（如出厂判定）——应在**结果层**
  （`test_case_result`/报告）建判据，不回灌 run 状态；
- `PARTIAL_SUCCESS` 存量行清零后若消费面字面量成为噪音，可另立轻量单收口
  （仅删字面量，不动枚举）；
- abort→FAILED 若与「完成即绿」产生新冲突（如 abort 前的设备失败），随 #783
  复审一并裁决。
