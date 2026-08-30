# 前端审查 A / D / E 类整改（2026-08-27 全量审查）

Status: implemented
Class: bug-fix

## Decision

按 `docs/reviews/FRONTEND_UI_REVIEW_2026-08-27.md` §6 的 A（a11y 3 项）、
D（单页 UX 8 项）、E（代码卫生 5 项）逐条整改。这一批的共同点是**每条都小、且
不需要产品决策**，此前一直没动只是因为没有整块的注意力预算。

**A 类（a11y）**
- A1 `RunReportPage` JIRA 草稿折叠按钮补 `aria-expanded` / `aria-controls`，
  被控容器补 `id="jira-draft-panel"`；
- A2 `LoginPage` / `RegisterPage` / `ChangePasswordPage` 错误（与成功）横幅补
  `role="alert"` + `aria-live="assertive"`，读屏自动播报；
- A3 `NotFoundPage` 补 `useDocumentTitle` 并改走 `PageContainer`（此前是全站唯一
  不用页面外壳的路由，标签页标题不随 404 更新）。

**D 类（单页 UX）**
- D1 `Dashboard`「更新于」不再取 WebSocket `lastUpdateTime`，改取 summary 查询的
  `dataUpdatedAt`——前者只反映 WS 事件到达时刻，与轮询数据无关，会出现
  「时间戳在动、数字没动」的错位；WS 订阅保留（实时失效仍需要它）；
- D2 `WifiPage` 最大设备数改用独立的字符串 state 承载，`parseInt(v) || 1` 会在
  清空那一刻把值弹回 1，用户根本无法先清空再输入；硬编码 `max={1000}` 提为
  `MAX_DEVICES_LIMIT`，提交时夹到 `[1, 1000]`；
- D3 `HostsPage` 整页错误态区分「无 HTTP 状态码 = 连不上后端」与「有状态码 =
  后端业务错误（展示状态码 + 后端 message）」，不再一律甩给
  「请检查后端服务连接」；
- D4 `FileServerPage` 有旧数据（`keepPreviousData`）时刷新失败补一条可见提示与
  重试入口，说明展示的是陈旧数据及其生成时间；
- D5 `SchedulesPage` 内联表单补三种反馈：打开时滚动到表单并聚焦首个字段、ESC
  关闭、取消逻辑收敛为 `closeForm`（此前取消的清理代码在 JSX 里内联）；
- D6 `PlanListPage` 卡片专项徽章改显示 `display_name`（与筛选下拉一致），原始
  `specialty_key` 保留在 `title`；
- D7 `NotificationsPage`「添加规则」禁用时补 `title` 说明需先创建通知渠道；
- D8 `PlanEditPage` 把非法 id 判出来报错——`Number(id) > 0` 对 `/plans/abc`
  得 NaN、比较为假，会静默落进「新建」分支，用户以为在编辑某个 Plan，实际拿到
  空白表单。与 `PlanRunDetailPage` 的 `Number.isNaN` 检查对齐。

**E 类（代码卫生）**
- E1 `HostsPage` `canManageWatcherAdminState` 与 `isAdmin` 合并为单一真值来源
  （此前是同一判定的两个独立表达式，改一处忘一处就静默漂移；能力名保留，
  `ExpandableHostTable` 的 prop 契约不动）；
- E2 `HostsPage` 的新增/编辑两个 `AddHostModal` 抽成 `hostModals` 片段——原先在
  empty 分支与主分支共 3 处声明，且 empty 分支的 `onSubmit` 传的是裸
  `createMutation.mutate`（与主分支的包装写法不同，正是「重复实现有同步风险」的
  实证）；
- E3 `FACET_FIELDS` 抽到 `pages/projects/facetFields.ts`（原先 `ProjectsPage` 存
  字符串数组 + label 映射、`ProjectDetailPage` 存 `[key, label]` 元组，同一概念两
  份且形态不同）。新模块同时导出 `FACET_FIELDS`（筛选逻辑用）与
  `FACET_FIELD_ENTRIES`（遍历渲染用）；
- E4 `WifiPage` 表单里的 `resource_type: 'wifi'` 删除——后端
  `ResourcePoolCreate.resource_type` 默认就是 `'wifi'`，前端携带一个恒值字段只会
  让人以为是真在传参；
- E5 `ResourcesPage` 硬编码重定向 —— **已不存在**（无该文件、无对应路由），
  审查记录已过时，本项关闭。

## Alternatives

- **D1 改成 WS 事件时刻与数据时刻双显示**：放弃——两个时间戳只会把「哪个才是我看
  到的数字的时间」这个问题又问一遍。用户要看的是数据新鲜度，取 `dataUpdatedAt`。
- **D5 把内联表单改成模态**（审查原文倾向）：放弃——模态化要重写
  `SchedulesPage` 的表单布局与 `CronExpressionInput` 的嵌入方式，收益（滚回顶部）
  已被「滚动 + 聚焦」覆盖，代价（测试重写、交互回归）不成比例。
- **E1 直接删掉 `canManageWatcherAdminState`、全用 `isAdmin`**：放弃——组件按
  「能力」收 prop，两个名字表达的是能力而非当前判定；删掉能力名等于把将来可能
  的分化提前埋掉。保留名字、收敛真值来源。

## Verification

- `npx tsc --noEmit` 通过；`npm run lint`（ESLint，`--max-warnings 0`）通过。
- vitest：`Dashboard` / `HostsPage` / `SchedulesPage` / `PlanEditPage` /
  `FileServerPage` / `PlanListPage` / `ProjectsPage` / `ProjectDetailPage`
  8 个文件 57 例全绿。
- 逐条人工核对：A1 折叠按钮的 `aria-expanded` 随展开态翻转；D2 输入框可清空后
  重填；D5 从列表下方点编辑会滚到表单且焦点在名称字段、ESC 可关闭；D8 访问
  `/plans/abc` 显示「无效的 Plan ID」而非空白新建表单。

## Revisit

- 审查原文 §6 的 P 类（分页 / 虚拟滚动 / 轮询策略）已单独立 issue（#496，
  deferred），不与本批混做。
- 若 `ExpandableHostTable` 的 watcher 管理能力判断将来真的与 admin 分化，E1 的
  「单一真值来源 + 能力名保留」正是为那一刻留的接口，届时只改一处赋值。
