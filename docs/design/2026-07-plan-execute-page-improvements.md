# 技术设计：Plan 执行页（/execution/plan-execute）改造

- **状态**：Living（随各 Phase / 迭代实施演进）
- **日期**：2026-07-17（§7 续篇 2026-07-20）
- **关联**：[ADR-0026](../adr/ADR-0026-plan-execution-scaling.md)（60 节点/1000 台目标规模）、[`03-frontend.md`](./03-frontend.md)
- **范围**：
  - Phase 1–6：以前端为主（分页拉全、复跑、容量/占用、全部节点等）
  - §7 迭代 A/B/C：走查债项续篇；含轻量后端（`GET /jobs/active-by-device`、`PlanRunTrigger.note` → `run_context.note`）
  - 仍不含 ADR-0026 V2 准入队列 UI；**不做** run 级巡检/超时覆盖（见 §4 / §7 C2）

---

## 1. 背景与问题清单

从稳定性测试工程师「执行测试计划」的视角对 Plan 执行页评审，结论：页面防呆设计扎实（四步向导、版本一致性核对、就绪预检、预览冻结设备集合、派发失败恢复路径），但存在以下问题：

| 优先级 | 问题 | 影响 |
|--------|------|------|
| P0 | 前端硬编码 `devices.list(0, 200)` / `plans.list(0, 100)`，无翻页、无截断提示 | 目标规模（1000 台）下第 201 台起的设备**静默不可见**，节点侧栏（由设备列表推导）同样缺失——正确性问题 |
| P1 | 无容量/占用视角：看不到 host 槽位与健康度；BUSY 设备不显示被哪个 PlanRun 占用；`scheduling_reason` 是前端 dead field | 发起前无法判断「什么时候真正开跑」、挑机器缺关键信息 |
| P1 | 无复跑入口：只有 Plan 列表页 `?plan=` 预填计划，无「沿用上次设备集」 | 每个新 build 对同批设备重跑同一 Plan 时，需手工逐节点重选几十上百台设备 |
| P2 | 第 4 步「测试参数确认」无任何参数可调，时长显示裸秒数，失败阈值空值伪装成 5% | 参数调整需离开执行流程回 Plan 编辑页 |
| P2 | 必须先选节点才能看设备，筛选/全选只在单节点内生效；版本/型号下拉选项来自全量设备 | 「跨节点凑够 N 台同版本机器」需逐节点操作 |
| P3 | 交互细节：自动移除只报数量不报明细、serial 搜索区分大小写、设备清空后不回退步骤、预览弹窗设备数/Job 数恒等展示两行、Plan 下拉无搜索 | 大批量操作体验 |

## 2. 调研结论（支撑决策的事实）

| 事实 | 出处 |
|------|------|
| `GET /devices` 支持 `status`/`tags` 过滤、`limit ≤ 1200`、带分页参数时返回 `{items, total, skip, limit}`；**无 host_id 过滤** | `backend/api/routes/devices.py:115-151` |
| `GET /plans` 的 limit 无上限校验，可直接调大 | `backend/api/routes/plans.py:435-442` |
| `GET /hosts` 列表已含 `capacity`（心跳上报 active_jobs/active_devices/online_healthy_devices）与 `health`（HEALTHY/DEGRADED/UNSCHEDULABLE + reasons）；`active_jobs` 明细（device_id→plan_run_id/job/started_at）**仅在** `GET /hosts/{id}` 详情返回（Phase 3）。§7 B1b 另增 `GET /api/v1/jobs/active-by-device` 供全部节点视图批量占用 | `backend/api/routes/hosts.py`；`backend/api/routes/jobs.py`；`frontend/src/utils/api/types.ts` |
| PlanRun 详情响应含 `run_context.dispatch_device_ids`（投影函数不剥除）；兜底可用 `GET /plan-runs/{id}/jobs` | `backend/api/routes/plan_runs.py:167-200`；`types.ts:930`；`planRuns.ts:48` |
| `scheduling_reason` 后端不存在，前端类型是 dead field | `types.ts:63`、`PlanExecutePage.tsx:81` |
| 已有可复用件：`formatDurationSeconds`（`utils/format.ts:13`）、`pagination-bar.tsx`；无 combobox/cmdk | `frontend/src/utils/`、`components/ui/` |
| Plan 编辑路由 `/orchestration/plans/:id` | `frontend/src/router/index.tsx:106` |

**数据模型决策**：维持「客户端全量设备」模型，把单次 200 上限改为 **total 感知的分页循环拉全**。1000 台规模即 1 次 `limit=1200` 请求（~200KB），节点侧栏/minimap/就绪检查均依赖全量数据；按节点懒加载（后端加 host_id 过滤 + 选中设备跨节点轮询）收益在数千台以上才显现，留作演进项。

**范围决策（已确认）**：run 级参数覆盖（发起时临时改超时/巡检周期）本轮不做——需改 `PlanRunTrigger`/`prepare_plan_run`/`plan_snapshot`，与 ADR-0026 准入队列在途改动撞车；待 P1 落地后随队列 UI 一起设计。

## 3. 实施方案（六阶段，可独立交付）

### Phase 1（P0）：修复分页截断

**文件**：`frontend/src/utils/api/devices.ts`、`utils/api/queryKeys.ts`、`pages/execution/PlanExecutePage.tsx`

1. `devices.ts` 新增 `fetchAllDevices(status?)`：循环 `api.devices.list(skip, 1200)` 直到 `items.length >= total`，返回合并数组。
2. `deviceKeys` 加 `all: () => ['devices-all'] as const`（沿用页面现有 key 字符串，避免缓存迁移）。
3. `PlanExecutePage` 设备查询改用 `fetchAllDevices`，保留 20s `refetchInterval`。
4. Plan 列表 `(0, 100)` → `(0, 500)`，queryKey 同步 `planKeys.list(500)`。
5. hosts 维持 `fetchHostList(0, 200)`（后端 le=200，60 节点目标内够用）。

### Phase 2（P1）：复跑入口

**文件**：`pages/execution/PlanRunDetailPage.tsx`、`PlanExecutePage.tsx`

1. PlanRun 详情页 header 加「复跑」按钮：设备集取 `run.run_context?.dispatch_device_ids`，为空则请求 `api.planRuns.jobs(runId)` 取 device_id 去重集合；跳转 `/execution/plan-execute?plan={plan_id}&devices=1,2,3`。
2. `PlanExecutePage` 解析 `devices` 参数：设备查询首次落定后与可调度设备求交集写入选择集；丢失设备 toast 列 serial（≤5 台 + 等 N 台）；恢复 ≥1 台则向导跳到第 2 步复核；`plan` 无效停第 0 步。ref 保证预填只执行一次，不与 20s 轮询自动移除打架。

### Phase 3（P1）：容量/占用可见性

**文件**：`PlanExecutePage.tsx`、`queryKeys.ts`、`types.ts`

1. 节点侧栏项追加 `health.status` 警示标识（UNSCHEDULABLE/DEGRADED，title 显示 reasons）与「忙 {capacity.active_jobs}」计数（数据已在 hosts 列表响应中）。
2. 选中节点时 `useQuery(hostKeys.detail(id), () => api.hosts.get(id))`（20s refetch），从 `active_jobs` 建 device_id → {plan_run_id, started_at} 映射；设备表「预检」列对 BUSY 设备显示「执行中 · PlanRun #x」并链接 `/execution/plan-runs/{id}`。
3. 第 4 步前置检查追加信息行「所选节点当前活跃任务 N 个」（不阻塞）。
4. 删除 `scheduling_reason` dead field（`types.ts` 与页面局部类型）。
5. legacy 派发路径对 BUSY 设备仍硬阻塞（保持现状）；V2 准入 QUEUED UI 不在本计划。

### Phase 4（P2）：跨节点批量选择

**文件**：`PlanExecutePage.tsx`

1. 节点侧栏顶部加「全部节点」项；`deviceHostFilter === 'all'` 时直接渲染跨节点设备表（沿用现有筛选与 minimap），不再显示占位。
2. 设备表用 `pagination-bar.tsx` 分页（50/页）；「全选当前结果」改「全选筛选结果 (N)」，明确作用于全部筛选结果而非当前页。
3. 版本/型号下拉选项改为按当前节点范围内设备推导。

### Phase 5（P2）：参数确认页展示优化

**文件**：`PlanExecutePage.tsx`

1. 巡检周期/超时用 `formatDurationSeconds` 显示（步骤 0 meta 行、第 4 步卡片、预览弹窗）。
2. `failure_threshold == null` 显示「未设置（按默认 5% 生效）」。
3. 第 4 步加「编辑 Plan」跳转 → `/orchestration/plans/{selectedPlanId}`。

### Phase 6（P3）：交互细节修缮

**文件**：`PlanExecutePage.tsx`（含 `PreviewDialog`）

1. 自动移除 toast 列被移除设备 serial（≤5 台 + 等 N 台）。
2. Serial 搜索大小写不敏感（与型号一致）。
3. `currentStep >= 2 && selectedDevices.length === 0` 时自动退回第 1 步并 toast。
4. 预览弹窗合并为「设备数（= Job 数）：N」一行。
5. Plan 下拉上方加搜索 Input 客户端过滤（不引入 cmdk）。

## 4. 明确不做（及原因）

- **run 级参数覆盖（巡检周期 / 超时 / 失败阈值）**：`PlanRunTrigger` 仅 `device_ids`（+ `note`）；dispatch 依 `plan_snapshot` 构图生命周期。§7 C2 可行性门禁结论为不可行 → UI 只读展示「继承 Plan，本次不可覆盖」+「编辑 Plan」跳转；**勿先开前端可编辑覆盖 UI**。与 ADR-0026 准入队列一并再议。
- **QUEUED/准入队列 UI**：feature flag 未开，等 ADR-0026 P1 落地。
- **按节点懒加载设备（后端 host_id 过滤）**：数千台以上才需要，演进项。
- **`plan_run.note` 独立列迁移**：C1 优先 `run_context.note`（JSONB 已有）；仅当出现列表筛选需求时再评估加列；禁止对本机生产库试跑迁移。

## 5. 验证

1. 单测：`cd frontend && npx vitest run src/pages/execution/PlanExecutePage.test.tsx src/pages/execution/PlanRunDetailPage.test.tsx`——每阶段补对应用例（分页拉全、复跑预填/丢失提示、占用列渲染、跨节点全选、时长格式、自动回退；§7 另含草稿持久化、侧栏排序、前置预检、容量告警）。
2. 类型/构建：`npm run type-check && npm run build`（或 `npx tsc --noEmit`）。
3. Agent / 后端（有后端变更时）：`pytest backend/agent/tests/` → 涉及 API 时 `pytest backend/tests/`（testcontainers，**严禁** `TEST_DATABASE_URL` 指本机生产库）。
4. 端到端：dev / 真实环境走完整向导（选 Plan → 全部节点筛选跨节点选机 → BUSY 占用链接 → 预览发起），再从 PlanRun 详情「复跑」验证预填与丢失提示；§7 重点：F5 恢复选择、侧栏轮询后顺序稳定、清空确认 Dialog、占用 PlanRun 链接。
5. 回归重点：20s 轮询自动移除与复跑预填/跨节点全选不互相干扰；`?plan=` / `?devices=` 旧入口不受影响。

## 6. 修订记录

| 日期 | 变更 |
|------|------|
| 2026-07-17 | 初版：评审问题清单 + 六阶段改造方案 |
| 2026-07-17 | Phase 1-6 全部落地。实现细节：Phase 4 将 `deviceHostFilter` 默认值设为 `'all'`（进入样机选择即渲染跨节点设备表，「全部节点」为侧栏首项）；设备表分页 50/页（`PaginationBar`）。验证：受影响 3 测试文件 37 例 + 全量 319 例通过，`type-check`/`build` 干净 |
| 2026-07-20 | 补入真实环境走查债项 **修复计划 v2**（§7）；修订文首范围与 §4（后端轻量接口 + C1/C2 降级）。迭代 A/B/C 已落地；C2 维持不可 run 级覆盖。真实环境回归：`walk-regress-a.mjs` 15/15（刷新恢复、侧栏排序、清空确认、占用 PlanRun 链接） |

## 7. 迭代 A/B/C — 修复计划 v2（走查债项）

基于新旧版真实页面走查与源码评审（v1 → v2 修订后批准）。相对 Phase 1–6 的「能力建设」，本节补的是**防误操作与选型效率债**。

**交付顺序（迭代 A）**：`A3 → A4 → A1 → A2 → B1a`（排序/显隐无状态风险先吞；A1 核心；A2 依赖 toast 扩展；B1a 纯前端收尾）。

### 7.1 评审意见处置（v1 → v2）

| 评审项 | 处置 |
|--------|------|
| B4 公式错误 | `effective_slots` 即剩余可派发槽位；告警条件 = 按节点「本次选中数 > `effective_slots`」；补 `types.ts` |
| B1 路由冲突 | 拆分 B1a/B1b；B1b 用 `GET /api/v1/jobs/active-by-device`（不挂 `/hosts/` 下，规避 `/{host_id}` 捕获） |
| A2 toast 无 action | 前置扩展 `useToast.action`（sonner） |
| A1 / prefill 竞态 | 与现有 prefill effect 单入口合并；URL `devices` > URL `plan` > 草稿 |
| A1「取消」范围 | 仅 step0「取消」+ 发起成功清草稿；「编辑 Plan」跳走 / 503 保留 |
| C1 备注 | 优先 `run_context.note`，独立列迁移标可选 |
| C2 覆盖 | 可行性门禁前置；不可行则只读 + 跳编辑 Plan |
| B2 验收 | 不承诺占用去向链接（属 B1b） |
| B3 tags | 扩 `ReadinessDevice.tags`，确认 `fetchAllDevices` 带回 |

### 7.2 迭代 A — 状态安全（已落地 ✅）

| 项 | 问题 / 方案 | 验收 | 状态 |
|----|-------------|------|------|
| **A3** 侧栏固定排序 | `nodeSummaries` 按 IPv4 八位组数值序；`unassigned` 置底；「全部节点」为独立按钮不参与排序 | 20s 轮询后侧栏顺序不变 | ✅ |
| **A4** 吸底栏按步显隐 | 设备计数 / 清空 / 移除阻塞仅 `currentStep >= 1`；step0 只留取消 / 进入样机选择 | step0 无「已选 0 台」无效计数 | ✅ |
| **A1** 选择持久化 | `sessionStorage` key `stp.planExecute.draft.v1`，防抖 300ms；字段含 Plan/设备/步骤/筛选器（含 tags）。恢复单入口：① URL 含 `devices` → 走 prefill，忽略草稿设备集，结果写回草稿；② 仅 `plan` → URL Plan 为准 + 恢复草稿设备；③ 无 URL → 整份读草稿。清除仅 step0 取消 + `handleConfirm` 成功；编辑 Plan / 503 保留；`suppress` 防清除后写回 | F5 恢复；URL 优先；成功清空；跳编辑返回仍在 | ✅ |
| **A2** 清空/移除防误触 | `useToast.action`；清空 → 确认 Dialog（「将移除已选 N 台」）；Minimap / 阻塞移除 → 先移除 + toast 撤销（5s） | 清空必确认；误删 5s 内可撤销 | ✅ |
| **B1a** 设备表「节点」列 | Serial 后加列，`hostMap` 取 IP/名称，未分配兜底；全部节点与钻取视图统一表结构 | 混排时可见归属节点 | ✅ |

### 7.3 迭代 B — 选型效率（已落地 ✅；B1b 含后端）

| 项 | 问题 / 方案 | 验收 | 状态 |
|----|-------------|------|------|
| **B2** 预检前置可见 | 对 `pagedDevices` 直接 `evaluateDeviceReadiness`，内联阻塞原因；「选择后检查」文案消失 | 未勾选即见就绪/阻塞原因；**不含**占用链接 | ✅ |
| **B3** tags 池化筛选 | `ReadinessDevice.tags`；筛选条多选，选项从全量设备聚合；纳入 A1 草稿 | 按 tag 圈选 +「全选筛选结果」联动 | ✅ |
| **B4** 容量超限警告 | 按节点：选中数 > `host.capacity.effective_slots` → 黄字「将排队执行」；非阻塞；注明心跳参考；缺槽位字段时不告警。`Host.capacity` 补 `effective_slots` / `available_slots` 等 | 超选出现警告；未超选不显示 | ✅ |
| **B1b** 占用全视图可见 | `GET /api/v1/jobs/active-by-device` → `[{ device_id, plan_run_id, ... }]`；前端单次拉取，占用单元格「执行中 · PlanRun #N」可跳转。落地前曾用并行 `GET /hosts/{id}` 兜底，接口就绪后已切换 | 任意视图可见占用来去并可跳转 | ✅ |

### 7.4 迭代 C — 发起信息完善（已落地 ✅；C2 降级）

| 项 | 方案 | 状态 |
|----|------|------|
| **C1** Run 备注 | 第 3 步选填「执行备注」→ 预览展示 → `PlanRunTrigger.note` 写入 `run_context.note`；详情页同字段读取。无独立列迁移 | ✅ |
| **C2** 参数覆盖 | **降级**：只读展示继承 Plan 的巡检/超时 +「本次不可覆盖」+「编辑 Plan」；不开可编辑覆盖 UI | ✅（降级） |
| **C3** 术语统一 | 侧栏「执行测试」→ 页标题「执行 Plan」；向导/卡片「样机选择」；正文「样机/节点」 | ✅ |
| **C4** Minimap 色弱 | 阻塞方块斜纹 + ✕；hover 悬浮卡（serial / 节点 / 型号 / 版本） | ✅ |
| **C5** 步骤参数可见 | 第 0 步步骤行可展开只读 `default_params`（格式化 JSON） | ✅ |
| **C6** 预览加载态 | `handlePreview` 期间按钮 spinner + disabled | ✅ |

### 7.5 关键文件与验证锚点（实施后）

- **前端主战场**：`PlanExecutePage.tsx`、`useToast.ts`、`planExecuteReadiness.ts`、`types.ts`、对应 vitest。
- **后端**：`backend/api/routes/jobs.py`（`active-by-device`）、`PlanRunTrigger` / `run_context.note` 写入路径。
- **验证顺序**（AGENTS.md）：`pytest backend/agent/tests/` → `npx tsc --noEmit` → `npm run build` → 后端变更用 testcontainers。
- **真实环境脚本**：`/tmp/stp-e2e/walk-regress-a.mjs`（重点项）、`walk-new2.mjs`（四步走查）；截图 `/tmp/opencode/screenshots/regress/`、`.../new/`。

### 7.6 顺手项（P3，未排期）

- 设备表列排序（Serial / 型号 / 版本 / 节点）；版本号省略号 + 悬浮全文
- Plan 下拉按 `updated_at` 倒序 +「最近执行」分组
- 步骤列表超过 ~8 行时内部滚动
