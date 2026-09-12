# /jobs/{id}/complete 未知状态串不得静默落 FAILED

Status: implemented
Class: bug-fix

## Decision

`complete_job` 用 `_RUN_TO_JOB.get(raw, JobStatus.FAILED)`：未知串
（`SUCCESS`/`DONE`/拼写错误）会静默变成 FAILED，随后通过「终态集合」校验，
终态快照与聚合按真实失败处理且审计无异常标记。

修复（#779）：先 `strip().upper()`，再要求 `raw in _RUN_TO_JOB`；未知键直接
400 + `INVALID_TERMINAL_STATUS` + `requested_status`。映射到非终态（如
`RUNNING`）仍走原有 400。缺省 `status` 仍为 `FAILED`（合法终态）。

涉及：`backend/api/routes/agent_api.py`、`backend/tests/api/test_agent_routes.py`。

## Alternatives

- 仅记录 warning 仍落 FAILED：问题仍不可见；否决。
- 409 代替 400：与既有 `INVALID_TERMINAL_STATUS` 400 口径不一致；否决。

## Verification

- `pytest backend/tests/api/test_agent_routes.py -k complete_job -q`
- `python scripts/run_gates.py check:quick`

## Revisit

若 Agent 契约扩展新终态别名，须同步扩 `_RUN_TO_JOB`，否则会被本校验拒绝。
