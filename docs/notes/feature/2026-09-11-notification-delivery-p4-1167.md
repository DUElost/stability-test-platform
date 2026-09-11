# 通知投递事实落库：notification_delivery 表 + 每通道 deadline（#1167 P4）

Status: implemented
Class: feature

## Decision

#1167 台账 P4（ADR-0036 **D3 / D6**）。基于 P3 分支堆叠（P1/P2→P3→本批，
逐层合入后自动改基）。

**D6 — 投递结果落业务事实层（1:N 可表达、可查）**

实现设计选择（#1167 标注「ADR 未选死」的决策点）：**新建单数表
`notification_delivery`**，而非扩 `NotificationLog.context` JSONB：

- 每行 = 一次「通知 × 通道」投递；`unique(notification_log_id, channel_id)`
  使 1:N 关系显式可查、可按 channel/state/outcome 建索引（JSONB 键值对做不到
  聚合与索引，D6 的「可追溯、可补偿」在关系模型下才成立）；
- 状态词表（D6，小写、向前兼容）：`requested → dispatched → accepted /
  retrying → failed`——实现以结果类推导：`accepted`（渠道接受）/ `retrying`
  （可重试失败，等 SAQ）/ `failed`（永久拒绝）；未来 `delivered`（§2.3 挂起项）
  不改契约；
- `outcome` 列保留 P1/P2 的四分类（大写），与生命周期状态分层（v1.0 澄清的
  「两层词表」落地）；
- 重试在原行累加 `attempt_count`、覆盖 `last_error/updated_at`（不新增行，
  unique 约束兜底）；
- **双写过渡**：JSONB `channel_delivery` 仍写（旧读取方兼容），但**本表为权威
  ——幂等判定优先读表**；P4 之前的历史日志无表行时回落 JSONB（读取侧与
  `GET /notifications/logs/{id}/deliveries` 的 `source` 字段均如此标注）。

**D3 — 每通道显式 deadline**：`STP_NOTIFY_WEBHOOK_TIMEOUT_S` /
`STP_NOTIFY_DINGTALK_TIMEOUT_S`（默认 10s，非法值告警回落）；EMAIL 复用
既有 `STP_SMTP_TIMEOUT_SECONDS`（#1122）。数值属实现/配置（ADR 非目标）。

**可查 API**：`GET /notifications/logs/{log_id}/deliveries`——事实表为权威，
返回 `state/outcome/attempt_count/last_error/时间戳`；历史日志回落 JSONB。

## Alternatives

- 只扩 JSONB（不做表）：改动最小，但 D6 验收「投递状态可查（1:N 可表达）」
  与 ADR 意图（可追溯/可补偿）需要关系查询——按通道聚合、按结果筛选、外键
  到 channel 全都做不到；JSONB 保留为过渡兼容层；
- 独立「投递」聚合表 + 尝试明细表两层：当前需要的是「通知×通道」粒度的
  事实（尝试次数是计数器），两层模型对现状过度设计；出现需要逐次尝试审计的
  需求（如对账 SLA）再加明细表；
- 立刻停写 JSONB：会让任何未升级的读取方（含历史脚本）看到空投递信息；
  双写在 P5 或独立清理时点再收。

## Verification

- 服务层：事实行首次插入 / 重试累加 `attempt_count`（UNKNOWN→accepted 两轮，
  断言 state/outcome/次数）/ 事实表权威跳过（JSONB 空也不重发）/ 历史 JSONB
  回落跳过（无表行仍不重发）/ deadline env 覆盖与非法值回落；
- API：`deliveries` 端点 table/legacy 两个来源 + 404 + 匿名 401 与普通用户
  200（并入 `_LOG_ENDPOINTS` 参数化）；
- 迁移链：`tests/test_alembic_heads.py` + `test_alembic_upgrade.py` 通过
  （新头 `cc33dd44ee55`，down_revision `bb22cc33dd44`，串联于 suite_global_sha 之后）；
- `pytest backend/tests` 全量：见 PR 验证节；ruff 全绿。

## Revisit

- **P5**（队列边界与背压参数化）为台账最后一批；
- JSONB 双写的退役时点：等读取侧全部切到事实表（或 API 成为唯一读口）后可
  移除 `_persist_channel_delivery`；
- `exhausted` 状态（SAQ 重试耗尽）需要 on_finish 钩子回写——当前 `retrying`
  是最后一次可见态，SAQ 侧可观测性（重试次数）在 P5/指标批次补；
- 历史数据的 JSONB→表迁移（回填）不做——读取回落已覆盖；若需要按表做全局
  对账，另立数据脚本单。
