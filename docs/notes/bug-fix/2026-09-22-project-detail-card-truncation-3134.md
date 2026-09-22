# 项目详情页两卡：用 project 计数驱动截断提示（#3134）

Status: implemented
Class: bug-fix

- 日期：2026-09-22
- 关联：`#3134`（本单）、`#3131`（同族缺陷在设备页的表现，确立了「`limit` 是单次响应
  护栏 ≠ fleet 总量」这条不变量）、`#496`（列表基础设施终态）、
  `ADR-0029 D8`（跨页项目上下文，挂起——本单据此**不**做预筛链接）
- 落点：`frontend/src/pages/projects/ProjectDetailPage.tsx`（`CARD_PREVIEW_LIMIT`、
  `TruncationNote`、两处卡脚）

## Decision

两处「设备」/「计划」卡同时显示两个互相矛盾的数字：title 用 `project.device_count` /
`plan_count`（真实值），列表却固定 `list(0, 20, …)` 最多渲染 20 条，**无提示无入口**。

| 项目 | API `device_count` | 该页列表渲染 | API 路径 `total` |
|---|---|---|---|
| V552AA | **513** | 20 | 513（一致） |
| A57 | **281** | 20 | 281（一致） |
| V551A | 20 | 20 | 20 |

6 个有设备的项目里 2 个超 20 ⇒ 这不是"越界后才会发生"，而是**今天就在发生**，且用户
看得见矛盾（标题 513、列表 20 行）却得不到解释。比 `#3131` 更该优先（那单的 862 < 1200
尚未发作）。

**判据取 title 那个计数本身**：`project.device_count > CARD_PREVIEW_LIMIT`。理由有三——

1. **卡内数字自洽**：提示怎么写都不会与 title 打架。若改用列表响应的 `total`，两者来源
   不同（`total` 走 devices 端点的 project 过滤，含 `include_retired=false`），将来口径
   分叉就会出现「标题 513 / 提示 511」这种**新的**自相矛盾。
2. **不依赖列表返回长度**：`count > LIMIT` 与 `devices.length < count` 在"请求 limit ==
   LIMIT"时等价，但前者不受"服务端为何少返回"影响（例如设备全在退役主机上被过滤掉）。
3. **计划侧零后端改动**：`GET /plans` 既没有 `le=` 也没有 `total`（响应是裸数组），若
   判据依赖列表 `total`，本单只能修好一半（设备卡）。

顺带把 `20` 提成 `CARD_PREVIEW_LIMIT`：此前是两处查询字面量 + 提示文案里的字面量，三处
手抄同一个数，改一处就会漂移（提示说 20 而实际请求 30）。

## Alternatives

- **用列表响应的 `total` 驱动**（设备侧本来就有）：放弃，理由 1（来源分叉会产生新矛盾）
  与理由 3（plans 没有 `total`，只修一半）。
- **把预览拉全并全部渲染**：放弃。513 台塞进一张卡是反 UX；且"列表怎么分页"属 `#496`。
- **链接带 `?project_key=` 预筛**：放弃，且是本单最容易顺手做错的一步。设备页**不读**
  search params（`useSearchParams` 只出现在 `LoginPage`/`PlanExecutePage`/`PlanRunDetailPage`/
  `NotificationsPage`/`RunReportPage`），要让它读就得先替 `ADR-0029 D8` 做决定——D8 明写
  **挂起**（v2 决策转向时与 D1/D4/D5/D7/D9 一同挂起），复议触发是「项目数 > ~20，或需要
  跨页保持项目上下文的真实场景」，现 8 个 `test_project`。本单只做裸链接，与
  `PlanExecutePage:1253` 的 `to="/devices"` 同形态。
- **顺手给 `/plans` 补 `total` / `le=`**：放弃。这是响应契约变更（`ApiResponse[List[PlanOut]]`
  → 带 total 的形态），波及 8 个调用点，要走 `add-api-endpoint` 那套；而且**加 `le=` 会
  让 `PlanExecutePage:223` 的 `plans.list(0, 500)` 直接吃 HTTP 422**，必须与调用点同一单
  收口。已在下方 Revisit 留档，不并进本单。

## Verification

- `ProjectDetailPage.test.tsx` 三条新用例：`device_count=513` 时出现「共 513 台，此处最多
  显示 20 台」+「在设备页筛选查看」链接；`plan_count=25` 时出现对应文案 +「在 Plan 管理页
  筛选查看」链接；两个计数都在上限内时**不出现**任何「此处最多显示」文案。
- **变异自证**：把两个 `truncated` 判据置 `false`，两条新用例**均红**
  （`Test Files 1 failed / Tests 2 failed`），恢复后绿。
- 全量前端 `vitest run`：**131 files / 1091 tests passed**。
- `scripts/run_gates.py check:quick`：14 门全绿（含 ruff/eslint/tsc/knip/gov-surface）。
- 未做：真机浏览器确认（断言走 jsdom；本单不涉及几何/命中/autofill，未触及
  `testing.md` §4 的 jsdom 边界）。

## Revisit

- **同族未修站点**：`fetchHostList` 丢 `total`（`HostsPage:60`、`DeviceOverview:464`、
  `PlanExecutePage:232`，host 48 / 上限 200）、`UsersPage:31` 与 `AuditLogPage:132`
  （users 4 / 200）。离边界远，等通用 `fetchAllPages(makeRequest, pageLimit)` 原语落地时
  一并收口——把 `#3131` 的 `fetchAllDevicePages` 形态提升为跨实体原语，而不是每实体抄一遍。
- **计划侧的真问题不在本页**：`PlanListPage:77`（`plans.list(0, 100)`）、
  `usePlanEditForm:83/356` 与 `SchedulesPage:78`（`plans.list(0, 200)`）是同一族的
  "单次请求当全量"，且是**选择器**——少选项没人知道。当前 55 个 plan，到 100 就踩线。
  它的难点是 `GET /plans` **没有 `total`**，截断**连信号都无处可取**，所以修它必须先加
  `total`，且要与 `PlanExecutePage:223`（`0, 500`）同一单收口（加了 `le=` 就会 422）。
- **项目数到达 ~20 时**：触发 `ADR-0029 D8` 复议。若 D8 解禁（设备页读 URL 参数），本单的
  两条裸链接应改成预筛链接。
- **预览上限若要调**：改 `CARD_PREVIEW_LIMIT` 一处即可（查询与文案同源）；但要注意它同时
  是"请求 limit"，调大等于每次进项目详情多拉数据。
