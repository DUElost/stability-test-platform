## Context

当前平台的 builtin action 全部已实现（18 个），pipeline_def 的 stages 执行模型（prepare → execute → post_process）可靠运行。Workflow 编排层（WorkflowDefinition → TaskTemplate → dispatch → JobInstance）已实现基本的 fan-out 分发。

**核心缺口**：没有机制将多个 pipeline_def 串联成一个完整的测试生命周期。以 Monkey AEE 为例，需要 init → 周期 patrol → teardown，但当前只能手动分发和管理各阶段。

**前置研究**：
- `task-orchestration-concept-map` (2026-02-28) — 定义了 Workflow/Pipeline/Tool 三层概念边界
- `test-session-lease` (2026-03-16) — 实现了设备并发锁 + 会话看门狗
- `aee-script-migration-to-builtin-actions` (2026-03-04) — 将 AEE 脚本逻辑迁移为 builtin action
- `builtin-action-param-forms` (2026-03-11) — 前端参数表单渲染

**双模型分析结论**（Codex + Gemini）：
- 服务端生命周期协调器是长期方案，但实现复杂度高
- Agent 侧简单调度足以验证 Monkey AEE 链路可行性
- 分阶段推进：Phase 1 MVP → Phase 2 服务端协调器

---

## Goals / Non-Goals

**Goals:**
- 打通 Monkey AEE 端到端测试链路：init → patrol_loop → teardown
- `stop_process` 支持按进程名杀进程（解决跨 job 状态丢失问题）
- 提供 `monkey_aee_teardown.json` 标准化收尾模板
- Teardown 步骤 best-effort 执行（单步失败不阻断后续）
- 验证 lifecycle pipeline_def 格式的可行性

**Non-Goals:**
- 服务端 lifecycle coordinator / `workflow_device_run` 状态机（Phase 2）
- Lifecycle 级设备锁（Phase 2）
- 前端生命周期编辑器 UI（Phase 2）
- 通用化 lifecycle 编排（Phase 1 仅支持 Monkey AEE 验证）
- 模板变量运行时渲染（`{resource_dir}`, `{wifi_ssid}` 等占位符解析仍由前端填充）

---

## Decisions

### D1: Lifecycle 定义内嵌于 pipeline_def

**选择**：在 `pipeline_def` 中新增 `lifecycle` 键，内嵌 init/patrol/teardown 三个子 pipeline。

**替代方案**：
- (A) 三个独立 TaskTemplate + dispatcher 顺序创建 JobInstance — 需要 `workflow_device_run` 状态机协调，Phase 1 不引入
- (B) PipelineEngine 内部 loop stage — StepTrace 唯一键冲突，reconciler 假设失效

**理由**：
- 对 PipelineEngine 接口零改动（lifecycle scheduler 在 engine 之上）
- 单个 JobInstance 包含完整生命周期定义，Agent 侧自包含
- 向后兼容：无 `lifecycle` 键时走现有 stages 路径

**格式示例**：
```json
{
  "lifecycle": {
    "timeout_seconds": 86400,
    "init": {
      "stages": { "prepare": [...], "execute": [...], "post_process": [] }
    },
    "patrol": {
      "interval_seconds": 300,
      "stages": { "prepare": [...], "execute": [...], "post_process": [...] }
    },
    "teardown": {
      "stages": { "prepare": [...], "execute": [...], "post_process": [...] }
    }
  }
}
```

### D2: Agent 侧 LifecycleRunner 架构

**选择**：在 `pipeline_engine.py` 的 `PipelineEngine.execute()` 入口增加 lifecycle 分支，委托给新的内部方法 `_execute_lifecycle()`。

**架构**：
```
PipelineEngine.execute(pipeline_def)
  ├── pipeline_def has "stages" → _execute_stages_format() (现有路径)
  └── pipeline_def has "lifecycle" → _execute_lifecycle()
        ├── _execute_stages_format(init.stages)
        ├── while not terminated:
        │     _execute_stages_format(patrol.stages)
        │     sleep(interval_seconds)
        └── _execute_stages_format(teardown.stages) [try/finally]
```

**理由**：
- 复用现有 `_execute_stages_format()` 执行每个子 pipeline
- lifecycle 调度逻辑（循环、终止判定、best-effort teardown）独立于 stages 执行
- `_shared` 上下文在整个生命周期内保持，init 的 PID 可传给 patrol 的 guard_process

### D3: stop_process process_name 实现

**选择**：`pid_from_step` 优先，`process_name` 作为 fallback。使用 `pgrep -f` 查找进程，复用 `guard_process` 中已验证的模式。

**实现细节**：
```python
def stop_process(ctx):
    # Priority 1: pid_from_step (same-job shared context)
    pid = resolve_pid_from_step(ctx)
    if pid:
        kill_by_pid(ctx, pid)
        return StepResult(success=True)

    # Priority 2: process_name (cross-job, e.g., teardown)
    process_name = ctx.params.get("process_name", "")
    if process_name:
        pids = pgrep(ctx, process_name)  # adb shell pgrep -f '<name>'
        for pid in pids:
            kill_by_pid(ctx, pid)
        return StepResult(success=True)

    # No target: no-op
    return StepResult(success=True)
```

**理由**：
- `pgrep -f` 已在 `guard_process` 中验证可靠，包括输出解析和空结果处理
- 杀所有匹配实例（kill ALL），避免遗漏后台子进程
- 返回 success 即使无进程可杀（幂等性，teardown 不应因进程已退出而报错）

### D4: Teardown best-effort 策略

**选择**：在 `_execute_lifecycle()` 的 teardown 阶段使用 try/except 包裹每个子 pipeline，不使用 stages 的 on_failure 语义。

**实现**：
```python
def _execute_lifecycle(self, pipeline_def):
    lifecycle = pipeline_def["lifecycle"]
    teardown_def = lifecycle.get("teardown")

    try:
        # Init
        init_result = self._execute_stages_format({"stages": lifecycle["init"]["stages"]})
        if not init_result.success:
            raise LifecycleError("init_failed")

        # Patrol loop
        self._run_patrol_loop(lifecycle)

    finally:
        # Teardown: best-effort, always runs
        if teardown_def:
            self._execute_teardown_best_effort(teardown_def)
```

`_execute_teardown_best_effort()` 内部对 teardown stages 的每个 step 单独 try/catch，记录失败但继续执行后续步骤。

**替代方案**：
- (A) stages 增加 `on_failure: continue` — 需要修改 stages 执行模型，影响面大
- (B) teardown 拆为独立 finalizer jobs — Phase 1 不引入 `workflow_device_run`，无法协调多 job

**理由**：
- try/finally 保证 teardown 在任何情况下执行（init 失败、patrol 异常、abort 信号）
- step 级别 catch 避免单步失败连锁
- 不修改现有 stages 执行模型，改动隔离

### D5: Patrol 终止条件判定

**终止触发源**：

| 触发源 | 检查时机 | 实现 |
|--------|---------|------|
| `timeout_seconds` 到期 | patrol 循环每次迭代前 | `time.time() - init_completed_at > timeout_seconds` |
| `is_aborted()` 回调 | patrol 循环每次迭代前 + sleep 期间 | 复用现有 `_is_aborted` / `_is_lock_lost()` |
| patrol 执行失败 | patrol stages 返回 `success=False` | 直接 break 循环 |

**sleep 期间 abort 检测**：patrol 的 `interval_seconds` 使用分段 sleep（每 5 秒检查一次 abort 信号），避免 300 秒的长 sleep 阻塞取消响应。

### D6: Status 上报扩展

**选择**：复用现有 `_report_job_status_mq()` 通道，新增 lifecycle phase 相关的 status 值。

**新增 status 值**：
- `INIT_RUNNING` — init 阶段开始
- `PATROL_RUNNING` — 进入 patrol 循环
- `TEARDOWN_RUNNING` — 进入 teardown 阶段

**新增 event metadata**：
- `iteration`: 当前 patrol 迭代编号
- `next_patrol_at`: 下次 patrol 的 ISO 时间戳
- `termination_reason`: teardown 触发原因
- `time_remaining`: lifecycle 剩余时间（秒）

**向后兼容**：前端未适配前，lifecycle status 值会被当作自定义字符串显示。Phase 2 前端适配时再结构化渲染。

---

## Risks / Trade-offs

### R1: Agent 崩溃导致生命周期状态丢失
- **Risk**: Agent 进程重启后不知道当前 lifecycle 处于哪个阶段
- **Mitigation (Phase 1)**: 不恢复，job 由 watchdog 标记为 UNKNOWN/FAILED。设备锁过期释放。用户需手动重新调度。
- **Mitigation (Phase 2)**: 服务端 lifecycle coordinator 从 `workflow_device_run` 恢复状态

### R2: 长 Job 的锁续期风险
- **Risk**: lifecycle 可能持续数天，锁续期线程必须稳定运行
- **Mitigation**: 现有 `LockRenewalManager` 已在 daemon thread 中运行，lock_lost → `_is_aborted()` → 触发 teardown

### R3: 跨 patrol 的增量 state_key 冲突
- **Risk**: `scan_aee` 的 `state_key_prefix` 如果不包含 lifecycle 标识，新 run 会把旧 run 的 AEE 当作已处理
- **Mitigation**: 模板中 `state_key_prefix` 使用 `scan_aee:{run_id}` 格式；`{run_id}` 在 engine 启动时替换（与 `{log_dir}` 同机制）

### R4: teardown 中设备不可达
- **Risk**: 设备断连后 teardown 的 ADB 命令全部失败
- **Mitigation**: best-effort 策略保证每个步骤都尝试执行；设备不可达时 teardown 标记为 DEGRADED

### R5: patrol 执行时间超过 interval
- **Risk**: 如果单次 patrol 花费超过 `interval_seconds`，不应叠加执行
- **Mitigation**: fixed-delay 语义 — 在 patrol 完成后才计算下次时间

---

## Migration Plan

### Phase 1 部署步骤
1. 更新 `stop_process` 实现 + `builtin_actions.json` schema
2. 创建 `monkey_aee_teardown.json` 模板
3. 实现 `PipelineEngine._execute_lifecycle()` + `_execute_teardown_best_effort()`
4. pipeline_schema.json 增加 lifecycle 格式验证
5. 前端 PipelineEditor 现有模板列表自动包含 teardown 模板
6. 手动构建一个 lifecycle pipeline_def 进行端到端测试

### Rollback
- `PipelineEngine.execute()` 入口的 lifecycle 分支是新增代码，不影响现有 stages 路径
- `stop_process` 的 `process_name` 是新增参数，现有 `pid_from_step` 路径不变
- 回滚只需移除 lifecycle 分支，无数据迁移

---

## Open Questions

### OQ1: lifecycle pipeline_def 的创建 UI
Phase 1 暂无专用 UI，用户需手动构建 JSON 或通过 API 创建。Phase 2 是否在 WorkflowDefinitionEditPage 中集成 lifecycle 配置？

### OQ2: patrol 日志分隔
多次 patrol 的日志如何在前端区分？Phase 1 通过 `[Patrol #N]` 前缀标记；Phase 2 是否需要按 iteration 分文件夹？

### OQ3: lifecycle 模板预设
是否为 Monkey AEE 创建一个预设的 lifecycle 模板（组合 init + patrol + teardown），还是由用户在创建 Task 时手动组装？
