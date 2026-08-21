# 前端页面外壳规范（宽度 · 页头 · 归属）

- **状态**：Living（规范已定，迁移分批进行）
- **日期**：2026-08-21
- **来源**：`docs/reviews/FRONTEND_UI_REVIEW_2026-08-19.md` §6 第 14/15 位（A7 宽度预设、A8 页头形态）
- **适用**：`frontend/src/pages/**` 下所有路由页；模态/对话框不适用

> **这份规范的收益在下一批页面，不在现有的 24 个。**
> ADR-0029 P2 新增的 `ProjectsPage` / `ProjectDetailPage` 都选了 `list`，
> 而当时没有任何规则告诉它们该选什么 —— 选对是运气。前端页面仍在增长
> （ADR-0030 P2 还有用例页），规范的作用是挡住下一批继续猜。

---

## 1. 页面分类（先判类型，再取宽度）

判定按顺序问三个问题，命中即停：

```
① 页面主体是自管布局的面板/控制台（自己处理滚动与分栏）？   → bleed
② 页面主体是宽数据表（≥8 列，或需要横向滚动）？             → wide
③ 页面主体是单列表单？                                       → form
否则                                                          → content
```

| 类型 | 宽度 | 内边距 | 典型内容 |
|------|------|--------|----------|
| `form` | `max-w-3xl`（768px） | 有 | 单列表单，字段不该拉长到读不到标签 |
| `content` | `max-w-6xl`（1152px） | 有 | 卡片列表、窄表格、图表栅格 —— **默认档** |
| `wide` | `w-full`（不设上限） | 有 | 宽数据表；列数决定宽度，不该被人为截断 |
| `bleed` | `w-full` | **无** | 编辑器、日志控制台；面板自己贴边并管理滚动 |

**`content` 是默认档。** 拿不准就用它 —— 分档的意义是标出三种少数情况，不是让每页做选择题。

### 为什么是 4 档不是 6 档

旧的 `list`(1024) / `default`(1152) / `wide`(1280) 每档只差 128px，
没有任何页面是「在 1152 成立、在 1024 崩掉」的。三档承载的不是设计意图，
而是十五个页面各自随手挑的结果。合并为单一 `content` 后，
**宽度选择从"挑一个数"变成"判一次类型"** —— 后者才有确定答案。

### `bleed` 是宽度值，不是布尔开关

旧 API 里 `fullBleed` 是独立布尔，且在 `PageContainer` 内**优先于 `width`**。
`PlanRunLogsPage` 因此写了 `width="logs" fullBleed` —— `width` 被静默忽略，
`logs`(1480px) 这一档从未生效。把 bleed 收进宽度枚举后，这类
「两个参数互相覆盖、错的那个没人发现」的 bug 在类型层面就不成立。

---

## 2. 页头归属

**AppShell 顶栏是全站唯一的页头。** 页面有两种写入方式，**不得在页面内再画一条横贯的头栏**：

| 需求 | 用法 |
|------|------|
| 标题 + 副标题 + 右上主操作 | `<PageHeader title subtitle action />` |
| 需要面包屑 / 页签 / 自定义结构 | `useHeaderSlot()`（参照 `usePlanRunHeaderSlot`） |

`HeaderSlotContext` 接受任意 `ReactNode`，`usePlanRunHeaderSlot` 已经在里面
放了返回链 + 页签 + 刷新按钮 —— **能力不是限制，自绘头栏没有技术上的理由。**

### 唯一的例外：错误分支的逃生口

查询失败时 slot 可能尚未填充，此时页面内可渲染一个返回按钮
（现例：`PlanRunDetailPage.tsx:187`）。**它不是页头**，不构成本规则的违例。

### 为什么必须与宽度同轮定

`bleed` 页面没有左右内边距，`PageHeader` 放进去会贴着视口边缘 ——
这很可能正是 `PlanEditPage` 自绘头栏的成因。**宽度决定页头方案可不可行**，
分两轮定必然打架。规范落地时，`bleed` 页面一律走 `useHeaderSlot`。

---

## 3. 逐页归属

### form（2）

| 页面 | 现状 | 变化 |
|---|---|---|
| `settings/SettingsPage` | `narrow` | 无（改名） |
| `account/ChangePasswordPage` | `narrow` | 无（改名） |

### content（15）

| 页面 | 现状 | 变化 |
|---|---|---|
| `audit/AuditLogPage` | `list` | +128px |
| `execution/PlanRunListPage` | `list` | +128px |
| `issues/IssueTrackerPage` | `list` | +128px |
| `orchestration/PlanListPage` | `list` | +128px |
| `projects/ProjectsPage` | `list` | +128px |
| `projects/ProjectDetailPage` | `list` | +128px |
| `scripts/ScriptManagementPage` | `list` | +128px |
| `users/UsersPage` | `list` | +128px |
| `notifications/NotificationsPage` | `default` | 无 |
| `results/ResultsPage` | `default` | 无 |
| `runs/RunReportPage` | `default` | 无 |
| `schedules/SchedulesPage` | `default` | 无 |
| `wifi/WifiPage` | `default` | 无 |
| `Dashboard` | `wide`(1280) | −128px |
| `storage/FileServerPage` | `fullBleed` | **现落 `bleed`**，见下 |

### wide（3）

| 页面 | 现状 | 变化 |
|---|---|---|
| `devices/DevicesPage` | `full` | 无（改名） |
| `hosts/HostsPage` | `full` | 无（改名） |
| `execution/PlanRunDetailPage` | 无 `PageContainer` | **未纳管**，见下 |

### bleed（4）

| 页面 | 现状 | 变化 |
|---|---|---|
| `orchestration/PlanEditPage` | 无 `PageContainer` | **未纳管** + 页头待移进 slot |
| `execution/PlanRunLogsPage` | `logs`+`fullBleed` | 无（`logs` 本就未生效，实测一直是贴边全宽） |
| `storage/FileServerPage` | `fullBleed` | 归属待定 |
| `execution/PlanExecutePage` | `fullBleed` | 归属待定 |

### 归属待定的两页

`FileServerPage` 与 `PlanExecutePage` 原本用 `fullBleed`，**但都自带 `className` 内边距**
（`p-4 lg:p-6` / `p-4`）—— 实测容器 padding 分别是 24px / 16px，而非 bleed 该有的 0。

这说明它们要的不是"贴边"，是"内边距比 `lg:p-8` 小"。**这不是宽度档位能表达的诉求**，
所以本轮先原样落 `bleed` 保持渲染不变，归属留待目视决策：
- 若确认它们该有内容上限 → 改 `content`，并去掉自带 padding
- 若确认贴边是对的 → 保留 `bleed`，但自带 padding 应下沉为规范的一部分而非页面各写

### 豁免（4）

| 页面 | 理由 |
|---|---|
| `auth/LoginPage` / `auth/RegisterPage` | 在 AppShell 之外，无侧栏与顶栏 |
| `NotFoundPage` | 整屏居中 |
| `resources/ResourcesPage` | **不是页面** —— 全文只有一个 `<Navigate>` 重定向 |

---

## 4. 页头规则的符合度

| 状态 | 页面 |
|---|---|
| 用 `PageHeader` ✓ | 20 页 |
| 用 `useHeaderSlot` ✓ | `PlanRunDetailPage`、`PlanRunLogsPage` |
| **违例** | `PlanEditPage` —— 自绘头栏，导致 AppShell 顶栏左侧整条留白、上下两条头栏 |

**A8 的实际工作量是一个页面。** 审查文档记的「三种页头形态并存」高估了 ——
另外两种里，`useHeaderSlot` 是规范内的合法用法，不是需要消灭的第三种。

---

## 5. 迁移状态与验证

### 已落地（枚举收敛 + 21 页迁移）

| 变化性质 | 页数 |
|---|---|
| 改名不改值（`narrow`→`form`、`full`→`wide`、`default`→`content`） | 7 |
| `list`→`content`（+128px） | 8 |
| `Dashboard` 1280→1152（−128px） | 1 |
| `fullBleed`→`width="bleed"`（渲染不变） | 3 |

**实测（1920×1080，computed style）**：`form`=768px / `content`=1152px /
`wide`·`bleed`=无上限；只有 `bleed` 不施加容器的 `lg:p-8`。8/8 通过。

`Dashboard` 收窄 128px 后目视无回退 —— 图表标签未进一步挤压
（该页图表标签本就紧张，见审查 B1）。

> **验证必须量 computed style，不能只断言类名。**
> `#351` 的 `cn()` 参数顺序覆写陷阱已经证明：磁盘上是新代码、渲染的却是旧值。
> 宽度由 `cn(LAYOUT.pageWidth[w], !bleed && LAYOUT.pagePadding)` 拼出，属同类风险。

### 未落地

| 项 | 原因 |
|---|---|
| `PlanEditPage` 纳管 + 页头移进 slot | 唯一的页头违例，独立可验，单独一轮 |
| `PlanRunDetailPage` 纳管 | 该页有自绘左栏布局，纳入容器需目视确认不被内边距挤压 |
| `FileServerPage` / `PlanExecutePage` 归属 | 见 §3 末 —— 诉求是"内边距更小"，非宽度档位问题 |

---

## 6. 何时重议

- **新增页面时**：走 §1 的判定树。判不出就是 `content`，不要新增第五档。
- **有人想加第五档时**：先说明它承载什么**类型**差异，而不是什么像素差异 ——
  旧的六档就是靠像素差异繁殖出来的。
- **`wide` 出现在超宽屏（≥2560px）不适用的证据时**：考虑给它加上限。
  当前定为无上限，是因为 `devices`（515 行 × 12 列）与 `hosts` 的列数应当决定宽度。
- **`bleed` 页面需要标准页头时**：不要退回自绘，改为扩展 `useHeaderSlot` 的可复用外壳。
