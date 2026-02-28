# 架构规范：实体定义、状态机、层职责

## 1. 三层架构与实体对齐

```
┌─────────────────────────────────────────────┐
│  编排层 (Server)                              │
│  Blueprint: WorkflowDefinition               │
│  Runtime:   WorkflowRun                      │
│  职责: 全局拓扑管理、结果聚合、JIRA、钉钉通知     │
└──────────────────┬──────────────────────────┘
                   │ 1:N 扇出
┌──────────────────▼──────────────────────────┐
│  调度层 (Server)                              │
│  Blueprint: TaskTemplate (含 pipeline_def)   │
│  Runtime:   JobInstance                      │
│  职责: 任务扇出至 40+ Host、全局状态对齐、Reduce │
└──────────────────┬──────────────────────────┘
                   │ 1:N 分发
┌──────────────────▼──────────────────────────┐
│  执行层 (Agent)                               │
│  Blueprint: Action/Tool (ID 强绑定)           │
│  Runtime:   StepTrace                        │
│  职责: 本地 ADB 驱动、脚本执行、日志采集         │
└─────────────────────────────────────────────┘
```

## 2. 核心实体关系

```
WorkflowDefinition
  └── has_many: TaskTemplate (pipeline_def[])
       └── pipeline_def step: { tool_id, version, params }
                                    │
                              references
                                    │
                             Tool (Tool Catalog)
                               script_path
                               script_class
                               param_schema

WorkflowRun (triggered from WorkflowDefinition)
  └── has_many: JobInstance
       └── has_many: StepTrace
```

**实体约束**：
- 一个 `WorkflowRun` 对应一次用户触发的测试活动
- 一个 `JobInstance` 对应一台设备上的一次完整测试执行
- 一个 `StepTrace` 对应 `pipeline_def` 中一个 Step 的单次执行结果

## 3. JobInstance 状态机

### 合法状态枚举

```python
class JobStatus(str, Enum):
    PENDING         = "PENDING"          # 等待 Agent 认领
    RUNNING         = "RUNNING"          # 执行中
    COMPLETED       = "COMPLETED"        # 全部 Step 成功
    FAILED          = "FAILED"           # Step 失败且超重试次数
    ABORTED         = "ABORTED"          # Watchdog 强制终止（设备死锁）
    UNKNOWN         = "UNKNOWN"          # Host 心跳超时，状态不确定
    PENDING_TOOL    = "PENDING_TOOL"     # 工具版本拉取失败，等待管理员介入
```

### 合法状态转换表

```python
VALID_TRANSITIONS: dict[JobStatus, set[JobStatus]] = {
    JobStatus.PENDING:       {JobStatus.RUNNING},
    JobStatus.RUNNING:       {JobStatus.COMPLETED, JobStatus.FAILED,
                              JobStatus.ABORTED, JobStatus.UNKNOWN},
    JobStatus.UNKNOWN:       {JobStatus.RUNNING, JobStatus.COMPLETED},
    JobStatus.FAILED:        set(),   # 终态
    JobStatus.COMPLETED:     set(),   # 终态
    JobStatus.ABORTED:       set(),   # 终态
    JobStatus.PENDING_TOOL:  {JobStatus.PENDING},  # 工具就绪后可重新排队
}
```

### 强制规则

```python
# 状态机唯一入口，禁止绕过
class JobStateMachine:
    @staticmethod
    def transition(job: JobInstance, new_status: JobStatus, reason: str = "") -> None:
        if new_status not in VALID_TRANSITIONS[job.status]:
            raise InvalidTransitionError(
                f"Cannot transition {job.status} -> {new_status} for job {job.id}"
            )
        job.status = new_status
        job.status_reason = reason
        job.updated_at = utcnow()
```

> ⚠️ **禁止**在任何地方直接 `job.status = "RUNNING"`，必须通过 `JobStateMachine.transition()`

## 4. WorkflowRun 聚合状态策略

```python
class WorkflowAggregator:
    """
    在所有 JobInstance 完成（终态或超时）后调用。
    failure_threshold: 来自 WorkflowDefinition.failure_threshold，默认 0.05 (5%)
    """
    def compute_status(self, jobs: list[JobInstance], failure_threshold: float) -> WorkflowStatus:
        total = len(jobs)
        failed  = sum(1 for j in jobs if j.status in {JobStatus.FAILED, JobStatus.ABORTED})
        unknown = sum(1 for j in jobs if j.status == JobStatus.UNKNOWN)

        if unknown > 0:
            return WorkflowStatus.DEGRADED          # 人工介入

        if failed == 0:
            return WorkflowStatus.SUCCESS

        if failed / total <= failure_threshold:
            return WorkflowStatus.PARTIAL_SUCCESS   # 生成报告，标注失败设备

        return WorkflowStatus.FAILED                # 阻断后续依赖，立即告警
```

## 5. pipeline_def 格式规范

```json
{
  "stages": {
    "prepare": [
      {
        "step_id": "check_device",
        "action": "builtin:check_device",
        "params": {},
        "timeout_seconds": 60,
        "retry": 1
      }
    ],
    "execute": [
      {
        "step_id": "run_monkey",
        "action": "tool:42",
        "version": "v2.1",
        "params": {
          "duration": 3600,
          "package": "com.example.app"
        },
        "timeout_seconds": 7200,
        "retry": 0
      }
    ],
    "post_process": [
      {
        "step_id": "scan_aee",
        "action": "tool:17",
        "version": "v1.0",
        "params": {},
        "timeout_seconds": 300,
        "retry": 2
      }
    ]
  }
}
```

**约束**：
- `action` 字段只允许两种格式：`"tool:<id>"` 或 `"builtin:<name>"`
- 禁止出现 `"script_path"` 或 `"script_class"` 字段（旧格式，已废弃）
- `timeout_seconds` 必填，用于 Watchdog 超时检测

## 6. Tool Catalog 规范

```python
class Tool(Base):
    id:           int          # 全局唯一 ID，pipeline_def 中引用的 tool_id
    name:         str          # 人类可读名称，如 "MonkeyAEEStabilityTest"
    version:      str          # 语义化版本，如 "v2.1"
    script_path:  str          # 绝对路径，仅存于 Tool 表，不暴露给 pipeline_def
    script_class: str          # 入口类名
    param_schema: dict         # JSON Schema，用于创建 Task 时前置校验
    is_active:    bool         # 下线工具设为 False，现有引用保留历史版本
    created_at:   datetime
    updated_at:   datetime
```

**param_schema 示例**：
```json
{
  "type": "object",
  "properties": {
    "duration": { "type": "integer", "minimum": 60, "description": "测试时长（秒）" },
    "package":  { "type": "string",  "description": "目标应用包名" }
  },
  "required": ["duration", "package"]
}
```
