# step_trace 静默回收（#3061）

Status: implemented
Class: bug-fix

## Decision

在 `recycler.recycle_once` 增加 Pass **#2c**：`RUNNING` job 若最新 `step_trace` 为终态
（非 `STARTED`）且 `original_ts` 早于 `STEP_TRACE_STALL_SECONDS`（默认 3600s），则 CAS
转 `UNKNOWN` 并写 `step_trace_stall_detected` 审计。补「心跳/coordinator 仍 fresh、但
pipeline 长期无 step 活动」的回收缝——典型于 `timeout_seconds=NULL` 的 watcher 计划。

## Alternatives

- **门禁侧**「活跃 = 最近 N 分钟有活动」：不释放租约，host 仍被 zombie job 占位。
- **计划侧**补 `timeout_seconds`：需逐计划运维，不覆盖已卡死实例。
- **仅 patrol_stall**：依赖 `last_patrol_heartbeat_at`；#3061 实测 12.5h 无 step 仍
  `RUNNING`，heartbeat 路径未覆盖。

## Verification

- `pytest backend/tests/scheduler/test_recycler.py -k step_trace_stall`
- `pytest backend/tests/core/test_job_timeout_config.py`

## Revisit

- 阈值是否应随 `patrol.interval_seconds` 分级（现统一 env）。
- 生产 `.87` job 42219 需 reconciler grace 后释放租约；本 PR 只负责 recycler 检出。

## 2026-09-22 回归收窄（#3146）：patrol 心跳新鲜 ⇒ 豁免

**问题**：本判据上线（`#3062`）后当天 15:00:45 出现**批量假阳**——`plan 55`（周期回归-monkey-1h）
479 个健康 job 中 738 条被标 `UNKNOWN`，进而 释放 lease → agent `lease_lost` → 任务 abort
→ `recovery_resume` 与在飞拆除竞态 → `PermitDenied` 1ms 秒败（`pipeline_engine` 该分支不写
`_last_step_error`，报文成为无证据的 `step failed in init: check_device`）。后果：链首 r510
被卡（77 FAILED + 102 ABORTED、179 台设备），`plan55.next_plan_id=54` 不触发 ⇒ **15:20 窗口整段丢失**。

**根因**：`ADR-0022` 让 patrol 成功步**按设计不写 step_trace**（`suppress_success_trace=True`），
因此 monkey 类计划巡航期的「trace 静默」是设计形态；巡航 55–75 min 必然越过 3600s 阈值
（实测：r510 job 46990 间隔 3617s；对照健康窗 r501 job 45624 间隔 3926s）。

**收窄**：`_patrol_liveness_since_cutoff()` —— `last_patrol_heartbeat_at` 在 cutoff 之后即豁免
（collector 与 CAS 两处同判据）。**不**用 `last_execution_heartbeat_at`/`last_progress_at` 豁免：
按本条 Alternatives 的初衷，「执行器/心跳可能仍在而 pipeline 已死」，用它们豁免会回退 #3061 能力；
patrol 心跳陈旧/NULL 的真僵尸仍会被抓（patrol 测试与 step_trace 真僵尸用例双覆盖）。

**Verification（本次）**：

- `pytest backend/tests/scheduler/test_recycler.py` **31 passed**；其中 `-k step_trace_stall` 7 例：
  巡航健康不标（新增）/ 真僵尸（非 patrol 段）仍标 / collector 双层判据直测（新增）/
  CAS race-in 不标（新增）/ STARTED 不标 / 新鲜 trace 不标 / 阈值 0 关闭；
- **变异检查**：删 collector 豁免 → 直测用例红；删 CAS 豁免 → race 用例红；恢复后全绿。

**运维留痕**：止血期以 `STEP_TRACE_STALL_SECONDS=0`（`.env.backend`，2026-09-22 17:48 重启生效）
临时关闭该检测；**本修复部署后应移除该覆盖**（恢复 3600 默认），否则 #3061 能力仍处关闭态。

