# ADR-0020: Plan-Step 一次性切换与旧编排模型移除

- 状态：Accepted
- 优先级：P0
- 目标里程碑：M3
- 日期：2026-05-05
- 决策者：平台研发组
- 标签：编排模型, 数据迁移, JobInstance, Plan, 一次性切换

## 背景

当前编排模型为：

```text
WorkflowDefinition -> TaskTemplate -> PipelineDef -> Phase -> Step
```

前端因此需要同时暴露 Workflow、TaskTemplate、Setup Pipeline、Task Pipeline、Teardown Pipeline 等概念。实际使用中多数 Workflow 只有一个 TaskTemplate，多模板场景反而让用户难以判断任务边界。`setup_pipeline` / `teardown_pipeline` 又以 Workflow 级 JSONB 字段存在，执行时由 dispatcher 与 TaskTemplate 的 `pipeline_def` 拼接，导致定义模型和执行模型都较复杂。

新的目标模型为：

```text
Plan -> PlanStep
PlanRun -> JobInstance -> StepTrace / JobArtifact / ResourceAllocation
```

当前系统尚未承载大量生产历史数据，因此不采用双写、兼容期或新旧 API 并存方案。迁移复杂度集中在 Alembic 数据迁移和上线前校验中，应用代码在切换后只保留新模型。

## 决策

### 1. 一次性切换，不保留应用层旧模型

本迁移不做过渡期：

- 不做 Workflow 与 Plan 双写。
- 不保留 `/api/v1/workflows`、`/api/v1/workflow-runs` 作为新路径的兼容分支。
- 不保留 `/api/v1/templates` 及 TaskTemplate 相关 schema。
- 不保留 `/api/v1/script-executions` 临时脚本执行链路；用户执行脚本必须先通过 Plan 编排并保存。
- 不保留 `WorkflowDefinitionEditPage`、`StagesPipelineEditor`、TaskTemplate 切换 UI。
- 不保留 per-step override 派发弹窗。
- 不保留 `setup_pipeline` / `teardown_pipeline` 作为独立可编辑概念。

旧模型只允许出现在一次性 Alembic 数据迁移脚本中。迁移完成后，应用代码以 Plan 模型为唯一事实源。

### 2. 编排定义使用 Plan / PlanStep

`Plan` 表达一个完整测试计划。一个 Plan 内只包含一个执行单元，通过 `init`、`patrol`、`teardown` 三个 phase 组织步骤。

建议 schema：

```sql
CREATE TABLE plan (
    id                      SERIAL PRIMARY KEY,
    name                    VARCHAR(256) NOT NULL,
    description             TEXT,
    failure_threshold       FLOAT NOT NULL DEFAULT 0.05,
    patrol_interval_seconds INTEGER,
    next_plan_id            INTEGER REFERENCES plan(id),
    watcher_policy          JSONB,
    created_by              VARCHAR(128),
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (next_plan_id IS NULL OR next_plan_id <> id)
);

CREATE TABLE plan_step (
    id              SERIAL PRIMARY KEY,
    plan_id         INTEGER NOT NULL REFERENCES plan(id) ON DELETE CASCADE,
    stage           VARCHAR(16) NOT NULL CHECK (stage IN ('init', 'patrol', 'teardown')),
    sort_order      INTEGER NOT NULL DEFAULT 0,
    step_key        VARCHAR(128) NOT NULL,
    script_name     VARCHAR(128) NOT NULL,
    script_version  VARCHAR(32) NOT NULL,
    timeout_seconds INTEGER NOT NULL DEFAULT 300,
    retry           INTEGER NOT NULL DEFAULT 0,
    enabled         BOOLEAN NOT NULL DEFAULT true,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (plan_id, step_key)
);

> **命名约定**：本表使用 `stage` 列名（与 `step_trace.stage` 对齐），值域仍是 ADR 早期文档中提到的"phase"枚举（init / patrol / teardown）。`pipeline_def.lifecycle` 内部分组键仍直接使用枚举值（不出现 `stage` 也不出现 `phase`），是 Agent 协议的稳定面。
```

约束：

- `PlanStep.step_key` 是稳定步骤标识，用于生成 `pipeline_def.lifecycle.*[].step_id`，不得随排序变化。
- `patrol_interval_seconds IS NULL` 表示没有 Patrol 阶段。
- `patrol_interval_seconds IS NOT NULL` 时必须存在至少一个 enabled patrol step（`stage = 'patrol'`）。
- Init 阶段（`stage = 'init'`）必须至少存在一个 enabled step，以满足 Agent 侧 lifecycle 校验。
- Teardown 阶段（`stage = 'teardown'`）可为空，但生成的 `pipeline_def.lifecycle.teardown` 必须是数组。
- Dispatcher 生成 lifecycle 时只包含 `enabled = true` 的 PlanStep。
- Plan 创建以及更新 `next_plan_id` 时必须做链路 DAG 校验，拒绝 `next_plan_id` 形成 A -> B -> A 这类长链循环。数据库 CHECK 只能防自环，不能替代应用层环检测。
- DAG 校验必须具备事务级一致性保证。实现时应在同一事务内对涉及链路的 Plan 行加行锁，或使用 PostgreSQL advisory lock 后再校验并写入，避免并发提交分别通过本地校验后合并成环。

### 3. 参数维护归属于脚本管理

Plan 编排器只选择脚本版本和维护执行配置，不提供任意 JSON 参数覆盖。脚本默认参数归属于脚本管理，并按强可追溯规则版本化。

参数来源规则：

- 脚本管理路由负责扫描脚本路径，将脚本纳入脚本管理界面，并维护脚本的 `param_schema`、`default_params` 和 `version`。
- `Script` schema 需补齐 `default_params JSONB NOT NULL DEFAULT '{}'`。`param_schema` 描述参数结构，`default_params` 是该版本实际执行默认值。
- 修改脚本默认参数必须创建新的脚本版本，不允许对既有版本原地改写默认参数。即使尚未被 Plan 引用，也按新版本保存，保证历史 PlanRun、JobInstance 与报告可追溯到当时的脚本参数定义。
- Plan 管理界面只读展示所选脚本版本的 `param_schema` 与 `default_params`，不能修改参数值。
- Dispatcher 生成 `pipeline_def` 时，从脚本版本的 `default_params` 生成 `params`。
- 如果历史 pipeline step 中存在非空 `params`，迁移脚本先尝试折叠到对应 `script` 版本的默认参数；若同一 `script_name + version` 出现冲突参数，preflight 必须失败并要求人工处理。
- 需要不同参数组合时，应创建新的脚本版本或后续引入脚本配置模板，而不是恢复 Plan 内 per-step override。
- 不做隐式 latest 绑定。即使某个脚本只有一个 active version，PlanStep 也必须显式记录 `script_version`。

### 4. 用户入口边界

新模型中用户入口分为三类：

- 脚本管理：扫描脚本路径，维护脚本版本、参数 schema 和默认参数。
- 计划管理：独立模块，只负责编排并保存 Plan / PlanStep；脚本参数只读展示，不在 Plan 内覆盖。
- 执行任务：读取计划管理中保存好的 Plan，用户选择 Plan 和设备后执行，生成 PlanRun / JobInstance。

`pipeline_def.lifecycle.*[].action = "script:<name>"` 仍是 Agent 执行脚本 step 的内部协议格式，不代表保留旧的临时脚本执行 API 或可绕过 Plan 的执行入口。

### 5. Plan 链替代 Block 和多 TaskTemplate

不引入 `Block` 表。

多段测试流程通过 `plan.next_plan_id` 串联：

```text
Plan A -> Plan B -> Plan C
```

链式执行继承上一段 PlanRun 的设备集合，但每个 Plan 都创建独立的 `PlanRun` 和 `JobInstance`，避免日志、产物、状态和失败归因混在同一运行单元内。

多 TaskTemplate 的旧 Workflow 迁移为多个 Plan 串联，按 `task_template.sort_order` 排序：

- 单 TaskTemplate：WorkflowDefinition 迁移为一个 Plan，名称尽量保持原 Workflow 名称。
- 多 TaskTemplate：每个 TaskTemplate 迁移为一个 Plan，名称使用 `"{workflow.name} / {template.name}"`。
- `setup_pipeline.lifecycle.init` 合并到链首 Plan 的 Init 前部。
- `teardown_pipeline.lifecycle.teardown` 合并到链尾 Plan 的 Teardown 尾部。
- 每个生成的 Plan 复制原 Workflow 的 `failure_threshold` 和 `watcher_policy`。

这是一次语义收敛：旧模型中 TaskTemplate 扇出表达的多个任务单元，迁移后显式表达为多个顺序 Plan。

代价是 wall-clock 时间可能变长：旧多 TaskTemplate Workflow 在同一个 WorkflowRun 内按 `device x template` 扇出，迁移后变成顺序 Plan 链。preflight 必须输出多 TaskTemplate Workflow 清单及其关联 schedule，由产品和运维确认时间预算变化。

### 6. PlanRun 替代 WorkflowRun

建议 schema：

```sql
CREATE TABLE plan_run (
    id                  SERIAL PRIMARY KEY,
    plan_id             INTEGER NOT NULL REFERENCES plan(id),
    status              VARCHAR(32) NOT NULL DEFAULT 'RUNNING',
    failure_threshold   FLOAT NOT NULL DEFAULT 0.05,
    triggered_by        VARCHAR(128),
    parent_plan_run_id  INTEGER REFERENCES plan_run(id),
    root_plan_run_id    INTEGER REFERENCES plan_run(id),
    chain_index         INTEGER NOT NULL DEFAULT 0,
    next_plan_triggered BOOLEAN NOT NULL DEFAULT false,
    plan_snapshot       JSONB NOT NULL,
    result_summary      JSONB,
    run_type            VARCHAR(16) NOT NULL,
    run_context         JSONB,
    started_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    ended_at            TIMESTAMPTZ,
    CHECK (run_type IN ('MANUAL', 'SCHEDULE', 'CHAIN'))
);

CREATE UNIQUE INDEX uniq_plan_run_chain_child
    ON plan_run (parent_plan_run_id, plan_id)
 WHERE parent_plan_run_id IS NOT NULL;
```

字段语义：

- `run_type = MANUAL`：用户从派发页手动触发。
- `run_type = SCHEDULE`：定时任务触发。
- `run_type = CHAIN`：上游 PlanRun 终态后自动触发。
- `run_context` 只存触发来源、设备集合摘要、操作者、schedule id 等运行上下文，不再承载编排定义。
- `plan_snapshot` 是 PlanRun 创建时刻的 Plan + PlanStep 编排定义快照。`job_instance.pipeline_def` 是 Agent 消费的每设备执行快照，可包含资源池注入等 device-specific 参数。两者不等价，报告页需要展示编排定义时读 `plan_snapshot`，需要展示实际执行内容时读 `job_instance.pipeline_def`。

`plan_snapshot` 最小结构：

```json
{
  "plan": {
    "id": 1,
    "name": "Monkey 稳定性测试",
    "description": "对设备进行 Monkey 压力测试并监控日志",
    "failure_threshold": 0.05,
    "patrol_interval_seconds": 60,
    "watcher_policy": {}
  },
  "steps": [
    {
      "stage": "init",
      "sort_order": 0,
      "step_key": "init_0_monkey_setup",
      "script_name": "monkey_setup",
      "script_version": "1.0.0",
      "param_schema": {},
      "default_params": {},
      "timeout_seconds": 300,
      "retry": 0,
      "enabled": true
    }
  ]
}
```

快照隔离规则：

- Plan 任意时刻可编辑。
- 进行中的 PlanRun 和 JobInstance 使用创建时刻的 `plan_snapshot` / `pipeline_def`，后续 Plan 编辑不回溯。
- PlanRun 创建时复制 Plan 的 `failure_threshold`，后续 Plan 修改不影响已创建 PlanRun 的阈值判定。
- 每个 PlanRun 仅使用其 Plan 自身的 `watcher_policy`，不继承上游 PlanRun 或 root PlanRun 的 watcher_policy。

链式触发幂等要求：

- `parent_plan_run_id` 记录由哪个 PlanRun 触发。
- `root_plan_run_id` 记录链路根运行，便于查询整条执行链。
- `next_plan_triggered` 防止重复触发。
- 链式触发仅在上游 PlanRun 终态属于 `SUCCESS` 或 `PARTIAL_SUCCESS` 时执行。
- `FAILED`、`DEGRADED`、`ABORTED`、`UNKNOWN` 等终态中止执行链。
- `uniq_plan_run_chain_child` 防止同一个 `parent_plan_run_id + plan_id` 创建重复 next PlanRun。
- 触发逻辑必须在事务内锁定父 PlanRun 行，检查 `next_plan_triggered = false` 后创建子 PlanRun，再回写 `next_plan_triggered = true`。

### 7. JobInstance 作为执行实例直接复用

`JobInstance` 是运行基础设施，不是旧 Workflow 专属概念。新模型继续复用它承载设备级执行：

```text
一个 JobInstance = 一个 PlanRun 在一台 device 上的 pipeline_def 快照
```

迁移后 `job_instance` 的编排外键改为：

```sql
ALTER TABLE job_instance ADD COLUMN plan_run_id INTEGER REFERENCES plan_run(id);
ALTER TABLE job_instance ADD COLUMN plan_id INTEGER REFERENCES plan(id);
```

数据迁移完成并校验通过后：

```sql
ALTER TABLE job_instance ALTER COLUMN plan_run_id SET NOT NULL;
ALTER TABLE job_instance ALTER COLUMN plan_id SET NOT NULL;
ALTER TABLE job_instance DROP COLUMN workflow_run_id;
ALTER TABLE job_instance DROP COLUMN task_template_id;
```

保留不变：

- `job_instance.pipeline_def`，作为 Agent 消费的执行快照。
- `step_trace.job_id -> job_instance.id`。
- `job_artifact.job_id -> job_instance.id`。
- `job_log_signal.job_id -> job_instance.id`。
- `resource_allocation.job_instance_id -> job_instance.id`。
- 设备锁、lease、recycler、watcher、post-completion 继续以 JobInstance 为运行事实源。

### 8. Agent 协议保持 lifecycle 快照，不感知 Plan 表

Agent 不读取 Plan / PlanStep。控制面 dispatcher 在创建 JobInstance 时生成：

```json
{
  "lifecycle": {
    "init": [],
    "patrol": {
      "interval_seconds": 60,
      "steps": []
    },
    "teardown": []
  }
}
```

每个 step 至少包含：

```json
{
  "step_id": "stable_step_key",
  "action": "script:script_name",
  "version": "1.0.0",
  "params": {},
  "timeout_seconds": 300,
  "retry": 0,
  "enabled": true
}
```

因此 Agent 端不需要感知新表，但 Agent 侧 contract 中的 `workflow_run_id` / `task_template_id` 命名应改为 `plan_run_id` / `plan_id`，避免新代码继续传播旧概念。

这是 breaking change，不做滚动兼容。控制面切换前必须通过既有 Agent 发布通道完成 Agent 代码同步，并在 Phase 0 校验所有在线 Agent 版本满足 Plan 协议要求。切换窗口内不得存在 in-flight job，否则旧 Agent 的续租、trace、complete 上报可能因字段不匹配或旧 URL 被删除而失败。

## 一次性迁移方案

### Phase 0：停机与备份

上线前执行：

1. 停止 API、scheduler、SAQ worker、Agent claim 入口、Recycler、device_lease_reconciler。
2. 备份数据库，至少包含：
   - `workflow_definition`
   - `task_template`
   - `workflow_run`
   - `job_instance`
   - `step_trace`
   - `job_artifact`
   - `job_log_signal`
   - `resource_allocation`
   - `task_schedules`
3. 停止所有 Agent 进程，或确认所有 Agent 无 in-flight Job 且已升级到支持 Plan 协议的版本。
4. 记录当前 Alembic revision。
5. 创建回滚锚点：
   - Git tag：`pre-adr-0020-YYYYMMDDHHmm`
   - 数据库备份：`stp_pre_adr_0020_YYYYMMDDHHmm.dump`
   - 记录控制面镜像 tag / commit sha / Agent 包版本。

本路线不承诺通过 downgrade 自动恢复旧模型。失败回滚方式是恢复备份数据库并回退应用版本。

### Phase 1：Preflight 校验

迁移前必须输出并人工确认：

- WorkflowDefinition 总数。
- TaskTemplate 总数。
- 多 TaskTemplate Workflow 清单。
- 多 TaskTemplate Workflow 关联的 `task_schedules` 清单。
- 非 lifecycle 格式 pipeline_def 清单。
- 缺少 init step 的 pipeline_def 清单。
- patrol interval 非正整数或 patrol steps 为空的清单。
- 非 `script:<name>` action 清单。
- script action 缺少 version 的清单。
- 引用不存在或 inactive script 的清单。
- step `params` 与脚本默认参数冲突清单。
- 当前 RUNNING / PENDING / UNKNOWN JobInstance 清单。
- `JobInstance`、`StepTrace`、`JobArtifact`、`ResourceAllocation` 的孤儿引用清单。
- `step_trace`、`job_artifact`、`job_log_signal`、`resource_allocation` 是否存在 `workflow_run_id`、`task_template_id`、`workflow_definition_id` 等旧外键或冗余字段的扫描结果。
- Agent 在线版本一致性清单。

阻断规则：

- 存在活跃 Job 时不迁移。
- 存在不可转换 pipeline_def 时不迁移。
- 存在参数冲突且无法折叠到脚本默认值时不迁移。
- 存在孤儿引用时先修复或归档，不在应用代码里写兼容分支。

### Phase 2：创建新表与临时映射表

创建：

- `plan`
- `plan_step`
- `plan_run`
- `plan_migration_audit`

`plan_migration_audit` 至少记录：

| 字段 | 说明 |
|------|------|
| `old_workflow_definition_id` | 原 WorkflowDefinition id |
| `old_task_template_id` | 原 TaskTemplate id，可为空 |
| `old_workflow_run_id` | 原 WorkflowRun id，可为空 |
| `new_plan_id` | 新 Plan id |
| `new_plan_run_id` | 新 PlanRun id，可为空 |
| `chain_index` | 多模板拆链后的顺序 |
| `note` | setup/teardown 归属、异常说明 |

该表用于迁移校验和问题追踪，不进入业务查询路径。

### Phase 3：迁移定义数据

迁移规则：

1. 按 WorkflowDefinition 分组读取 TaskTemplate。
2. 单模板 Workflow 生成一个 Plan。
3. 多模板 Workflow 按 `sort_order, id` 生成多个 Plan，并设置 `next_plan_id`。
4. 将 pipeline lifecycle 拆为 `plan_step` 行。
5. `setup_pipeline.lifecycle.init` 插入链首 Plan 的 Init 前部。
6. `teardown_pipeline.lifecycle.teardown` 插入链尾 Plan 的 Teardown 尾部。
7. 为每个 step 生成稳定 `step_key`：
   - 若旧 step 有 `step_id`，保留并去重。
   - 若没有，则使用 `{phase}_{sort_order}_{script_name}`，冲突时追加序号。
8. 写入 `plan_migration_audit`。

### Phase 4：迁移运行数据

迁移规则：

1. 单模板 WorkflowRun 生成一个 PlanRun。
2. 多模板 WorkflowRun 为每个旧 TaskTemplate 对应的 Plan 生成一个 PlanRun。
3. 多模板产生的 PlanRun 按 `chain_index` 连接 `parent_plan_run_id`，并共享 `root_plan_run_id`。
4. `run_type` 填充规则：
   - 能通过 `task_schedules.workflow_definition_id` 关联到旧定时任务的根 PlanRun 填 `SCHEDULE`。
   - 不能关联旧定时任务的根 PlanRun 填 `MANUAL`。
   - 多模板拆链产生的非首段 PlanRun 填 `CHAIN`。
5. `plan_snapshot` 以迁移时刻该 PlanRun 关联的 Plan / PlanStep 当前定义生成。
6. 历史 PlanRun 的 `plan_snapshot` 不保证完整还原原 WorkflowRun 创建时刻定义。这是一次性切换的已知代价；历史执行的实际步骤仍以既有 `job_instance.pipeline_def` 为准。
7. 旧 JobInstance 通过 `(workflow_run_id, task_template_id)` 定位新 PlanRun，回填 `plan_run_id` 和 `plan_id`。
8. `StepTrace`、`JobArtifact`、`JobLogSignal`、`ResourceAllocation` 不搬表，只继续通过 `job_id` 指向原 JobInstance。

### Phase 5：收紧约束并删除旧模型

校验通过后执行：

```sql
ALTER TABLE job_instance ALTER COLUMN plan_run_id SET NOT NULL;
ALTER TABLE job_instance ALTER COLUMN plan_id SET NOT NULL;

DROP INDEX IF EXISTS idx_job_instance_workflow;
ALTER TABLE job_instance DROP COLUMN workflow_run_id;
ALTER TABLE job_instance DROP COLUMN task_template_id;

DROP TABLE workflow_run;
DROP TABLE task_template;
DROP TABLE workflow_definition;
```

同步修改 `task_schedules`：

- 删除 `workflow_definition_id`。
- 删除 `task_template_id`。
- 删除 `tool_id`。
- 删除 `task_type`。
- 新增 `plan_id INTEGER REFERENCES plan(id)`。
- 定时任务只触发 Plan。

当前项目尚未上线生产，旧 `task_schedules` 行不作为必须保留的数据资产。Phase 5 不要求把旧 schedule 行回填为新 Plan schedule；preflight 只输出旧 schedule 清单，供切换后人工按 Plan 重建。实现可选择 drop/recreate `task_schedules`，或在同一事务中 truncate 旧行后收紧为 Plan-only schema。

该取舍会丢失旧定时任务配置。如果停机窗口前已有有效定时任务，未人工重建会导致切换后不再自动触发；这不影响历史执行记录，但会影响运维自动化，需要在 Phase 0 checklist 中明确确认。

Alembic 删除顺序必须显式：先 drop 依赖旧列的 index / constraint，再 drop column，最后 drop table。不得依赖 PostgreSQL cascade 隐式清理旧依赖。

Phase 5 的 `ALTER ... SET NOT NULL`、`DROP COLUMN`、`DROP TABLE` 必须包裹在单一事务中执行。任何步骤失败即整体回滚，并按 Phase 0 的 Git tag 和数据库备份锚点恢复，不得部分提交后人工补写。

### Phase 6：应用代码切换

删除或替换：

- `backend.models.workflow.WorkflowDefinition`
- `backend.models.workflow.WorkflowRun`
- `backend.models.job.TaskTemplate`
- `backend.services.dispatcher.dispatch_workflow`
- `/api/v1/workflows`
- `/api/v1/workflow-runs`
- `/api/v1/templates`
- `/api/v1/script-executions`
- `backend.services.script_execution`
- 旧 Workflow 前端页面和旧 Pipeline 编辑器
- 旧临时脚本执行页面、脚本执行历史页面
- 前端 `WorkflowDefinition` / `TaskTemplateEntry` / `WorkflowRun` / TaskTemplate 类型

新增：

- Plan / PlanStep / PlanRun ORM。
- Plan CRUD API。
- Plan dispatcher。
- PlanRun 查询和矩阵页 API。
- Plan 编排页。
- 派发页选择 Plan。
- scheduler 触发 Plan。
- results / report / post-completion 按 PlanRun / JobInstance 查询。
- 脚本管理中的 `default_params` 维护与版本化创建能力。
- 执行任务页的 Plan 选择与设备选择派发入口。

### Phase 7：验证

必须通过：

- Alembic 在备份库上的完整 upgrade。
- `rg "WorkflowDefinition|WorkflowRun|TaskTemplate|workflow_definition|workflow_run|task_template|dispatch_workflow|setup_pipeline|teardown_pipeline|script_executions|script-executions|/templates"`，确认应用代码无旧模型和旧执行入口引用。
- Plan CRUD 单元测试。
- PlanStep lifecycle builder 单元测试。
- Dispatcher 集成测试：PlanRun + 每设备一个 JobInstance + pipeline_def 快照正确。
- Plan 链式触发幂等测试。
- Agent claim / run / complete E2E。
- StepTrace、Artifact、ResourceAllocation 旧数据查询验证。
- 旧 JobInstance 的 StepTrace / JobArtifact 可从 PlanRun 详情入口访问。
- scheduler 立即执行和 cron 执行验证。
- 前端 Plan 列表、Plan 编辑、派发、Run 矩阵冒烟测试。
- Prometheus / dashboard / alert 中的 `workflow_run_*` 指标命名是否需要改为 `plan_run_*` 的影响评估。

## 备选方案与权衡

### 方案 A：双写兼容期

优点：

- 单次上线风险较低。
- 可在一段时间内新旧路径并行验证。

缺点：

- 应用代码长期存在 Workflow 和 Plan 两套模型。
- dispatcher、API、前端类型、报表查询容易出现条件分支。
- 与本次重构目标冲突，可能留下新的技术债。

结论：不采用。

### 方案 B：归档旧历史，新模型从空数据开始

优点：

- 代码和迁移最干净。
- 不需要处理历史 WorkflowRun / JobInstance 映射。

缺点：

- 平台内无法继续查看旧运行历史。
- 需要额外提供离线归档查询方式。

结论：作为回退选项保留；默认仍迁移历史 JobInstance，因为现有历史量不大。

### 方案 C：一次性切换并迁移历史数据

优点：

- 应用代码最终只有一套模型。
- 复用 JobInstance 运行基础设施，避免复制 StepTrace、Artifact、ResourcePool、watcher、recycler。
- 历史运行仍可通过新模型查询。

缺点：

- Alembic 迁移和 preflight 要更严格。
- 需要停机窗口。
- 回滚依赖数据库备份恢复。

结论：采用。

## 影响

正向影响：

- 用户只理解 Plan 和 Step，编排概念显著减少。
- 后端删除 TaskTemplate 和 setup/teardown 拼接逻辑。
- JobInstance 继续作为执行事实源，运行基础设施可复用。
- 新代码不携带旧模型兼容分支。

风险：

- 多 TaskTemplate 迁移为顺序 Plan 链，会改变旧模型中并行扇出的语义。
- 参数从 Plan 编排器移出后，历史 per-step params 需要 preflight 收敛。
- 禁止 PlanStep 参数覆盖会增加脚本版本数量；这是强可追溯的刻意取舍。修改脚本默认参数必须创建新脚本版本，避免历史运行被静默改写。如果后续频繁出现"同脚本不同参数"场景，应单独设计脚本配置模板，而不是在 PlanStep 上恢复任意 JSON override。
- 删除临时脚本执行链路后，用户必须先保存 Plan 再执行；这会增加一次编排步骤，但能统一审计、报告和权限边界。
- 旧 `task_schedules` 不回填会丢失旧定时任务配置，需要停机窗口后按 preflight 清单人工重建。
- 删除旧表后，回滚必须依赖备份。
- Plan 链存在循环风险，必须通过 Plan 保存时的 DAG 校验和数据库自环 CHECK 同时防护。
- Agent 协议字段重命名是 breaking change，要求控制面和 Agent 在同一停机窗口同步升级。
- ADR-0018 中提到的 dispatch 扇出职责需从 TaskTemplate x Device 改为 Plan x Device。
- ADR-0019 中如有旧字段兼容投影，应在本 ADR 实施时重新评审，避免与“一次性切换”原则冲突。

`plan_migration_audit` 保留至少 6 个月。到期后可转入 archive schema 或导出到冷存储，不参与业务查询。

## 落地与后续动作

1. 更新 `docs/plan-block-step-migration.md`，删除策略 A/B 可选表述，固定为一次性切换。
2. 编写 preflight SQL。
3. 编写 Alembic migration，并在备份库上演练。
4. 实现 Plan / PlanStep / PlanRun ORM 与 API。
5. 改造 dispatcher、scheduler、results、report、post-completion。
6. 替换前端编排和派发页面。
7. 删除旧 ORM、旧 API、旧前端组件。
8. 执行全量 grep 和 E2E 验证。
9. 评估 Schedule 抖动短时间重复触发同一 Plan 的去重策略；`uniq_plan_run_chain_child` 只约束链式子 PlanRun，不约束 root PlanRun。

## 关联实现/文档

- `docs/plan-step-design-rationale.md`
- `docs/plan-block-step-migration.md`
- `docs/prototypes/workflow-editor-redesign-v3.html`
- `backend/models/job.py`
- `backend/models/workflow.py`
- `backend/services/dispatcher.py`
- `backend/api/routes/orchestration.py`
- `frontend/src/pages/orchestration/WorkflowDefinitionEditPage.tsx`
- `frontend/src/components/pipeline/StagesPipelineEditor.tsx`
