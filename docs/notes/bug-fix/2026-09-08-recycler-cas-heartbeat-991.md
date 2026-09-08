# RUNNING 超时 CAS 改按执行/协调心跳复核（#991）

Status: implemented
Class: bug-fix

## Decision

`_mark_running_timeout` 去掉 `updated_at == observed` CAS，改为与裁决同源的
存活信号复核：

- `EXECUTING_STEP`：`execution_state` 仍为 EXECUTING_STEP，且
  `last_execution_heartbeat_at` 仍空或 ≤ deadline
- `WAITING_*` / `PATROL_SLEEP`：状态仍在等待集，且 coordinator 心跳仍缺失或
  仍 stale
- 未上报：`execution_state` 仍未知

`recycle_once` 按子状态传入对应 deadline。这样 extend-batch「钉住
updated_at、只刷执行心跳」后，健康 Job 不会被旧 `updated_at` 误打 UNKNOWN。

涉及：`backend/scheduler/recycler.py`；回归于
`backend/tests/scheduler/test_recycler.py`。

## Alternatives

- CAS 同时要求 `updated_at` 与执行心跳：续租钉住字段后仍误杀，不解决根因。
- 让 extend-batch 改 bump `updated_at`：与 #288「租约不得伪造存活」冲突。

## Verification

- `test_running_timeout_cas_does_not_overwrite_concurrent_heartbeat`
- `test_running_timeout_cas_not_vetoed_by_updated_at_only_refresh`
- `test_postgresql_heartbeat_wins_against_stale_timeout_candidate`
- `TESTING=1 JWT_SECRET_KEY=test-secret python -m pytest
  backend/tests/scheduler/test_recycler.py -q`

## Revisit

若 WAITING 无 PlanRunHost 行与「有行但 heartbeat NULL」需区分审计，可拆
reason 字符串；当前 CAS 语义上均视为 coordinator 时钟未报活。
