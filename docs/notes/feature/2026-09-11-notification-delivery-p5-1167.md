# 通知投递：队列边界与背压参数化（#1167 P5，台账收尾）

Status: implemented
Class: feature

## Decision

#1167 台账 **P5**（ADR-0036 **D3 / D4**），也是该台账的最后一批。P1–P4 已合入
main（#1383/#1390/#1396），本批基于 main 独立成 PR。

**D4/D5 — 参数单一事实源（不再两处各写一版）**：

- `NOTIFICATION_SAQ_RETRIES` **默认派生**自 D5 的策略对象
  （`DEFAULT_RETRY_POLICY.max_attempts = 3`）——SAQ 入队参数不再写死魔数，
  调整改 `STP_NOTIFY_SAQ_RETRIES`（非法值告警回落、下限保护 1）；
- `NOTIFICATION_SAQ_TIMEOUT_S`（env `STP_NOTIFY_SAQ_TIMEOUT_S`，默认 120s）：
  SAQ 单次 job 上限需覆盖「一次投递串行经过全部通道」的最坏耗时
  （通道 deadline × 通道数）——3 通道 × 15s 有富余；通道级 deadline 在
  P4 已参数化（WEBHOOK/DINGTALK/SMTP）；
- `_int_env` / `_channel_deadline` 统一保留「非法值告警 + 安全回落」语义。

**D4 — 背压链定案（文档化 + 测试锁定）**：

```text
dispatch_notification_async
  ├─ SAQ 可用 → enqueue（send_notification_task，retries=策略上限，
  │             timeout=saq 上限）——唯一 retry owner（P3）
  ├─ SAQ 不可用/Redis 故障/enqueue 异常 → 有界降级线程池（#1122：
  │             BACKGROUND_POOL_SIZE=8 / BACKGROUND_POOL_MAX_QUEUE=200）
  │             └─ 池满 PoolQueueFullError → 丢弃 + warning（可观测）
  └─ 两级都不可用 = 丢弃（best-effort：不阻塞调用方、不无限积压）
```

- 「不无限积压」由两段边界共同保证：SAQ 侧 Redis 队列 + 降级池信号量上限；
- 丢弃点均留 warning 日志（`notification_dropped_queue_full` /
  `notification_enqueue_unavailable_fallback_pool`），指标化归 ADR-0011。

## Alternatives

- 只文档不参数化：`retries=3`/`timeout=120` 留在代码里与
  `RetryPolicy.max_attempts` 各写一版，正是 P5 要避免的「两版参数」；
- 给降级池再加一层通知专属队列：与 #1122 的共享有界池重复，且
  ADR-0026 有界队列先例已选定共享池方案；
- 在 SAQ 侧配置 max_jobs 全局背压：属 SAQ/Redis 部署层（ADR-0018 域），
  不在通知契约层重复定义。

## Verification

- `pytest backend/tests/services/test_notification_service.py` +
  `test_notification_delivery.py`：46 passed——入队参数取自常量（key/retries/
  timeout/args）/ 默认重试派生自策略对象 / `_int_env` 非法值与下限回落 /
  背压链末端（SAQ 不可用 + 池满）丢弃且**留下可观测告警**；
- `pytest backend/tests` 全量：见 PR 验证节；ruff 全绿。

## Revisit

- 台账 P1–P5 至此全部落地；ADR-0036 §5 的 7 项次序状态均为「已完成/由本
  台账承接且完成」，无需再改 ADR 文本（Accepted v1.0 的裁决未变）；
- 丢弃/降级频率的指标与告警阈值归 ADR-0011（后续可观测性批次）；
- `exhausted`（SAQ 重试耗尽）终态回写仍留 P4 的 Revisit（on_finish 钩子），
  与本批无关；
- JSONB `channel_delivery` 双写退役时点见 P4 Note（读取侧全切表后）。
