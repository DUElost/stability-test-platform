# 前端各界面全量审查（功能 / UX / a11y / 错误处理）

- **状态**：Living（A 轨源码取证 + B 轨截图目视 + 复核轮第三方裁决，均已完成）
- **日期**：2026-08-27
- **范围**：27 个路由屏（25 lazy + Login/Register）+ AppShell 外壳 + 8 个主要模态/对话框。
  **已知盲区（复核轮指出，已补）**：初版范围误写"24 屏"，实际遗漏执行链路 5 屏
  （PlanExecutePage / PlanRunDetailPage / PlanRunListPage / PlanRunLogsPage /
  ScriptManagementPage），已在 §10 补充审查。
- **方法**：A 轨源码取证——逐一读取页面组件，核对功能、状态管理、交互、三态
  （loading/empty/error）、a11y、性能与代码一致性。**B 轨**用 playwright-cli 驱动
  chromium 登录后逐页截图判读（§9）；生产数据截图存 `/tmp/stp-ui-shots-0827/`
  未入库，与 08-19 惯例一致。**复核轮**（第三方独立逐条裁决）见 §10。
- **前置**：本次审查沿袭 2026-08-19 那份 `FRONTEND_UI_REVIEW_2026-08-19.md` 的
  一致性与三态收敛话题，重点补上**功能正确性 / 错误处理 / a11y / 潜在 bug**四个
  上次未覆盖的维度。上次已立案的 A1–A9 / B1–B14 不在此重复，只在此次新证据处引用。
- **净结果**：A 轨 28 条 + B 轨原列 1 条（H5，**复核轮 B 已撤销**，详见 §11）+ 补漏 5 页约 9 条。
  A 轨复核轮（§10）：22 条完全确认、2 条下调（H3/M1）、1 条部分确认（P2）、5 处表述修正。
  B 轨复核轮（§11）：B-H1/B-H2/B-H3 + 08-19 复核表全部确认；H5 根因归因错误（实为陈旧
  `dist` 触发的版本错配 artifact，**当前 HEAD 重新构建复跑不崩**），ErrorBoundary 粒度独立保留。
  **真正高危收敛为 H1 / H2 两条**（B 轨目视确认）。

---

## 1. 基线（已核实，非待办）

| 项 | 现状 |
|---|---|
| 技术栈 | React 19 + Vite 8 + TypeScript + TanStack Query v5 + Tailwind v4 + react-router v7 |
| 数据获取范式 | 绝大多数页面用 `@tanstack/react-query`（useQuery/useMutation/invalidateQueries），设计系统 token 覆盖良好 |
| 设计系统 | `src/design-system/tokens.ts` 语义 token 全站复用，硬编码调色板类名 0 处（承 08-19 基线） |
| 路由 | `router/index.tsx`：ProtectedRoute / AdminRoute / PublicRoute 三层守卫清晰，懒加载按路由分包 |
| 组件规模 | 24 个页面屏，核心页面集中在 100–700 行，整体结构清晰、注释含 CR 决议记录 |

**本次问题定性**：与 08-19 一样，问题的主题不是"有没有基建"，而是"同一件事几套画法 /
几个漏网的反模式"。下列 28 条里大部分是**一致性欠债**与**个别页面的具体缺陷**。

---

## 2. 高危发现（建议优先处理）

### H1 — 系统设置页是"假页面"，全部配置硬编码 —— **已修复（2026-08-27，见 §13）**

**证据**：`pages/settings/SettingsPage.tsx`（114 行）无任何状态、无 API 调用，
所有值写死：数据库连接状态"已连接"（`:57`）、心跳间隔"30 秒"（`:75`）、离线阈值
"90 秒"（`:82`）、设备离线/任务失败通知"已启用"（`:99/:106`）、时区（`:33`）。

**影响**：管理员进入"系统设置"看到的是与真实系统无关的静态数字，数据库连接状态
永远显示"已连接"。这既误导，又因**名称叫"设置"而实为只读概览**，标题与功能错位。

**最小改法**：要么接入真实后端 API（`/api/v1/settings` 或复用现有配置端点）展示运行时
实际值；要么把页面更名为"关于 / 配置概览"并标注"只读"。当前形态属于**未完成的功能**，
比"UX 欠债"更接近"交付缺陷"。

**风险**：中。若后端本就提供配置查询，接入是低成本；若没有需新增端点。

---

### H2 — WiFi 密码明文展示

**证据**：`pages/wifi/WifiPage.tsx:182-190`，WiFi 密码用 `<input type="text">`（`FORM.input`），
未用 `type="password"`。而 `hosts/components/AddHostModal.tsx:185` 的 SSH 密码正确用了
`type="password"`。

**影响**：凭据在页面上明文可见（肩窥 / 截屏泄露），且与同属"测试资产"的主机页处理
不一致。另外表单 `<label>` 无 `htmlFor`、input 无 `id`（见 §3.1），连带 a11y 关联缺失。

**最小改法**：`WifiPage.tsx` 密码字段改 `type="password"`；顺带补 `htmlFor`/`id`。

**风险**：低。

---

### H3 — render 期调用 setState 反模式（**复核轮下调：高危 → 中危/代码气味**）

**证据**（A 轨 + 补漏共 6 处，含 2 个"prev 快照对比"样板）：
- `pages/notifications/NotificationsPage.tsx:71-74`：body 内 `setTab`/`setTabAutoDetected`
  （依据 `hasLogs` 自动跳 logs 页签；**有 `tabAutoDetected` 一次性标志守卫**）。
- `pages/users/components/UserModal.tsx:33-48`：`prevModal` 快照对比 → body 内 setFormData/setErrors。
- `pages/hosts/components/AddHostModal.tsx:35-55`：**同款 `prevModal` 快照对比**（复核轮指出）。
- `pages/execution/PlanExecutePage.tsx:309-314`（`previewResetKey` 快照对比 → `setPreview(null)`；
  key 构造起始 `:309`）
  与 `:474-479`（`devicePageResetKey` `JSON.stringify` 对比 → `goToDevicePage(1)`；key 构造
  起始 `:474`）。
- `pages/scripts/ScriptVersionDialog.tsx:26-39`（`resetKey` 快照对比 → body 内 setState×7；
  路径为 `pages/scripts/` 下，非 `components/` 子目录）。

**复核轮定性**：均为"先 setPrev 再在渲染末尾收敛"的**受控自愈模式**，不会死循环、
不会触发跨组件 "Cannot update a component while rendering" 警告；`NotificationsPage`
有一次性守卫、`UserModal` 的 `editUser` 来自 state 引用稳定（当前不会误触 reset）。
但**本质仍是渲染期副作用**，且 `UserModal` 用对象引用比较 `prevModal.editing !== editUser`
确实易错（父组件传新引用会误触 reset）。**B 轨佐证**：console 无 React 警告
（React 19 production 不报警），属静默反模式。

**最小改法**（2026-08-27 已实施，见 §13）：分两类处理——
1. **NotificationsPage（真副作用）**：异步日志数据到达后的一次性导航，迁 `useEffect`
   （块级豁免 `set-state-in-effect`，`tabAutoDetected` 守卫防循环）。
2. **其余 5 处（官方推荐模式）**：**保留** render 期"adjust state when prop changes"
   模式——React 19 lint 规则 `react-hooks/set-state-in-effect` 明确反对 effect 内
   setState，原模式才是官方推荐；真正的易错点是**对象引用比较**，仅
   UserModal / AddHostModal 改为**按 id 比较**（`prevOpen` + `prevEditingId`），
   ScriptVersionDialog / PlanExecutePage×2 的 resetKey 本就是稳定字符串比较无需改。

**风险**：低。回归重点：UserModal / AddHostModal 表单初始化（打开/切换编辑对象时预填
与清空）、NotificationsPage 自动切 tab 行为。已跑 3 个相关测试文件 96 用例全过。

---

### H4 — 设备指标模态缺 error 态（三态不全）—— **复核轮下调：高危 → 中危；已修复（2026-08-27，见 §13）**

**证据**：`pages/devices/components/DeviceMetricsModal.tsx:26-31`，`useQuery` 只取
`data, isLoading`，未取 `error`。失败时 `data?.points || []` 渲染为空图表，无任何提示。
同款静默吞错模式还见于：
- `pages/execution/PlanRunDetailPage.tsx:330-333`：`ArchiveStatusCard` 只传
  `watcherQ.data?.archive?.ops_metrics`，未传 isError/isLoading；
  `components/plan-run/ArchiveStatusCard.tsx:37-40` `!opsMetrics → return null`，
  watcherQ 失败/加载中整卡**静默消失**（范围比初版所述更广）。
- `pages/execution/PlanExecutePage.tsx:230-233/274-279/288-302`：hosts/scripts/recentRuns
  只取 `data` 无 `isError`，失败静默降级（hostMap 空 → 节点名退化为 id；脚本参数默认值
  不显示；墙钟估算空）。

**影响**：失败被静默吞成"空数据/空卡"，用户无法区分故障与真空。**复核轮定性**：
无数据破坏，属误导性 UX，**中危**（原高危略偏重）。

**最小改法**：补 `isError` 分支（DeviceMetricsModal 弹窗内 `InlineError` + 重试；
ArchiveStatusCard 传 isError 后降级占位；PlanExecutePage 三处补错误提示）。

**风险**：低。

---

### H5 — B 轨方法论 artifact：陈旧 `dist` 触发的版本错配（**复核轮 B 重新定性**）

**初版定性**（已撤销）：B 轨截图 `02-storage.png` 实证 `/storage` 整页崩溃
`Cannot read properties of undefined (reading 'forEach')`，初版判为"FileServerPage 代码
缺陷（优先级 1）"。

**复核轮 B 三级证据链**（已全部核验）：
1. `frontend/dist/assets/FileServerPage-C4n8uu2E.js`（8-24 19:28 构建）含字段 `cpu_iowait_pct`
   （dist grep 命中）。
2. **前端源码全历史零命中该字段**（`git log --all -S"cpu_iowait_pct" -- frontend/` 无输出），
   **后端全历史零命中**（同指令 -- backend/ 无输出），**当前 `FileServerHistory` schema
   仅 4 个数据字段**（`capacity_usage_pct`/`cpu_usage_pct`/`memory_usage_pct`/
   `nfs_requests_per_second`，`backend/api/schemas/file_server.py:101-107`）。
3. 当前源码 `mergeHistory`（`FileServerPage.tsx:171-184`）只 `add` 3 个 schema 必填字段
   （`capacity_usage_pct`/`cpu_usage_pct`/`memory_usage_pct`），无 `cpu_iowait_pct` 访问。

**直接证伪**：用当前 HEAD `npx vite build --outDir /tmp/stp-build-test`（581ms）后起 preview
复跑 `/storage` —— 截图 `05-storage-rebuild.png` 显示**完全正常渲染**（KPI"共享存储健康 正常"、
资源趋势图、控制面 tab、Prometheus 在线、CPU 30.6%），**无任何 `forEach undefined` 错误**。

**复核轮 B 结论（采纳）**：H5 不是当前源码缺陷，而是 **B 轨伺服陈旧 dist 触发的
版本错配 artifact**。报告此前对 FileServerPage 代码做防御性修复是修不存在的问题。

**保留项（独立成立）**：`App.tsx:12` `ErrorBoundary` 包裹整 App，**任何页面 render-throw
都整页无壳**（侧栏顶栏同崩）—— 这是真实结构弱点，与具体页面无关。建议**独立立项**：
把 ErrorBoundary 下沉到路由层（侧栏/顶栏保住），让单页崩不破坏导航。

**复盘教训**：
- B 轨伺服前应**重建或校验 `dist` 与当前 HEAD 一致**（`stat` mtime 比对 / 关键字段 grep）
- A 轨纯源码取证对"陈旧产物"无能为力，**B 轨是版本错配的唯一实战检验**
- 复跑证伪是 B 轨复核轮最直接的确认手段（`<2 分钟`成本 vs 错误的"代码 bug"排期）

---

---

## 3. 中危潜在 bug

### M1 — UsersPage 编辑弹窗硬传 `onSubmit=create`（**复核轮定性：死代码，非误创建 bug；已并入 E6；已修复 2026-08-27**）

**证据**：`pages/users/UsersPage.tsx:181-188` 编辑态 `UserModal` 同时传了
`onSubmit={(data) => createMutation.mutate(...)}` 与 `onUpdate={handleModalUpdate}`。
`UserModal.tsx:92-115` 的 `handleSubmit`：`if (isEditMode && onUpdate) { ...onUpdate } else {
onSubmit }` —— 编辑态且传了 `onUpdate` 时 `onSubmit` 分支**恒不可达**。

**复核轮定性**：这是**死代码**（冗余 prop 的维护性风险），不是可触发的误创建 bug。
当前实际不会误触发（报告原话已说对），"脆弱的误创建路径"措辞夸大了功能风险；
将来删 `onUpdate` 分支才会引爆。**降为代码卫生**（并入 E 类）。

**最小改法**：编辑态只传 `onUpdate`、不传 `onSubmit`（或传 `() => {}` 守卫）。

**风险**：低。

---

### M2 — SchedulesPage 表单校验缺失 + 设备 ID 静默丢弃 —— **已修复（2026-08-27，见 §13）**

**证据**：`pages/schedules/SchedulesPage.tsx:91-98` `handleSave` 只校验 plan_id 与
device_ids，**未校验 name / cron_expr 非空**；设备 ID 解析（`:40-46`）对 `"1,abc"`
这类非法值静默丢弃 abc 且无提示。

**影响**：空名称 / 非法 cron 直接提交，错误落到后端才报；设备 ID 被悄悄截断用户无感。

**最小改法**：表单校验补 name 非空 + cron 合法性；设备 ID 解析对非法项 toast 提示。

**风险**：低。

---

### M3 — AuditLogPage 时间区间非法时静默 return，无任何提示 —— **已修复（2026-08-27，见 §13）**

**证据**：`pages/audit/AuditLogPage.tsx:52` 当 `start_time > end_time` 直接 return，
但此前 `setLoading(true)` 未执行、也不提示用户。用户选错时间会看到旧列表 / 空态且无解释。

**最小改法**：非法区间时给用户 toast 或表单级错误提示，而非静默不请求。

**风险**：低。

---

## 4. 一致性欠债（多页同概念多实现）

### C1 — 数据获取范式分裂：react-query vs 手写 useEffect —— **已修复（2026-08-28：AuditLogPage + SchedulesPage 均已迁 react-query）**

**证据**：绝大多数页面用 react-query；但 `AuditLogPage.tsx:51-76` 与
`SchedulesPage.tsx:51-84` 用 `useState` + `useEffect` + `useCallback` 手动拉取。
后者没有缓存、没有自动重试、没有请求去重。

**最小改法**：两页迁移到 react-query。`SchedulesPage` 还需把分页/缓存语义一并理顺。

---

### C2 — 页签（tab）实现三种画法，无障碍语义全缺 —— **已修复（2026-08-28：抽 StateTabs 组件，underline/segmented 两变体 + role="tablist"/aria-selected；IssueTracker/Notifications 已接入）**

**证据**：
- `IssueTrackerPage.tsx:82-94`：手写 `<button>` + `tabLinkClass`，无 `role="tablist"`。
- `NotificationsPage.tsx:242-252`：`SEGMENTED` token，同样无 `role`/`aria-selected`。
- `FileServerPage.tsx:634-707`：`role="tablist"/tab/tabpanel` + `aria-selected`（做得最规范）。

**影响**：三处页签三种实现；前两处缺 ARIA 角色与选中态，屏幕阅读器无法感知当前页签。
`FileServerPage` 是现成的正确样板。

**最小改法**：以 `FileServerPage` 为准抽一个页签组件，IssueTracker / Notifications 接入。

---

### C3 — 可点击整行 / 整卡缺键盘可达性 —— **已修复（2026-08-28：抽 ClickableRow 组件（role/tabIndex/Enter+Space）；Results/ProjectDetail 行已接入；ProjectsPage 项目卡此前已达标）**

**证据**（多处）：
- `results/ResultsPage.tsx:164-166` 表格行 `onClick` 导航
- `projects/ProjectDetailPage.tsx:297-301` 运行记录行 `onClick`
- `projects/ProjectsPage.tsx:256-261` 项目卡 `onClick`
- 对比 `components/ui/DashboardStatCard.tsx:93` 已有完整 `role="button"`/`tabIndex`/`onKeyDown`。

**影响**：键盘用户无法用 Tab+Enter 触发这些跳转。交互语义不统一。

**最小改法**：给这些行/卡补 `role="button"` + `tabIndex={0}` + `onKeyDown`（Enter/Space），
或改为内部可聚焦元素。

---

### C4 — 表单实现与 label 绑定不统一 —— **已修复（2026-08-28：WifiPage/SchedulesPage/ChangePasswordPage/NotificationsPage/ScriptVersionDialog/PlanRunHero 已补 htmlFor/id；包裹型 label 判定隐式关联无需改）**

**证据**：
- 封装 `Input` 组件（LoginPage / RegisterPage / 多数页）vs 原生 `<input className={FORM.input}>`（ChangePasswordPage / UserModal / WifiPage 内联表单）。
- label 绑定：LoginPage / UserModal 有 `htmlFor`+`id`；ChangePasswordPage:73-104、
  WifiPage、SchedulesPage 的 `<label>` 无 `htmlFor`，input 无 `id`。

**最小改法**：全站统一表单输入实现，并补齐 `htmlFor`/`id` 关联（可 a11y 扫描批量定位）。

---

### C5 — 错误提示 token 与消息粒度不统一 —— **已修复（2026-08-28：LoginPage 横幅改 ALERT_BOX；SchedulesPage/NotificationsPage/WifiPage 错误均带 toApiError 详情）**

**证据**：
- 错误横幅 token：LoginPage 用 `STATUS_CHIP.destructive`，RegisterPage/ChangePasswordPage 用 `ALERT_BOX.destructive`。
- 错误消息：Hosts/Devices/Schedules 的保存路径用 `toApiError(error).message` 展示后端详情；
  WifiPage、NotificationsPage 的保存 `onError` 只用"创建/更新/删除/保存失败"泛化文案，丢失后端 detail。
- **复核轮补例外（方向相反的两条，恰好加强"同一页内粒度也不统一"论点）**：
  NotificationsPage `handleTestChannel:164` 实际用了 `toApiError(err).message`；
  SchedulesPage 的 `delete:131` / `toggle:140` 反而是泛化 `'删除失败'`/`'切换失败'`。

**最小改法**：统一错误横幅 token 与 `toApiError` 用法。

---

### C6 — 批量操作并发策略不一致 —— **已修复（2026-08-28）**

**证据**：`devices/DevicesPage.tsx:151-170` 标签批量更新用并发 worker（5）；`hosts/HostsPage.tsx:278-295` 批量删除是串行 `for...of`，大批量下慢且失败不汇总（`onError` 只 toast 单台，最终仍报"已完成"）。

**最小改法**：Hosts 批量删除改并发受控 + 汇总失败数。

---

### C7 — 模态关闭语义相反 —— **已修复（2026-08-28：统一为提交中禁止所有关闭路径）**

**证据**：`AddHostModal` 的"取消"按钮仍 `disabled={isSubmitting}`（复核轮修正：无条件允许关闭
仅限 X 与 overlay 两条路径，注释确实写了"始终允许关闭"）；`AddDeviceModal:64-66` 在
`isSubmitting` 时全部路径禁止关闭。同一类模态两种交互。

**最小改法**：定一条"提交中能否关闭"的统一规则。

---

### C8 — 注册页绕过 api 客户端 —— **已修复（2026-08-28：走 `api.auth.register`；用户名前端校验未补，随 C4 表单统一轮）**

**证据**：`pages/auth/RegisterPage.tsx:35` 直接 `axios.post('/api/v1/auth/register', ...)`，
而 LoginPage 走 `api.auth.login`。会导致 token/拦截器/错误规范化不一致；且前端对用户名
无 `/^[a-zA-Z0-9_]+$/` 校验（UserModal 有），提交非法用户名靠后端拒绝。

**最小改法**：注册走 `api.auth.register`（若存在），补用户名前端校验。

---

## 5. 三态 / 错误处理缺口（各页单独）

| 页面 | loading | empty | error | 备注 |
|---|---|---|---|---|
| Dashboard | ✅ | ✅ | ✅（summary 短路 + 子图内联） | 子图与 summary 加载态判断不一致（§6.D1） |
| ProjectsPage | ✅ | ✅（两种 empty 区分） | ✅ | 本组最强 |
| ProjectDetailPage | ✅（detail） | ✅（子块） | ⚠️ 子块缺 | 设备/计划/结果/型号 4 个子 query 失败被吞成空态 |
| WifiPage | ✅ | ✅ | ✅（内联） | 见 H2 / §3.4 / §6.D2 |
| HostsPage | ✅ | ✅ | ✅ | 404/业务错误未区分（§6.D3） |
| DevicesPage | ✅ | ✅ | ✅（含 404 特判） | DeviceMetricsModal 缺 error（H4） |
| FileServerPage | ✅ | ⚠️ 局部兜底 | ✅ | 有旧数据时刷新失败静默降级（§6.D4） |
| ResultsPage | ✅ | ✅ | ✅ | 行可点击非键盘可达（C3） |
| RunReportPage | ✅ | ✅ | ✅ | JIRA 折叠缺 aria-expanded（§6.A1） |
| IssueTrackerPage | ✅ | ⚠️ 仅 drafts 页签 | ⚠️ 仅 drafts 页签 | N+1 拉 draft（§6.P1） |
| SchedulesPage | ✅ | ✅ | ✅ | 校验缺失（M2）、内联表单 UX（§6.D5） |
| NotificationsPage | ✅ | ✅ | ❌ 缺 error | 全部 query 无 error 分支，失败静默成空态 |
| LoginPage | ✅ | N/A | ✅ | 错误横幅缺 role="alert"（§6.A2） |
| RegisterPage | ✅ | N/A | ✅ | 同 §6.A2 |
| ChangePasswordPage | ✅ | N/A | ✅ | label 无 htmlFor（C4） |
| UsersPage | ✅ | ✅ | ✅ | 无分页（§6.P2）、M1、UserModal render setState（H3） |
| AuditLogPage | ✅ | ✅ | ✅ | 非 react-query（C1）、时间区间静默（M3） |
| SettingsPage | ❌ | N/A | ❌ | **硬编码假数据**（H1） |
| NotFoundPage | N/A | N/A | N/A | 无文档标题（§6.A3） |
| PlanRunListPage | ✅ | ✅ | ✅（含 404 特判） | 无分页（§6.P2，`:34` list(0,50)）；ClickableCard 键盘可达（C3 样板） |
| PlanRunLogsPage | ✅（子组件） | ✅ | ✅ | 分页完整（PAGE_SIZE=50）；runQ 缺 error（§10.4）；事件行 onClick 缺键盘（C3） |
| PlanRunDetailPage | ✅ | ✅ | ✅（runQ） | **ArchiveStatusCard 无 error 静默消失**（H4 同款，`:330`） |
| ScriptManagementPage | ✅ | ✅（搜索/真空区分） | ✅ | list(true) 无 limit（P2 家族）；ScriptVersionDialog render setState（H3 实例） |
| PlanExecutePage | ✅（devicesQ） | ✅ | ⚠️ 多处静默降级 | H3×2（`:310/:475`）；P2/P3 多处（`:227/232/266/293/300`）；hosts/scripts/recentRuns 无 error 分支（H4） |

---

## 6. 其余发现（低危）

### A（a11y）
- **A1** `RunReportPage.tsx:264-270` JIRA 草稿折叠按钮缺 `aria-expanded`/`aria-controls`。
- **A2** Login/Register/ChangePassword 错误横幅均缺 `role="alert"`/`aria-live`，SR 不自动播报。
- **A3** `NotFoundPage.tsx` 未设 `useDocumentTitle`，标签页标题不随 404 更新；未用 `PageContainer`。

### P（性能）
- **P1** `IssueTrackerPage.tsx:36-45` draft 加载是 N+1（每 run 一次请求，无并发上限、一次拉 50 run）。
- **P2** 一次性拉取 + 无分页 UI，数据超量时静默截断（**复核轮修正证据 + 补漏扩清单**）：
  - `UsersPage` `list(0, 200)`、`SchedulesPage` `list(0, 200)`、`NotificationsPage` 渠道/规则 `list(0, 200)`
  - `PlanRunListPage:34` `list(0, 50)`、`PlanExecutePage:227` `list(0, 500)` / `:232` `list(0, 200)`
    / `:293` `list(0, 10)` / `:300` `list(0, 30)`
  - `WifiPage` 用的是 `api.resourcePools.listLoads()`（**无分页参数端点**，复核轮修正：非 `list(0, 200)`），
    "无分页 UI 一次拉全量"的实质成立、代码写法以修正为准
  - **分页澄清**（复核轮修正）：`NotificationsPage` 的日志页签（`:533-541`）有手写 `page`/`pageSize`
    分页，不是无分页；`AuditLogPage` 是**唯一用 `PaginationBar` 组件**的，而非"唯一有分页"
- **P3** `DevicesPage:48` `list(0, 1200)` 一次性拉全量 + 前端全渲染，无虚拟滚动（60+ host / 1000 device 目标下会恶化）。
- **P4** Dashboard 6 个 query 各自轮询（summary 10s + 5 图 60s），无 `staleTime`，切页重挂载全量重拉。

### D（UX / 单页）
- **D1** `Dashboard` 双数据源时间戳：WebSocket `lastUpdateTime` 与 summary 轮询数据无关，时间戳更新≠数据更新，易感知错位。
- **D2** `WifiPage:202-209` `parseInt(...) || 1` 使输入框无法先清空再输入（清空即回 1）；`max={1000}` 硬编码魔法数。
- **D3** `HostsPage` 整页 `ErrorState` 只提示"请检查后端服务连接"，未像 DevicesPage 区分 404/业务错误语义。
- **D4** `FileServerPage` 有旧数据（keepPreviousData）时刷新失败**无提示**，静默降级为旧数据。
- **D5** `SchedulesPage:223-289` 表单用内联卡片而非模态，编辑多行数据需滚回顶部找表单；打开无聚焦管理、无 ESC 关闭。
- **D6** `PlanListPage.tsx:208` 列表卡片显示 `specialty_key` 原始 key，而筛选下拉用 `display_name`，两处文案不对应。
- **D7** `NotificationsPage:332-337` "添加规则"按钮 `disabled` 时无原因提示。
- **D8** `PlanEditPage.tsx:27` `Number(id) > 0` 判断：URL 非法 id（如 `/plans/abc`）会被静默当成"新建"而非报错（对比 PlanRunDetailPage 有 `Number.isNaN` 检查）。

### E（代码卫生）
- **E1** `HostsPage:48-49` `canManageWatcherAdminState` 与 `isAdmin` 都是 `role==='admin'`，两名同值，语义混淆。
- **E2** `HostsPage:587-592` empty 分支只渲染一个 AddHostModal，主分支渲染两个（新增+编辑），重复实现有同步风险。
- **E3** `FACET_FIELDS` 在 `ProjectsPage` 与 `ProjectDetailPage` 各定义一份，形态不同，可抽公共常量。
- **E4** `WifiPage` 表单固定 `resource_type:'wifi'` 仍存 state 提交，属残留字段。
- **E5** `ResourcesPage.tsx`（复核轮修正：实际 6 行）硬编码重定向到 `/wifi`，无测试。
- **E6** `UsersPage.tsx:184` 编辑态传 `onSubmit=create` 为死代码（原 M1，复核轮定性，见 §3）。

---

## 7. 方法局限与何时重议

**B 轨已做（§9）、A 轨复核轮已完成（§10）、B 轨复核轮已完成（§11）**：B 轨用
playwright-cli + 缓存 chromium 登录逐页截图，证实 H1/H2/H3 行为级目视可见，意外发现
**FileServerPage 整页渲染崩溃（初版列 H5）**；A 轨复核轮独立裁决后 H3/M1 降级、P2/C5
等表述修正、补漏 5 页，全部已采纳；**B 轨复核轮进一步指出 H5 根因归因错误**（实为
陈旧 dist 触发的版本错配，§11.2 B-TS1 复跑已证伪）—— H5 撤销，ErrorBoundary 粒度
独立保留为 H5'。

- **H1（Settings 假页面）动手前**：先确认后端是否存在可用的配置查询端点；若没有，这属于
  **产品范围决策**（要不要做真实设置页），应先在 PRD/需求层定，再谈前端。
- **H3（render setState，6 处）**：改动小但触及表单初始化，`UserModal`/`AddHostModal`/
  `PlanExecutePage` 若有测试先看覆盖再改。
- **M1（UsersPage onSubmit 死代码，现 E6）**：随手清理，不占排期。
- **C2 / C3 / C4**：都是"定规矩再逐页对齐"的一致性命门，建议先立规范表（页签组件 /
  可点击行语义 / 表单实现）再铺开，避免边改边定。
- **P1–P4（分页/虚拟滚动/轮询）**：与 08-19 的 B7 密度同源，**60+ host / 1000 device
  目标推进时**会从低危升为高危，建议在规模上线前统一做一轮"列表基础设施"（分页 +
  虚拟滚动 + 轮询策略）；P2 实例清单已扩入执行链路 5 页（§10.4）。
- **E1/E2/E3/E6** 属内务，搭其它轮的车顺手清即可。

---

## 8. 汇总

本次 28 条新发现按优先级排序（建议合并立项）：

| 序 | 发现 | 影响 | 成本 | 类别 |
|---|---|---|---|---|
| 1 | **H1** Settings 假页面（硬编码） | 高（误导管理员 / 未完成功能） | 中（需后端支持） | 单页缺陷 |
| 2 | **H2** WiFi 密码明文 | 高（凭据泄露风险） | 极低 | 单页缺陷 |
| 3 | **H3** render 期 setState 反模式（6 处：Notifications/UserModal/AddHostModal/PlanExecute×2/ScriptVersionDialog）| 中 | 低 | 代码气味 |
| 4 | **M2 + M3** Schedules 校验缺失 / Audit 时间静默 | 中 | 低 | 单页缺陷 |
| 5 | **H4** 子 query 失败静默吞空（DeviceMetrics/ArchiveStatusCard/PlanExecute）| 中 | 低 | 单页缺陷 |
| 6 | **C1** 数据获取范式分裂 | 中 | 中 | 一致性 |
| 7 | **C2** 页签三种实现缺 ARIA | 中（FileServer 是样板） | 中 | 一致性 + a11y |
| 8 | **C3** 可点击行/卡非键盘可达 | 中 | 低 | a11y |
| 9 | **C4** 表单/label 绑定不统一 | 中 | 低 | 一致性 + a11y |
| 10 | **C5 + C6 + C7 + C8** 错误粒度 / 并发 / 关闭语义 / 注册直连 | 中 | 低–中 | 打包 |
| 11 | **A1–A3** 各 a11y 小项 | 低–中 | 低 | a11y |
| 12 | **P1–P4** 分页/虚拟滚动/轮询 | 低→高（规模上线时） | 中–高 | 性能 |
| 13 | **D1–D8** 单页 UX | 低–中 | 低 | 打磨 |
| 14 | **E1–E6** 代码卫生（含 M1 死代码） | 低 | 极低 | 内务 |
| 15 | **H5'** ErrorBoundary 粒度（独立保留项）—— **已修复 2026-08-28** | 中（单页崩会带掉整壳） | 中 | 结构弱点 |
| 附 | ~~H5 FileServerPage 整页崩~~ | 已撤销 | — | B 轨方法论 artifact |

### 推荐 top 3（复核轮 B 后调整）

**① H1 + H2 打包**：B 轨目视确认的"看一眼就知道不对"——一个删假数据改真实/改名，
一个一行改 `type="password"`。**真正高危收敛为这两条**。

**② H3 打包（render 期 setState 清理，6 处）**：统一迁移到 `useEffect`，
低成本收敛一处代码气味（复核轮已确认非高危、非 bug）。

**③ H4 + C2 + C3 + C4 打包（a11y + 一致性 + 静默错误）**：页签统一、可点击行补键盘、
表单/label 统一、子 query 补 error 分支。以 `DashboardStatCard` / 封装 `Input` 为样板
逐页对齐。

**④ H5' 独立立项（ErrorBoundary 下沉）**：H5 主结论已撤销，但 `App.tsx:12` 包裹整 App
是真实结构弱点。任何页面 render-throw 都整页无壳，建议把 ErrorBoundary 下沉到路由层
（侧栏/顶栏保住），**与具体页面解耦**。

> **复审注**：此处旧版"top 3（B 轨后调整）"段落（① H5 优先等，与本节矛盾的已撤销结论）
> 已在复审时删除，仅保留本节权威版本。

---

## 9. B 轨（浏览器目视）结果

**取证方式**：playwright-cli 0.1.18（内部 playwright-core 1.62.1，驱动
`~/.cache/ms-playwright/chromium-1234`）+ `vite preview` 服务 `frontend/dist`
（端口 4173；`vite.config.ts` 无 preview 段，`server.proxy` 是否被 preview 继承存疑，
但生产 DB 数据吻合证明请求实际到达后端——机制未写全，见 §11.3）。登录用仓库根
`.env.backend` 的 `STP_ADMIN_USER` / `STP_ADMIN_PASSWORD`（shell 变量传递，命令记录
不含明文）；`AGENT_SECRET` 注入到浏览器上下文作为 `X-Agent-Secret` 请求头
（CSRF 中间件对该头放行，AGENTS.md 描述一致）。
**截图共 16 张**存 `/tmp/stp-ui-shots-0827/`，未入库。前 15 张视口 1920×1080；
第 16 张（B-TS1 复跑 `05-storage-rebuild.png`）为浏览器默认 1280×720（复跑未 resize，
不影响"不崩"结论，见 §11.4 视口注记）。

**截图索引**：

| # | URL | 验证目标 |
|---|---|---|
| 01-dashboard | `/` | 概览、08-19 B1/B9 复核 |
| 02-settings | `/settings` | **H1 假页面（目视确认）** |
| 02-wifi | `/wifi` | 列表空态 |
| 02-schedules | `/schedules` | M2 / D5 表格静态形态 |
| 02-notifications | `/notifications` | **H3 自动跳 logs（目视确认）** |
| 02-issue-tracker | `/issue-tracker` | C2 页签下划线样式 |
| 02-storage | `/storage` | **H5 整页渲染崩溃（B 轨新发现）** |
| 03-orchestration-plans | `/orchestration/plans` | D6 specialty 一致性 |
| 03-projects | `/projects` | C3 facet 筛选 + 卡片 |
| 03-hosts | `/hosts` | 08-19 B6/B7/B9 复核 |
| 03-devices | `/devices` | KPI + 表格密度 |
| 03-users | `/users` | M1 静态形态 |
| 03-account-password | `/account/password` | **C4 label/无 type=password（目视确认）** |
| 03-execution-plan-runs | `/execution/plan-runs` | 列表视觉 |
| 04-wifi-new | `/wifi`（展开新增） | **H2 密码明文字段（目视确认）** |

**B 轨关键发现**：

### B-H1 — Settings 假页面（目视实锤 H1）
截图 `02-settings.png` 显示：数据库连接状态"已连接"（绿点）、心跳间隔"30 秒"、
离线判定阈值"90 秒"、通知"已启用"、时区"Asia/Shanghai (UTC+8)"，与源码硬编码
完全一致。绿点"已连接"**直接误导**——与系统实际数据库连接状态无关。
A 轨 H1 升级为**目视确认级高危**。

### B-H2 — WiFi 密码明文（目视实锤 H2）
截图 `04-wifi-new.png`（展开"新增 WiFi 池"表单）：**密码**字段与其它文本框外观
一致（无 `type=password` 暗点遮罩、无密码框小眼睛图标），与 SSID/路由器 IP 同型。
源码使用 `FORM.input`（不带 `type`），浏览器以 `type=text` 渲染，**凭据明文可见**。
A 轨 H2 升级为**目视确认级高危**。

### B-H3 — Notifications 自动跳 logs（目视实锤 H3）
截图 `02-notifications.png`：**进入 `/notifications` 直接显示"通知记录"页签**
（顶部三段页签"通知渠道 (0) / 告警规则 (0) / **通知记录**"中第三个高亮，
"共 1310 条通知"），而非源码默认的 channels 页签。
这是 A 轨认定的 `hasLogs` 渲染期 setState 反模式的目视证据。
**Console 复检**：`playwright-cli console` 显示浏览器无 React 警告（0 warnings,
1 error 为 SocketIO 鉴权失败，预期内）——React 19 production 模式不报警告，
属"静默反模式"。A 轨 H3 升级为**目视确认级**，建议仍按反模式清理。

### B-H5 — FileServerPage 整页渲染崩溃（B 轨新发现）
截图 `02-storage.png`：`/storage` 路由整页只有"页面出错了"+ 红色错误条
`Cannot read properties of undefined (reading 'forEach')` + 刷新按钮。
**侧栏、顶栏全部消失**（截图证实只剩错误卡与背景色），说明 ErrorBoundary 兜底
粒度是整 App，文件服务器页 render-throw 即整页无壳。
此条**A 轨未发现**（A 轨只读源码，未触发现实数据；FileServerPage 需真实 /api/v1/file-server-overview
响应进入特定分支才会 throw）。**初版曾升为 §2 H5（§8 优先级表第 1 位）—— 该结论
已被 B 轨复核轮撤销**（实为陈旧 `dist` 触发的版本错配 artifact，B-TS1 复跑证伪，
详见 §11.2）。此处保留为 B 轨历史记录，不按此排期。

### B-08-19 复核
08-19 那份审查列出的 B 轨条目，本轮复核结果：

| 08-19 条目 | 本轮状态 | 证据 |
|---|---|---|
| B1 仪表盘双层标题 + 中英图例 | **部分解决** | 设备状态/主机资源图内层标题与边框已去掉，图例中文化（01-dashboard）；任务活动趋势/完成趋势仍有内层卡标题 |
| B2 审计页筛选栏塌行 | 沿袭 | 本轮未截 audit 页（聚焦本次审查目标），但源码未改 |
| B3 Toast 压住页头 | 沿袭 | toast 系统未变 |
| B4 原生 select vs Radix 11:2 | 沿袭 | Hosts/Devices/Schedules/Notifications 等继续原生 select |
| B5 结果页重复列 | 沿袭 | 本轮未截 results 页 |
| B6 主机列 IP 重复 | **仍存在** | 03-hosts：每行只有 IP 172.21.x.x，副标题 Watch 已激活 |
| B7 密度：1920×1080 只见 12 行 | **仍存在** | 03-hosts：行高 ~63px，1080p 可见 12 行 |
| B8 空态四套 | 沿袭 | WiFi 空态用 InlineEmpty（窄带灰字），与 EmptyState 卡片居中不同 |
| B9 KPI 高度不齐 | **仍存在** | 03-hosts：34 主机总数卡比其余三张多副文案，高度多约 10px |
| B10 错误态透出内部异常 | 沿袭 | ErrorState 行为未改 |
| B11 提示语重复 | 部分仍存 | IssueTracker 提单卡片子标题与面板头同；B 轨内未严重 |
| B12 危险图标无区分 | **仍存在** | 03-schedules：操作列 4 个等大裸图标；03-hosts "批量更新" + 三个点菜单一律同色 |
| B13 同一卡内两种分段控件 | 沿袭（FileServer 不可达） | 见 §11 B 轨复核轮：H5 根因归因错误，重新构建后可达 |
| B14 告警色用于无数据 | 沿袭 | 03-devices 错误数 3 仍用 destructive 色，但有真实值时合理 |

**A 轨条目 B 轨复核**：

- **A1 空态三套** — 目视证实三套并存（WiFi 内联灰字、Hosts EmptyState 大图标卡片、Audit/Users 手写 Card+ 图标）
- **C2 页签三种** — 目视证实（IssueTracker 下划线、Notifications pill、FileServer aria tablist 但 H5 后不可达）
- **C3 可点击行/卡** — Projects / Plan 列表 / 主机行均整卡/整行 onClick，目视看不出键盘可达性（a11y 工具验证非目视可证）
- **C4 label 无 htmlFor** — 修改密码页 3 个 label 与 input 视觉相邻但无语义关联（03-account-password）

**B 轨未做的项目**（留给复核轮或后续）：
- 截 audit 页复核 B2（塌行）
- 截 results 页复核 B5（重复列）
- 截 PlanEditPage 编辑器（复核轮已定性 M1 为死代码，无需交互复现误创建）
- 触发 DeviceMetricsModal 失败态验证 H4（需 route 拦截）
- 触发 NotificationsPage API 失败验证 §5 三态缺口（需 route 拦截）
- 触发 Schedules 编辑展开内联表单（验证 D5）
- 复核轮（第三方独立 DOM 几何 / 计算样式实测）— 08-19 末段教训
- console 之外：性能 profile、内存泄漏、网络瀑布等

**取教训**（与 08-19 末段格式一致）：
- A 轨纯源码取证对"真实数据下 throw 的页面"无能为力（H5 即此类）—— B 轨在生产/真实数据下
  跑一遍是新发现最直接的来源
- FileServerPage 这次崩页说明本项目的"前端测试"对真实后端响应覆盖不足（vitest 单元测试无法
  捕获集成路径上的 undefined.forEach）—— 一个反向证据，建议 E2E 用 Playwright 跑全路由烟雾测试
- B 轨已经"修好一个目视"+"发现一个 B 轨独有"两项有效工作，证明补 B 轨价值高
- 截图判读应同时跑"标准场景"（有数据/有交互）与"崩溃场景"（route 拦截模拟失败），后者
  能验证三态/错误处理最关键

### B-TS1 — H5 复跑：当前 HEAD 重新构建后 `/storage` 不崩（B 轨复核轮根因证伪）

**触发**：B 轨复核轮指出 H5 根因归因错误（`dist` 引用了源码全历史不存在的
`cpu_iowait_pct`），建议"用当前 HEAD 重新 `vite build` 后复跑 `/storage`"作为最直接的
证伪/确认手段。

**执行步骤**（已在文档报告时间 `2026-08-27` 完成）：
1. `rm -rf /tmp/stp-build-test` + `cd frontend && npx vite build --outDir /tmp/stp-build-test`
   —— 构建成功，581ms。
2. `npx vite preview --outDir /tmp/stp-build-test --port 4173` —— preview 起来。
3. playwright-cli 打开浏览器 → 注入 `X-Agent-Secret` → 管理员登录 → `goto /storage` →
   截图 `05-storage-rebuild.png`。

**结果（截图 05-storage-rebuild.png，目视判读）**：
- 顶部 KPI"共享存储健康 正常"（绿点）
- 资源趋势图正常渲染（容量/内存折线，时间范围 6H/24H/7D）
- 控制面 / 中心存储机 tab 正常（控制面已选，Prometheus 在线，CPU 30.6%）
- **无任何 `forEach undefined` 错误，无 ErrorBoundary 兜底页面**

**结论**：H5 在当前 HEAD 重新构建的产物下不复现 —— **H5 是 B 轨方法论 artifact
（陈旧 `dist` 触发的版本错配），不是当前源码缺陷**。H5 主结论已撤销；
**ErrorBoundary 粒度（`App.tsx:12` 包裹整 App）** 独立保留为 H5'（§8 第 15 位 + top 推荐 ④）。

---

## 10. 复核轮（第三方裁决）采纳记录

复核轮对 A 轨 28 条逐条核验：**22 条完全确认、2 条严重性下调（H3、M1）、1 条部分确认
（P2）、5 处表述修正、1 个方法论盲区**。下述全部修正已采纳进本文档正文
（各条目标题/内容已就地改写），此处留裁决凭证。

### 10.1 逐条裁决

| 判定 | 条目 |
|---|---|
| ✅ 完全确认（22 条） | H1、H2、H4、M2、M3、C1–C8、A1–A3、P1、P3、P4、D1、D3–D8、E1–E4 |
| ⚠️ 严重性下调（2 条） | **H3**（高危 → 中危/代码气味）、**M1**（误创建 bug → 死代码） |
| ⚠️ 部分确认（1 条） | **P2**（证据两处不精确，实质成立） |
| ✅ 表述修正（5 处） | C5 补两条例外、C7 修正"无条件"、E5 行数 6 行、H4 降中危、P2 见上 |

### 10.2 修正明细与采纳

1. **H3 下调**：复核轮指出两处 setState 均有收敛条件（NotificationsPage 有 `tabAutoDetected`
   一次性守卫；UserModal 属 React 官方认可的"storing information from previous renders"
   模式），不会死循环、不触发跨组件警告；`AddHostModal:35-55` 有第三处同模式，是全库惯例。
   另补审又发现 `PlanExecutePage:309-314 / :474-479` 与 `ScriptVersionDialog:26-39` 两处同款，
   **共 6 处**。已降级为"代码气味"并统一最小改法（迁移 useEffect）。✅ 采纳。
2. **M1 改为死代码**：`UserModal:95` 的 `isEditMode && onUpdate` 分支在编辑态恒成立，
   `onSubmit=create` 是恒不可达路径，定性为冗余 prop 维护性风险（并入 E6）。✅ 采纳。
3. **P2 证据修正**：`WifiPage` 实为 `api.resourcePools.listLoads()`（无分页参数端点）；
   `AuditLogPage` 是唯一用 `PaginationBar` 组件的（`NotificationsPage` 日志页签有手写分页）。
   ✅ 采纳，并已扩入 `PlanRunListPage:34`、`PlanExecutePage:227/232/293/300` 实例。
4. **C5 补例外**：`NotificationsPage:164 handleTestChannel` 用 `toApiError`；
   `SchedulesPage:131/140` delete/toggle 反而泛化 —— 方向相反的两条例外恰加强"同一页内
   粒度也不统一"论点。✅ 采纳。
5. **表述修正**：E5 实际 6 行（非 7）；C7"无条件允许关闭"仅限 X 与 overlay（取消按钮仍
   `disabled={isSubmitting}`）；H4 无数据破坏、属误导性 UX，降中危。✅ 全部采纳。

### 10.3 方法论盲区与补漏（最重要）

初版范围"24 屏"与实际不符：router 有 27 个路由屏（25 lazy + Login/Register），
**漏掉执行链路 5 屏**（PlanExecutePage / PlanRunDetailPage / PlanRunListPage /
PlanRunLogsPage / ScriptManagementPage + ScriptVersionDialog）。已补审（§10.4），
结果并入 §5 三态表与 P2/H3/H4 实例清单。

### 10.4 补漏 5 页审查结论

| 页面 | 三态 | 主要补漏项 |
|---|---|---|
| PlanRunListPage | ✅ 完整（含 404 特判） | P2：`:34` `list(0,50)` 无分页 UI；用 `ClickableCard`（自带 role/tabIndex/onKeyDown），是 C3 应有样板 |
| PlanRunLogsPage | ✅（事件流子组件三态全） | runQ 只取 data 无 error（`:33-37`）；事件行 onClick 无键盘（`PlanRunEventStream.tsx:80`，C3 实例）；分页完整（PAGE_SIZE=50） |
| PlanRunDetailPage | ✅（runQ 有 error） | **H4 同款**：`ArchiveStatusCard` 只传 ops_metrics 无 isError（`:330`），watcherQ 失败整卡静默消失 |
| ScriptManagementPage | ✅（搜索/真空区分） | `list(true)` 无 limit（P2 家族）；**H3 实例**：`ScriptVersionDialog:26-39` resetKey 快照 setState×7 |
| PlanExecutePage | ⚠️ 多处静默降级 | **H3×2**（`:309-314` previewResetKey、`:474-479` devicePageResetKey）；**P2/P3 家族**（`:227` list(0,500)、`:232` list(0,200)、`:266` fetchAllDevices 全量+前端分页、`:293/:300`）；hosts/scripts/recentRuns 无 error 分支（H4） |

**补漏结论**：5 页中 3 页（List / Logs / Scripts 主列表）三态完整、用 ClickableCard /
原生 button，是本轮审查质量最高的一组；真正需补漏集中在 **H3 反模式在
PlanExecutePage（×2）与 ScriptVersionDialog（×1）共 3 处新增实例**，以及
**ArchiveStatusCard 一处 H4 同款静默消失**。

### 10.5 基线 claim 核验（复核轮）

- "硬编码调色板类名 0 处" ✅ 基本成立（`text-success`/`bg-success` 是 tokens.ts 语义 token
  类名，非 hex/任意值）。
- "token 全站复用"略放宽：SettingsPage 等页面裸写 token 类名而非引用 `tokens.ts` 常量
  （如 `STATUS_TEXT_COLORS.success`），属同体系不同写法 —— 并入 C4 一并收敛。

### 10.6 采纳后最终状态

- **A 轨时高危列 3 条**（H1/H2/H5），B 轨复核轮（§11）撤销 H5 根因，**真正高危收敛为
  2 条**：H1（Settings 假页面）、H2（WiFi 密码明文），均有 B 轨目视证据。
- **中危**：H3（6 处反模式）、H4（静默吞错）、M2、M3。
- A 轨复核轮总体建议（采纳方向不变、H1+H2 优先、补充 5 页审查）已全部落实；P2 清单
  已按修正后实例重新排列。
- H5 详细撤销与复跑证伪见 §11。

---

## 11. B 轨复核轮（第三方裁决）采纳记录

B 轨复核轮对 B 轨结果与 H5 根因做独立裁决：**B-H1/B-H2/B-H3 全部确认、08-19 复核表
引用正确、截图取证真实**，但 **H5 根因归因错误**——H5 是 B 轨伺服了非仓库产物的陈旧
`dist` 触发的版本错配 artifact，**不是当前源码缺陷**。下述全部已采纳进本文档正文。

### 11.1 B 轨复核轮确认属实

| 项 | 核验结果 |
|---|---|
| 截图取证 | 初轮 15 张 PNG 全部存在（17:39–17:42，1920×1080 有效），与索引表一一对应；加 B-TS1 复跑 1 张（1280×720）共 **16 张** |
| B-H3 数据 | 生产 DB 只读查询：notification_channels=0、alert_rules=0、notification_logs=1310——与截图"通知渠道 (0) / 告警规则 (0) / 共 1310 条通知"逐字吻合，**证明页面加载了真实生产数据** |
| B-H1 / B-H2 | 与 A 轨源码核验一致（Settings 硬编码值、WiFi 密码无 type 均属实） |
| ErrorBoundary 粒度 | `App.tsx:12` 包裹整个 App——"页面 render-throw 即整页无壳"结构判断属实 |
| 08-19 复核 | B6/B7/B9 编号与原始描述对照正确，"源码未改 → 仍存在"推断合理 |
| 环境 | chromium-1234 缓存存在；playwright-cli 工具可用 |

### 11.2 H5 根因归因错误（最重要，已纠正）

**原判断**：FileServerPage 需定位 `forEach(undefined)` 根因（怀疑 `summaryQ.data?.xxx`
解构不安全），列为优先级第 1 位。

**复核轮 B 三级证据链（已核验）**：
1. `frontend/dist/assets/FileServerPage-C4n8uu2E.js`（8-24 19:28 构建）含字段
   `cpu_iowait_pct`（dist grep 命中）
2. **前端源码全历史零命中该字段**（`git log --all -S"cpu_iowait_pct" -- frontend/`
   无输出），**后端全历史零命中**，**当前 `FileServerHistory` schema 仅 4 个数据字段**
   （`backend/api/schemas/file_server.py:101-107`）
3. 当前源码 `mergeHistory`（`FileServerPage.tsx:171-184`）只 `add` 3 个 schema 必填字段
   （`capacity_usage_pct`/`cpu_usage_pct`/`memory_usage_pct`），无 `cpu_iowait_pct` 访问

**直接证伪（B-TS1 复跑）**：用当前 HEAD `npx vite build --outDir /tmp/stp-build-test`
（581ms）后起 preview 复跑 `/storage` —— 截图 `05-storage-rebuild.png` 显示
**完全正常渲染**（KPI"共享存储健康 正常"、资源趋势图、Prometheus 在线、CPU 30.6%），
**无任何 `forEach undefined` 错误**。

**采纳结论**：H5 主结论**已撤销**（从 §2 高危、§8 优先级表 1、top 推荐 ① 全部移除）。
**ErrorBoundary 粒度（`App.tsx:12` 包裹整 App）** 独立保留为 **H5'**（§8 第 15 位 + top
推荐 ④），与具体页面解耦独立立项。

### 11.3 表述修正（不改变结论方向，已采纳）

1. "vite preview proxy 验证后端 8000 生效"机制描述存疑（`vite.config.ts` 无 preview 段；
   `server.proxy` 不会继承到 preview 模式）。但 DB 数据吻合证明效果有实证，机制描述
   与配置不符（playwright-cli / vite 内部可能有内置转发，报告未写全）。
2. playwright-cli 版本：CLI 包实际 0.1.18（`playwright-cli --version`），报告写 1.62.1
   应为内部 playwright-core 版本，未直接验证。
3. B-H3 Console 复检（0 warnings / 1 error）：无法重现验证，但逻辑自洽（前端浏览器无
   `AGENT_SECRET`，SocketIO 握手被拒属预期）。

### 11.4 复盘教训

- **B 轨伺服前应重建或校验 `dist` 与当前 HEAD 一致**（`stat` mtime 比对 / 关键字段
  grep）。这是本次 B 轨的**最核心方法论教训**。
- A 轨纯源码取证对"陈旧产物"无能为力，**B 轨是版本错配的唯一实战检验**。
- 复核轮提出的"重新构建复跑"是**最直接的证伪手段**（`<2 分钟`成本 vs 错误的"代码 bug"
  排期），建议作为 B 轨 SOP：每张崩页截图都应触发一次"重建复跑"做根因定性。
- 复核轮 1 小时内连续发现（H5 根因 + 陈旧 dist）这一对**联动证据**——这是单轨审
  查难以获得的交叉验证价值。

### 11.5 采纳后最终状态（第二次更新）

- **真正高危收敛为 2 条**：H1（Settings 假页面）、H2（WiFi 密码明文），B 轨目视确认。
- H5 撤销；H5'（ErrorBoundary 粒度）独立保留。
- 中危：H3（6 处反模式）、H4（静默吞错）、M2、M3。
- 复跑产物已清理（`/tmp/stp-build-test` 已删，preview 已关），截图 `05-storage-rebuild.png`
  保留在 `/tmp/stp-ui-shots-0827/`。

---

## 12. 复审（A 轨 + B 轨修改后，第三次外部裁决）采纳记录

复审轮对两轮修改后的正文逐条核验：**10/11 条新增证据确认、1 条证伪、3 处路径/计数
微调、4 处文档一致性残留**。下述全部已处理，此处留裁决凭证。

### 12.1 新增证据核验（10 确认 + 1 证伪）

| 修改项 | 判定 | 备注 |
|---|---|---|
| H3 扩至 6 处 | ✅ 确认 | `PlanExecutePage:309-314`（previewResetKey→setPreview(null)）、`:474-479`（JSON.stringify 对比→goToDevicePage(1)）、`ScriptVersionDialog:26-39`、`AddHostModal:35-55` 均属实，行号 ±1 偏移 |
| H4 扩至 ArchiveStatusCard | ✅ 确认 | `PlanRunDetailPage.tsx:330-333` 只传 opsMetrics 未传 isError；`components/plan-run/ArchiveStatusCard.tsx:37-40` `!opsMetrics → return null`——失败/加载中整卡静默消失（比报告所述范围更广） |
| H4 扩至 PlanExecutePage 三 query | ✅ 确认 | hosts `:230-233` / scripts `:274-279` / recentRuns `:288-296` 只取 data 无 isError，对比同页 plans `:220-228` 有完整错误面 |
| M1 并入 E6 | ✅ 确认 | 定性正确 |
| P2 修正 + 扩清单 | ✅ 确认 | 与上轮修正一致 |
| 补漏 5 页三态行 | ✅ 确认 | PlanRunListPage list(0,50) 无分页；ClickableCard 确有 Enter/Space+role/tabIndex（`components/ui/clickable-card.tsx:31-50`，C3 应有样板成立）；PlanRunLogsPage runQ `:33-37` 无 error、事件行 onClick `:80` 无键盘、PAGE_SIZE=50 分页完整；ScriptManagementPage list(true) 无 limit；PlanRunDetailPage runQ 有 error |
| **§10.4 低危边界「:457 空筛选 .every() 全选误亮」** | ❌ **证伪** | `:457` 实际为 `filteredAvailableIds.length > 0 && filteredAvailableIds.every(...)`——已有 length > 0 前置守卫，空数组时短路为 false，全选态不会误亮（toggleAll 同样有守卫）。属误报，**已从文档移除** |

### 12.2 路径/行号微调（已采纳）

| 项 | 修正 |
|---|---|
| ScriptVersionDialog 路径 | `pages/scripts/ScriptVersionDialog.tsx`（非 `pages/scripts/components/`） |
| ArchiveStatusCard 路径 | `components/plan-run/ArchiveStatusCard.tsx`（非 `components/execution/`），行号 `:37-40` |
| H3 PlanExecutePage 行号 | key 构造起始 `:309` / `:474`（原写 310/475，范围 `:309-314` / `:474-479`） |

### 12.3 文档一致性清理（已处理）

1. **删除 §8 旧版「推荐 top 3（B 轨后调整）」段**（含已被撤销的「① H5 优先」）—— 与新
   版（复核轮 B 后调整，H1+H2 优先、H5' 独立立项）矛盾，已删除并留注。
2. **§9 B-H5 段加撤销注记** —— 保留为 B 轨历史记录，标注「初版曾升为 §2 H5，已被 B 轨
   复核轮撤销，详见 §11.2，不按此排期」。
3. **统一截图张数** —— 实际 16 张（索引表 15 + 复跑 1）；§9「14 张」初版遗留错误修正为
   「16 张」；§11.1 同步为「初轮 15 + 复跑 1 = 16」。
4. **视口注记** —— B-TS1 复跑 `05-storage-rebuild.png` 为 1280×720（未 resize），已注明
   「前 15 张 1920×1080，第 16 张 1280×720，不影响结论」。

### 12.4 最终状态（第三次更新）

- **真正高危收敛为 2 条**：H1（Settings 假页面）、H2（WiFi 密码明文），B 轨目视确认。
- **H5 撤销**（版本错配 artifact）；**H5'**（ErrorBoundary 粒度）独立保留。
- 中危：H3（6 处反模式）、H4（静默吞错）、M2、M3。
- 三轮外部裁决（§10 A 轨复核轮 / §11 B 轨复核轮 / §12 复审）全部采纳完毕；正文无
  「已撤销结论」残留、无计数矛盾、无误报条目。

---

## 13. 修复记录（2026-08-27）

| 项 | 状态 | 改动 |
|---|---|---|
| **H2** WiFi 密码明文 | ✅ 已修复 | `WifiPage.tsx` 密码字段 `type="password"` + `autoComplete="new-password"`；顺带补全该表单 6 字段 `htmlFor`/`id`（C4 部分） |
| **H1** Settings 假页面 | ✅ 已修复 | 新增后端 `GET /api/v1/settings`（`backend/api/routes/settings.py`，`require_admin`，聚合 env/模块常量/`SELECT 1` 探测/通知规则）；前端 `SettingsPage.tsx` 改 react-query 拉真实数据 + 三态；`backend/tests/api/test_settings_endpoints.py` 6 用例全过；临时 uvicorn 验证返回真实值 |
| **H3** render 期 setState 反模式 | ✅ 已修复 | 见下详注。`NotificationsPage` 自动切 tab 迁 `useEffect`（真副作用）；`UserModal`/`AddHostModal` 保留官方推荐 render 期模式但改 **id 比较**；`ScriptVersionDialog`/`PlanExecutePage`×2 确认无需改（resetKey 为稳定字符串比较）。相关 3 测试文件 96 用例全过 |
| **H4** 子 query 失败静默吞空 | ✅ 已修复 | `DeviceMetricsModal` 失败显示 `InlineError`+重试；`ArchiveStatusCard` 补 `isLoading`/`isError`/`onRetry`，失败/加载/空显示卡壳占位不再消失；`PlanExecutePage` 三 query 取 error，页面顶部聚合 `ALERT_BANNER` 提示条+重试。相关 3 测试文件 76 用例全过 |
| **M2** Schedules 校验缺失 + 设备 ID 静默丢弃 | ✅ 已修复 | `handleSave` 补 name/cron 非空校验；`parseDeviceIds` 返回非法项并 `toast.info` 提示。`SchedulesPage.test.tsx` 2 用例全过 |
| **M3** Audit 时间区间非法静默 | ✅ 已修复 | `start_time > end_time` 改 `setError` + 清空列表，复用 `InlineError` 渲染 |
| **M1/E6** UsersPage 编辑弹窗死代码 | ✅ 已修复 | `UserModal.onSubmit` 改可选，编辑弹窗删除 `onSubmit=create`（复核轮定性死代码），只传 `onUpdate` |
| **H5'** ErrorBoundary 粒度 | ✅ 已修复（2026-08-28） | `AppShell.tsx` main 区域包 `ErrorBoundary fullscreen={false}`（页面级兜底，单页 render-throw 只崩内容区、侧栏顶栏保住）；`App.tsx` 顶层 ErrorBoundary 保留为 Provider/Shell 级最后防线；`ErrorBoundary` 新增 `fullscreen` prop（默认 true 保持顶层全屏，紧凑模式 `h-full min-h-64`）。`ErrorBoundary.test.tsx` + `PlanRunDetailPage.test.tsx` 23 用例全过 |

**验证证据**：
- 后端 pytest：`test_settings_endpoints.py` 6 passed（testcontainers PG）
- 前端：`eslint` + `tsc --noEmit` 全过
- 手工 curl `/api/v1/settings` 真实响应：`database_connected: true`（真实探测）、
  `agent_heartbeat_interval_seconds: 20`、`offline_threshold_seconds: 300`（真实默认，
  替代原硬编码 30s/90s）、通知开关按真实规则聚合
- 设计取舍与验证细节见 `docs/notes/feature/2026-08-27-settings-endpoint.md`

**遗留（不在本轮）**：H1 的"可编辑设置"属产品决策，重议条件见 Agent Note「何时重议」。

### 剩余问题 issue 跟踪（2026-08-28 建）

| Issue | 内容 | 排期 |
|---|---|---|
| #496 | [UI] P 类列表基础设施（分页 + 虚拟滚动 + 轮询） | deferred，60+ host / 1000 device 目标推进时升第 1 位（与 #370 同源） |
| #497 | [UI] A 类 a11y 小项 | 随手 |
| #498 | [UI] D 类单页 UX 打磨（D6 与 #448 交叠） | 随手 |
| #499 | [UI] E 类代码卫生 | 内务 |
