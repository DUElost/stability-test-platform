## 1. stop_process 增加 process_name 支持

- [x] 1.1 在 `backend/agent/actions/process_actions.py` 的 `stop_process` 函数中增加 `process_name` 参数处理：优先 `pid_from_step`，fallback 到 `process_name`，使用 `pgrep -f` 查找并 kill 所有匹配 PID
- [x] 1.2 在 `backend/schemas/builtin_actions.json` 的 `stop_process` 条目中，`param_schema` 增加 `process_name` 字段（type: string, label: "Process Name", placeholder: "com.example.app"）
- [x] 1.3 在 `frontend/src/components/pipeline/actionCatalog.ts` 的 `stop_process` 条目中同步更新 `paramSchema`，增加 `process_name` 字段
- [x] 1.4 编写单元测试验证 stop_process 的三种场景：pid_from_step 优先、process_name fallback、两者均无时 no-op

## 2. Teardown Pipeline 模板

- [x] 2.1 创建 `backend/schemas/pipeline_templates/monkey_aee_teardown.json`，包含 prepare（ensure_root）、execute（stop_process + collect_bugreport + scan_aee + export_mobilelogs）、post_process（aee_extract + log_scan + adb_pull）
- [x] 2.2 验证 teardown 模板通过 `GET /api/v1/pipeline/templates` 和 `GET /api/v1/pipeline/templates/monkey_aee_teardown` 可访问

## 3. Lifecycle Pipeline 定义格式

- [x] 3.1 在 `backend/schemas/` 中更新或新增 pipeline schema 验证，允许 `pipeline_def` 包含 `lifecycle` 键（含 `init`, `patrol`, `teardown` 子结构和 `timeout_seconds`, `interval_seconds` 字段）
- [x] 3.2 在 `backend/core/pipeline_validator.py` 中扩展 `validate_pipeline_def()`，增加 lifecycle 格式校验：init/teardown 必填，patrol 可选，interval_seconds > 0，timeout_seconds > 0
- [x] 3.3 确保非 lifecycle 格式（仅有 `stages` 键）的 pipeline_def 验证行为不变（向后兼容）

## 4. Agent Lifecycle 调度器核心

- [x] 4.1 在 `backend/agent/pipeline_engine.py` 的 `PipelineEngine.execute()` 入口增加 lifecycle 分支：检测 `pipeline_def` 是否包含 `lifecycle` 键，委托给 `_execute_lifecycle()`
- [x] 4.2 实现 `_execute_lifecycle()` 主流程：执行 init stages → 进入 patrol 循环 → try/finally 执行 teardown
- [x] 4.3 实现 patrol 循环逻辑：循环调用 `_execute_stages_format(patrol.stages)`，每次迭代后按 `interval_seconds` 等待（分段 sleep，每 5 秒检查 abort 信号）
- [x] 4.4 实现 patrol 终止条件判定：timeout_seconds 到期（从 init 完成时计算）、patrol 执行失败、`_is_lock_lost()` / `_is_aborted()` 回调
- [x] 4.5 实现 `_execute_teardown_best_effort()`：对 teardown 的每个 stage 的每个 step 单独 try/catch，记录失败但继续执行后续步骤
- [x] 4.6 实现 patrol iteration 计数器，在 step logger 中标记 `[Patrol #N]`

## 5. Lifecycle 状态上报

- [x] 5.1 在 `_execute_lifecycle()` 的各阶段转换点调用 `_report_job_status_mq()`，发送 `INIT_RUNNING`、`PATROL_RUNNING`、`TEARDOWN_RUNNING` 状态
- [x] 5.2 在每次 patrol 完成后的 status event 中包含 `iteration`、`next_patrol_at`、`time_remaining` metadata
- [x] 5.3 在 teardown 触发时的 job completion event 中包含 `termination_reason`（值：timeout / patrol_failure / abort / init_failure / manual_cancel）

## 6. Lifecycle 模板预设

- [x] 6.1 创建 `backend/schemas/pipeline_templates/monkey_aee_lifecycle.json`，组合 monkey_aee_init + monkey_aee_patrol + monkey_aee_teardown 为一个完整的 lifecycle pipeline_def，包含 timeout_seconds=604800（7天）和 interval_seconds=300（5分钟）
- [x] 6.2 验证 lifecycle 模板通过 `GET /api/v1/pipeline/templates/monkey_aee_lifecycle` 可访问且通过 schema 验证

## 7. 端到端验证

- [x] 7.1 在 WSL Agent 环境中，使用 monkey_aee_lifecycle 模板创建一个 lifecycle pipeline_def 的任务，验证 init → patrol → teardown 完整执行流程
- [x] 7.2 验证 patrol 定时执行（interval_seconds 间隔）和终止触发（timeout 或手动取消）
- [x] 7.3 验证 teardown best-effort：模拟设备断连场景，确认 teardown 步骤部分失败时后续步骤仍然执行
- [x] 7.4 验证 stop_process process_name：确认 teardown 中的 stop_process 能按进程名杀掉 monkey 进程
