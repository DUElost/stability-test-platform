# 钉钉业务 errcode 不得当发送成功（#1120）

Status: implemented
Class: bug-fix

## Decision

`_send_dingtalk` 在 `raise_for_status()` 之后解析 JSON：`errcode != 0`
抛 `RuntimeError`。`send_to_channel` / 测试通道接口因此走失败路径（502），
不再把 HTTP 200 + 业务拒绝报告为成功。

涉及：`backend/services/notification_service.py`；测试见
`test_notification_service.py`。

## Alternatives

- 仅改测试接口展示：生产 dispatch 仍假成功。
- 返回 `(ok, err)` 元组：调用面更大，现有 raise 契约已够。

## Verification

- `test_send_dingtalk_raises_on_business_errcode`
- `test_send_to_channel_dingtalk_surfaces_business_error`
- `TESTING=1 JWT_SECRET_KEY=test-secret python -m pytest
  backend/tests/services/test_notification_service.py -q`

## Revisit

若企业微信等通道有同类业务码，可抽共享 `raise_if_provider_error`。
