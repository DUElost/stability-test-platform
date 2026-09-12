# recycler PENDING 同 tick 跳过失败作业（#791）

Status: implemented
Class: bug-fix

## Decision

1. **EXECUTING_STEP CAS / 心跳脱钩**：已由 #991（PR #1090）落地——CAS 复核
   `last_execution_heartbeat_at`，不再绑 `updated_at`。本单不重复改写。
2. **PENDING 热旋**：`recycle_once` 的 PENDING `while True` 增加本 tick
   `pending_skip_ids`。`_mark_pending_timeout` / 延后聚合失败后将该 `job.id`
   排除出后续批次查询，避免 savepoint 回滚后仍 PENDING 的作业被无限重选、
   拖死整个 recycler tick。

## Alternatives

- **连续失败 N 次后 break**：与 skip 集合等价但更难测「同 job 不重入」；否决。
- **聚合失败也回滚终态**：与 #1172 defer_aggregation 契约冲突；否决。

## Verification

- `python -m pytest backend/tests/scheduler/test_recycler.py::test_pending_timeout_skips_mark_failure_same_tick backend/tests/scheduler/test_recycler.py::test_running_timeout_cas_does_not_overwrite_concurrent_heartbeat -q`
- `python scripts/run_gates.py check:quick`

## Revisit

若 RUNNING 批循环出现同类「失败仍候选」热旋，可复用 skip 集合模式；当前
RUNNING 路径 CAS 失败即 `return False`，不重抛进 while。
