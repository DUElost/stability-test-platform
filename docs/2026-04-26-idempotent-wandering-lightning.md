# 测试平台简化：脚本管理 + 序列执行 + Watcher 集成

## Context

### 方向收敛

旧模型（WorkflowDefinition -> TaskTemplate -> PipelineDef -> stages/lifecycle）在“运维脚本管理 + 按设备批量执行”场景下过重。用户不需要在前端维护复杂测试步骤、测试场景和多阶段编排，需要的是：

1. 脚本统一管理（NFS + 平台元数据）
2. 按顺序排列脚本组成执行序列
3. 选设备、填参数、下发执行
4. 接收 stdout/stderr/metrics/产物
5. Android 设备执行期间 Watcher 自动采集 crash 信号

### 已接受修订

本方案采用“前端简化、底层复用”的修订版：

1. 保留脚本库、执行任务、执行记录三类用户入口。
2. 新增 `script_sequence` 仅作为脚本序列模板。
3. 不新增独立的 `script_batch` / `script_run` 执行状态表作为主链路。
4. 不新增 `ScriptBatchRunner`，Agent 继续复用 `JobSession + PipelineEngine`。
5. 执行下发时由后端把脚本序列合成为最小 `pipeline_def`，创建现有 `WorkflowRun + JobInstance`。
6. Watcher、设备锁、LogSignal、Artifact、session watchdog 都继续以 `job_instance.id` 作为权威 `job_id`。
7. 旧 Workflow 路由和数据保留，但新导航隐藏复杂编排入口。

### 可复用基础（已实现）

| 能力 | 位置 | 状态 |
|------|------|------|
| script 表 + CRUD + NFS 扫描 | `backend/models/script.py`, `backend/api/routes/scripts.py`, `backend/services/script_catalog.py` | 已实现 |
| ScriptRegistry（Agent 缓存+同步） | `backend/agent/registry/script_registry.py` | 已实现 |
| `script:` action + subprocess 执行 | `backend/agent/pipeline_engine.py:_run_script_action` | 已实现 |
| `STP_*` 环境变量契约 | `backend/agent/pipeline_engine.py` | 已实现 |
| `JobSession` + Watcher 绑定 | `backend/agent/job_session.py`, `backend/agent/watcher/` | 已实现 |
| 设备锁续期 | `backend/agent/lock_manager.py`, `backend/api/routes/agent_api.py` | 已实现 |
| StepTrace / Artifact / LogSignal | `backend/models/job.py`, `backend/api/routes/agent_api.py` | 已实现 |
| session watchdog | `backend/tasks/session_watchdog.py` | 已实现 |

---

## 总体架构

```text
前端 React
  脚本库
    - 浏览、搜索、扫描、查看元数据和版本
  执行任务
    - 左侧脚本库
    - 右侧序列编辑
    - 参数填写
    - 设备选择
    - 执行
  执行记录
    - 历史列表
    - Job 详情
    - step stdout/stderr/metrics
    - 产物下载
    - Watcher 摘要
    - 重新执行

后端 FastAPI
  scripts 表（已有）
    - GET/POST/PUT/DELETE /api/v1/scripts
    - POST /api/v1/scripts/scan

  script_sequence 表（新增，仅保存序列模板）
    - CRUD /api/v1/script-sequences

  script execution facade（新增用户侧 API，不新增 Agent 执行链路）
    - POST /api/v1/script-executions
      输入：sequence_id? / items[] / device_ids[] / on_failure
      行为：校验脚本 -> 合成 pipeline_def -> 创建 WorkflowRun + JobInstance
    - GET /api/v1/script-executions
      从 WorkflowRun + JobInstance 聚合执行记录
    - GET /api/v1/script-executions/{workflow_run_id}
      展示 jobs、step traces、artifacts、watcher summary

  旧 Workflow 端点
    - 路由保留
    - 数据不迁移、不删除
    - 新导航不再暴露复杂编排入口

Agent Linux
  现有主链路不变
    - /api/v1/agent/jobs/claim
    - run_task_wrapper
    - JobSession.__enter__ -> Watcher.start
    - PipelineEngine.execute
    - script: action -> ScriptRegistry.resolve -> subprocess.run
    - StepTrace / complete / artifact / log-signal 上报
    - JobSession.__exit__ -> Watcher.stop
```

---

## 组件 1：数据模型

### 1.1 `script_sequence`（新增，脚本序列模板）

`script_sequence` 只保存用户可复用的序列，不承载运行态。运行态继续使用现有 `WorkflowRun`、`JobInstance`、`StepTrace`、`JobArtifact`、`JobLogSignal`。

```python
# backend/models/script_sequence.py

class ScriptSequence(Base):
    __tablename__ = "script_sequence"

    id = Column(Integer, primary_key=True)
    name = Column(String(256), nullable=False)
    description = Column(Text)
    items = Column(JSONB, nullable=False)
    # items = [
    #   {
    #     "index": 0,
    #     "script_name": "connect_wifi",
    #     "version": "1.0.0",
    #     "params": {"ssid": "TestNet"},
    #     "timeout_seconds": 30,
    #     "retry": 0
    #   }
    # ]
    on_failure = Column(String(16), nullable=False, default="stop")  # stop | continue
    created_by = Column(String(128))
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )
```

约束：

- `items[].script_name + version` 必须对应 active script。
- `timeout_seconds >= 1`。
- `retry` 默认为 0，范围建议 `0..10`。
- `on_failure=stop` 时，沿用 PipelineEngine 当前行为：步骤失败后 Job 失败，后续步骤不执行。
- `on_failure=continue` 需要作为后续增强，不能假装当前 PipelineEngine 已支持继续执行失败步骤。

### 1.2 运行态复用现有模型

脚本执行记录不新增 `script_batch` / `script_run` 主表。映射关系如下：

| 用户概念 | 现有模型 | 说明 |
|----------|----------|------|
| 一次脚本执行提交 | `WorkflowRun` | `triggered_by="script_execution"`，`result_summary.mode="script_execution"` |
| 每台设备的一次执行 | `JobInstance` | 每个 device 生成一个 JobInstance，`pipeline_def` 是脚本序列合成结果 |
| 单个脚本步骤状态 | `StepTrace` | `step_id` 使用稳定规则：`script_{index}_{script_name}` |
| Watcher 摘要 | `JobInstance.watcher_*` | 由现有 `/agent/jobs/{id}/complete` 回填 |
| crash 信号 | `JobLogSignal` | `job_id = job_instance.id` |
| 产物 | `JobArtifact` | `job_id = job_instance.id` |

后端需要一个系统级 WorkflowDefinition / TaskTemplate 作为外键锚点：

```text
WorkflowDefinition:
  name: "__script_execution__"
  created_by: "system"
  description: "System workflow anchor for script execution facade"

TaskTemplate:
  workflow_definition_id: system workflow id
  name: "__script_sequence__"
  pipeline_def: {"stages": {"execute": [{"step_id": "placeholder", ...}]}}
```

每次执行提交时，真实 `pipeline_def` 写入 `JobInstance.pipeline_def` 快照；系统 TaskTemplate 只作为外键锚点，不作为用户可编辑模板。

---

## 组件 2：Pipeline 合成

### 2.1 输入结构

用户侧执行接口接收：

```json
{
  "sequence_id": 12,
  "items": [
    {
      "script_name": "connect_wifi",
      "version": "1.0.0",
      "params": {"ssid": "TestNet"},
      "timeout_seconds": 30,
      "retry": 0
    }
  ],
  "device_ids": [1, 2, 3],
  "on_failure": "stop"
}
```

规则：

- `sequence_id` 和 `items` 至少提供一个。
- 同时提供时，以 `items` 为本次执行快照，`sequence_id` 只用于“来自哪个模板”的展示。
- 执行前校验所有脚本 active 且版本匹配。
- 默认只实现 `on_failure=stop`。`continue` 在后续任务中扩展 PipelineEngine 后再开放。

### 2.2 合成 `pipeline_def`

```python
def synthesize_script_pipeline(items: list[dict]) -> dict:
    return {
        "stages": {
            "execute": [
                {
                    "step_id": f"script_{index}_{item['script_name']}",
                    "action": f"script:{item['script_name']}",
                    "version": item["version"],
                    "params": item.get("params") or {},
                    "timeout_seconds": item.get("timeout_seconds") or 3600,
                    "retry": item.get("retry", 0),
                    "enabled": True,
                }
                for index, item in enumerate(items)
            ]
        }
    }
```

该结构直接走现有 `validate_pipeline_def()`、Agent claim、`PipelineEngine.execute()` 和 `script:` action。

### 2.3 stdout/stderr/metrics 存储补强

现有 `PipelineEngine._run_script_action()` 已执行脚本并解析 stdout JSON 中的 `metrics`，但成功场景没有把 stdout/stderr 清晰持久化到 `StepTrace.output`。

需要做小范围增强：

- `StepResult` 增加可选 `output` 或 `stdout` / `stderr` 字段。
- `_run_script_action()` 返回截断后的 stdout/stderr。
- `_execute_step_stages()` 在 `_report_step_trace_mq()` 时把成功 stdout 写入 `output`，失败 stderr/错误写入 `error_message`。
- 前端执行详情从 StepTrace 展示每个脚本步骤的输出。

截断建议：

- stdout 最多 64 KiB。
- stderr 最多 64 KiB。
- 完整日志仍以现有 log archive / artifact 为准。

---

## 组件 3：API 端点

### 3.1 脚本序列 CRUD

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/v1/script-sequences` | 序列列表，支持分页和关键字搜索 |
| `POST` | `/api/v1/script-sequences` | 创建序列 |
| `GET` | `/api/v1/script-sequences/{id}` | 序列详情 |
| `PUT` | `/api/v1/script-sequences/{id}` | 更新序列 |
| `DELETE` | `/api/v1/script-sequences/{id}` | 删除序列 |

### 3.2 脚本执行外观 API

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/v1/script-executions` | 创建一次脚本序列执行，内部生成 WorkflowRun + JobInstance |
| `GET` | `/api/v1/script-executions` | 执行记录列表，从 WorkflowRun/JobInstance 聚合 |
| `GET` | `/api/v1/script-executions/{run_id}` | 执行详情，含 jobs、step traces、artifacts、watcher summary |
| `POST` | `/api/v1/script-executions/{run_id}/rerun` | 复用上次 items 和设备重新执行 |

`POST /api/v1/script-executions` 返回：

```json
{
  "workflow_run_id": 42,
  "job_ids": [1001, 1002, 1003],
  "device_count": 3,
  "step_count": 4
}
```

### 3.3 Agent 端点

不新增 Agent 专用脚本执行端点。继续复用：

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/v1/agent/jobs/claim` | Agent 认领 JobInstance |
| `POST` | `/api/v1/agent/jobs/{job_id}/status` | Job 状态更新 |
| `POST` | `/api/v1/agent/jobs/{job_id}/steps/{step_id}/status` | step 状态上报 |
| `POST` | `/api/v1/agent/jobs/{job_id}/complete` | Job 完成上报，含 watcher_summary |
| `POST` | `/api/v1/agent/jobs/{job_id}/extend_lock` | 设备锁续期 |
| `POST` | `/api/v1/agent/log-signals` | Watcher 信号批量上报 |
| `POST` | `/api/v1/agent/jobs/{job_id}/artifacts` | Watcher artifact 上报 |

这样设备锁、Watcher 外键、artifact 外键、session watchdog 都不需要复制新逻辑。

---

## 组件 4：Agent 执行流

### 4.1 主链路保持不变

```text
Agent main loop
  fetch_pending_jobs()
  executor.submit(run_task_wrapper, job, ...)

run_task_wrapper
  update_job(RUNNING)
  JobSession.__enter__()
    - 注册 active job/device
    - 启动 Watcher
  execute_pipeline_run()
    - PipelineEngine.execute()
    - stages.execute 按顺序执行
    - script: action 调用 ScriptRegistry.resolve()
    - subprocess.run(script, env=STP_*)
    - StepTrace 上报
    - 日志归档 artifact
  JobSession.__exit__()
    - Watcher drain
    - 释放 active job/device
  complete_job()
    - 写终态
    - 回填 watcher_summary
    - 触发后处理
```

### 4.2 与现有基础设施的关系

```text
脚本执行 facade
  -> 复用 WorkflowRun / JobInstance
  -> 复用 JobSession
  -> 复用 PipelineEngine
  -> 复用 ScriptRegistry
  -> 复用 StepTrace
  -> 复用 LogWatcherManager / DeviceLogWatcher
  -> 复用 OutboxDrainer
  -> 复用 ArtifactUploader
  -> 复用 LockRenewalManager
  -> 复用 session_watchdog
```

不做：

- 不新增 `backend/agent/script_batch_runner.py`。
- 不新增 `/api/v1/agent/script-batches/*`。
- 不把 `script_batch.id` 当 `job_id`。
- 不绕过 `PipelineEngine` 自己 `subprocess.run()`。

---

## 组件 5：Watcher 集成验证

### 5.1 链路对照

```text
JobInstance 生命周期             Watcher 行为
---------------------           -------------------------------
agent claim job                  -
JobSession.__enter__()       ->  LogWatcherManager.start()
                                  CapabilityProber.probe()
                                  DeviceLogWatcher.start()
                                  SignalEmitter -> LocalDB outbox

PipelineEngine 执行 script steps  LogSignal 持续产出
                                  OutboxDrainer 异步上报
                                  ArtifactUploader 异步上传

JobSession.__exit__()        ->  LogWatcherManager.stop()
                                  EventBatcher.drain()
                                  LogPuller.drain()

complete payload             <-  WatcherSummary
                                  watcher_started_at
                                  watcher_stopped_at
                                  watcher_capability
                                  log_signal_count
```

### 5.2 关键字段映射

| Watcher 需要的 | 来源 |
|----------------|------|
| `job_id` | `job_instance.id` |
| `device_serial` | `device.serial`，由 claim 端点注入 |
| `host_id` | `job_instance.host_id` |
| `log_dir` | `get_run_log_dir(job_instance.id)` |
| `watcher_policy` | 系统 WorkflowDefinition 或默认环境策略 |

### 5.3 离线容错

- LogSignal 仍写入 LocalDB outbox 后 ACK，Agent 崩溃后 OutboxDrainer 恢复。
- `watcher_state` 仍由 `LogWatcherManager._reconcile_on_startup()` 清理残留。
- Host heartbeat timeout、device lock expiration、UNKNOWN grace period 继续由 `session_watchdog.py` 处理 `JobInstance`。
- Artifact 继续通过 `/api/v1/agent/jobs/{job_id}/artifacts` 入库。

---

## 组件 6：前端页面

### 6.1 脚本库 (`/scripts`)

```text
脚本库                                      [重新扫描]

左侧：category 筛选
  全部
  device
  app
  resource
  log

右侧：脚本列表
  connect_wifi
    v1.0.0  shell
    设备 WiFi 连接
    参数 schema

  push_bundle
    v2.0.0  python
    资源包推送
    参数 schema
```

### 6.2 执行任务 (`/execute`)

```text
执行任务

左侧：脚本选择
  搜索脚本
  按 category 分组
  点击加入序列

右侧：序列编辑
  1. connect_wifi v1.0.0
     参数：ssid / password
     timeout / retry
  2. install_apk v1.0.0
     参数：apk_path
  3. run_monkey v1.5.0
     参数：duration

设备选择
  搜索 serial/model
  勾选 ONLINE 设备

操作
  保存为模板
  执行
```

交互原则：

- 表单控件必须有 label，不只依赖 placeholder。
- 参数表单复用现有 `DynamicToolForm` 或抽取通用 JSON schema 表单。
- 页面面向运维执行，保持密集、清晰、低装饰。
- `on_failure=continue` 在后端支持前不开放，避免 UI 假能力。

### 6.3 执行记录 (`/history`)

```text
执行记录

列表
  #42  2026-04-26 15:30  3 台设备  RUNNING
  connect_wifi -> install_apk -> run_monkey

详情
  Run #42
  Job #1001 device-001 COMPLETED
    1. connect_wifi exit=0
       stdout / stderr / metrics
    2. install_apk exit=0
       stdout / stderr / metrics
    3. run_monkey exit=0
       stdout / stderr / metrics

  Watcher
    capability
    signal count
    artifacts

  操作
    重新执行
    下载产物
```

---

## 实施顺序

### Phase 1 — 后端脚本序列与执行外观

| 文件 | 操作 | 说明 |
|------|------|------|
| `backend/models/script_sequence.py` | 新建 | ScriptSequence ORM，仅保存模板 |
| `backend/models/__init__.py` | 修改 | 注册 ScriptSequence |
| Alembic migration | 新建 | 只新增 `script_sequence` 表 |
| `backend/services/script_execution.py` | 新建 | 校验脚本、合成 pipeline、创建 WorkflowRun + JobInstance |
| `backend/api/routes/script_sequences.py` | 新建 | 序列 CRUD |
| `backend/api/routes/script_executions.py` | 新建 | 用户侧执行和历史查询外观 |
| `backend/main.py` | 修改 | 注册新路由 |

### Phase 2 — PipelineEngine 输出补强

| 文件 | 操作 | 说明 |
|------|------|------|
| `backend/agent/pipeline_engine.py` | 修改 | `script:` action 返回 stdout/stderr/metrics |
| `backend/agent/tests/test_pipeline_engine_script_action.py` | 修改 | 覆盖 stdout/stderr/metrics 上报 |
| `backend/tests/api/test_script_executions.py` | 新建 | 覆盖执行创建、JobInstance 快照、历史查询 |

### Phase 3 — 前端三页面

| 文件 | 操作 | 说明 |
|------|------|------|
| `frontend/src/pages/scripts/ScriptLibraryPage.tsx` | 新建 | 脚本库 |
| `frontend/src/pages/execution/ScriptExecutePage.tsx` | 新建 | 脚本序列执行 |
| `frontend/src/pages/execution/ScriptHistoryPage.tsx` | 新建 | 执行记录 |
| `frontend/src/utils/api/scripts.ts` | 新建/修改 | scripts、scriptSequences、scriptExecutions API |
| `frontend/src/router/index.tsx` | 修改 | 新增 `/scripts`、`/execute`、`/history` |
| `frontend/src/layouts/Sidebar.tsx` | 修改 | 导航改为脚本库、执行任务、执行记录 |

### Phase 4 — 旧页面处理

| 操作 | 说明 |
|------|------|
| 导航栏移除旧入口 | `/orchestration/*`、旧 `/execution/run`、旧 `/execution/runs` |
| 路由保留 | 直接 URL 访问仍可用，不做破坏性删除 |
| 数据不动 | `workflow_definition` / `workflow_run` / `job_instance` 表完整保留 |
| 新旧记录隔离 | 新执行记录通过 `triggered_by="script_execution"` 或 `result_summary.mode` 筛选 |

---

## 验证方案

### 后端

1. Alembic migration 只新增 `script_sequence`，不新增 `script_batch` / `script_run`。
2. `POST /api/v1/script-executions` 创建 `WorkflowRun + JobInstance`。
3. `JobInstance.pipeline_def` 是脚本序列合成快照。
4. `GET /api/v1/script-executions/{run_id}` 能返回 jobs、steps、artifacts、watcher summary。
5. 旧 Workflow 端点仍可访问。
6. `session_watchdog.py` 不需要新增 ScriptBatch 分支。

### Agent

1. 无新增 Agent runner。
2. 现有 `/api/v1/agent/jobs/claim` 能 claim 到脚本执行生成的 JobInstance。
3. `run_task_wrapper()` 能创建 JobSession 并启动 Watcher。
4. PipelineEngine 能按序执行多个 `script:` step。
5. `STP_STEP_PARAMS`、`STP_DEVICE_SERIAL`、`STP_LOG_DIR`、`STP_JOB_ID` 正确传递。
6. stdout/stderr/metrics 能落到 StepTrace 或可查询结果中。
7. ArtifactUploader 和 LogSignal outbox 仍按 `job_instance.id` 关联。

### 前端

1. 脚本库浏览、搜索、按 category 过滤、重新扫描。
2. 选择脚本、填写参数、加入序列、调整顺序。
3. 保存/加载脚本序列模板。
4. 选设备并发起执行。
5. 执行记录列表只展示脚本执行外观记录。
6. 执行详情展示每台设备的 step 状态、stdout/stderr、metrics、Watcher 摘要和产物。
7. 重新执行能复用上次 items 和设备。

---

## 风险与边界

1. `on_failure=continue` 当前不应进入首期，因为现有 PipelineEngine stages 执行失败即终止。首期只开放 `stop`。
2. stdout/stderr 不应无限写入数据库，必须截断；完整日志依赖现有日志归档。
3. 系统级 WorkflowDefinition / TaskTemplate 是外键锚点，不能出现在新前端主入口。
4. 旧复杂编排页面只隐藏导航，不删除路由和数据，避免破坏已有任务。
5. 如果后续确实需要独立 ScriptBatch 表，应先证明现有 JobInstance 无法承载，再设计迁移；当前不需要。
