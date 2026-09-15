# DLE 终态视图：平台列/筛选与失败态语义色（#2184）

Status: implemented
Class: feature

## Decision

`LogEventsCard`（终态 PlanRun 的 DLE 归档视图，#529 归档权威）三处改动，都是**让已有的信息可见**：

1. **平台列**：`PlanRunLogEvent.platform` 早就两端都有（后端 `routes/plan_runs.py` 的事件行、
   前端 `types.ts` 的 `PlanRunLogEvent.platform`），唯独表格没渲染它——MTK / UNISOC 事件混在一张表里
   只能靠 serial 猜。表格在「序列号」后增「平台」列（**纯展示，无契约变更**）。

2. **补齐四态语义色**：`STATE_CHIP` 此前只有 5 个状态，`UPLOADING` / `UPLOAD_FAILED` /
   `PULL_FAILED` / `PRUNED` 全部落到 `STATUS_CHIP.muted` 兜底（等值于"没信息"），而这四个恰好回答
   「卡在哪 / 要不要处理」：
   - `UPLOADING` → `primary`（在途，与 `LOCAL` 同属"尚未上中心但非异常"的中性态）；
   - `UPLOAD_FAILED` / `PULL_FAILED` → `destructive`（真失败）；
   - `PRUNED` → `muted`，**故意不用 destructive**：它是"已按 retention 清理"的终态，
     用失败色会把正常的清理动作读成故障。

3. **平台筛选（服务端）**：`GET /plan-runs/{id}/log-events` 新增**可选** query 参数 `platform`，
   与既有的 `state` 参数同构（`services/device_log_event.list_plan_run_device_log_events` 里
   多一个 `filters.append`）。前端加一组 chip（复用 `SEGMENTED`）驱动它。
   - **为什么必须服务端**：客户端过滤只作用于已加载页，在分页场景下会给出"MTK 只有 3 条"
     这类错误印象（实际是第 1 页只有 3 条）；且 `state` 参数既已有此模式，两条筛选同口径更一致。
   - 查询键纳入 `platform`（`planRunKeys.logEvents`，`?? null` 保证键可比）——否则切平台会命中
     上一平台的缓存。
   - 切平台时把 `limit` 收回首页，避免把上一筛选放大过的窗口带过去。

4. **筛到 0 条 ≠ 没有数据**：筛选生效且结果为 0 时，空态文案变为「平台 {p} 无 device_log_event 记录」，
   否则会被读成"这份 run 没日志"。

**筛选项的来源与取舍**：选项 = 当前已加载行里出现的平台 ∪ 当前筛选值（**纯派生**，无记忆态）。
筛选生效后行集只剩该平台，选项随之收窄，但**「全部」恒在**（渲染条件是
`platform || platformOptions.length > 1`），所以任何时刻都能切回，也不会出现"点了某平台却 0 条"的死选项。

**契约影响面**：响应形状零变更（平台字段本就在 `PlanRunLogEventOut` 里，故
`tests/test_api_response_shape_contract.py` 的轴线 C 无需新增登记）；新增的只是**可选请求参数**，
老客户端不传 `platform` 行为完全不变。

涉及：`frontend/src/components/plan-run/LogEventsCard.tsx`（+ 用例）、
`frontend/src/utils/api/planRuns.ts`、`frontend/src/utils/api/queryKeys.ts`、
`backend/api/routes/plan_runs.py`、`backend/services/device_log_event.py`（+ `backend/tests/api/test_plan_run_log_events.py`）。

## Alternatives

- **客户端过滤（不动后端）**：**否决**。分页 + 客户端过滤会让计数与"该平台有几条"都失真，是
  比"没有筛选"更糟的形态——用户会据此得出错误结论。服务端筛选的代价只是 3 行（照抄 `state` 模式）。
- **用 effect + state 记住"未筛选时的选项"，让其它平台 chip 在筛选后仍可见**：**否决**。
  需要 setState-in-effect（`react-hooks/set-state-in-effect`，本仓 eslint 为 error；仓库对他处同类
  情形用 disable 注释放行，但这里能绕开就不该 disable），且会引入与当前行集不一致的陈旧选项
  （选项承诺某平台存在、点了却 0 条）。代价是少点一次「全部」，不值得用一致性换。
- **硬编码平台词表（MTK/UNISOC/QCOM）做选项**：**否决**。那会在前端再造一份平台词表，与
  `core/dedup_platform`（控制面平台词表，ADR-0032 R4 刻意唯一化）形成第二处权威——正是本仓
  一直在收口的"同一语义两处描述"。
- **只做平台列 + 四态 chip，不做筛选**：可行的最小版。否决理由：筛选是"看见"到"可用"的分界，
  且服务端已有同构参数可抄；若只做展示，问题会以"我还是得肉眼过 200 行"的形式再次出现。
- **顺手把 `merge_platforms` 也展示出来**（I-1 的"未做"部分）：**缓做**。它属另一条链
  （merge 结果可观测），与本单的 DLE 明细表不同源；登记在台账 I-1"未做"里，不在本单扩大范围。

## Verification

- 前端：`CI=1 npx vitest run src/components/plan-run/LogEventsCard.test.tsx` → **11 passed**
  （7 原有 + 4 新增：平台列渲染、四态语义色且 `PRUNED` 不含 destructive、筛选带 `platform` 参数
  且能经「全部」切回、筛到 0 条文案可区分）；前端全量 → **817 passed（106 files）**。
- 后端：`pytest backend/tests/api/test_plan_run_log_events.py` → **3 passed**（含新增
  `test_plan_run_log_events_filters_by_platform`，同时钉住 `total` 随筛选收窄——否则前端
  "已显示 1 / 2"照样误导）；`backend/tests/api backend/tests/services` → **2102 passed**；
  根 `tests/` → **985 passed**。
- 门禁：`run_gates check:pr` → **[OK] 18 gates**；`tsc --noEmit` 通过。
- **一次真实红灯（记录在案）**：首版用 `useEffect` 同步选项集，`eslint` 的
  `react-hooks/set-state-in-effect` 判红（`check:pr` 直接失败）——不是绕过，而是改成纯派生后
  问题消失，行为不变（11 passed 复跑）。这佐证了 Alternatives 里对"记忆态"的否决理由。

## Revisit

- **选项集收窄的体验**：若实测中"筛完只能经「全部」切到别的平台"被反馈为别扭，正确解法是让
  **服务端给 facet**（例如响应里附该 run 的平台分布），而不是把选项重新搬回前端记忆态。
- **平台筛选与状态筛选叠加**：本单只加了平台参数；若将来需要"平台 × 状态"组合筛选，应同样走
  服务端（两个参数已天然可叠加），不要把任一维拖回客户端。
- **阈值**：若某 run 的 DLE 行数常态超过单次上限 500（当前 `MAX_LIMIT`），筛选虽正确但"翻页看全"
  会失效——那时应改为服务端聚合视图（按平台/状态分组计数）而不是继续放大 limit。
