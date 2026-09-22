# ADR-0049：audit_logs 分层保留期与裁剪

- 状态：**Accepted** v1.0（2026-09-19 owner 裁决，四问全采推荐项；裁决记录见 #2741 评论）
- 优先级：P2（无界增长有真实出口缺口，但增速 29 行/小时、表 26.7 万行——非紧急，先裁决防漂移）
- 目标里程碑：M7
- 日期：2026-09-19
- 决策者：owner（DUElost）
- 标签：audit_logs, retention, 保留期, 分层, 裁剪, #2694, #2741, #777-R08
- 关联：[#2694](https://github.com/DUElost/stability-test-platform/issues/2694)
  （原始报告——已由 PR #2699 落地索引 + facets 有界两项后关闭，无界增长前提未变）/
  [#2741](https://github.com/DUElost/stability-test-platform/issues/2741)（裁决与实现载体）/
  [#2789](https://github.com/DUElost/stability-test-platform/issues/2789) /
  [ADR-0050](./ADR-0050-install-evidence-retention-alignment.md)（`install_agent*` 落 business 90d 与 ADR-0044 D3
  「持久证据」的视界对齐——已裁决：丙案「明示接受 90d 视界」，Accepted v1.0）/
  #777 R-08（风险总表对应行）/
  [ADR-0020](./ADR-0020-plan-step-one-shot-migration.md)（`plan_migration_audit`「保留 ≥6 个月后转归档」先例）

## 1. 背景与问题

`audit_logs` 只增不减：生产 266,882 行（2026-09-18 只读实测，PR #2699），
增长由**事件驱动**（89% 行集中在 8/3–8/4 `terminal_payload_conflict` 两天爆发）
叠加稳态 29 行/小时。#2694 的 4 项方案只落地 2 项（两个索引、facets top-N
有界），**无界增长没有任何出口**；全后端对本表零裁剪实现、零保留期取向
记录（#777 R-08 列了风险但无专属裁决单元）。本 ADR 补上取向裁决，
#2741 承接实现。

## 2. 决策

**D1 分层保留期（取向裁决）**：按 `action` 分三层，env 可调
（`AUDIT_LOG_{SECURITY,BUSINESS,SESSION}_RETENTION_DAYS`，默认 180/90/30）：

| 层 | 默认 | 覆盖 | 依据 |
|---|---|---|---|
| security | 180d | `login_failed` / `login_locked` / `change_password(_failed)` / `token_{issued,failed,locked}` / `refresh_rejected` / `register` / `user_{created,updated,deleted,active_toggled}` / `host_key_replaced` / `initial_admin_created` | 安全事件链（谁对账号/凭据做了什么）；对齐 ADR-0020 六个月先例。`register`＝公开自助注册（账号创建），与 `user_created` 同族，v1.1 由 business 纠正归此（#3108） |
| business | 90d | **默认桶**：未显式归入另两层的全部 action | 业务诊断价值随时间衰减；`terminal_payload_conflict` 爆发行**不例外**（裁决问 3） |
| session | 30d | `refresh` / `login`（成功）/ `logout` | 例行会话心跳，取证价值最低；生产占 3%、dev 占 62%，短保留在两种环境都成立 |

与 #2694 临时观测分组的差异：`token_issued` 在 #2694 的 dev 观测里被并入
「会话类」，本裁决归 **security**——它记录「谁取得了凭据」，是账号失陷
调查的事实链，不应随会话心跳短保留。

**D2 默认桶封闭性**：business 层的删除谓词是 `NOT IN (session ∪ security)`
而非枚举「business 有哪些」——新增 action 自动落 90d 保留网，不需要登记
动作。代价与义务：**新增安全相关 action 必须显式登记进
`backend/scheduler/audit_log_cleanup.py::SECURITY_ACTIONS`**，漏登的后果是该
事件只留 90d。该义务由本 ADR 成文；不设自动门禁（词表演化低频，自动化
误报成本高于收益）。

**D3 会话类同表 + 不折叠（裁决问 2）**：会话类事件继续与业务审计同表、
同生命周期；不做拆表迁移、不做 UI 折叠（#2694 原方案第 4 项）——生产
会话类仅 3%，拆表/折叠收益不抵成本；dev 62% 的观感问题由 30d 短保留期
自然消化。

**D4 裁剪形态（裁决问 4，工程侧）**：APScheduler 单例 sync 作业
（`audit_log_cleanup_job`，`AUDIT_LOG_RETENTION_INTERVAL_SECONDS` 默认 1h，
**0=停用**——事故取证期冻结裁剪的逃生阀），三层各自按 id 升序批量删
（`AUDIT_LOG_RETENTION_BATCH_SIZE` 默认 5000/层/tick）。`audit_logs` 无
FK 子树、无引用闭包——结构上不存在 #1827「引用闭包保留整批 ⇒ 饿死
后续清理」的形态，因此**不**复用 `run_retention_cleanup` 的锁序机器。
`*_RETENTION_DAYS=0` 与 PlanRun 家族同义：cutoff=now，该层全量到期
（清库/排障场景）。

**D5 裁剪自身写审计 + 自免环**：仅当 tick 确有删除时写**一条**汇总审计
（`action=audit_retention_pruned`，details 含各层删除数与配置天数），落
business 默认桶——该行**不豁免**于裁剪谓词，90d 后自清，不形成「审计行
阻止自身裁剪」的自持环。每 tick 一条而非逐行：审计的是治理动作（删了
多少），不是给被删行陪葬。指标：`stability_audit_retention_pruned_total`
（按层分桶），与 PlanRun 的 `stability_retention_*` 家族平行、不共用。

## 3. Alternatives（未采纳）

- **统一 N 天（90/180）**：实现与心智最简，但安全事件与例行心跳同寿命——
  要么浪费存储留心跳，要么丢安全事实链；分层的全部成本只是一个分类集合。
- **按月子表/分区**（#2694 建议项之一）：26 万行/约 90MB 远够不着分区
  收益阈值，纯增迁移与查询复杂度；表显著增大时再议（Revisit）。
- **UI 折叠会话类**：被 D3 否决（生产前提不成立，PR #2699 实测）。
- **停写 `refresh`**：#2694 自列不建议——削弱安全事件链完整性。
- **复用 `run_retention_cleanup`**：其复杂度全部服务于 FK 闭包与行锁窗口，
  对无闭包的 `audit_logs` 是纯负担（见 D4）。

## 4. Consequences

- 正面：无界增长获得出口；dev/生产两种构成下的噪声都被短保留期消化；
  可观测（计数器 + 汇总审计）、可停用（interval=0）、分层可调。
- 负面/代价：**>90d 的业务审计不可查**（含 `terminal_payload_conflict`
  爆发行——裁决明示不例外）；安全相关新 action 有登记义务（D2）。
- 首轮运行会裁掉存量到期行（数千至数万行量级，按 3×5000/tick 的速率
  数个 tick 内完成），属预期的一次性收缩。

## 5. Revisit

- 表再次显著增大（如 >5M 行）或查询性能退化 ⇒ 重议分区方案（索引侧的
  同类出口见迁移 `a1b2c3d4e5f7` docstring 的 CONCURRENTLY 备注）。
- 安全/合规提出更长保留要求 ⇒ 调 `AUDIT_LOG_SECURITY_RETENTION_DAYS`
  即可，不需结构变更。
- `SECURITY_ACTIONS` 漏登导致安全事件仅 90d 首次实际发生时 ⇒ 重议 D2
  的自动化登记校验（如从 routes 的 admin-only 写面反推安全 action 集）。
