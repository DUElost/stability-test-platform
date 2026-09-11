# 参数默认值语义统一：展示默认 = 执行默认 + 保存期 schema 校验（#977 / R05-F14）

Status: implemented
Class: bug-fix

## Decision

本质问题：有 `param_schema` 时，「前端展示的默认值」与「派发生效参数」来源不同——
前端 `PlanStepInspector` 回落 `schema.default`（优先级 step.params > default_params
> schema.default），派发只合并 `default_params + step.params`。展示出来的默认值
可能**从未生效**，非法参数值要到脚本执行期才暴露。

对齐为单一语义（与前端既有实现严格同源）：

1. 新增 `backend/services/script_params.py`：
   - `merge_effective_params(schema, defaults, step_params)`：schema.default 为底，
     default_params 覆盖，step.params 最高；
   - `validate_params_against_schema(params, schema)`：已提供键的类型/枚举校验
     （bool 不算 integer；未声明键属已接受的自由键模式；required 不强制——wifi/
     suite 注入在派发期补键）。
2. 派发与快照重放共用该模块：
   - `script_defaults(metadata)` 由「仅 default_params」改为 schema+defaults 合并
     （steps 派发路径的单一改点）；
   - `build_lifecycle_from_snapshot` 用快照内的 param_schema/default_params/params
     合并（快照本就带 param_schema，**无需改快照格式**；旧快照无该键 → 行为不变）。
3. 保存期校验：`plans.py` 新增 `_validate_step_param_values`，在 create/update/
   clone 三入口（与 `_validate_script_refs` 同处）对步骤参数做 schema 类型/枚举
   校验，非法 → 422 `INVALID_STEP_PARAMS`（带 step_key 与逐条 problem）。
4. **未改前端**：前端优先级就是目标语义，本次是后端向其对齐（前端既有单测
   `PlanStepInspector.test.tsx` 已固化该优先级）。

## Alternatives

- **前端对齐后端（展示只读 default_params）**——放弃：`schema.default` 是脚本对
  外声明的默认值，撤掉展示会丢失「脚本自带默认」的表达；验收明确「展示默认 =
  执行默认」，注入方向与前端既有实现一致，无需改 UI；
- **只在派发期校验（不碰保存路径）**——放弃：坏 Plan 可入库、首次运行才炸；
  保存期 422 反馈更早（验收允许二选一）；
- **强制 required**——放弃：wifi/suite 注入路径在派发期补键（ADR-0020 的两处
  豁免），保存期强制会误伤；required 语义留给后续独立裁决；
- **拒绝 schema 未声明的键**——放弃：无 schema 的自由键模式是已接受设计，且
  存量 Plan 可能带额外键；只校验已声明键的类型/枚举；
- **在各 dispatcher 调用点内联合并**——放弃：三处（steps/快照/校验）分散实现
  正是本单漂移的成因，抽成纯函数模块并由测试固化优先级。

## Verification

实际运行（worktree `/tmp/stp-977`，2026-09-11）：

- `pytest backend/tests/services/test_script_params.py
  backend/tests/services/test_plan_dispatcher.py -q` → **44 passed**
  （新增：三级优先级、无 mutation、无 default 字段跳过、bool≠integer、enum、
  自由键；script_defaults schema 合并、快照重放合并、旧快照行为不变）；
- `pytest backend/tests/api/test_plans_api.py backend/tests/services/test_plan_barrier_timeout.py
  backend/tests/services/test_dispatch_complete_repair.py
  backend/tests/services/test_plan_run_dispatch_retry.py
  backend/tests/services/test_plan_dispatcher_precheck.py
  backend/tests/services/test_plan_dispatcher_device_validation.py -q` → **146 passed**
  （含新增 5 例：非法类型 422、enum 违规 422、合法 201、自由键放行、update 422）；
- `pytest backend/tests/services/ -q` → **716 passed**（核心合并改动全量回归）；
- `pytest tests/ -q` → **150 passed**；
- `ruff check .` → All checks passed；
- `check:quick` → **7 gates 全绿**。

未完成（pending）：无（前端无需改动；语义由本 Note + 模块 docstring 承载）。

## Revisit

- 若后续裁决 required 强制或「有 schema 时拒绝未声明键」，在同一模块扩展并在
  保存期接入（注意 wifi/suite 注入豁免）；
- 前端 `setParam` 的「等于 default 则删除键」逻辑以 default_params 为参照，
  不涉及 schema.default——当前行为正确（schema 默认由默认链兜底）；若未来要允许
  显式覆盖 schema.default 需同步该逻辑。
