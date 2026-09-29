# B2-G3 列表截断修复（#3195 + 日程 + 用户）：三处固定单页改 fetchAllPages 翻页取全量

Status: implemented
Class: bug-fix

## Decision

按批次 B2 规划单 #3497 §3 G3 执行 F2 形态修复（层级＝形态级，参照实现＝§1 F2 行的
`fetchAllPages`）：`PlanListPage`、`SchedulesPage`、`UsersPage` 三处 `useQuery` 此前都是
固定单页取数（`list(0, 100)` / `list(0, 200)`）却把已取子集当全集渲染，集合越过页大小后
尾部条目静默消失。修复＝用 `frontend/src/utils/api/paginate.ts` 的 `fetchAllPages` 按服务端
`total` 翻页取全量，页大小取后端 `le` 上限 200（`backend/api/routes/plans.py` `_PLAN_LIST_MAX_LIMIT`
=200、`schedules.py:116` `le=200`、`users.py:57` `le=200`）。

规划单 §3 已核对的稳定全序前提在 tip `cc92b470` 复核通过：plans `created_at DESC, id DESC`
（`backend/api/routes/plans.py:949`）、schedules `id DESC`（`schedules.py:121`）、users `id ASC`
（`users.py:62`），三端点均返回 `PaginatedResponse` 且含 `total`——与方案不符时本应退回规划者，
实测相符，按方案执行。

计划列表筛选参数透传：现成 `fetchAllPlanPages` 不带筛选参数，按方案改用
`fetchAllPages((skip, limit) => api.plans.list(skip, limit, projectKey, specialtyKey), 200)`。

实施中的一项本地裁定（方案未展开的缓存键细节）：`PlanListPage` 原查询键为
`planKeys.list(100, projectKey, specialtyKey)`，改页大小后若仍返回 `{items,total}` 信封并以
`planKeys.list(200, undefined, undefined)` 入缓存，会与既有用 `planKeys.list(200)` 缓存
`Plan[]` 数组的选择器查询（`usePlanEditForm.ts:83`、`SchedulesPage` `plansQ`）同键不同形状冲突。
故 `queryFn` 返回 `Plan[]`（`fetchAllPages(...).then(res => res.items)`），无筛选态与选择器查询
同键同形、语义一致（都是全量计划集），共享缓存反而正确；`DashboardStatCard`「Plan 总数」
改为取翻页后的全集计数——#3147 当时担心的「单页上限把已加载条数当总数」在翻页取全后不再
存在（该不变量由翻页本身承担）。既有 #3147 KPI 测试夹具（total 130 > 行数 2）在新语义下
不再可达，改造为翻页全量版并保留 KPI 断言。

## Alternatives

- 直接复用 `fetchAllPlanPages`：不可行，它不带 `project_key` / `specialty_key`，翻页第二页会按
  未过滤集偏移取行（规划单 §3 G3 已预先排除此路）。
- 保留单次请求 + 截断横幅提示：方案明确不取——§3 G3 定的是「按服务端 total 翻页取全量」，
  横幅方案属 F2 参照实现的另一支（`fetchAllPages` 优先），且三端点均具备稳定全序前提。
- `PlanListPage` 继续返回 `{items,total}` 信封并换独立缓存键：需要改 `queryKeys.ts`
  （加维度区分键），超出本组 scope；返回数组形态与既有选择器查询天然兼容。

## Verification

- 反例测试（#3497 §4 G3）：
  - PlanList：`fetches every page when the server total exceeds one page, rendering all rows`
    （260 条 > 页 200 ⇒ 两页取全、末条渲染、KPI=260）；
    `carries the specialty filter on every page ... (#3195)`（筛选后第二页请求仍携带
    `specialty_key`，断言 `list(200, 200, undefined, 'mtbf')`）。
  - Schedules：`fetches all schedules across pages when the server total exceeds one page`
    （260 条两页取全，断言 `list(0, 200)` 与 `list(200, 200)`）。
  - Users：`fetches all users across pages when the server total exceeds one page`
    （250 条两页取全，同上）。
  - 既有断言追平：#448 筛选测试的 `(0, 100, ...)` → `(0, 200, ...)`。
- 变异自证：反向应用三页面修复补丁（保留全部测试）→ 5 条测试转红
  （3 条新反例 + #448 追平 + 翻页 KPI），其余 10 条不受影响；恢复补丁 → 15/15 全绿。
- 门禁命令与结果见本 PR 正文（lint / type-check / vitest 本组文件 / knip / run_gates check:quick / pytest tests/）。

## Revisit

- 若后端 `/plans`、`/schedules`、`/users` 的排序加入非全序列（如纯 `created_at` 去掉 `id`
  tie-breaker），`fetchAllPages` 的跨请求去重前提失效，翻页会重复/漏行——端点侧改动时必须
  同步复查本组三个查询（`paginate.ts` 顶部注释为判据源）。
- `planKeys.list` 的 limit 维度区分缓存设计（`queryKeys.ts:12-13` 注释仍写 PlanListPage
  limit=100）已随本次改动过时；若未来再引入不同 limit 的计划列表查询，优先加语义维度而非
  继续复用 limit 作区隔。
