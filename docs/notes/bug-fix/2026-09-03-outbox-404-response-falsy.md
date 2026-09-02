# Outbox 404 ack 被 `requests.Response` 真值踩空（#729）

日期：2026-09-03 · 类型：bug-fix · 关联：#729

## 决定了什么

`OutboxDrainThread` 用 `e.response.status_code if e.response else None`
取状态码。`requests.Response.__bool__` 等价于 `ok`，**4xx/5xx 恒为假**，
于是 404 被当成「无 response」→ `bump_terminal_attempt` 永重试，永不
`ack_terminal`。

修复：改为 `e.response is not None`。回归用例用真实 `Response(status=404)`
（断言 `not response`）锁住该脚枪。

## 放弃的备选

- 仅运维清 outbox、不改代码：风暴会在下次幽灵 job 时复发。
- 控制面把幽灵 complete 改成 200：掩盖 Agent 契约，且无法区分真 404。

## 如何验证

```bash
python -m pytest backend/agent/tests/test_terminal_outbox_drainer_metrics.py -q
```

生产：4 台 host（15.70/81/84/83）对 `job_terminal_outbox` 中
`acked=0` 且 `last_error` 含 404 的行执行 ack；控制面
`POST .../complete` 404 速率应跌至近 0。

## 何时重议

若其它 Agent HTTP 路径仍用 `if response:` 取 `status_code`，同一脚枪复开。
