# ADR-0029 P2 前端：项目登记簿 + 设备批量归入 + 四页标签/筛选

- **日期**：2026-08-20
- **ADR**：[ADR-0029](../../adr/ADR-0029-project-taxonomy-and-param-layering.md)
- **前置**：[PR 1 后端接口 + 审计]（projects 路由 / bulk-project / 四端点 project_key 筛选，已验收）

## 决定了什么

P2 前端整包（影响表「前端」行 + 侧栏导航）：

1. **侧栏决策（决策者交裁）**：「项目」一级导航进侧栏，置于「概览」与「测试编排」之间
   （`FolderKanban` 图标）。依据：影响表「前端」行本身写明「新增『项目』一级导航 +
   `/projects` `/projects/:projectKey` 两条路由」——那是 v2 最小形态组成部分，不属
   挂起的 D8 机制；D8 挂起的是「全局选择器 + `?project=` 跨页跟随 + localStorage
   上下文」，导航项只是入口。
2. **`/projects` 列表页**：卡片网格（key/display_name/facet/status/设备数/在跑数）+
   四 facet（客户/平台/形态/产品线）组合筛选（选项从数据 distinct 提取，前端过滤；
   空态区分「暂无项目」与「没有匹配的项目」）。P2 无创建入口（P1 已回填存量）。
3. **`/projects/:projectKey` 详情页**：四块 = 设备（前 20 台）/ 计划（前 20 条）/
   结果（summary 5 条 + 快照语义文案）/ JIRA（`jira_project_key` 非空显示 key，
   否则「未配置」占位 + 等 P3 开放配置能力）。未知 key 按 **404 错误态**渲染
   （「项目不存在」+ 返回列表），不吞成空态——用户裁定「key 是 URL 路径段，拼错
   就是路由错误」。
4. **设备页**：项目筛选下拉（走后端 `?project_key=`，未知 key 404 错误态 + 清除筛选）
   + 行内项目 key 标签 + admin-only「归入项目」批量按钮（`AssignProjectDialog`，
   复用 `sessionQ.data?.role === 'admin'` 显隐，与既有「批量标签」同形态）。
5. **Plan / PlanRun / 结果页**：各自独立项目筛选下拉（共享 `ProjectFilterSelect`，
   页面刷新回到「全部」——**无跨页跟随**）+ 行/卡项目 key 标签。结果页标签走后端
   `RecentRun.project_key`（快照语义）。
6. **后端小补丁**（前端标签依赖）：`PlanOut.project_key` / `PlanRunDetailOut.project_key`
   / `RecentRun.project_key` 三处 + `PlanRun.project` relationship（与 Plan 对齐）。
   均 F2 口径（key，不暴露数字 id）。

## 放弃的备选

| 备选 | 放弃理由 |
|------|----------|
| 影响表「实时」行（`device.project_id` 归属变更广播，弱化版 `project_changed`）| 前端设备页 10s refetchInterval + 批量归入后显式 invalidate `['devices']`/`['projects']`/`['project-devices']` 已覆盖正确性；实时广播改动 SocketIO 契约，独立小 PR 评审更稳 |
| 设备页项目筛选前端过滤（全量拉 1200 台再 filter）| 丢失未知 key 404 语义（用户约束 ②）；1200 台全量拉取不变，但筛选必须走后端 |
| 详情页「查看全部设备」跳设备页并带 `?project_key=` | 接近跨页跟随（D8 边界）；详情页显示前 20 台即可，跨页跟随 P2 后复议时一并评估 |
| 批量归入「移出项目」= 归入 LEGACY 的 UI 提示 | 后端已实现语义，P2 对话框仅做目标选择（LEGACY 与其他项目同在下拉），专门 UI 留给后续 |

## 如何验证

- 前端：`npx vitest run`（608 用例全过，新增 12：ProjectsPage 5 / ProjectDetailPage 4 /
  DevicesPage 归入 2 / 非 admin 入口 1）+ `tsc --noEmit` + `vite build` + `eslint --max-warnings 0` 全绿
- 后端：`pytest backend/tests/api/test_project_routes.py`（16 过，含 3 个 project_key 字段断言）+
  `ruff check` 零告警
- 关键回归钉死：`test_results_summary_uses_plan_run_snapshot_not_plan_ownership`
  （D5 快照语义）+ `test_unknown_project_key_404_across_list_endpoints`（统一 404）

## 何时重议

- **实时广播**（影响表「实时」行）作为独立小 PR（SocketIO 契约改动单独评审）
- **跨页跟随**：D8 挂起机制触发复议时，一并评估详情页 → 列表页带筛选跳转
- **P3**：jira_project_key 填充 + 配置 UI（详情页占位文案已预留）

## 测试基础设施补充

`src/test/setup.ts` 增加 jsdom polyfill：`hasPointerCapture` / `setPointerCapture` /
`releasePointerCapture` / `scrollIntoView`——Radix Select（下拉筛选/对话框）在 jsdom
缺这四个 API，首个 Select 交互测试即炸。现有 79 个测试文件不受影响（纯 additive）。
