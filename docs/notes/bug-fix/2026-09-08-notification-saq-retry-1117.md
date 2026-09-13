# 通知通道失败需可 SAQ 重试且不重发成功通道（#1117）

Status: implemented
Class: bug-fix

## Decision

`dispatch_notification` 不再吞掉通道发送异常：汇总失败后抛
`NotificationDeliveryError`，供 `send_notification_task` 上抛重试。
每通道结果写入 `NotificationLog.context.channel_delivery`；重试时按
`run_id` 等身份复用同一 log，跳过 `status=ok` 的通道。
`dispatch_notification_async` 仍吞异常，保持 fire-and-forget。

**#1826（审计 F05）无 Run 事件身份闭环**：`DEVICE_OFFLINE` 的上下文没有
`run_id`，旧读取分支直接跳过去重，导致 SAQ 重试重发已成功通道。生产异步
入口现在在排队前为无 Run 的事件生成 `notification_event_id`，同一标识随
SAQ kwargs 重试、同步/异步入队失败的线程池降级和 NotificationLog.context
一起传递。调用者的原始上下文不被修改；两次独立离线转换取得不同标识与
队列 key，显式复用标识则代表同一事件。

查找既有通知改为数据库按事件身份过滤，不再扫描「最近 30 条」：无 Run
事件按 UUID，Run 事件保持 event_type/run_id/task_id/device_serial 语义与
既有队列 key。投递结果以 P4 的 `NotificationDelivery` 为权威，历史 JSON
为读取回退（见 [P4 note](../feature/2026-09-11-notification-delivery-p4-1167.md)）。
无需新增数据库表或 Redis 业务账本；#625 已合入并包含于本单基线，不覆盖其
Alertmanager 跳转上下文改动。

涉及：`backend/services/notification_service.py`、`backend/tasks/saq_tasks.py`；
测试见 `test_notification_service.py`。

## Alternatives

- 仅打日志不 raise：SAQ 仍不会重试（本 bug）。
- 每次重试新建 log：站内通知重复，且难做幂等跳过。
- Redis 投递账本：多一处状态，JSON 上下文已够。
- 用 device_serial 或 `run_id=None` 作为恒定身份：会把之后真正的新离线事件
  误当重试；无 Run 事件必须由生产入口生成独立身份。
- 只扩大最近日志扫描数量：仍存在高通知流量下回退成「新通知」的窗口。

## Verification

#1826 本次验证（临时 testcontainers Postgres、网络发送桩化）：

- `python -m pytest backend/tests/services/test_notification_service.py backend/tests/services/test_notification_delivery.py backend/tests/tasks/test_saq_tasks.py backend/tests/api/test_notifications.py -q`
  → **114 passed**。
- `python scripts/run_gates.py check:quick` → **7 gates 通过**；变更文件 Ruff
  与 diff check 通过。
- 新回归把异步生产入口的 kwargs JSON 序列化后交给真实 SAQ task 两次执行：
  首次 A accepted/B transient，夹入 35 条同类型通知，重试只发 B；同设备新
  离线事件仍重新发 A/B。另覆盖三种入队失败降级的身份保留与 Run 事件跨
  30 条日志的重试查找。

以下为 #1117 原有验证入口：

- `test_dispatch_raises_when_channel_send_swallows_would_have_succeeded`
- `test_dispatch_skips_already_ok_channels_on_retry`
- `TESTING=1 JWT_SECRET_KEY=test-secret python -m pytest
  backend/tests/services/test_notification_service.py -q`

## Revisit

- 未携带事件标识的旧无 Run 队列记录无法从 device_serial 可靠区分新事件与
  重试；本修复为新入队事件建立身份，不伪造历史身份或按设备吞掉通知。
- 独立投递事实表已由 P4 落地；更强并发发送互斥或外部渠道去重另行设计。
  ADR-0036 D7 的 at-least-once 与 UNKNOWN 可能重复的边界不变，不承诺
  exactly-once。
