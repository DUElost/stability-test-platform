# #703 abort 推送坍缩：逐 job JOB_STATUS → 汇总一条

Status: implemented
Class: bug-fix

## Decision

`abort_plan_run` 在 commit 后原先对每个 ABORTED / abort_requested job 各
`schedule_emit(job_status)`，再发一条 `plan_run_status`。#327（~497 job）实测
数百次 `run_coroutine_threadsafe` 打满事件循环，ASGI/认证路径饥饿——与前端
「过载误踢登录」同因链的后端半边。

改为：

- 有变更时只发 **一条** 汇总 `JOB_STATUS`（`abort_bulk=true` + counts），供
  dashboard results 节流刷新；
- 保留一条 `PLAN_RUN_STATUS`（详情页已靠它做 devices/timeline/logs 全量
  invalidate，与逐条 JOB_STATUS 等价）。

Host 侧 `control` abort 扇出不变（~30 host，量级可接受）。

## Alternatives

- 仅限流/延迟逐条 emit：仍 O(n) 入队，过载窗口只是拉长。
- 提高 DB `pool_size`：不解决事件循环阻塞；且掩盖写放大。
- 前端已合入的 refresh 三分（PR #1759）单独不够——后端仍可打满循环。

## Verification

- `TESTING=1 JWT_SECRET_KEY=test-secret python -m pytest backend/tests/services/test_plan_run_abort_aggregator_race.py -k emit_collapse -q`
- `python scripts/run_gates.py check:quick`

## Revisit

- Host control 扇出若上百 host 仍需限流；
- abort 长事务占连接（commit 前）与池观测另单；
- 与 #1759 一并后可评估是否关闭 #703。
