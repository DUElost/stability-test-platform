# 技术设计：Plan 执行页（/execution/plan-execute）改造

- **状态**：Living（随各 Phase 实施演进）
- **日期**：2026-07-17
- **关联**：[ADR-0026](../adr/ADR-0026-plan-execution-scaling.md)（60 节点/1000 台目标规模）、[`03-frontend.md`](./03-frontend.md)
- **范围**：纯前端改造；不改后端端点、不含 ADR-0026 V2 准入队列 UI

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
| `GET /hosts` 列表已含 `capacity`（心跳上报 active_jobs/active_devices/online_healthy_devices）与 `health`（HEALTHY/DEGRADED/UNSCHEDULABLE + reasons）；`active_jobs` 明细（device_id→plan_run_id/job/started_at）**仅在** `GET /hosts/{id}` 详情返回 | `backend/api/routes/hosts.py:120-193, 251-283`；`frontend/src/utils/api/types.ts:25-41` |
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

- **run 级参数覆盖**：与 ADR-0026 在途改动撞车，已确认延后。
- **QUEUED/准入队列 UI**：feature flag 未开，等 ADR-0026 P1 落地。
- **按节点懒加载设备（后端 host_id 过滤）**：数千台以上才需要，演进项。
- **后端改动**：无——现有端点能力已够用。

## 5. 验证

1. 单测：`cd frontend && npx vitest run src/pages/execution/PlanExecutePage.test.tsx src/pages/execution/PlanRunDetailPage.test.tsx`——每阶段补对应用例（分页拉全、复跑预填/丢失提示、占用列渲染、跨节点全选、时长格式、自动回退）。
2. 类型/构建：`npm run type-check && npm run build`。
3. 端到端：dev server 登录后走完整向导（选 Plan → 全部节点筛选跨节点选机 → BUSY 占用链接 → 预览发起），再从 PlanRun 详情「复跑」验证预填与丢失提示。
4. 回归重点：20s 轮询自动移除与复跑预填/跨节点全选不互相干扰；`?plan=` 旧入口不受影响。

## 6. 修订记录

| 日期 | 变更 |
|------|------|
| 2026-07-17 | 初版：评审问题清单 + 六阶段改造方案 |
| 2026-07-17 | Phase 1-6 全部落地。实现细节：Phase 4 将 `deviceHostFilter` 默认值设为 `'all'`（进入样机选择即渲染跨节点设备表，「全部节点」为侧栏首项）；设备表分页 50/页（`PaginationBar`）。验证：受影响 3 测试文件 37 例 + 全量 319 例通过，`type-check`/`build` 干净 |
