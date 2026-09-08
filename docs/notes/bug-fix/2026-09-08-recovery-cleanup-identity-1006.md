# R07-F05 落地：worker 退出补偿清理设备占位（#1006）

Status: implemented
Class: bug-fix

## Decision

`ABORT_LOCAL` / `CLEANUP` 恢复动作（`backend/agent/main.py`）先
`abort_local_job(jid)`（异步取消）再 `lease_renewer.clear_fencing_token(jid)`
（返回的 device_id 被丢弃）。worker 随后退出走 `JobRunnerState.release` →
`lock_deregister` → `_cleanup_after_job_exit`：其中
`clear_fencing_token_if_current` 依赖 fencing 映射取 device_id——映射已被
恢复动作清除时返回 None，`_active_device_ids.discard(device_id)` 不执行，
**占位残留**：同设备新 Job 在 claim 检查被 `skip_device_busy` 永久跳过，
通常需 Agent 重启才恢复。

修复（`backend/agent/job_runner.py` 单点）：

1. `JobRunnerState.release` 用**自带的 device_id 参数**（worker 上下文快照，
   三个调用点均已传入真实值）在 `active_jobs_lock` 内补偿
   `active_device_ids.discard(device_id)`——不依赖 lease_renewer 映射存亡；
2. 补偿仅当 `is_current_worker`（`active_job_tokens` 仍匹配本 worker）：
   被新 fencing_token 取代的旧 worker 释放时不得清除新 worker 的设备占位
   （既有 `test_job_runner_state_replacement_token_*` 语义）；
3. 直接对 state 持有的 `active_device_ids` 集合（main 与 fixture 均传真
   引用）在锁内操作：复用现有锁、无跨锁窗口、幂等；`device_id_deregister`
   回调字段保留不动。

时序语义：恢复动作清 fencing 映射（停续租）时机不变；占位保留至 worker
真实退出（release）才清——若 runner.cancel 失败 worker 未死，占位残留
（防同设备双驱动，与现状一致）。

## Alternatives

- **恢复动作（ABORT_LOCAL/CLEANUP）改用 `clear_fencing_token` 返回值立即
  discard 占位**——放弃：cancel 是异步的，worker 可能仍在跑；过早清占位
  会让同设备新 Job 与残活 worker 双驱动设备（占位正是为此存在）；
- **`_cleanup_after_job_exit` 改为不依赖映射**——放弃：它没有 device_id
  参数，改动面更大；release 已有完整快照，单点补偿即闭环；
- **经 `device_id_deregister` 回调补偿**——放弃：回调内部再取同一把
  `threading.Lock`（不可重入）会死锁，需挪出锁外又引入跨锁窗口；state
  直接持有集合引用，锁内 discard 最简且原子。

## Verification

- **反例实证**：回退实现保留测试 → 主验收用例 **1 failed**（占位残留）；
  修复版全绿；
- 新增用例（`backend/agent/tests/test_fencing_token.py`）：
  - `test_release_deregisters_active_device_when_fencing_map_already_cleared`：
    register 后 release（等价映射已清状态）→ 占位清空、active_job_ids 清空
    （验收 1/3）；
  - `test_superseded_worker_release_keeps_new_workers_device_placeholder`：
    旧 worker release 不动新 worker 同设备占位（guard）；
- `backend/agent/tests/` 全目录 **1433 passed**（2m15s，含 fencing/run_task
  wrapper/recovery executor 全套回归）；
- `check:quick` 与 PR 门禁：见 PR 描述。

## Revisit

- `test_recovery_executor.py` 现有断言只覆盖「清理方法被调用」，未串联
  release 级占位状态——补测落在 test_fencing_token.py（JobRunnerState 主场
  fixture 所在）；若日后做「恢复清理→worker 退出→同设备再次认领」的真
  run_task_wrapper 端到端，可扩展 run_task_wrapper 级用例；
- `device_id_register`/`device_id_deregister` 回调仍为未消费注入（main
  与 JobRunnerState 都直接操作集合）——保留，不做本单范围外的清理；
- #1003（进程树终止收敛，codebuddy 在窗）与 #1006 相邻但不同层：前者
  保证 worker 真死，后者保证 worker 死后占位清干净。
