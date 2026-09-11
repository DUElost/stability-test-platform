# terminal outbox 对齐死信上限（#742）

Status: implemented
Class: bug-fix

## Decision

为 `job_terminal_outbox` 增加与 `log_signal` / `step_trace` 同构的死信语义：

1. schema：`dead_letter` 列（新建表 + 幂等 ALTER）；
2. `get_pending_terminals` / `count_pending_terminals` 过滤 `dead_letter=0`；
3. `bump_terminal_attempt` 返回新 attempts；`OutboxDrainThread` 在
   `attempts ≥ 10` 时 `mark_terminal_dead_letter` 并停发；
4. heartbeat 上报 `terminal_outbox_dead_letter_total`，控制面写入 `host.extra`。

404 仍直接 ack（#729）；非 404 / 冲突保留路径共用 `_bump_or_dead_letter`。

## Alternatives

- **仅对 5xx 死信、409 冲突永不封顶**：冲突同样会无限打 `/complete`，与本单目标冲突，弃用。
- **删行代替死信**：失去审计与回放面，弃用。

## Verification

```bash
/home/debian13/stability-test-platform/.venv/bin/python -m pytest \
  backend/agent/tests/test_terminal_outbox_dead_letter_742.py \
  backend/agent/tests/test_terminal_outbox_drainer_metrics.py \
  backend/tests/api/test_heartbeat_outbox_metric.py -q
```

## Revisit

- 若需运维回放 terminal 死信，可仿 `replay_log_signal_dead_letter` 另开单。
- `_MAX_ATTEMPTS=10` 与 log_signal 对齐；若要按环境覆盖再议。
