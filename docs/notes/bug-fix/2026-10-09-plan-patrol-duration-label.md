# Plan「全局超时」改名巡检时长：按引擎语义写清含义

Status: implemented
Class: bug-fix

## Decision

`Plan.timeout_seconds` 会组装成 `pipeline_def.lifecycle.timeout_seconds`。它在引擎里的含义是**巡检时长预算**：

- 每台设备在**自己的 init 完成**时开始计时；
- 计满后，在下一轮巡检开始前以 `timeout` 结束巡检（`backend/agent/pipeline_engine.py` 的 `_run_patrol_loop`）；
- 这个终止原因判为**成功**（`success = termination_reason in ("completed", "timeout")`），随后执行 teardown；
- 控制面不消费这个字段，不存在「PlanRun 级超时中止」。

UI 原先把它叫「全局超时」（编辑器）、「超时」（执行页选择与驾驶舱）。驾驶舱还解释成
「整个 PlanRun 超时后中止；已完成步骤的结果会保留」，这把专项最核心的参数「跑多久」说成了故障兜底。

本次改动：

- 新增 `frontend/src/components/pipeline/planTiming.ts`，集中定义：
  - 标签「巡检时长」与完整说明；
  - 取值展示：无巡检步骤显示「无巡检」，未设显示「不限」，否则显示时长；
  - 编辑器的一句话结论。

  编辑器（`PlanCanvas`）、执行页选择（`PlanSelectPhase`）、执行前确认（`DispatchCockpit`）三处共用。
- 编辑器在参数行下方常显结论，并以 `aria-describedby` 关联输入框。
- 编辑器输入：后端写入边界是 `ge=1`（`backend/api/routes/plans.py` 的 `PlanCreate` / `PlanUpdate`）。
  此前 `min=0` 且把负数夹到 0，填 0 要到保存时才收到 422。现在留空、0、负数都按「不限」（null）提交，
  与步骤级「0 = 不限」的直觉一致。

不在本单范围：
- 「Patrol 间隔 / 巡检周期」的命名统一；
- 快照抽屉里的原始键名（`timeout_seconds`）。

两者都归「参数含义与生效值的单一事实源」ADR 及其批次处理。

来源：UI 人类可达性审查（2026-10-09）A5；owner 同意改名并写清含义。

## Alternatives

- **只改驾驶舱的错误说明、保留「超时」字样**：字面仍把「跑多久」读成故障兜底，三处口径继续分裂。不采纳。
- **0 夹到 1 秒**：会产出一个 init 完成后几乎立刻结束的计划，比报错更隐蔽。不采纳。
- **改后端字段名**：牵动 API、快照与 Agent 契约，收益只在可读性。由展示层改名即可。

## Verification

- 先改测试，在旧实现上运行：PlanCanvas 3 条、PlanExecutePage 1 条失败；
  改后 `npx vitest run src/components/pipeline/ src/pages/execution/ src/components/execution/` 共 276 条全绿，
  含新增 `planTiming.test.ts`。
- 布局目视：渲染编辑器头部，套构建产物 CSS，用 headless chromium 在 860px 画布宽度下截图。说明行正常单行展示。
- `scripts/run_gates.py check:quick` 全绿。

## Revisit

参数含义事实源的 ADR 落地后，「巡检时长」的定义迁入 Plan 级设置登记表，本模块改读登记表。
