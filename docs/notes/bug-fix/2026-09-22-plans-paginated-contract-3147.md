# `/plans` 收敛到统一分页契约 + 共享 `fetchAllPages` 原语（#3147）

Status: implemented
Class: bug-fix

- 日期：2026-09-22
- 关联：`#3147`（本单）、`#3131`（设备页的同族缺陷：`limit` 不是 fleet 总量）、
  `#3134`（项目详情页的同族形态：同屏数字自相矛盾）、`#3123`（排序键必须构成跨请求
  不变的全序，本单的排序 tie-breaker 是同判据在 `/plans` 上的应用）、
  `#496`（列表基础设施终态：本单**不做**分页 UI）
- 落点：`backend/api/routes/plans.py:list_plans`（契约 + 过滤下推 + 排序 + `le`）、
  `backend/scripts/seed_and_smoke.py`、`backend/tests/api/test_plans_api.py`、
  `frontend/src/utils/api/paginate.ts`（新增原语）、`plans.ts`、`devices.ts`、
  `PlanListPage.tsx`、`usePlanEditForm.ts`、`SchedulesPage.tsx`、`PlanExecutePage.tsx`、
  `ProjectDetailPage.tsx`

## Decision

`GET /plans` 此前返回 `ApiResponse[List[PlanOut]]`——**仓内唯一一个"列表但没有 `total`"
的端点**（`devices`/`hosts`/`plan_runs` 都是 `{items, total, skip, limit}`）。后果是计划侧
的截断**无法被任何消费方检测**：`PlanListPage` 的 KPI 只能用 `plans.length`（已加载条数）
当总数；两个选择器与 Plan 编辑器只能把 `limit` 调大来"保证够用"。收敛到
`PaginatedResponse`（语义与 `devices.list_devices` 对齐），并把三件必须先修的事一起收口：

1. **legacy AEE 过滤下推到分页之前。** 此前先 `offset/limit` 取行、再用 Python 谓词滤掉
   含 `scan_aee` / `export_mobilelogs` 步的计划，于是 ① `total` 会把被隐藏的多算；
   ② 页边界按**未过滤**行数算 ⇒ 逐页翻（`skip` 递增 = 已取条数）时可见计划会被跳过。
   谓词只认两个 `script_name`（`backend/core/legacy_aee.py`），下推为 `NOT EXISTS`
   子查询即可。实测当前 **59 个计划、被隐藏 0** ⇒ 潜伏缺陷，但它是加 `total` 的前置条件。
2. **排序补唯一 tie-breaker**：`order_by(Plan.created_at.desc())` → `+ Plan.id.desc()`。
   `created_at` 同批创建可并列，offset 翻页要求跨请求不变的全序（#3123 判据）。
3. **`le=200`（`_PLAN_LIST_MAX_LIMIT`）与调用点同单收口。** 此前 `/plans` 无 `le=`，
   前端 `PlanExecutePage` 请求 500 是合法的——**加护栏的那一刻它就会吃 HTTP 422**。
   所以这次必须同时把 500 那处改成翻页。

前端把 `#3131` 在 `devices.ts` 里写的私有翻页实现提升为共享原语
`fetchAllPages(fetchPage, pageLimit)`（计划侧是第二个消费者，"每实体抄一遍"不再划算），
并适配 6 个调用点：要"全部计划"的（两个选择器、Plan 编辑器）改 `fetchAllPlans()`；
`PlanListPage` 的 KPI 改用服务端 `total`；`ProjectDetailPage` 取 `.items`。

## Alternatives

- **保留裸数组，只加 `le=`**：放弃。截断仍不可检测（没有 `total` 就没有判据），而
  `plans.list(0, 500)` 会立刻 422——等于用一个新错误换掉一个旧隐患。
- **兼容双形状**（`skip`/`limit` 缺失时返回裸数组，`devices` 有先例）：放弃。那处是历史
  遗留，而新工作引入条件形状更坏；且前端 `plans.list` **总是**带 `skip`/`limit`，shim
  永不触发，只会给测试留一份假安全感。
- **单加一个 `count` 端点**：放弃。只解决 KPI 的总数，不解决"选择器要全量"——后者必须
  翻页，仍然要 `total`。
- **在 `/plans` 内部翻页把结果拼全**：放弃。服务端无法知道客户端是要一页还是要全量，
  全量语义只能在消费方表达。
- **KPI 借 `project.plan_count`**：不适用。`PlanListPage` 是全站列表，没有项目上下文
  （`#3134` 里能用 project 计数是因为那一页天然有 project）。

## Verification

- **后端新用例** `test_list_plans_total_excludes_hidden_and_paging_is_gapless`：3 个可见
  计划 + 1 个隐藏计划交错，`limit=1` 逐页翻完 ⇒ `total` 不含隐藏项、可见计划一个不少。
- **变异自证（后端）**：把过滤改回"分页之后再滤" ⇒ 该用例红
  （`total 应为可见计划数 3，实为 4`），恢复后绿。
- **前端新用例**：`plans.test.ts` 三例（单页 / 250 个跨 2 页按 `skip=200` 翻 / 空页出口
  = 不死循环）；`PlanListPage.test.tsx` 的 KPI 用例刻意给 `total=130` 而只 2 行，
  断言 KPI 报 130。
- **变异自证（前端）**：`total: plansPage?.total ?? plans?.length ?? 0` 改回
  `plans?.length ?? 0` ⇒ KPI 用例红，恢复后绿。
- 全量前端 `vitest run`：**132 files / 1096 tests passed**；
  `pytest backend/tests/api + test_seed_and_smoke.py`：**1336 passed**；
  `scripts/run_gates.py check:quick`：15 门全绿。
- 未做：真机浏览器确认（断言走 jsdom，本单不涉几何/命中，未触及 `testing.md` §4 边界）。

## Revisit

- **`usePlanEditForm` 的"最近 200 条"窗口刻意保留**：该处用 `plans.list(0, 200)` 只为取
  链尾的版本令牌，**链尾超出窗口时省略令牌**是原设计（服务端行锁仍保证原子追加）。本单
  只把形状改成 `.items`，**没有**改成 `fetchAllPlans()`——那是行为变更，须单独评估
  （取全量会让每次链尾追加都多拉数据）。若将来窗口语义要变，注释与服务端兜底要一起改。
- **`PlanListPage` 仍是"取一页 + 真实总数"，没有分页 UI**：计划数超过请求上限时 KPI 会
  诚实地报 >100，但列表只显示 100 行且**无提示**——这是 `#496` 的范畴（分页 + 虚拟滚动 +
  轮询策略），本单只保证"总数不说谎"。
- **同族未迁移**：`fetchHostList` 仍丢 `total`（`HostsPage:60`、`DeviceOverview:464`、
  `PlanExecutePage:232`，host 48 / 上限 200）、`UsersPage:31`、`AuditLogPage:132`。
  统一走 `fetchAllPages` 时一并收口；`coerceHostList` 那份双形状容忍也应随之退休。
- **新增列表消费方一律禁用 `list(0, N)` 形态**：要么 `fetchAllPages`/`fetchAllPlans`，要么
  把 `total` 用起来。`le` 不是"一次能装下整个集合"的承诺。
- `devices.ts` 的 `DEVICE_PAGE_LIMIT = 1200` 与 `le=_DEVICE_LIST_MAX_LIMIT` 仍是两处手抄
  同一个数（无契约测试钉住）。若将来要机械化，先看 `tests/test_api_response_shape_contract.py`
  的增量契约风格再决定值不值得。
