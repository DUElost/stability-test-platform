# 通知通道失败需可 SAQ 重试且不重发成功通道（#1117）

Status: implemented  
Class: bug-fix

## Decision

`dispatch_notification` 不再吞掉通道发送异常：汇总失败后抛
`NotificationDeliveryError`，供 `send_notification_task` 上抛重试。
每通道结果写入 `NotificationLog.context.channel_delivery`；重试时按
`run_id` 等身份复用同一 log，跳过 `status=ok` 的通道。
`dispatch_notification_async` 仍吞异常，保持 fire-and-forget。

涉及：`backend/services/notification_service.py`、`backend/tasks/saq_tasks.py`；
测试见 `test_notification_service.py`。

## Alternatives

- 仅打日志不 raise：SAQ 仍不会重试（本 bug）。
- 每次重试新建 log：站内通知重复，且难做幂等跳过。
- Redis 投递账本：多一处状态，JSON 上下文已够。

## Verification

- `test_dispatch_raises_when_channel_send_swallows_would_have_succeeded`
- `test_dispatch_skips_already_ok_channels_on_retry`
- `TESTING=1 JWT_SECRET_KEY=test-secret python -m pytest
  backend/tests/services/test_notification_service.py -q`

## Revisit

若需跨进程更强幂等，可把 `channel_delivery` 迁到独立表并以 SAQ job key
作唯一约束。
