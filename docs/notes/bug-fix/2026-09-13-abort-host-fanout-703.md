# #703 abort host control 扇出限流

Status: implemented
Class: bug-fix

## Decision

在已合入的 refresh 三分、abort 推送坍缩、持锁缩短之上，收口 Revisit
「Host control 扇出若上百 host 仍需限流」：

1. **`schedule_agent_control_fanout`**：将 N 个 agent `control` emit 合并为
   **一次** `run_coroutine_threadsafe` 提交；协程内按 host 逐个 `await sio.emit`，
   每 8 个 `await asyncio.sleep(0)` 让出主循环。
2. **`abort_plan_run`**：host 扇出改走该助手；dashboard 的汇总
   `JOB_STATUS` / `PLAN_RUN_STATUS` 仍用 `schedule_emit`（O(1)）。

终态语义与 per-host `job_ids` 作用域不变。

## Alternatives

- 仍逐 host 调 `schedule_emit` + 调用方 `time.sleep`：阻塞 abort 线程且
  不减少主循环排队次数。
- Agent 侧广播 room：需协议/订阅面改动，超出本切片。
- ACK 批量化 / `lock_timeout`：另一半 Revisit，本单不触。

## Verification

- `TESTING=1 JWT_SECRET_KEY=test-secret python -m pytest backend/tests/realtime/test_emit_agent_control.py backend/tests/services/test_plan_run_abort_aggregator_race.py backend/tests/api/test_plan_run_abort_api.py::test_abort_control_emit_scoped_per_host -q`
- `python scripts/run_gates.py check:quick`

## Revisit

- abort ACK 风暴下的 `on_job_terminal` 锁排队仍可能占池——可评估
  `lock_timeout` 或批处理 ACK；
- 与 #1759/#1791/#1878 一并现场回归后可评估关闭 #703。
