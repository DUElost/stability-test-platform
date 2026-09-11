# pre-engine 终态路径补记 PATROL barrier 到达（#801）

Status: implemented
Class: bug-fix

## Decision

INIT→PATROL barrier 的 `barrier_total` 来自 `plan_run_host_total_job_count`
（PRH 快照全员数），但三条 **pre-engine 终态路径**从不进入引擎、也就不调
`arrive_at_barrier`：

- `job_runner` pipeline 校验失败（直接 complete+release）；
- `job_runner` `JobStartupError`（watcher 启动失败）；
- `main` executor submit 失败（未起 worker）。

任一作业在此终态后，同 wave 健康 peer 会等满 `barrier_timeout_seconds`
（缺省 600s）→ 集体 `barrier_timeout` FAILED——**1 台小故障放大为整波失败**
（23 台 wave 中 1 台即触发）。

修复：新增 `job_runner._arrive_patrol_barrier_preengine(run, coordinator,
job_id)`——条件与 `PipelineEngine._barrier_enabled` 对齐（coordinator 非空
+ PRH id + total ≥2 + `STP_PHASE_BARRIER_ENABLED`），执行
`set_barrier_total → arrive_at_barrier →（最后到达）advance_phase`；三处
pre-engine 终态路径在 complete 后、release 前调用。异常仅告警，不影响
终态路径本身。

## Alternatives

- **控制面 barrier_total 下修为实际存活 claim 数**——放弃：需要控制面感知
  Agent 侧启动结果（新协议面），且 claim 后失败在 Agent 侧天然可得；
- **缩短 barrier_timeout**——放弃：默认长窗口是给慢 init 的合法预算，缩短
  会把「慢但健康」变成失败；到达计数才是正确性缺口。

## Verification

- **反例实证**：回退 job_runner/main 实现保留测试 → 3 用例失败（helper 与
  计数不存在）；修复版全绿；
- 新增用例（`test_fencing_token.py` +3）：helper 计数/最后到达者 advance /
  无 peer、无 coordinator 时不动作；**run_task_wrapper 校验失败集成**
  （PIPELINE_REQUIRED 路径 arrive 被调）；
- `backend/agent/tests/` 全套 **1634 passed**（2m33s，含进程树/watcher/
  barrier 既有回归）；
- `check:quick` 与 PR 门禁：见 PR 描述。

## Revisit

- 引擎内 init 失败（pipeline_engine `_arrive_phase_barrier("PATROL")`）既有
  行为不变；本单补齐引擎外的三处，至此「所有会终态的作业都计入到达」闭合；
- 若未来新增 pre-engine 终止路径（如 claim 后协议校验失败），同样要接
  `_arrive_patrol_barrier_preengine`（可考虑收敛为统一的 pre-engine 终态
  收尾函数）。
