# OPSX Proposal: Pipeline Lifecycle Orchestration

**Change ID**: pipeline-lifecycle-orchestration
**Date**: 2026-03-22
**Status**: Proposal
**Type**: Architecture Extension (Phased)

---

## Context

当前平台的 builtin action 全部已实现（18 个），pipeline_def 的 stages 执行模型（prepare → execute → post_process）可靠运行。但**完整的测试生命周期链**未串通：

```
[缺失的完整链路]
Init Pipeline (一次性) → Patrol Loop (周期巡检) → Teardown Pipeline (一次性收尾)
```

具体缺口：
1. **无 Teardown 模板** — 测试结束后没有标准化收尾流程
2. **无生命周期编排** — PipelineEngine 只执行单个 pipeline_def，无法编排 init → patrol → teardown 序列
3. **无定时巡检调度** — `monkey_aee_patrol` 设计为循环执行，但无调度机制
4. **无测试终止信号** — 缺少 timeout / fatal error / manual cancel → teardown 触发链
5. **跨 Job 状态丢失** — init 的 PID 无法传给 teardown 的 `stop_process`
6. **设备锁间隙** — patrol 间隔期间设备无锁保护

---

## User Decisions

| 歧义 | 决策 | 约束 |
|------|------|------|
| 实现范围 | **分阶段推进** | Phase 1 MVP 打通 Monkey AEE 链路，Phase 2 服务端协调器 |
| 设备锁策略 | **Lifecycle 级锁** | 锁 owner 绑定生命周期而非单个 job |
| Teardown best-effort | **拆为独立 finalizer jobs** | 每个 teardown 步骤作为独立 job，失败不影响其他步骤 |

---

## Phase 1: MVP — Monkey AEE 端到端链路打通

### 目标
以最小改动打通一条完整的 Monkey AEE 测试链路，验证 init → patrol_loop → teardown 可行性。

### 变更范围

#### 1.1 `stop_process` 增加 `process_name` 支持
- **问题**：当前 `stop_process` 仅支持 `pid_from_step`（通过 shared 上下文传递 PID），但 teardown 是独立 job，拿不到 init 的 PID
- **方案**：增加 `process_name` 参数，使用 `pgrep -f <process_name>` 查找并杀进程
- **文件**：`backend/agent/actions/process_actions.py`
- **向后兼容**：`pid_from_step` 优先，`process_name` 作为 fallback

#### 1.2 创建 `monkey_aee_teardown.json` 模板
- 组合现有 action：`ensure_root → stop_process(process_name) → collect_bugreport → final_scan_aee → export_mobilelogs → aee_extract → log_scan → adb_pull`
- 注意：teardown 中每个 step 应尽量独立，即使前一步失败也要尝试后续步骤
- 因选择 finalizer jobs 策略，此模板作为参考定义，实际执行时可按需拆为多个独立 job

#### 1.3 Agent 侧简单生命周期调度
- **机制**：Agent 收到带有 lifecycle 元数据的 job 后，内部管理 init → patrol_loop → teardown 序列
- **调度逻辑**：
  - 执行 init pipeline_def
  - init 成功后，按 `interval_seconds` 循环执行 patrol pipeline_def
  - 任一终止条件命中（timeout / patrol 失败 / is_aborted）→ 执行 teardown pipeline_def
- **限制**：Agent 崩溃时状态丢失（Phase 2 由服务端恢复）

#### 1.4 `builtin_actions.json` Schema 更新
- `stop_process` 的 `param_schema` 增加 `process_name` 字段

#### 1.5 前端 Pipeline Templates 可访问
- 确保 teardown 模板通过 `GET /api/v1/pipeline/templates` 可用

### 不包含
- 服务端 lifecycle coordinator
- `workflow_device_run` 数据模型
- 前端生命周期编辑器
- Lifecycle 级设备锁
- finalizer jobs 拆分（Phase 1 teardown 仍在单 pipeline 内串行执行，通过扩展 stages 支持 on_failure 或直接用 Agent 侧 try/finally 保证 best-effort）

---

## Phase 2: 服务端生命周期协调器（后续 Change）

> Phase 2 仅在此处记录架构方向，不在本 change 实施。

### 2.1 `workflow_device_run` 实体
- 状态机：`INIT_PENDING → INIT_RUNNING → PATROL_WAITING → PATROL_RUNNING → TEARDOWN_PENDING → TEARDOWN_RUNNING → COMPLETED/FAILED/DEGRADED`
- 字段：`next_patrol_at`, `interval_seconds`, `timeout_at`, `current_iteration`, `teardown_requested_at`, `runtime_context(JSONB)`

### 2.2 Lifecycle Coordinator（服务端后台任务）
- 扫描 `workflow_device_run`，按状态机创建/调度 JobInstance
- fixed-delay 语义：同一 device 最多一个 patrol job
- 终止判定：timeout / error threshold / manual cancel / UNKNOWN grace
- Teardown 唯一保证：`teardown_requested_at` + 唯一约束

### 2.3 Lifecycle 级设备锁
- 锁 owner 从 `job.id` 升级为 `workflow_device_run.id`
- 整个生命周期内设备独占

### 2.4 前端适配
- 生命周期编辑器（Init/Patrol/Teardown 三段式）
- Patrol cycle 分组监控
- 矩阵视图

---

## Hard Constraints

1. **所有现有 builtin action 不可破坏** — `stop_process` 的 `process_name` 是新增参数，不影响现有 `pid_from_step` 路径
2. **现有 pipeline_def schema 保持兼容** — teardown 模板使用标准 stages 格式
3. **Agent 单 job 执行模型不变** — Phase 1 的生命周期调度是 Agent 内部逻辑，不改变 PipelineEngine 接口
4. **Patrol 单实例** — 同一 device 同时最多一个 patrol 执行
5. **Teardown 至多一次** — 同一生命周期最多触发一次 teardown

## Soft Constraints

1. `monkey_aee_init.json` 和 `monkey_aee_patrol.json` 保持现有内容不变
2. Phase 1 Agent 侧调度可以硬编码 Monkey AEE 的模板组合（不要求通用化）
3. teardown 中 `state_key_prefix` 应包含 lifecycle/run 级标识，避免增量扫描串 run

---

## Success Criteria

1. 可通过前端创建一个 Monkey AEE Lifecycle 任务
2. Agent 自动执行 init → patrol_loop → teardown 完整链路
3. Teardown 中 `stop_process` 能按 `process_name` 杀掉 monkey 进程
4. Teardown 步骤即使部分失败，其余步骤仍然执行
5. 日志可在前端实时查看（init / patrol / teardown 各阶段）
