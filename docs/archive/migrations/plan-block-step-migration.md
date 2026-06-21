# Plan → Step 迁移方案（Block 内化于 Plan）

## 1. 目标模型

```
Plan "Monkey 稳定性测试"                    Plan "DDR 专项测试"
  patrol_interval_seconds = 60                patrol_interval_seconds = NULL
  next_plan_id = 2 ─────────────────────────► (被链式触发)
  │                                           │
  ├─ Init:    [Step, Step]                    ├─ Init:    [Step, Step, Step]
  ├─ Patrol:  [Step]                          └─ Teardown: [Step]
  └─ Teardown: [Step, Step]

一个 Plan = 一个完整的测试计划（如 Monkey 压力测试、DDR 专项测试）。
Plan 内只包含一个执行单元，通过 Init / Patrol / Teardown 三个阶段组织步骤。
Plan 通过 next_plan_id 形成执行链：Plan A 完成后自动触发 Plan B。
```

核心约束：
- **一个 Plan 只有一个执行单元**（不再有 TaskTemplate 列表或 Block 列表）
- **阶段是 Plan 的直接属性**（Init / Patrol / Teardown），不是嵌套对象
- **Patrol 可选**：`patrol_interval_seconds IS NULL` 时 Plan 无巡检阶段
- **链式执行**：`next_plan_id` 自引用实现 Plan 间的顺序串联

## 2. 现状对照

| 旧概念 | 新概念 | 变化 |
|--------|--------|------|
| WorkflowDefinition | Plan | 增加 next_plan_id + patrol_interval_seconds |
| TaskTemplate（多个） | 无 | 一个 Plan 只对应一个执行单元 |
| setup_pipeline (JSONB) | Plan 自身的 Init 阶段首部步骤 | 不存为独立概念 |
| teardown_pipeline (JSONB) | Plan 自身的 Teardown 阶段尾部步骤 | 不存为独立概念 |
| task_template.pipeline_def.lifecycle.init[] | plan_step (stage='init') | JSON 拆为行 |
| task_template.pipeline_def.lifecycle.patrol | plan_step (stage='patrol') + plan.patrol_interval_seconds | 间隔时间升级为 Plan 字段 |
| task_template.pipeline_def.lifecycle.teardown[] | plan_step (stage='teardown') | JSON 拆为行 |
| WorkflowRun | PlanRun | 增加 next_plan_triggered |
| JobInstance | 保留 | task_template_id → plan_id |
| StepTrace | 保留 | 无变化 |

## 3. 数据库迁移

### 3.1 新增表

```sql
-- plan 表（替代 workflow_definition + task_template）
CREATE TABLE plan (
    id                      SERIAL PRIMARY KEY,
    name                    VARCHAR(256) NOT NULL,
    description             TEXT,
    failure_threshold       FLOAT NOT NULL DEFAULT 0.05,
    patrol_interval_seconds INTEGER,          -- NULL = 无 Patrol 阶段
    next_plan_id            INTEGER REFERENCES plan(id),
    watcher_policy          JSONB,
    created_by              VARCHAR(128),
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_plan_next ON plan(next_plan_id);

-- plan_step 表（替代所有 pipeline_def JSONB 中的 step）
CREATE TABLE plan_step (
    id              SERIAL PRIMARY KEY,
    plan_id         INTEGER NOT NULL REFERENCES plan(id) ON DELETE CASCADE,
    stage           VARCHAR(16) NOT NULL CHECK (stage IN ('init', 'patrol', 'teardown')),
    sort_order      INTEGER NOT NULL DEFAULT 0,
    step_key        VARCHAR(256) NOT NULL,
    script_name     VARCHAR(128) NOT NULL,
    script_version  VARCHAR(32) NOT NULL,
    timeout_seconds INTEGER,
    retry           INTEGER NOT NULL DEFAULT 0,
    enabled         BOOLEAN NOT NULL DEFAULT true,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (plan_id, step_key)
);
CREATE INDEX idx_plan_step_plan ON plan_step(plan_id, stage, sort_order);

-- plan_run 表（替代 workflow_run）
CREATE TABLE plan_run (
    id                  SERIAL PRIMARY KEY,
    plan_id             INTEGER NOT NULL REFERENCES plan(id),
    status              VARCHAR(32) NOT NULL DEFAULT 'RUNNING',
    failure_threshold   FLOAT NOT NULL DEFAULT 0.05,
    triggered_by        VARCHAR(128),
    next_plan_triggered BOOLEAN NOT NULL DEFAULT false,
    result_summary      JSONB,
    run_type            VARCHAR(64),
    run_context         JSONB,
    started_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    ended_at            TIMESTAMPTZ
);
CREATE INDEX idx_plan_run_plan ON plan_run(plan_id);

-- JobInstance 修改
ALTER TABLE job_instance ADD COLUMN plan_id INTEGER REFERENCES plan(id);
-- 数据迁移完成后删除 task_template_id 列
```

### 3.2 数据迁移逻辑

```
workflow_definition ──────────────────────────────────────────► plan
  id, name, description, failure_threshold                         id, name, description, failure_threshold
  (setup_pipeline/teardown_pipeline 的步骤合并到 plan_step)        patrol_interval_seconds (来自首个 task_template 的 pipeline_def)

task_template ────────────────────────────────────────────────► （不产生新行）
  workflow_definition_id = 42                                     该 workflow 的所有 task_template
  pipeline_def.lifecycle.init/patrol/teardown                    合并为 plan 42 的 plan_step 行
  （仅取第一个 task_template，如果有多个则按 sort_order 合并步骤）

workflow_run ─────────────────────────────────────────────────► plan_run
  workflow_definition_id → plan_id                                next_plan_triggered = false
```

### 3.3 特殊处理：多 TaskTemplate 的 Workflow

当前可能存在一个 Workflow 下多个 TaskTemplate（如 default / foreground_watch / resource_smoke）。迁移策略：

**策略 A（推荐）**：每个 TaskTemplate 拆为独立 Plan，通过 `next_plan_id` 串联。
- `default` → Plan "Monkey 压测 - default"，next_plan_id 指向 `foreground_watch`
- `foreground_watch` → Plan "Monkey 压测 - foreground_watch"，next_plan_id 指向 `resource_smoke`
- Setup/Teardown pipeline 的步骤合并入首个和末个 Plan

**策略 B（保守）**：多 TaskTemplate 的步骤按 sort_order 合并入同一个 Plan。
- 所有 template 的 init 步骤按顺序排列 → Plan 的 Init
- 所有 template 的 patrol 步骤取第一个有 patrol 的 → Plan 的 Patrol
- 所有 template 的 teardown 步骤按顺序排列 → Plan 的 Teardown

策略 B 迁移简单但语义上不精确。策略 A 更符合目标模型（一个 Plan = 一个测试单元），但需要处理 Setup/Teardown pipeline 的归属。

### 3.4 Alembic 版本规划

| 版本 | 内容 | 可回滚 |
|------|------|--------|
| M1 | CREATE plan / plan_step / plan_run + job_instance.plan_id | 是 |
| M2 | 数据迁移（策略 A 或 B） | 是 |
| M3 | 应用代码切换到新表 | 是 |
| M4 | DROP 旧表 | 需备份确认 |

## 4. API 变更

### 4.1 端点

| 端点 | 说明 |
|------|------|
| `GET /api/v1/plans` | Plan 分页列表（返回链式关系） |
| `POST /api/v1/plans` | 创建 Plan（含 steps） |
| `GET /api/v1/plans/{id}` | Plan 详情（含 steps 按 stage 分组） |
| `PUT /api/v1/plans/{id}` | 更新 Plan 结构 |
| `DELETE /api/v1/plans/{id}` | 删除 Plan |
| `POST /api/v1/plans/{id}/run` | 触发执行 |
| `POST /api/v1/plans/{id}/run/preview` | 派发预览 |
| `GET /api/v1/plan-runs` | PlanRun 分页 |
| `GET /api/v1/plan-runs/{id}` | PlanRun 详情矩阵 |

### 4.2 请求体示例

```json
{
  "name": "Monkey 稳定性测试",
  "description": "对设备进行 Monkey 压力测试并监控日志",
  "failure_threshold": 0.05,
  "patrol_interval_seconds": 60,
  "next_plan_id": 2,
  "steps": [
    { "stage": "init",     "sort_order": 0, "step_key": "init_0_monkey_setup",    "script_name": "monkey_setup",    "script_version": "1.0.0", "timeout_seconds": 60 },
    { "stage": "init",     "sort_order": 1, "step_key": "init_1_monkey_launch",   "script_name": "monkey_launch",   "script_version": "2.0.0", "timeout_seconds": 30 },
    { "stage": "patrol",   "sort_order": 0, "step_key": "patrol_0_monkey_check", "script_name": "monkey_check",    "script_version": "1.0.0", "timeout_seconds": 30 },
    { "stage": "teardown", "sort_order": 0, "step_key": "td_0_monkey_teardown",  "script_name": "monkey_teardown", "script_version": "1.0.0", "timeout_seconds": 30 },
    { "stage": "teardown", "sort_order": 1, "step_key": "td_1_clean_env",         "script_name": "clean_env",       "script_version": "1.0.0", "timeout_seconds": 30 }
  ]
}
```

### 4.3 Dispatcher 简化

```python
# 旧: dispatch_workflow
#   1. 加载 WorkflowDefinition + TaskTemplates
#   2. _resolve_pipeline(setup, task_pipeline, teardown) 合并
#   3. per (device × template) 创建 JobInstance

# 新: dispatch_plan
#   1. 加载 Plan + Steps
#   2. 按 stage 分组组装 pipeline_def（lifecycle 格式）
#   3. per device 创建 1 个 JobInstance（一个 Plan 只产生一个 Job）
async def dispatch_plan(plan_id, device_ids, failure_threshold, triggered_by, db):
    plan = await db.get(Plan, plan_id)
    steps = await db.execute(
        select(PlanStep).where(PlanStep.plan_id == plan_id).order_by(PlanStep.stage, PlanStep.sort_order)
    )
    steps = steps.scalars().all()

    pipeline_def = _build_lifecycle(steps, plan.patrol_interval_seconds)

    run = PlanRun(plan_id=plan_id, status="RUNNING", ...)
    db.add(run)
    await db.flush()

    for device_id in device_ids:
        job = JobInstance(plan_run_id=run.id, plan_id=plan_id, device_id=device_id, pipeline_def=pipeline_def, ...)
        db.add(job)
    # ...
```

## 5. Plan 链式触发

```python
# Plan 对应的 PlanRun 终态时：
async def _on_plan_completed(plan_run: PlanRun, db: AsyncSession):
    plan = await db.get(Plan, plan_run.plan_id)
    if plan.next_plan_id and not plan_run.next_plan_triggered:
        next_run = await dispatch_plan(
            plan_id=plan.next_plan_id,
            device_ids=_devices_from_run(plan_run),  # 继承当前执行的设备
            failure_threshold=plan.failure_threshold,
            triggered_by=f"chain:plan_run:{plan_run.id}",
            db=db,
        )
        plan_run.next_plan_triggered = True
        logger.info("plan_chain_triggered: %d → %d (run=%d)", plan.id, plan.next_plan_id, next_run.id)
```

## 6. Agent 端

**零变化**。pipeline_engine 消费 `{ "lifecycle": { "init": [...], "patrol": {...}, "teardown": [...] } }`，Dispatcher 在创建 Job 时按 stage 组装即可。

## 7. 前端

### 7.1 页面结构

- **Plan 列表页** (`/plans`)：展示所有 Plan，用 `→` 箭头可视化链式关系
- **Plan 编排页** (`/plans/:id/edit`)：Plan 元信息 + Init/Patrol/Teardown 三个 stage 区域 + 步骤列表
- **Plan 只读页** (`/plans/:id`)：同上但只读，顶部有 [发起测试] 按钮
- **派发页** (`/execution/run`)：选 Plan + 选设备 → 确认执行
- **Run 矩阵页**：适配新 API，无结构变化

### 7.2 编排页布局

```
┌────────────────────────────────────────────────────────────┐
│  Plan 名称: [Monkey 稳定性测试]   Patrol:[60s ▾]  阈值:[5%]│
│  链式执行: 当前 Plan → [DDR 专项测试 ▾]                     │
├────────────────────────────────────────────────────────────┤
│                                                            │
│  ┌─ Init ──────────────────────────────────────────────┐  │
│  │  #1 monkey_setup    v1.0.0   60s   retry 0  [↑↓⧉✕] │  │
│  │  #2 monkey_launch   v2.0.0   30s   retry 0  [↑↓⧉✕] │  │
│  │  [+ 添加步骤]                                        │  │
│  └─────────────────────────────────────────────────────┘  │
│                                                            │
│  ┌─ Patrol · ♻ 每 60s 循环 ───────────────────────────┐  │
│  │  #1 monkey_check    v1.0.0   30s   retry 0  [↑↓⧉✕] │  │
│  │  [+ 添加步骤]                                        │  │
│  └─────────────────────────────────────────────────────┘  │
│                                                            │
│  ┌─ Teardown ──────────────────────────────────────────┐  │
│  │  #1 monkey_teardown v1.0.0   30s   retry 0  [↑↓⧉✕] │  │
│  │  #2 clean_env       v1.0.0   30s   retry 0  [↑↓⧉✕] │  │
│  │  [+ 添加步骤]                                        │  │
│  └─────────────────────────────────────────────────────┘  │
│                                                            │
└────────────────────────────────────────────────────────────┘
```

### 7.3 Plan 列表页展示链式关系

```
┌─────────────────────────────────────────┐
│  Monkey 稳定性测试                       │
│  Init 2 · Patrol 1 · TDown 2 · 5 Steps  │
│              │                          │
│              ▼ next                     │
│  DDR 专项测试                            │
│  Init 3 · TDown 1 · 4 Steps             │
│              │                          │
│              ▼ next                     │
│  报告生成                         [+ 添加] │
│  Init 1 · 1 Step                        │
└─────────────────────────────────────────┘
```

## 8. 阶段划分

| 阶段 | 范围 | 时间 |
|------|------|------|
| Phase 1 | DB：plan / plan_step / plan_run 建表 + 数据迁移 | 1.5d |
| Phase 2 | 后端：Plan/PlanStep/PlanRun ORM + CRUD API | 2d |
| Phase 3 | 后端：新 Dispatcher（Plan → Job）+ 链式触发 | 1.5d |
| Phase 4 | 后端：Agent API 适配（pipeline_def 组装，兼容旧格式） | 0.5d |
| Phase 5 | 前端：Plan 列表页（含链式关系展示） | 1.5d |
| Phase 6 | 前端：Plan 编排编辑器 | 2.5d |
| Phase 7 | 前端：派发入口 + Run 矩阵适配 | 1d |
| Phase 8 | 切换 + E2E 验证 | 2d |
| Phase 9 | 清理旧表/旧 API/旧组件 | 0.5d |

总估计：约 13 工作日。

## 9. 风险

| 风险 | 缓解 |
|------|------|
| 多 TaskTemplate 的 Workflow 迁移 | 策略 A 拆为多个 Plan 串联，或策略 B 合并且记录原始结构日志 |
| 链式循环 | Plan 编辑时前端做环检测（next_plan_id 不能形成环），后端保存时校验 |
| 链式中断（next Plan 的设备不匹配） | 链式触发时检查 device 可用性，不可用时 PlanRun 标记为 BLOCKED 而非 FAILED |
| pipeline_def 格式兼容性 | 新 Dispatcher 生成的 lifecycle 格式与旧格式完全一致，Agent 端零差异 |
