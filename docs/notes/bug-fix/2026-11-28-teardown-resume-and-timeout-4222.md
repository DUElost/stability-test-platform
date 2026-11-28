# Issue #4222 Problem 2：teardown 阶段心跳失真与 UNKNOWN RESUME 回退 Init 修复

Status: implemented
Class: bug-fix

## Decision

针对 Issue #4222 问题 2（24h 长稳巡检进入 teardown 后约百台健康作业被 `job_running_timeout` 误判 `UNKNOWN`，随后 `RESUME` 报 `lifecycle init failed: init_return_check:not_executed` 并误锁设备），从“防误判进入 `UNKNOWN`”与“防 `RESUME` 回退到 Init”双侧闭环：

1. **避免 teardown 期间误触发 `job_running_timeout`**：
   - `PipelineEngine._run_step_with_permit` 在 `disarm_step` 结束释放许可的 `finally` 分支立即调用 `coord.publish_disarm_evidence(self._run_id, None)`，避免紧随其后的收集步（如 `powercycle_finish`）在 `WAITING_EXECUTION_SLOT` / `EXECUTING_STEP` 阶段继续夹带上一解除步的 `execute` 证据。
   - 服务端 `record_agent_coordinator_heartbeat` 对“Agent 端已切换至 `WAITING_EXECUTION_SLOT`/`EXECUTING_STEP` 且重放同一 `seq <= guard.seq` 解除 `execute` 证据”做兼容防御，不再把状态覆盖回 `EXECUTING_DISARM` 或跳过等待心跳时间戳刷新。
   - `running_heartbeat_timeout_seconds` 对已进入 teardown（`execution_state == EXECUTING_DISARM`、`disarm_guard`、`disarm_results` 或 `finishing_kind == teardown_disarm`）的作业退出 patrol 判定，恢复使用 `RUNNING_HEARTBEAT_TIMEOUT_SECONDS`。
2. **禁止已进入 teardown 的 `RESUME` 回退到 Phase 1 Init**：
   - `PipelineEngine` 不再在 `_run_patrol_loop` 结束时过早删除本地 `patrol_cycle_checkpoint`，而是于进入 `Phase 3: Teardown` 前写入 `phase="teardown"` 检查点（含 `termination_reason`、`footprint_families`、`disarm_results`），待 teardown 执行完成后再统一清理。
   - 控制面 `build_recovery_job_payload` 依据 `execution_state == EXECUTING_DISARM`、`disarm_guard`、`disarm_results`、`finishing_guard` 或 `StepTrace(stage="teardown")` 识别已进入 teardown 的恢复作业，随 `job_payload` 下发 `teardown_resumed=True`、`footprint_families`、`disarm_results` 与 `termination_reason`。
   - `job_runner` / `pipeline_runner` / `PipelineEngine._run_lifecycle` 合并本地与控制面 teardown 恢复状态，跳过 `Phase 1: Init` 与 `Phase 2: Patrol` 直接衔接 `Phase 3: Teardown`，对已成功解除的家族跳过重复 `disarm` 执行并保留其 `disarm_results`，同时继续执行收集步与重试未完成的解除步。

## Alternatives

- **仅在 Agent 侧保留检查点、不改控制面 payload**：若遇旧版检查点丢失或非 patrol 作业在 teardown 阶段触发 `UNKNOWN -> RESUME`，仍会因本地行缺失回退到 Init；由控制面结合数据库实证补充 `teardown_resumed` 提示后，新旧链路可协同兜底。
- **仅扩大 `PATROL_RUNNING_HEARTBEAT_TIMEOUT_SECONDS`**：只能延后误杀时间窗口，无法解决收集步被前序解除步证据劫持、等待心跳被跳过以及 `UNKNOWN -> RESUME` 回退 Init 的根因，因此未采用。

## Verification

- 在隔离工作树中通过 `scripts/run_pytest.py` 执行 Agent 单测（`test_teardown_resume_4222.py` 与现有 checkpoint/disarm 回归）与控制面单测（`test_teardown_resume_4222.py`、`test_job_timeout_config.py`、`test_agent_coordinator_heartbeat.py`），并运行仓库静态门禁与治理检查脚本；实际命令与结果见 PR 描述。

## Revisit

- 待后续 PR #4239（`B12-U5 atomic_commit`）合入时，确认 `_run_lifecycle` 入口与 `pipeline_runner.py` 恢复合并逻辑与其停机 CAS / critical section 无冲突；生产控制面部署与 Agent 热更新后，回访下一次长稳计划进入 teardown 的作业是否不再出现 `job_running_timeout` 或 `init_return_check:not_executed` 误杀。
