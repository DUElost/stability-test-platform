# PlanRun 脚本身份贯通：快照派生字段 + 前端展示 + 快照抽屉（#3350 / ADR-0023 D2–D4）

Status: implemented
Class: feature

## Decision

**排查要回答的是「当时跑的是哪个脚本版本」，而不是「现在配的是哪个版本」**——所以三处
字段全部从 `plan_snapshot.steps` 查表派生（ADR-0023 D2 原文口径），不回落当前 PlanStep：
现行定义可能已被改指别的版本，回落会给出看起来合理的错答案。全部 Optional，查不到即
None（旧 PlanRun 的 `plan_snapshot` 缺 `steps` 时不抛错）。

### 后端（C2）

- `EventOut.{script_name,script_version}`：仅 `category="step"` 的失败/abort 事件按
  `StepTrace(stage, step_id)` 查；trigger/system/log_signal/audit 与 job 级
  （`step_id="__job__"`）恒 None。
- `DeviceMatrixItem.current_script_{name,version}`：`current_step` 来自巡检心跳
  （`JobInstance.current_patrol_step`，patrol 段语义），按 `("patrol", step)` 查；
  快照里没有（改版后的残留心跳/手写值）→ None，不猜。
- `StageStepOut.script_version`：timeline 直接取快照 step 的 `script_version`。
- 共用派生规则在 `plan_run_read_common.snapshot_step_scripts` / `resolve_step_script`
  （三条读端点同一份表）；**不新增查询**——devices 端点复用路由已加载的 `pr`，
  timeline/events 复用 `pr.plan_snapshot`。

### 前端（C5 + C6）

- 展示口径统一 `name@version`（`components/plan-run/scriptIdentity.ts`）：
  `BusinessFlowStepper` 段落内 step chip、`DeviceOverview` 表格 current_step 列、
  `DeviceDetailDrawer` 脚本 KV 行、`PlanRunEventStream` step 事件行；
  **缩略图（minimap）视图不加载荷**（保持紧凑，ADR C5 第 4 条）。
- `ScriptManagementPage` 消费 `?name=&version=`：初始值直接取自 URL（`useState` 初始化器，
  不用 effect——避免 mount 后覆盖用户输入）；目标版本行自动展开参数详情。
- 新增 `PlanSnapshotDrawer`（D4）：步骤按 `(stage, sort_order)` 排序的折叠卡片，
  含 `script@version`（深链回脚本库）、`timeout/retry/enabled/nfs_path`、
  `default_params`/`param_schema` 展开；入口在 `PlanRunHero` 动作条「查看快照」；
  数据直接读 `GET /plan-runs/{id}` 已返回的 `plan_snapshot`（不新增端点、不 lazy fetch）；
  快照缺失走空态。

### 两处明示的偏离（留痕）

1. **D4 原文第 4 条的 WiFi 一节不做**：D5 撤销后 `run_context.wifi_assignments` 没有任何
   写入方，`ResourceAllocation` 也没有 run 级读取面（只有 pool 管理端点）——今天渲染它
   必然是空壳。触发器：出现 run 级 allocation 读取面时补（ADR-0023 实施记录已写明）。
2. **原 C8 的跨链 e2e 未做**：本 PR 交付 API 级 6 例 + 组件/页面级 vitest；原设想的
   scan → 建 Plan → 触发 → 三端点连跑未实现。触发器：需要跨链回归时补。

## Alternatives

- **回落当前 PlanStep 定义**：能给旧 PlanRun 补上身份，但会把「后来改指」的版本说成
  「当时跑的」——排查场景下这是错误答案而非缺省值。否决。
- **events 端点对非 step 事件也查设备当前步骤补身份**：语义混淆（事件是「那一刻的失败」，
  设备当前步骤是「此刻的状态」），且 ADR 明确 `category != 'step'` → None。否决。
- **给 devices 端点只按 `current_step` 查（不带 stage）**：`step_key` 在不同 stage 可重名，
  只按 key 查会把 init 的同名步骤说成 patrol 的。按 `("patrol", step)` 精确查。否决。
- **快照抽屉新增 `/plan-runs/{id}/snapshot` 端点**：ADR D4 明确「不新增端点」——
  detail 已返回整份快照，重复端点只会多一个漂移面。否决。
- **深链用 effect 消费 URL**：首版这么写，被 `react-hooks/set-state-in-effect` 门禁拦下，
  且 effect 会在每次 searchParams 变化时覆盖用户输入；改 `useState` 初始化器后语义更准。
- **WiFi 节从 `ResourceAllocation` 现拉**：需要新端点或 detail 增字段，超出本单范围，
  且当前无消费方需求。否决（见上「偏离 1」）。

## Verification

| 命令 | 结果 |
|---|---|
| `pytest backend/tests/api/test_plan_run_script_identity_3350.py -q` | **6 passed**（ADR C2 的 6 条验收逐条） |
| `pytest backend/tests/api/ -q -k "plan_run or timeline or events or devices"` | **266 passed**（1070 deselected） |
| `npx vitest run src/components/plan-run src/pages/execution/PlanRunDetailPage.test.tsx src/pages/scripts` | **183 passed**（16 files） |
| `npm run type-check` / `npx eslint`（改动文件） | 通过 |
| `scripts/run_gates.py check:quick` | **[OK] check:quick（16 gates）** |

用例覆盖：events step 事件带身份 / 非 step 类（trigger·audit·log_signal）与 job 级恒 None /
devices 命中与越界（`ghost_step`）/ timeline 版本 / 空快照三端点全 None 不抛错；
前端：chip 渲染与缺失不渲染、表格有而缩略图无、抽屉 KV 行 `—` 回退、
快照抽屉排序/展开/空态/关闭、深链定位并展开（含只给 name 不展开）。

## Revisit

- **WiFi 一节**：出现 run 级 `ResourceAllocation` 读取面（或 detail 响应增字段）时补渲染。
- **跨链 e2e（原 C8）**：需要「脚本版本 → Plan → PlanRun → 观测面」整链回归时补。
- **events 的 `category != step` 恒 None**：若将来某类事件（如 log_signal）也需要脚本身份，
  应先改 ADR 口径再改代码——现状是按裁决实现的。
