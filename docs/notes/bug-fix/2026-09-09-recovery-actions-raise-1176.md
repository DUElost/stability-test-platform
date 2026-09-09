# recovery sync 执行器吞错不得静默报成功（#1176）

Status: implemented
Class: bug-fix

## Decision

`backend/agent/main.py` 两处「吞错」改为**上抛**，让上层
`run_recovery_sync_if_needed` 落到 except → 返回 False → heartbeat 保留
`_pending_reconnected_serials`，由心跳周期自动重试（#1009 语义真正闭合）：

1. `execute_recovery_actions_impl` 的 outbox 优先 flush：`drain_sync()`
   失败原先 catch 后直接 `return`——本轮 RESUME/CLEANUP/ABORT_LOCAL 全部
   跳过，但不抛给上层；`run_recovery_sync_if_needed` 据此仍记成功、清重连
   标记，恢复被推迟到下次物理插拔。
2. RESUME 的 `resume_job` submit 失败原先 catch 记日志后继续并打成功日志——
   `register_active_job` 已把 job 标活跃、续租持有 fencing token，worker 却
   未启动，job 成僵尸直到 patrol 兜底。现上抛保留重连标记；下一轮重试时
   register 幂等、resume 重发即可自愈。

涉及：`backend/agent/main.py`；测试见 `test_recovery_executor.py` 三条新增
（drain 失败上抛且不继续 CLEANUP、resume submit 失败上抛、`execute_actions`
上抛时 `run_recovery_sync_if_needed` 返回 False）。

## Alternatives

- 让执行器返回状态码由调用方判定：所有调用点（启动、reconnect、patrol）与
  测试均按「异常=失败」约定，上抛改动面最小；返回码方案需改全部调用点。
- submit 前回滚 register：删除 active 记录会使后端不再重发 RESUME，反而
  丢恢复机会；保留注册 + 上抛重试 + patrol 兜底更稳。

## Verification

- `pytest backend/agent/tests/test_recovery_executor.py`：30 passed
  （TESTING=1 + sqlite DATABASE_URL 收集；原有 28 例语义不变）。

## Revisit

register-then-submit 的成对原子性仍是理论缺口（submit 仅在线程池关闭等
极端时序失败）；现状靠「上抛重试 + patrol 兜底」双保险，若线上出现该
时序可再评估先 submit 后 register 或补偿注销。
