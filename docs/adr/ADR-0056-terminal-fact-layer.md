# ADR-0056：终态事实层——签名行、DLE 摘要、设备健康时间线与 MTBF 分母

- 状态：**Proposed** v0.1（2026-09-26 起草，待 owner 裁决 §7 裁决点）
- 优先级：P1
- 目标里程碑：M7
- 日期：2026-09-26
- 决策者：owner（待裁）；起草：平台研发组
- 归属域：n/a（平台侧长期事实层在语义归属表尚无对应概念；裁决后新增 key 并补登）
- 标签：数据资产, 保留, 签名, MTBF, 可靠性结论, 分层保留
- 关联：[#3326](https://github.com/DUElost/stability-test-platform/issues/3326)（本 ADR 的立项单）、
  [#3230](https://github.com/DUElost/stability-test-platform/issues/3230) G4 / G13、
  [ADR-0053](./ADR-0053-center-storage-event-dedup.md)（中心存储的内容与引用；D5「提单外部标识、工具版本、输入清单与结果摘要是长期事实」、D7「提单单位是故障发生」）、
  [ADR-0049](./ADR-0049-audit-log-retention-layering.md)（分层保留先例）、
  [ADR-0045](./ADR-0045-risk-level-vocabulary.md)（S/A/B 风险词表）、#718（MTBF 套件能力，不同层）、#717（提单相似度去重，不同层）
- 版本记录：v0.1（2026-09-26）首次起草，Proposed

## 1. 背景

### 1.1 问题定性

平台的最终产品是**可信的可靠性结论**。运行越久，原始行越多，但三类「越用越值钱」的资产至今为零：
已知问题库（同一问题在哪些 run、设备、版本上复发）、版本回归曲线（按 `build_version` 对比）、
MTBF / 千小时故障率的历史。它们目前只能从原始行临时反推，且原始行的寿命挂在 PlanRun 生命周期上。

生产 `PLAN_RUN_RETENTION_DAYS=36500`（owner 确认有意保史），所以问题**不是数据被删**，而是**资产没有形成**：
保史只是 env 取值，不是 schema 承诺；一旦将来为控制库增长而开启清理，现在仅存于运行行里的事实会随 run 一起消失。

### 1.2 事实（`main@8dc9d82` 读码）

| 事实 | 出处 |
|---|---|
| 平台模型没有「问题签名」列；签名只存在于厂商 merge 工具产出的 xls | #3326 正文；`backend/models/` 全量 |
| `plan_run.build_version` 有列，只在全部设备同版本时写入（分歧走 `run_context`），读方近零 | `backend/models/plan_run.py:55`；`backend/services/plan_dispatcher_sync.py:773-811` |
| DLE（`device_log_event`）行挂在 run 上：`plan_run_id` / `job_id` 为 `ON DELETE SET NULL`，`host_id` 为 `ON DELETE CASCADE` | `backend/models/device_log_event.py:38-52` |
| 用例结果随 run 级联删除 | `backend/models/case_result.py:23-24`（`plan_run_id` / `job_id` `ON DELETE CASCADE`） |
| 设备运行时长只能从租约行反推（`acquired_at` / `renewed_at` / `expires_at`），无聚合 | `backend/models/device_lease.py:26-39` |
| 提单结果（`issue_keys`）记在 `jira_run`，与具体发生、签名无结构化关联 | `backend/models/jira_run.py:38-61` |
| 保留清理按 run 年龄整树删除 | `backend/scheduler/cron_scheduler.py:414-560`、`:755` `run_retention_cleanup` |

### 1.3 与 ADR-0053 的边界

ADR-0053 管**中心存储**里证据的内容身份、引用与回收；本 ADR 管**平台库**里长期保留的**结论性事实**。
两者的接口是 ADR-0053 D7 定义的「故障发生」身份：本 ADR 的签名行与 DLE 摘要以它为主键之一，不另造身份。

## 2. 决策（提案）

### D1：长期事实与运行行分层，事实行不随 run 删除

新增一组「事实表」，对 `plan_run` / `job_instance` **只存引用 id、不建级联 FK**（或 `ON DELETE SET NULL`），
使保留清理删除运行行时，事实行保留、引用变空但事实自身完整。事实行只追加、不改写（更正以新行 + 作废标记表达）。

### D2：签名行（已知问题库的载体）

表 `issue_signature`（一个签名一行）与 `issue_occurrence`（一次发生一行）：

- `issue_signature`：平台侧归一签名（厂商签名原文 + 归一化键 + 平台 + 事件类型）、首次 / 最近出现时间、关联 JIRA key（可多个）。
- `issue_occurrence`：签名 id、ADR-0053 D7 的发生身份、`serial`、`build_version`、`project_key`、`specialty_key`、`plan_run_id`（引用）、观测时间、S/A/B 风险级（ADR-0045）。
- 写入时机：merge / 提单链产出签名时追加；平台不按签名压制新发生（与 ADR-0053 D7 规则 3 一致）。

### D3：DLE 摘要长存

表 `dle_summary`：每条 DLE 一行，字段 = 类型、子类型、`serial`、平台、检测时间、发生身份、签名 id（可空）、`plan_run_id` 引用。
原始 `device_log_event` 行与中心存储证据继续按各自保留规则；摘要行不随之删除。

### D4：设备有效运行时长（MTBF 分母）

按日聚合表 `device_runtime_daily`（`serial`、日期、有效运行秒数、所属 run 数），由租约行（`acquired_at` → 释放 / 过期）推导，
每日任务增量写入。MTBF / 千小时故障率 = 发生数（D2） / 运行小时（D4），按 `build_version`、`project_key`、专项切片。

### D5：保留契约从 env 值升格为分层规则

- 事实层（D2–D4）：**永久保留**，不受 `PLAN_RUN_RETENTION_DAYS` 影响。
- 运行层（`plan_run`、`job_instance`、`step_trace`、`case_result`、原始 DLE）：保留期仍由 env 决定，但删除前必须确认对应事实行已写入（清理任务的前置检查）。
- 证据层（中心存储）：归 ADR-0053 D5 / #3230 G4 的 N / M 参数，不在本 ADR 定值。

### D6：`build_version` 口径

`build_version` 从「全设备同版本才写列」改为按设备记录在 `issue_occurrence` 与 `device_runtime_daily` 上；
`plan_run.build_version` 保持现状（run 级展示用），不作为统计口径。

## 3. 备选与否决

| 方案 | 结论 | 理由 |
|---|---|---|
| 维持现状，需要时从原始行现算 | 否决 | 保留期一旦开启即不可恢复；现算跨全表，规模上不可行 |
| 签名去重交给厂商工具 / JIRA | 部分采纳 | 「是否并单」仍归厂商与 JIRA（ADR-0053 D7）；平台只保存发生与签名的事实 |
| 在 `plan_run` / `device` 上加汇总列 | 否决 | 汇总会随 run 删除，且无法按版本、项目切片 |
| 引入独立数仓 / OLAP | 暂不采纳 | 当前规模单库足够；事实表形态先定，将来可同步到外部数仓 |

## 4. 影响

- 迁移：新增四张表（additive），存量回填为可选步骤（从现存运行行尽力回填，标注「回填」来源）。
- 写路径：merge / 提单链、DLE 入库、每日聚合任务各加一处写入；读路径新增按签名、版本、设备的查询 API 与页面。
- 保留清理增加「事实行已写入」前置检查（D5）。
- 与 #3327（库增长观测）互补：事实表体量小、增长可预测，应纳入其监测。

## 5. 分期

1. 事实表 + 写路径（D1–D3），不含回填。
2. 运行时长聚合（D4）与 MTBF 查询。
3. 保留清理前置检查（D5）；视需要回填存量。
4. 前端：已知问题库、版本回归曲线、MTBF 视图。

## 6. Revisit

- ADR-0053 D4 强身份落地后，复核 `issue_occurrence` 的发生身份是否由「推断」转为「证明」。
- 单库事实表超过既定体量（由 #3327 的基线给出）时，评估外部数仓同步。

## 7. 裁决点（待 owner）

| # | 问题 | 起草取向 | 备选 |
|---|---|---|---|
| F1 | 事实层与运行层是否分表 | 分表，事实行不级联删除（D1） | 在运行表上加「永不删除」标记 |
| F2 | 签名归一化由谁做 | 平台侧保存原文 + 归一化键，归一化规则按厂商适配器实现 | 只存厂商原文，不归一化 |
| F3 | MTBF 分母口径 | 租约持有时长（D4） | 心跳在线时长；或按脚本上报的有效测试时长 |
| F4 | 存量是否回填 | 可选，尽力回填并标注来源 | 不回填，只从上线日起累计 |
| F5 | 保留清理前置检查的严格度 | 缺事实行即跳过删除该 run 并告警 | 只告警不阻断 |
