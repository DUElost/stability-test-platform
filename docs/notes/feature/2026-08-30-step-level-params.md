# 步骤级 params 注入：Web UI 自定义脚本参数（#508）

Status: implemented
Class: feature

## Decision

「版本即参数」不变量保留：脚本版本 `default_params` 不可变（已存在版本 422）。
新增 **步骤级 params**（`plan_step.params` JSONB 可空列），Plan 步骤
（init/patrol/teardown）在 Web UI 可自定义脚本参数（如 `apk_path`），执行时
**步骤级参数覆盖/合并脚本版本 default_params**，无需再为换参数新建脚本版本
+ 三端同步 + Agent 重启。

### 合并语义（唯一事实源）

`backend/services/plan_dispatcher_core.py:build_lifecycle_from_steps`：

```python
merged = deepcopy(default_params)       # 脚本版本默认
if step_params: merged.update(step_params)  # 步骤级覆盖，仅替换声明键
```

- `step.params={"apk_path": "a"}` + default `{"apk_path": "d", "timeout": 30}`
  → `{"apk_path": "a", "timeout": 30}`（未声明键保留）。
- `params=None` / `{}` → 纯 default_params（行为不变）。
- 与前端 `ParamFormCard` 语义一致：`step.params overrides default_params
  overrides schema.default`。

### 快照与重放

`build_plan_snapshot` 的 step 段固化 `params`（步骤级），
`build_lifecycle_from_snapshot` 重放时同样做 default+override 合并——
历史 PlanRun 可复核生效参数。旧快照无 `params` 键 → 仅 default_params，
行为不变。

### API / 前端

- `PlanStepIn` / `PlanStepOut` / `PlanStepCreate` 加 `params?: dict | null`；
  create / update（全量 DELETE+INSERT）/ chain-tail create 三路径透传。
- 前端 `rebuildLifecycleFromPlan` 读回 params；`buildStepsForApi` 仅在非空时
  发 `params` 键（空对象不发，保持既有 Plan 保存后不塞 `{}`）。
- `PlanStepInspector.ParamFormCard` 无 `param_schema` 时降级为**自由键值
  编辑**（原来只读 tags）：default_params 键渲染可编辑输入、支持新增/移除
  自定义键（install_apk 场景 `apk_path` 即新增键）。切换脚本版本时，无
  schema 且无 default_params 的脚本保留自定义参数（不按空 validKeys 清空）。

### 影响面

| 文件 | 改动 |
|------|------|
| `backend/models/plan.py` | `PlanStep.params` JSONB 列 |
| `backend/alembic/versions/e3f4a5b6c7d8_plan_step_params.py` | 新增列（可空） |
| `backend/api/routes/plans.py` | schema + 三处 PlanStep 构建 |
| `backend/services/plan_dispatcher_core.py` | lifecycle 合并 + 快照固化/重放 |
| `frontend/src/utils/api/types.ts` | `PlanStep`/`PlanStepCreate.params` |
| `frontend/src/pages/orchestration/planEditUtils.ts` | 读回/发送 params |
| `frontend/src/components/pipeline/PlanStepInspector.tsx` | 自由键值编辑 |

## Alternatives

- **每次换参数新建脚本版本**（`install_apk@1.0.1` 前例）：换 `apk_path`
  必须重走「新版本 + 三端同步 + Agent 重启」，运营痛点（issue #508 实测）。
- **步骤级 params 做 param_schema 校验**：install_apk 等无 schema 脚本
  会被强制校验拒掉——自由键值（无 schema 时）是唯一让此类场景可用的选择。
  有 schema 的脚本仍走结构化表单，不受影响。
- **运行期临时改参**（PlanRun 触发时）：范围外，仅步骤定义级。

## Verification

- `backend/tests/services/test_plan_dispatcher.py`：step params 覆盖/保留/
  None 纯默认/不污染 script_defaults。
- `backend/tests/services/test_plan_barrier_timeout.py`：快照固化 + 重放合并。
- `backend/tests/api/test_plans_api.py`：create/read-back/PUT round-trip。
- `frontend` vitest：`planEditUtils.test.ts`（读回/发送/空对象不发）、
  `PlanStepInspector.test.tsx`（自由键编辑/新增/重名/移除）。
- 123 backend tests + 52 frontend tests 全绿；ruff / eslint / tsc / compileall 通过。

## Revisit

- 步骤级 params 与 WiFi 资源池注入、suite 参数注入（`inject_wifi_params` /
  `inject_suite_params`）现在并存三条注入通道，均为「缺省才注入」语义。
  若注入通道继续增长，应考虑统一注入优先级规则。
- `plan_step.params` 是否要在 `plan_run` 级提供「运行时的生效参数」只读
  视图（当前从 snapshot 可查）。
