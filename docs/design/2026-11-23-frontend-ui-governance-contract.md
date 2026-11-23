# 前端 UI 治理前置契约（v1.0）

> **最后更新**：2026-11-23  
> **状态**：Accepted v1.0（Owner 已完成 D1–D6 全部战略裁决；统一跟踪台账见 [#4117](https://github.com/DUElost/stability-test-platform/issues/4117)）  
> **代码基线**：`origin/main@29ce5a060`  
> **关联文档**：[`2026-08-21-frontend-page-shell-spec.md`](./2026-08-21-frontend-page-shell-spec.md)、[`writing-conventions.md`](../development/writing-conventions.md)（ADR-0059）、[`ADR-0029`](../adr/ADR-0029-project-taxonomy-and-param-layering.md)、[`ADR-0060`](../adr/ADR-0060-plan-parameter-meaning-and-effective-values.md)

本文的规则句按 ADR-0059（`docs/development/writing-conventions.md` §3）书写，只使用「必须 / 不得 / 建议 / 可以」并写明执行者。本文约束 `frontend/src/**` 中所有新写与改动的页面、组件及前端守卫测试；本契约生效后，`frontend/src/design-system/tokens.ts` 中「全站仅用原生 `<select>`」的历史 B4 注释正式废止，以本文 §2.1 为准。

---

## 1. 适用范围与不可破坏的底盘

以下各条均为硬保护边界，实施者在后续治理中必须遵守：

1. 实施者不得绕过 CSS 语义变量在业务组件或页面中硬编码 Tailwind 调色板色值（例如 `bg-blue-500`、`text-amber-700`、`text-gray-600`）；图表颜色仅可以消费 `frontend/src/design-system/colors.ts` 中的语义常量。
2. 实施者不得在前端重写后端已声明权威投影的业务真值（包括但不限于 ADR-0060 参数合并、AEE 聚合计数、设备欠账状态与中心存储挂载真值）；当接口口径缺失或失真时，实施者必须将真值问题转入后端或产品议题，不得在前端凑数字。
3. 实施者不得破坏 `QueryProvider` 四档 `staleTime` 分级、`useSocketIO` 单例房间引用计数与断线退避策略、路由级 `ErrorBoundary`、`readNamedValues` 自动填充读取机制，以及 CSV 公式中和与 Markdown 防 XSS 边界。
4. 实施者必须将列表或详情的错误态、加载态、截断态与空态严格区分；实施者不得在查询报错时渲染空列表或 `0` 统计值。

---

## 2. 交互原语唯一性契约（D1 / D2 与基础控件）

### 2.1 下拉控件两档分层（D1 裁决）

1. 在筛选栏、搜索工具条以及任何动态列表或选项数大于 8 的选择器中（例如项目、主机、机型、Product、系统版本、脚本、Plan、PlanRun），实施者必须使用统一的 `SearchableSelect`（或其业务包装组件如 `ProjectFilterSelect`），不得使用原生 `<select>` 或 `<datalist>`。
2. 在表单或高级筛选弹出层中的固定短枚举选择器（选项数不超过 8 项，例如用户角色、通道类型、运行类型、失败策略），实施者可以使用统一样式的原生 `<select>`（绑定 `FORM.select` 或 `FORM.selectSm`），不得自绘第三套下拉组件。
3. 实施者不得在同页面筛选栏中混用会顶推下方布局的自研内联展开面板替代 `SearchableSelect`。
4. 共享原语维护者必须为 `SearchableSelect` 补齐 Portal 浮层定位（防止被 `overflow-hidden` 裁切）与基础键盘导航（上下方向键、Enter 选中、Esc 关闭）。

### 2.2 列表详情右侧抽屉与弹窗分工（D2 / `#465` 裁决）

1. 共享原语维护者必须在 `frontend/src/components/ui/` 中提供唯一的标准 `Sheet` 原语（基于 Radix Dialog 封装，内置焦点陷阱、Esc 关闭、关闭后焦点归位、遮罩点击关闭及宽度档 `md` 480px / `lg` 640px / `xl` 760px）。
2. 全站高密业务列表（`DevicesPage`、`HostsPage`、`PlanRunDetailPage` 设备/快照/包榜，以及后续解锁页）的详情查看，实施者必须统一采用「表格行内速览 + 右侧标准 `Sheet` 详情」范式，不得在表格内继续堆叠多卡片大块行内展开区，也不得手写 `fixed inset-0` 加 `role="dialog"` 的无焦点陷阱抽屉。
3. 抽屉与模态框的分工均为要求，实施者必须按场景选用：
   - **右侧 `Sheet`**：承载实体只读详情、轻量行内编辑与排障上下文（例如设备遥测与归还欠账、主机诊断与脚本在位、PlanRun 单机明细与参数快照），并支持点击列表另一行原地切换抽屉目标而保持列表上下文可见。
   - **居中 Radix `Dialog`**：承载独立的创建/编辑表单与多步骤配置操作（例如新建设备、归入项目、批量标签、新建版本），且共享原语维护者必须保证受控关闭后焦点归位（收口 `#3259`）。
   - **居中 Radix `AlertDialog`（经 `useConfirm()`）**：承载一切破坏性或不可逆二次确认（删除、退役、解除退役、中止 PlanRun、审批拒绝）；实施者不得调用浏览器原生 `window.confirm`、`window.prompt` 或 `window.alert`。
4. 在未冻结页面中，实施者不得再使用「页内顶部折叠卡片表单」承载新建或编辑流程。

### 2.3 筛选栏、表格、分页与按钮原语

1. 所有包含 2 个及以上筛选条件的列表页，实施者必须使用 `FilterWorkbench`、`FilterSearchInput` 与 `MoreFiltersPopover`；主筛选行仅保留高频条件，低频布尔开关或次要维度必须收纳进 `MoreFiltersPopover`，且 `hiddenChips` 只展示被收纳条件的激活态。
2. 列表页中任何带汇总计数的快捷视图（`QuickViewOption`），实施者必须保证点击后应用的筛选谓词与计数口径完全一致，不得出现计数包含两类状态而点击仅筛选一类的不一致逻辑。
3. 所有业务数据表必须使用 `frontend/src/components/ui/table.tsx` 原语，实施者不得编写裸 HTML `<table>`；所有整行可点击的表格行必须使用 `ClickableRow`（具备 `role`、`tabIndex`、Enter/Space 键盘触发与可见焦点环，收口 `#3264`）。
4. 当列表展示分页条时，实施者必须使用共享 `PaginationBar`（统一 1-based 页码与页容量切换），不得手绘仅含「上一页 / 下一页」的私有分页条。
5. 共享原语维护者必须修复 `FilterSearchInput` 的 `cn()` 覆写缺陷（`filter-bar.tsx:224-227`）及 `Button` 基类 `[&_svg]:size-4` 压过调用点小图标尺寸的问题（`#3487`），确保同排主筛选控件高度差不超过 2px。

---

## 3. 壳层、视觉密度与状态层级契约

### 3.1 页面壳层与主操作位归属

本节扩展并修订 [`2026-08-21-frontend-page-shell-spec.md`](./2026-08-21-frontend-page-shell-spec.md)：

1. 所有位于 `AppShell` 内的业务路由页（含其 loading、error、invalid-id 与 empty 分支）必须由 `PageContainer` 包裹；实施者不得裸渲染无容器或零边距的错误态。
2. `PageContainer` 宽度档维持 4 档（`form` / `content` / `wide` / `bleed`）；自管理内部滚动与高密边距的页面，实施者必须使用 `scrollable={false}` 加 `flex-1 min-h-0`（或经 `PageContainer` 正式支持的紧凑边距变体），不得对 `width="bleed"` 叠加临时手写 `p-4 lg:p-6`，也不得使用 `h-[calc(100vh-...)]` 硬算高度。
3. `AppShell` 顶栏为全站唯一页头，所有业务页必须通过 `PageHeader` 或 `useHeaderSlot` 写入标题与上下文，实施者不得在正文区自绘页头或与顶栏重复的标题栏。
4. 页面级主新建或主触发操作（例如「添加设备」「添加主机」「新建 Plan」「新建套件」「扫描脚本目录」）必须统一放置在 `PageHeader.action`；筛选工作台 `FilterWorkbench.headerRight` 仅可以放置命中统计、重置筛选与视图切换等筛选相关控件。
5. 共享壳层维护者必须在 `AppShell` 全局工具区与左侧页面动作区之间提供清晰分隔，并消除 `HeaderSlotContext` 根级非 memo 重渲染与 `useSocketIO` 广播触发 `AppShell` 双重渲染、卸载误删全局 `online` 监听的缺陷。

### 3.2 视觉密度与首屏预算标准

1. 高密运维列表页（`wide` 档）在 `1440×900` 桌面视口下，页头以下至数据表首行数据之间的行政区（KPI 条 + 筛选工作台 + 表头）总高度建议不超过 `260px`（`1280×800` 下建议不超过 `280px`；Track B 改造前基线为 `DevicesPage` `405px / 451px`、`HostsPage` `429px`）；当 KPI 摘要与快捷视图语义重复时，实施者必须收敛为紧凑单行摘要或将计数并入快捷视图，不得同时堆叠高大 KPI 卡与多行筛选栏。
2. 高密列表表格表头必须实现真实可生效的粘性吸顶（由带确定高度的滚动容器承载，实施者不得把 `sticky top-0` 置于无高度上限的 `overflow-auto` 内）。
3. 列表底部浮动批量操作栏（`BulkActionBar`）直接平铺的高频按钮不得超过 4 个，其余低频或高危批量操作必须收进「更多操作」`DropdownMenu`。
4. 高密列表主行可见的显著彩色胶囊或徽标总数不得超过 **3 个**（通常为 1 个主运行状态加至多 2 个异常或关键属性信号）。

### 3.3 状态视觉层级与无障碍对比度

1. 表格与卡片必须遵守「常态静音、异常着色」原则：正常或默认归属类属性（例如「已映射」「正常」）必须降级为次要纯文本或移入右侧 `Sheet`，实施者仅可以在异常、缺口、告警或进行中态使用彩色徽标。
2. 全站所有实体状态（Agent host、物理设备、PlanRun、Job、Risk、JiraRun）必须通过 `StatusBadge` 渲染中文标签与图标，实施者不得在界面直接输出未经翻译的后端英文枚举（例如 `ONLINE`、`QUEUED`、`MANUAL`、`FAILED`）。
3. 语义颜色跨页必须统一：
   - 失败、致命错误与硬件异常必须使用 `destructive`；
   - 进行中态（`RUNNING` / `TESTING`）必须统一为单一进行色；
   - 主动中止或取消（`ABORTED` / `CANCELED`）必须统一为中性 `secondary` 或明确非失败警告态，不得与系统失败混用 `destructive`；
   - 风险等级 `B` 在同一视图内必须保持单一色调，不得同时出现绿色徽标与琥珀文字。
4. 字号不大于 `14px` 的错误提示正文与表单校验文案必须使用已达 WCAG AA 对比度的 `text-destructive-text`（`frontend/src/index.css`），实施者不得在浅色底正文继续使用 3.76:1 的 `text-destructive`。

---

## 4. URL 状态与跨页深链协议（D4 / D6 裁决）

### 4.1 URL 查询参数持久化（D4 裁决）

1. 本平台维持**页面级项目筛选**，实施者不得在 `AppShell` 顶栏引入全局项目切换器（遵守 ADR-0029 v2.5；支持多浏览器标签独立查看不同项目，点击侧栏导航默认进入全局视图）。
2. 所有列表页、检索页与多页签页的筛选条件、页码、页容量及激活页签，实施者必须通过 `useUrlFilterParams` 同步至 URL Query Params。
3. 跨页通用查询键必须统一使用下列键名：`project`、`host`、`q`、`status`、`plan`、`planRun`、`tab`、`page`、`pageSize`、`drawer`。
4. 当筛选条件导致列表为空时，实施者必须保留 `FilterWorkbench` 与「清除筛选」入口，不得卸载筛选栏退回首次引导空态。
5. 实施者不得在 React render 阶段同步调用 `setSearchParams` 或父级 URL 更新回调；级联筛选失效清理必须在事件处理或受控 `useEffect` 中完成。

### 4.2 跨页实体深链与上下文透传

1. 从列表进入详情页再点击页头「返回列表」时，实施者必须优先还原来源列表的筛选查询串（经 `location.state.from` 携带，缺失时回落默认列表路由），不得硬编码清空全部筛选参数。
2. 以下核心实体在表格、详情抽屉或事件流中出现时，实施者必须提供可点击跳转链接或带上下文深链：
   - `project_key` 跳转 `/projects/:projectKey`；`ProjectDetailPage` 跳设备、Plan 或 PlanRun 列表必须携带 `?project=:projectKey`；
   - Agent host 跳转 `/hosts?q=<ip_or_name>`；`HostsPage` 跳设备列表必须携带 `?host=:hostId`；
   - `plan_id` 跳转 `/orchestration/plans/:planId`；
   - `plan_run_id` 跳转 `/execution/plan-runs/:runId`；`PlanRunDetailPage` 跳 `RunReportPage` 必须携带 `?planRun=:runId`，且 `RunReportPage` 面包屑必须据此指回该 `PlanRun`；
   - `DedupReportCard` 跳转 `/issue-tracker?tab=form&planRun=:runId`；
   - `DeviceDetailDrawer` 跳转 `/execution/plan-runs/:runId/logs?q=<serial>`；
   - `script@version` 跳转 `/script-management?name=<name>&version=<version>`。

### 4.3 AI 助手协同与多站点身份接入（D6 与多站点 R6）

1. AI 助手维持独立全页工作台 `/assistant`，实施者必须在公共壳层与核心运维页补齐三条触达通道：
   - 侧栏「AI 助手」置顶入口与 `/assistant` 页头「待审批」按钮必须向管理员展示待审批数量角标；
   - `SettingsPage` 必须提供直达 `/settings/ai-assistant` 的入口，且 `/assistant/approvals` 与 `/settings/ai-assistant` 必须补齐面包屑；
   - `PlanRunDetailPage`、`HostsPage` 与 `DevicesPage` 详情抽屉必须提供「交给 AI 诊断」跳转入口（携带 `?contextType=...&contextId=...` 预填首轮上下文）。
2. 当后端或运行环境提供当前站点身份配置时，`AppShell` 必须清晰展示当前站点标识（满足多站点 PRD R6/MS-10）。

---

## 5. 规模性能与防返工冻结边界（D5 裁决）

### 5.1 规模性能红线（D5 两阶段契约）

1. **Phase 1（零后端阻塞止血提速）**：
   - 当高密列表数据超过 80 行时，实施者必须启用 `@tanstack/react-virtual` 虚拟滚动，不得在单屏全量挂载数百或数千 DOM 行；
   - 详情级高开销查询（例如设备归还欠账、Agent host 脚本在位明细）必须在右侧 `Sheet` 打开时按需懒加载，实施者不得在列表首屏对全量行预加载；
   - 实施者不得在选中或输入热路径编写 `O(H × D)` 嵌套扫描或未 memo 的全量过滤排序；
   - 已达终态的 `PlanRun` 子卡片（`LogEventsCard`、`TestCaseResultsCard`、`DedupReportCard`）必须自动停止周期轮询。
2. **Phase 2（前后端联调）**：
   - 实施者必须联调 `#496`、`#3325`、`#83` 与 `#3514`，将 `DevicesPage`、`HostsPage` 与 `PlanRunDetailPage` 设备矩阵切换为服务端真分页、服务端检索与真实 facet 计数，并完成 3,750 设备多查看者真实浏览器规模验收。

### 5.2 Phase 1 防返工冻结清单（9 页仅最小止血）

在对应 `[L3]` 产品规划或模块定位拍板前，实施者对以下 9 个冻结页面仅可以执行明确的最小止血（修错态、错色、断链、越界、AI 入口与角标），不得进行整页信息架构重设计或深度 UI 精修：

1. `ResultsPage`
2. `RunReportPage`
3. `IssueTrackerPage`
4. `Dashboard`
5. `FileServerPage`
6. `AssistantPage`
7. `AssistantApprovalsPage`
8. `SchedulesPage`
9. `SettingsPage`（含 `/settings/ai-assistant` 归属）

`DevicesPage`（1 号标杆页）与 `HostsPage`（2 号铺开页）已提前解锁，不属冻结清单；在 `DevicesPage` 完成 `stp-ui-review` 实机验收定调前，实施者不得启动 `HostsPage` 与 Wave 4 页面改造。

---

## 6. 机械门禁与 B 轨验收门槛

### 6.1 静态契约守卫（CI 强制拦截，存量走只减不增 allowlist）

1. 守卫测试必须拦截 `frontend/src/pages/**` 与 `frontend/src/components/**` 中任何未登记在只减不增 allowlist 内的 `window.confirm(`、`window.prompt(`、`window.alert(` 调用。
2. 守卫测试必须拦截业务代码直接使用裸 HTML `<table`（`ui/table.tsx` 与既有 allowlist 除外），以及 `<TableRow` 携带 `onClick` 却未通过 `ClickableRow` 的写法（收口 `#3264`）。
3. 守卫测试必须拦截非豁免路由页缺失 `PageContainer`、已迁移筛选栏回潮原生 `<select>`、以及新增硬编码 Tailwind 调色板类。
4. 守卫测试必须拦截业务组件新建 `fixed inset-0` 加 `role="dialog"` 的手搓抽屉或模态框（`ui/dialog.tsx`、`ui/alert-dialog.tsx`、`ui/sheet.tsx` 与既有 allowlist 除外）。
5. 实施者每清理一页存量违例，必须在同一 PR 同步从对应 allowlist 删除该文件；任何人不得向 allowlist 新增条目。

### 6.2 B 轨实机预览验收门槛（`stp-ui-review`）

凡改动列表页工作台、壳层、抽屉或公共原语的 PR，实施者必须按 `.agents/skills/ui-preview-review/SKILL.md` 在隔离预览栈 `stp-ui-review` 运行 Playwright 巡检并通过 `read_file` 人工审阅截图，满足以下全部门槛：

1. `1440×900` 与 `1280×800` 双视口、亮/暗双主题下，`docOverflow === 0`；
2. 同排主筛选控件高度差不超过 `2px`；
3. 主表表头粘性吸顶生效，右侧 `Sheet` 打开时键盘 Tab 不逃逸背景、Esc 正常关闭且焦点归位；
4. 快捷视图往返切换（例如「全部视角 → 需关注 → 全部视角」）的计数与筛选结果一致；
5. 预览验收结束后，执行者必须运行 `.venv/bin/python tools/dev/ui_review_env.py down` 清理全部预览容器。
