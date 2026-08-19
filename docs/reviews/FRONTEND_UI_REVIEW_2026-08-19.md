# 前端界面视觉与布局审查

- **状态**：Living（A 轨源码取证 + B 轨目视取证 + 第三方复核轮，均已完成）
- **日期**：2026-08-19
- **范围**：25 个路由屏 + AppShell 外壳 + 6 个模态
- **方法**：三轮。**A 轨**只做源码可证的结构与一致性判定（§2）；**B 轨**用 Playwright
  驱动无头浏览器登录后逐页截图并判读（§5）；**复核轮**由第三方改用 DOM 几何 / 文本 /
  计算样式实测独立复现，不依赖目视。
- **净结果**：23 条发现无一虚报；B 轨推翻 A 轨 2 条推断，复核轮修正 5 处表述与
  1 处优先级定性。全部留档于 §5 末尾。
- **复跑证据**：`bash /tmp/verify-ui-review.sh`（源码类，只读）；
  截图索引 `bash /tmp/serve-shots.sh` → `http://localhost:8899/index.html`。

---

## 1. 基线（已核实，非待办）

设计系统已建成且干净，本轮不重复这些结论：

| 项 | 现状 |
|---|---|
| 语义 token | `src/design-system/tokens.ts`(591 行) + `colors.ts` + `index.css` 双主题 HSL 变量 |
| 硬编码调色板**类名** | **0 处**（`gray-*/slate-*/red-*/blue-*` 全库无命中） |
| 硬编码**十六进制**字面量 | **12 处**，全部集中在 `components/log/XTerminal.tsx:131-142` 的 xterm `theme` 对象（其中 131–140 行 10 处） |
| `knip --include files --dependencies` | 干净（exit 0，无输出） |
| 测试基线 | 78 files / 580 tests |

**关于那 12 处十六进制**：xterm.js 的 `theme` 只接受字面色值，读不了 CSS 变量，
所以这是**合理例外**，不作为发现。但由此产生一个真实后果：
**终端配色被写死为深色**（`background: '#0f172a' // slate-900`），浅色主题下不跟随。
是否要让它跟随主题，属产品决策，记在 §7。

> A 轨最初把这一行写成"硬编码调色板 0 处"，是只 grep 了 Tailwind 类名的盲点。

**因此本轮的问题不在"有没有设计系统"，而在"同一件事有几种画法"。** 下列 9 条里有 6 条是
同概念多实现，不是缺实现。

---

## 2. 发现清单

### A1 — 空态三套画法并存，其中一处是不可达死分支

**证据**

| 画法 | 位置 |
|---|---|
| `EmptyState` 组件 | 10 个页面 |
| 手写 `Card + h3` | `pages/users/UsersPage.tsx:161-172`、`pages/audit/AuditLogPage.tsx:121-125`、`pages/hosts/HostsPage.tsx:645-656` |
| 表格内 `colSpan` 单行文案 | `pages/storage/FileServerPage.tsx:418`、`pages/users/components/UserTable.tsx:43`、`pages/wifi/WifiPage.tsx:245` |

**死分支**：`HostsPage.tsx:567` 已 `if (tableData.length === 0) return <EmptyState "还没有主机">` 提前返回；
`:620` 又写 `{tableData.length > 0 ? <表格> : <Card>暂无主机</Card>}`。`tableData` 是 `:341` 的
`useMemo`，两处之间不重新赋值 —— **`:644-656` 的 else 分支永远渲染不到**。

**文案**：同一页两种说法（`:572`「还没有主机」/ `:646`「暂无主机」）；全站「暂无 X」与「还没有 X」混用。

**影响**：同一产品里"没有数据"有三种视觉形态（卡片居中大图标 / 卡片无图标 / 表格内一行灰字）。
HostsPage 那份还是维护者会去修改、用户永远看不到的死代码。

**最小改法**：删 `HostsPage.tsx:644-656`（约 12 行）；`UsersPage` / `AuditLogPage` 两处手写改
`EmptyState`（各 ~10 行→3 行）；表格内空行统一为 `EmptyState` 或明确定为"表格内空行"这一独立形态并抽成组件。

**风险**：低。`UsersPage` 手写空态带一段内联 SVG 人形图标，换成 `EmptyState` 需传 `icon` 保留语义。

---

### A2 — 加载态四套画法

**证据**

| 画法 | 位置 |
|---|---|
| `SKELETON_BLOCK` 内联 div（清一色 `h-32` + `h-64`） | `DevicesPage:247-248`、`HostsPage:546-547`、`SchedulesPage:168-169`、`NotificationsPage:247-248,557` |
| `LoadingGrid` + `CardSkeleton` | 3 处（`PlanRunListPage:38` 等） |
| `Loader2` 旋转图标 | `AuditLogPage:118`、`UsersPage`、`ScriptManagementPage`、`RunReportPage`、`PlanEditPage`、`StoragePage` 等 8 处 |
| 基础 `Skeleton` | 7 处 |

**影响**：菜单间切换时加载形态跳变（骨架块 → 转圈 → 卡片骨架）。四套里 `h-32 + h-64` 那套是四处逐字复制。

**最小改法**：定两套即可 —— 「首屏结构已知 → 骨架」「局部/短操作 → spinner」，把 `h-32+h-64`
那四处收敛成一个 `PageSkeleton` 组件。

**风险**：低，纯展示层。

---

### A3 — Job 状态两套视觉语言，且在同一行内并置

**证据**：`pages/results/ResultsPage.tsx`

```
:136  <span className={cn('rounded px-1.5 py-0.5 text-xs', RUN_RESULT_STATUS_CHIP[run.status] …)}>
:137    {run.status}                      ← 方角 / 淡底(bg-*/10) / 英文原文 "FINISHED" / 无图标
:141  <StatusBadge kind="risk" status={run.risk_level} size="sm" />
                                          ← 圆角 / 实底(bg-*) / 中文「高」/ 带图标
```

同一表格行里两种胶囊语言。此外同状态色相也不一致：
`status-badge.tsx` 的 `JOB.RUNNING` → `variant="info"`（`199 89% 48%`，青），
`tokens.ts:224` 的 `RUN_RESULT_STATUS_CHIP.RUNNING` → `STATUS_CHIP.primary`（`217 91% 60%`，蓝）。

**已排除的误判**：`FINISHED` 不是错值 —— `backend/api/routes/results.py:72` 有
`"COMPLETED": "FINISHED"` 的出参映射，Results API 确实返回 `FINISHED`。所以这是**视觉与术语**问题，
不是数据问题，改法不能是"改 key"。

**影响**：用户每天面对的结果页出现中英混排的状态胶囊；同一个 RUNNING 在执行页是青色徽章、
在结果页是蓝色 chip。这是本次主诉「视觉语言是否一致」最直接的反例。

**最小改法**：给 `status-badge.tsx` 的 `REGISTRY` 补一个 `job-result` kind（`FINISHED/DISPATCHED/QUEUED/CANCELED`
→ 中文 + 与 `job` 同色），`ResultsPage:136` 改为 `<StatusBadge kind="job-result" …>`。
`RUN_RESULT_STATUS_CHIP` 随后可删。

**风险**：中。胶囊从方角淡底变圆角实底，列宽会变（英文 8 字符 → 中文 2 字），需 B 轨确认表格不撑破。
`status-badge.test.tsx` 需补 kind 用例。

---

### A4 — 表格底座分裂：`ui/table` 4 处 vs 原生 `<table>` 6 处

**证据**

`components/ui/table.tsx` 自带 `overflow-auto` 滚动容器（`:9-14`）与统一表头（`h-10 px-2`，`:79`）。
原生手写的 6 处各自为政：

| 文件 | 滚动容器 | thead 底色 | 单元格 padding |
|---|---|---|---|
| `pages/audit/AuditLogPage.tsx` | **无** | `bg-muted/50` | `px-4 py-3` / `px-3 py-1` |
| `pages/schedules/SchedulesPage.tsx` | **无** | `bg-muted/50` | `px-4 py-3` / `px-3 py-2` |
| `components/execution/plan-execute/DeviceTablePanel.tsx` | 有（`:69` `overflow-auto` + `min-w-[800px]`） | `bg-muted/95` | — |
| `pages/results/ResultsPage.tsx` | 有 | `bg-muted/50` | — |
| `components/plan-run/DeviceOverview.tsx` | 有 | `bg-muted/50` | `px-3 py-2` / `px-2 py-2` |
| `components/execution/plan-execute/DispatchCockpit.tsx` | 有 | `bg-muted/60` | — |

**定性修正（复核轮）**：A 轨初版把 `DeviceTablePanel` 也列为"缺滚动容器"，
是**只 grep `overflow-x-auto` 的盲点** —— 它用的是双向 `overflow-auto`，且配了
`min-w-[800px]` 主动触发横滚，是 6 处里实现最完整的一个。

同时，"窄视口真实破版"**未被证实**：实测 `AuditLogPage` / `SchedulesPage` 在
1280 / 1024 / 900 / 800 下表格均可压缩、不溢出（`tsw == tcw`）。

**所以本条的性质是**：表头底色三种深浅、行高各写各的（密度不齐）、
两张表缺滚动容器属**防御性缺口**（列数一旦增加就会溢出），**不是当前可见的破版 bug**。

**最小改法**：给 `AuditLogPage` / `SchedulesPage` 补滚动容器 + 统一 `bg-muted/50`（约 4 行）；
全量迁 `ui/table` 是另一回事，宜单独一轮。

**风险**：窄修低；全量迁移会动到 `DeviceTablePanel` 的虚拟滚动（`@tanstack/react-virtual`），需谨慎。

---

### A5 — 时间格式三套并存，两处逐字重复

**证据**

| 实现 | 用量 |
|---|---|
| `utils/format.ts:formatDateTime` | 26 处（主力） |
| `utils/time.ts:formatLocalDateTime` / `formatLocalTime` | 少量 |
| 内联 `new Date(x).toLocaleString('zh-CN')` | `ui/NotificationBell.tsx:139`、`pages/notifications/NotificationsPage.tsx:590`、`pages/storage/FileServerPage.tsx:170` |

`NotificationBell:139` 与 `NotificationsPage:590` 是**同一份通知记录的时间戳**，两个组件各写了一遍
一模一样的表达式，且都绕过了统一函数。

**影响**：通知铃与通知页当前显示一致纯属巧合；将来改格式会漏掉一处。三套工具本身也构成"同事多做"。

**最小改法**：3 处内联改调用统一函数（3 行）；`utils/time.ts` 与 `utils/format.ts` 的职责合并留后。

**风险**：低。需确认统一函数与 `toLocaleString('zh-CN')` 的输出是否一致，不一致就是可见变更。

---

### A6 — 孤儿组件（knip 判不出）

**证据**（手工计数；`knip --include files` 因这些经桶文件 `layout/index.ts` 导出而漏判）

| 组件 | 消费者 |
|---|---|
| `layout/StatsGrid.tsx` + `StatItem` 类型 | **0** |
| `ui/loading-skeleton.tsx` 的 `ListItemSkeleton` | **0** |
| 同上 `TableRowSkeleton` | **0** |
| 同上 `StatCardSkeleton` | **0** |
| 同上 `CardSkeleton` / `LoadingGrid` | 3 / 3（存活） |

**影响**：无用户可见影响。但维护者会误以为"统计网格已有统一方案"，而实际每页各写各的
（对照 A2 里 `h-32+h-64` 的四处复制 —— `StatCardSkeleton` 本该正是那个位置）。

**最小改法**：要么删，要么在 A2 收敛时真正用起来。**建议搭 A2 的车一并处理，不单独立项。**

---

### A7 — 页面宽度预设分布无规律

**证据**

`list`×8 / `full`×8 / `default`×6 / `fullBleed`×4 / `narrow`×2 / `logs`×1 + 2 处未传（默认 `wide`）。

同为列表页却分三档：`PlanListPage`/`UsersPage`/`AuditLogPage`/`ScriptManagementPage` = `list`；
`DevicesPage`/`HostsPage` = `full`；`SchedulesPage`/`WifiPage` = `default`。

另：`width="full"`（`w-full`）与 `fullBleed`（`w-full` 且去掉左右内边距）概念重叠，
`PlanRunLogsPage` 同时传了 `width="logs" fullBleed` —— 后者会让前者失效（`PageContainer.tsx:32`
的三元里 `fullBleed` 优先，`width` 被忽略）。

**影响**：侧栏切换时内容区宽度反复跳动；6 个预设对 24 个页面属于过度分档。

**最小改法**：先定「页面类型 → 宽度」规则表，再逐页对齐（每页一行）。
`PlanRunLogsPage` 的冗余 prop 可立即清掉。

**风险**：中。规则表是设计决策，**建议等 B 轨截图看过实际观感再定**，否则是拍脑袋。

---

### A8 — 页头有三种形态（B 轨修正）

| 页面 | 裁定 |
|---|---|
| `LoginPage` / `RegisterPage` | **合理豁免** —— 在 AppShell 之外，无侧栏 |
| `NotFoundPage` | **合理豁免** —— 整屏居中 |
| `PlanEditPage` | 自管面板布局合理，但**页头是自绘的**（见下） |
| `ResourcesPage` | **待商榷** —— 无 `PageContainer` 也无 `PageHeader` |
| `PlanRunDetailPage` | 自绘页头，**不是缺页头**（见下） |

**B 轨修正**：A 轨据"矩阵里没有 `PageHeader`"推断这两页"面包屑消失、返回只能靠浏览器后退"，
截图证明这个推断是错的 —— 两页都有返回路径，只是各画各的：

- `PlanRunDetailPage`：返回链 + `PlanRun #210` + `概览 │ 日志` 页签 + 右侧「最后更新 / 刷新」。
  返回链有两份文案共存：`usePlanRunHeaderSlot.tsx:54`「返回执行列表」（AppShell 顶栏槽位）
  与 `PlanRunDetailPage.tsx:187`「返回列表」（页内）。
- `PlanEditPage`：`← 测试计划 › dle-e2e-216-aee-trigger ● 已保存` + 右侧「查看 JSON / 发起测试 / 保存修改」
- 其余 18 页：`PageHeader`（标题 + 副标题 + 右上 action）

**真正的问题**是三种形态并存，且 `PlanEditPage` 未接 `HeaderSlotContext` ——
AppShell 顶栏左侧因此整条留白（截图 12 可见约 80px 空带），页面自己又画了一条头，**上下两条头栏**。

**影响**：详情/编辑类页面各有一套返回与操作区位置，用户要重新找"保存在哪"。

**最小改法**：先定"列表页 / 详情页 / 编辑页"三类的页头规范，再让自绘的两页至少把
AppShell 顶栏槽位填上（`PlanRunDetailPage` 已用 `usePlanRunHeaderSlot`，照抄即可）。

**风险**：中。碰 `HeaderSlotContext`，值得单独一轮。

---

### A9 — 对比度风险（源码可证，结论待目视）

**证据**：`text-muted-foreground/40` ×12、`/50` ×3、`/60` ×1、`/70` ×29、`/80` ×1。

`--muted-foreground` 暗色为 `215 16% 68%`，叠 40% 透明度落在 `222 28% 8%` 的画布上；
亮色为 `215.4 16.3% 46.9%` 叠 40% 落在纯白上。二者都可能低于可读线。

**影响**：`/40` 多用于分隔符与占位图标（如 `PageHeader.tsx:37` 的面包屑箭头），影响有限；
但若命中正文或数值则是真实可读性问题。

**最小改法**：无 —— **本条不给改法，先拍图确认哪些 `/40` 落在了正文上。**

---

## 3. A 轨优先级（输入，权威排序见 §6）

> **本节是 A 轨当时的判断快照，保留是为了让 §5 的修正有对照物，不要据此排期。**
> 其中「A4 窄修 = 窄视口真实破版」与「A8 = 缺面包屑」两条已被后续两轮推翻。

| 序 | 发现 | 影响 | 成本 | 类别 |
|---|---|---|---|---|
| 1 | **A1** 空态三套 + 死分支 | 高（10+ 页 / 含死代码） | 低 | 一致性欠债 |
| 2 | **A3** 状态两套视觉语言 | 高（主诉正例，天天可见） | 中 | 一致性欠债 |
| 3 | **A4 窄修** 3 处表格补滚动容器 | 中（窄视口真实破版） | 极低 | 个别页面缺陷 |
| 4 | **A5** 时间格式三套 | 中（当前不可见，将来必漏） | 低 | 一致性欠债 |
| 5 | **A2** 加载态四套 | 中 | 中 | 一致性欠债 |
| 6 | **A6** 孤儿组件 | 低（搭 A2 车） | 极低 | 内务 |
| 7 | **A7** 宽度预设 | 中 | 中（须先定规则） | 一致性欠债 |
| 8 | **A8** PlanRunDetailPage 面包屑 | 中 | 中（碰 HeaderSlot） | 个别页面缺陷 |
| 9 | **A4 全量** 6 张原生表迁移 | 中 | 高 | 一致性欠债 |
| 10 | **A9** 对比度 | 未知 | — | 待目视 |

### A 轨初判 top 3（B 轨后已调整，见 §6）

**① A3 状态视觉语言统一** —— 本次主诉是"视觉语言是否一致"，而 ResultsPage 同一行里
中英混排 + 同状态两个色相，是全站最刺眼的一处反例。改完还顺带消灭 `RUN_RESULT_STATUS_CHIP`
这条平行词汇线。

**② A1 + A2 + A6 打包：空/加载态收敛** —— 三条同源（状态展示各写各的），一起改才立得住
"五态只有一套画法"的规矩；顺手删掉 HostsPage 那 12 行死代码和 4 个孤儿组件。波及 10+ 页但每处都是替换。

**③ A4 窄修** —— ~~6 行换掉三张表在窄视口的真实破版~~
**已被复核轮推翻**：`DeviceTablePanel` 不缺滚动容器，另两张表实测不溢出。
降级为防御性加固，见 §5 修正表第 1 条与 §6 第 12 位。

---

## 4. B 轨截图清单（已拍摄）

按"最可能藏问题"排序，20 张。建议存 `/tmp/ui-shots/`，不必入库。

| # | URL | 视口 | 主题 | 数据态 |
|---|---|---|---|---|
| 1 | `/results` | 1440×900 | dark | 有数据（验 A3 同排两种胶囊） |
| 2 | `/results` | 1440×900 | light | 同上 |
| 3 | `/audit` | 1280×800 | dark | 有数据（验 A4 无滚动容器是否撑破） |
| 4 | `/schedules` | 1280×800 | dark | 有数据（同上） |
| 5 | `/execution/plan-execute` | 1440×900 | dark | 设备满屏（验 A4 `DeviceTablePanel`） |
| 6 | `/hosts` | 1920×1080 | dark | 20 台（验密度与 A7 `full` 观感） |
| 7 | `/devices` | 1920×1080 | dark | 500+ 台（验密度上限） |
| 8 | `/` Dashboard | 1440×900 | dark | 有数据（验 A9 `/40` 落在何处） |
| 9 | `/` Dashboard | 1440×900 | light | 同上 |
| 10 | `/execution/plan-runs/:id` | 1440×900 | dark | RUNNING（验 A8 无面包屑的实际观感） |
| 11 | `/execution/plan-runs/:id` | 1440×900 | dark | SUCCESS |
| 12 | `/orchestration/plans/:id` | 1440×900 | dark | 有步骤（验编辑器自管布局） |
| 13 | `/users` | 1440×900 | dark | **空**（验 A1 手写空态） |
| 14 | `/hosts` | 1440×900 | dark | **空**（验 A1 EmptyState 对照） |
| 15 | `/audit` | 1440×900 | dark | **空**（验 A1 第三种画法） |
| 16 | `/devices` | 1440×900 | dark | **加载中**（验 A2 骨架） |
| 17 | `/execution/plan-runs` | 1440×900 | dark | **加载中**（验 A2 CardSkeleton 对照） |
| 18 | `/notifications` | 1440×900 | dark | 有渠道 + 有记录（验 A5 时间与 A1 三处空态） |
| 19 | `/storage` | 1440×900 | dark | 有图表（验 Recharts 与 token 的配合） |
| 20 | 任一页 | 1280×800 | dark | 超长文本（长 Plan 名 / 长报错，验截断策略） |

---

## 5. B 轨结论（目视，20 张截图）

**取证方式**：Playwright（临时装在 `/tmp/ui-shot-rig`，未进仓库）驱动无头 chromium，
登录后只做只读 GET 导航；空态用 `route` 拦截把真实响应里的数组清空后回填，加载态用
永不响应的路由定格。截图落 `/tmp/ui-shots/`，含生产数据，未入库。
本节结论基于其中 16 张逐张判读；`11`/`18`/`20` 与已判读的同页近重复，未单独展开。

**A 轨结论的目视校验**：A1（空态三套）、A2（加载态分裂）、A3（同排两种胶囊）、A4（表格底座）、
A5（时间格式）**全部证实**；A8 的推断**被推翻并已改写**（见上）。

---

### B1 — 仪表盘每张图表都套着两层标题，内层还是英文

外层区块标题「设备状态分布」包着一张卡，卡自己的标题是「Device Status」；
「主机资源负载」包「Host Resources」；「任务活动趋势 (24h)」包「任务活动趋势」；
「完成趋势 (7d)」包「完成趋势」。**四张图无一例外**，其中两张中英混排。

饼图图例同样是英文 `Error / Idle / Offline / Testing`，而**同一组枚举在设备页是
「错误 / 空闲 / 离线 / 测试中」** —— 同一个状态集，两页两种语言。

**影响**：首页是新用户第一眼，双层边框 + 双份标题让每张图白占约 40px，中英混排削弱专业感。
**最小改法**：外层区块只留标题、去掉内层卡的标题与边框；图例文案接 `status-badge` 的中文表。

---

### B2 — 审计页筛选栏塌成三行，两个日期输入各占满整宽

`全部资源`/`全部操作` 两个下拉在第一行按内容宽度排列，其后**两个 `datetime-local`
输入各自独占一整行**（约 960px 宽）。二者**没有标签**，只有浏览器默认的
`yyyy/mm/dd --:--` 占位，用户分不清哪个是开始、哪个是结束；也没有查询/重置按钮。

1280 与 1440 两个视口均如此。这是本轮最严重的单页布局缺陷。

**最小改法**：给两个日期框加标签、限宽（`w-52` 量级）、与两个下拉并入同一行。

---

### B3 — Toast 压住页头主操作

定时任务页弹出「加载定时任务失败」，浮层正好盖住右上角的「新建」按钮与通知铃
（截图 04）。而页面下方表格其实已经渲染出数据 —— 报错来自另一个查询。

**最小改法**：toast 位置下移避开页头 action 区，或该页错误改为页内 inline 提示。

---

### B4 — 原生 `<select>` 与 Radix `Select` 混用，前者是后者的 5 倍

| | 文件数 |
|---|---|
| 原生 `<select>` | **11**（含 `DevicesPage` 的 3 个筛选、`AuditLogPage` 的 2 个、`SchedulesPage`、`NotificationsPage`、`UserModal`、`PlanStepInspector`、`DeviceFilterBar` 等） |
| `components/ui/select`（Radix） | **2**（`plan-execute/DeviceFilterBar`、`pagination-bar`） |

截图里原生下拉带系统三角箭头、高度与相邻 `Input` 不齐（设备页、审计页、PlanRun 详情的
HOST 选择器都可见）。

**最小改法**：这条**不建议现在做** —— 11 处全换是大工程，且原生 select 在长列表下
性能与可访问性反而更好。真正该做的是**定规矩**：要么统一 Radix，要么承认原生并把
`ui/select` 那 2 处换回去，别两条线并存。

---

### B5 — 结果页「类型」列与「任务」列内容逐行相同

截图 01/02：`任务 = smoke-plan-001`，`类型 = smoke-plan-001`，十行全同。
源码（`ResultsPage.tsx:129,132`）取的是 `run.task_name` 与 `run.task_type` 两个不同字段，
**是后端两个字段返回了同值**，不是前端取错。

**最小改法**：先确认后端 `task_type` 是否还有独立语义；若没有，这一列该删 —— 它现在
白占约 180px，且让人以为「类型」就是 Plan 名。

---

### B6 — 主机页「主机」列把同一个 IP 显示两遍

截图 06：标题行 `172.21.15.20`，下方副标题又是 `172.21.15.20`（小一号灰字）。
34 行全部如此 —— 该处本是「名称 + IP」的双行位，而生产环境的主机名就等于 IP。

**最小改法**：两值相同时只渲染一行。

---

### B7 — 密度：1920×1080 只能看到 12 行

| 页面 | 数据量 | 行高 | 满屏可见 |
|---|---|---|---|
| 主机管理 | 34 | ~63px | 12 |
| 设备管理 | 515 | ~63px | 12 |
| 操作日志 | 245,660 | ~46px | 10（1280×800） |

设备页的「网络」「标签」两列**已加载行全部是 `—`**，白占约 15% 宽度。
审计页分页只有「上一页 / 第 1 页 / 下一页」，无跳页、无页大小选择；
`AuditLogPage.tsx:30` 的 `pageSize = 50`，245,668 条约 **4,914 页**。

这不是"留白太多"的审美问题：本项目的既定目标是 **60+ host / 1000 device**，
按当前行高，主机页届时要翻 5 屏。

**最小改法**：给这三张表提供紧凑行高（`py-1.5`）；设备页空列做成可隐藏；
审计页接 `PaginationBar`（已存在，只有 1 处消费者）。

---

### B8 — 空态实际有四种，不是三种

A1 找到三种，截图 10 里还有第四种：PlanRun 详情内嵌面板用**无图标、无边框的居中灰字**
（「当前范围内未发现新增 AEE / Vendor AEE 异常」「当前范围内暂无异常包名数据」）。

四种的图标尺寸分别是：`w-8`（用户页，带圆形底座）、`w-16`（主机页，裸图标）、
`w-12`（审计页，裸图标）、无（PlanRun 详情）。

---

### B9 — 主机页 KPI 卡数值基线不齐

第一张卡比其余三张多一行副文案（`Agent 已对齐 0/34`），导致其大数字比另外三张高约 10px
（截图 06）。设备页五张卡因结构一致而对齐良好，可作参照。

---

### B10 — 错误态把内部异常字符串原样透给用户

用户页在查询失败时渲染 `加载用户失败：["users"] data is undefined` —— TanStack Query 的
内部报错直接进了 UI，且用的是**自绘红条**（非 `ErrorState`），**没有重试按钮**，
页面其余内容全部消失。

（该截图是拦截造出来的失败，但错误分支的渲染方式与文案拼装是真的。）

---

### B11 — 提示语重复出现两次

- Plan 执行页：右侧面板头写「选中后展示」，面板体又写「从左侧选择一个 Plan」
- Plan 编辑页：右侧「未选择步骤 / 在中央画布点击任意步骤以查看其属性。」卡片，
  底部脚注再写一遍「点击中央画布的步骤可在此查看 / 编辑属性。」

---

### B12 — 危险操作与常规图标按钮并排，无视觉区分

定时任务页操作列是 `▶ ⏻ ✎ 🗑` 四个等大裸图标；Plan 编辑器每个步骤行是 `↑ ↓ ⧉ 🗑`。
删除与"上移"「复制」同色同大小、间距相同，误点成本不对称。

---

### B13 — 同一张卡里两种分段控件

文件服务器页「资源趋势」卡头：左边 `存储 / CPU / 内存` 是纯彩色文字切换，
右边 `6H / 24H / 7D` 是带边框的分段组（`SEGMENTED` token）。同一行两种切换语言。

---

### B14 — 「无数据」沿用告警色，accent 不随取值退让

`AnomalyDashboard.tsx:594-601` 给两张 SummaryCard 写死了 accent：

```
label="Top 包名"  accent={KPI_TONE.warning.value}      → text-warning（琥珀）
label="Top 类型"  accent={KPI_TONE.destructive.value}  → text-destructive（红）
```

accent 是**卡片的固定属性**，与 `value` 无关。所以当值是「无」时，
它照样用琥珀 / 红渲染 —— 而这两色在本产品其余位置都表示"有问题"。

**修正（复核轮）**：初版写成"两个「无」都是红色"，不准确 ——
只有 Top 类型是红，Top 包名是琥珀。核心主张（告警色用于无数据）不变。

**最小改法**：值为空时降级到 `KPI_TONE.default`。

---

### 被推翻 / 修正的判断（记录在案，防止再犯）

**B 轨推翻 A 轨的**

- **"浅色主题下侧栏仍是深色"** —— 目视误判。实测 `aside` 计算样式为
  `rgb(255,255,255)`，截图取样点同样是 `255,255,255`。侧栏用 `SURFACE.elevated`(`bg-card`)，
  双主题正常跟随。
- **"PlanRun 详情没有返回路径"** —— A 轨据组件矩阵推断，截图证否，见 A8。

**复核轮（第三方独立以 DOM 几何/计算样式实测）修正的 5 处**

| # | 原表述 | 修正 | 根因 |
|---|---|---|---|
| 1 | `DeviceTablePanel` 缺滚动容器；三张表窄视口"真实破版" | 它有 `overflow-auto` + `min-w-[800px]`，是 6 处里最完整的；另两张表 800–1280 实测不溢出 | 只 grep 了 `overflow-x-auto`，漏掉双向 `overflow-auto` |
| 2 | 审计日志 24,566 页 | `pageSize=50`，约 **4,914 页** | 按 10 条/页臆断，未读 `AuditLogPage.tsx:30` |
| 3 | 两个「无」都是报警红 | Top 包名是琥珀(`warning`)，只有 Top 类型是红 | 目视归色，未查 `KPI_TONE` 实参 |
| 4 | 硬编码调色板 0 处 | 类名 0 处属实，但有 12 处十六进制字面量（`XTerminal.tsx:131-142`） | 只 grep 了 Tailwind 类名 |
| 5 | 返回链文案「返回执行列表」 | 两份共存：`usePlanRunHeaderSlot.tsx:54` 与 `PlanRunDetailPage.tsx:187`「返回列表」 | 只认了截图里可见的那份 |

**这五处的共同教训**：grep 模式写窄（1、4）、不查实参就归色（3）、
不读常量就算数（2）—— 三类都是"证据看着够了就下结论"。
本文档所有计数类断言，改动前请用 `/tmp/verify-ui-review.sh` 复跑一次。

---

## 6. 合并优先级与推荐（权威）

23 条发现（A1–A9、B1–B14）合并排序。**A9 降级**：截图里 `/40` 只出现在面包屑箭头与
分隔符上，未见落在正文；未做对比度数值测量，故不列入待办，仅在改 token 时复查。

| 序 | 发现 | 影响 | 成本 | 类别 |
|---|---|---|---|---|
| 1 | **B2** 审计页筛选栏塌成三行、日期框满宽无标签（**结构性，非视口相关**） | 高（管理员每次都撞） | 低 | 单页缺陷 |
| 2 | **B1** 仪表盘四张图双层标题 + 中英混排图例 | 高（首页第一眼） | 低 | 一致性 |
| 3 | **A3 + B1 图例** 状态视觉语言与文案统一 | 高（天天可见） | 中 | 一致性 |
| 4 | **A1 + B8** 空态四套 + HostsPage 死分支 | 高（10+ 页） | 低 | 一致性 |
| 5 | **B5 + B6** 结果页重复列、主机页重复 IP | 中（白占 180px / 每行） | 极低 | 单页缺陷 |
| 6 | **B3** Toast 压住页头主操作（实测重叠 50px，通知铃全遮） | 中（挡住 CTA） | 极低 | 单页缺陷 |
| 7 | **B7** 密度与分页（60+ host 目标下会恶化） | 中→高 | 中 | 单页缺陷 |
| 8 | **A2 + A6** 加载态四套 + 孤儿组件 | 中 | 中 | 一致性 |
| 9 | **B10** 错误态透出内部异常、无重试 | 中 | 低 | 一致性 |
| 10 | **A5** 时间格式三套（截图证实两种可见格式并存） | 中 | 低 | 一致性 |
| 11 | **B12** 危险图标按钮无区分 | 中（误点成本） | 低 | 一致性 |
| 12 | **A4 窄修** 2 处表格补滚动容器（**防御性，非现存破版**） | 低 | 极低 | 防御性加固 |
| 13 | **B11 + B14 + B13 + B9** 重复提示 / 告警色表示无数据 / 双分段控件 / KPI 基线 | 低 | 极低 | 打磨 |
| 14 | **A7** 宽度预设 6 档 | 中 | 中（须先定规则） | 一致性 |
| 15 | **A8** 页头三种形态 | 中 | 中（碰 HeaderSlot） | 一致性 |
| 16 | **B4** 原生 select vs Radix（11 : 2） | 低 | 高 | 一致性 |
| 17 | **A4 全量** 6 张原生表迁移 | 中 | 高 | 一致性 |

### 推荐 top 3（经复核轮调整）

**① 单页硬伤打包：B2 + B3 + B5 + B6** —— 全是"看一眼就知道不对"的具体缺陷，
加起来约 30 行：审计页筛选栏排成一行并加标签、toast 让开页头、结果页删重复列、
主机页同值不重复渲染。

> **B2 是本条的核心，且性质比初判更重**：它不是"1280 下才塌"，而是
> `Input` 默认 `w-full` + `flex-wrap` 导致的**结构性塌行——任何视口都如此**。
>
> **A4 窄修已移出本条**：复核证明 `DeviceTablePanel` 不缺滚动容器，
> 且另两张表在 800–1280 全区间实测不溢出。它降级为防御性加固（第 12 位），
> 可搭任意一轮的车，不占 top 3 名额。

**② B1 + A3：仪表盘图表壳与状态词汇统一** —— 去掉四张图的内层标题与边框，
图例接中文表；同时把 `RUN_RESULT_STATUS_CHIP` 并入 `StatusBadge`。
两件事共用一条"状态/图表文案只有一套中文"的规矩，一起改才不会改一半。

**③ A1 + B8 + A2 + A6：空/加载/错误态收敛** —— 空态四套、加载态四套、
错误态两套，全部收敛到 `EmptyState`/`ErrorState` + 两套骨架；
顺手删 `HostsPage` 那 12 行死代码和 4 个孤儿组件。波及面最大但每处都是替换。

### 其余为何先不做

- **B7 密度** 要先定"紧凑/舒适"是否给切换开关，属产品决策；但 60+ host 目标一旦推进，
  它会从第 7 位升到第 1 位。
- **A7 / A8** 都是规范先行的活（宽度分档表、页头三形态规范），不该边改边定。
- **B4** 11 处原生 select 全换收益低风险高；**只需先定规矩，不必现在动手**。
- **A4 全量** 涉及 `DeviceTablePanel` 的虚拟滚动。
- **A9** 见本节抬头。

---

## 7. 何时重议


- **B7 密度落地前**：需先定"紧凑/舒适"是否做成用户可切换的开关。60+ host / 1000 device
  的目标一旦推进，B7 会从第 7 位升到第 1 位。
- **A7 / A8 动手前**：先出"页面类型 → 宽度"与"页头三形态"的规范表，不要边改边定。
- **A3 落地后**：`RUN_RESULT_STATUS_CHIP` 与 `DEDUP_STATUS_CHIP` 是否也该并入 `StatusBadge` 注册表。
- **`ui/table` 全量迁移启动时**：A4 的窄修（补滚动容器）会被覆盖，不必保留。
- **改 `tokens.ts` 的透明度阶梯时**：复查 A9 的 `text-muted-foreground/40`（12 处），
  本轮只确认它没落在正文上，未做对比度数值测量。
- **B5 删列前**：先确认后端 `results` 的 `task_type` 是否还有独立语义
  （复核轮已查到 `results.py:232-233` 两字段同取 `plan_name_norm`，但"是否还有别的消费者"未查）。
- **终端是否跟随主题**：`XTerminal` 的 12 处十六进制把终端锁死为深色（§1）。
  xterm.js 不接受 CSS 变量，要跟随就得在主题切换时重建 theme 对象 —— 属功能，非本轮范围。
- **任何一张表新增列时**：A4 的防御性缺口会立即兑现（`AuditLogPage` / `SchedulesPage`
  当前只是因为列少才没溢出）。
