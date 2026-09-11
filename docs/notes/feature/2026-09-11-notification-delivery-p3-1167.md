# 通知投递：retry owner 收敛 SAQ + 投递级幂等（#1167 P3）

Status: implemented
Class: feature

## Decision

#1167 台账 P3（ADR-0036 **D4 / D7**）：重试 owner 收敛到异步队列（SAQ）+
投递级幂等为成对硬约束。本批基于 P1/P2 分支（#1383，堆叠 PR；P1/P2 合入后
GitHub 自动改基到 main）。

**D4 — retry owner 唯一（SAQ）**：

- `dispatch_notification_async` 从「线程池 fire-and-forget」改为**入 SAQ
  队列**（`enqueue_sync("send_notification_task", key=…, retries=3,
  timeout=120)`）——生产投递的三条调用方（PlanRun 终态 / RISK_HIGH /
  DEVICE_OFFLINE）语义不变（不抛、不阻塞），但失败从此有唯一重试者；
- 队列不可用（SAQ 未运行 / Redis 故障 / enqueue 异常）→ **降级 best-effort
  线程池直达**并告警（无重试语义，仅保可用性）——降级路径独立成
  `_dispatch_notification_via_pool`，与既有 #1122 队列满拒绝语义兼容；
- SAQ 去重键：`notif:{event_type}:{run_id}:{device_serial}`（同一事件的重复
  终态/心跳不重复入队——D7 去重键形态之一）。

**D7 — 投递级幂等**（重试不重发已 `ACCEPTED` 通道）：

- `dispatch_notification` 的通道跳过条件补 `outcome == ACCEPTED`（兼容 P1 前
  的旧记录 `status == "ok"`）——`channel_delivery` 以 channel_id 为每通道
  去重键，重试只补失败通道；
- 实测语义：SAQ 重试是同一 job 重跑 → 已 ACCEPTED 通道不再发（本批用真实
  dispatch + 真实 DB 的用例锁定，**不 mock dispatcher**——issue 特别要求）。

**测试口径**（P3 验收「真实吞异常路径，不得只 mock dispatcher」）：
`test_send_notification_task_real_path_idempotent_retry` 只 mock HTTP 层：
首次 ok 通道 ACCEPTED + bad 通道 Timeout→UNKNOWN 抛出；第二次真实重跑只对
bad 通道发出 HTTP 请求（`assert calls == [".../bad"]`）。

## Alternatives

- 把 fire-and-forget 直接删掉、队列失败即丢弃：可用性回退（Redis 抖动时通知
  全丢）；降级路径保留「尽力投一次」且明确无重试语义（告警可观测）；
- 由调用方各自 enqueue（plan_run_aggregation / heartbeat 各自引 saq_worker）：
  三处重复且降级逻辑会漂移——收口在 `dispatch_notification_async` 一处；
- 幂等键用「上一次是否成功」判定可否重试：违反 D7「去重键必须与投递结果
  无关」——本实现以 channel_id 为键、状态只决定是否跳过。

## Verification

- `pytest backend/tests/services/test_notification_service.py`：3 新例——入队成功
  不再走线程池（断言 key/retries/args）、SAQ 不可用降级线程池、enqueue 异常
  不外溢且降级；
- `pytest backend/tests/tasks/test_saq_tasks.py`：真实路径幂等重试用例通过；
- 调用方回归：`test_plan_run_aggregation_shared.py` / `test_heartbeat.py` /
  `test_notification_delivery.py` 等 79 passed；
- `pytest backend/tests` 全量：见 PR 验证节；ruff 全绿。

## Revisit

- **P4**（D6）：投递事实落库形态（扩 `notification_logs` 还是新建单数表）——
  当前仍在 `NotificationLog.context.channel_delivery`（JSONB，1:N 以 channel_id
  键表达）；落库形态定案后本批的 `record()` 结构直接迁移；
- **P5**：队列边界与背压参数化（#1122 已在 D3 字段层落地；D4 队列边界与
  `enqueue_sync` 的 timeout/retries 数值统一在 P5 校准）；
- 降级路径的频率可观测性（入队失败指标）属 ADR-0011 指标域，随后续批次；
- 若 SAQ 去重键与实际终态重放需求冲突（例如同 run 重复失败需再次通知），
  调整 key 形态即可，不影响 D7 的投递级幂等。
