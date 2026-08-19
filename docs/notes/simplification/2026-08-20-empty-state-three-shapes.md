# 空态五种画法收敛为三种形态 + HostsPage 死分支删除

- **状态**：已实施
- **类别**：simplification
- **日期**：2026-08-20
- **关联**：`docs/reviews/FRONTEND_UI_REVIEW_2026-08-19.md` §6 第 4 位（A1 + B8）

---

## 决定了什么

### 1. 不是「收敛成一个组件」，是「定义三种形态」

审查文档原话是"空态四套画法收敛"。摸完落点后改了方向 —— 全部塞进一个
`EmptyState` 会更糟：它带 `py-16` 留白和 64px 图标，放进表格 `<tbody>` 或
巴掌大的图表面板里都会失调。

真正的问题不是"有几个组件"，而是**同一形态被复制粘贴了几遍**。所以按用途拆三个：

| 形态 | 用在哪 | 组件 |
|------|--------|------|
| 页面/整卡无数据 | 列表页拉到空结果 | `EmptyState`（`ui/empty-state.tsx`） |
| 面板内小区域无数据 | 卡片里的图表/分区 | `InlineEmpty`（同上，新增） |
| 表格已有表头、只是没有行 | `<tbody>` 内 | `TableEmptyRow`（`ui/table.tsx`，新增） |

分工写进两个文件的抬头注释，避免下一个人再各写一份。

### 2. 图标底座统一补上

四种画法里图标处理是 `w-8`+圆形底座（用户页）/ 裸 `w-16`（主机页）/ 裸 `w-12`
（审计页）/ 无（面板内）。统一取**圆形底座 + 内嵌 32px 图标**：裸描边图标在
`--background: 222 28% 8%` 的暗色画布上过轻，压不住整卡的留白。

`EmptyState` 用 `[&>svg]:w-8 [&>svg]:h-8` 统一子图标尺寸，调用方只传 `<Shield />`
不用自己写尺寸类 —— 尺寸不一致的根因就是每个调用方各写一遍。

### 3. `InlineEmpty` 的 `chart` 变体不是多余的开关

`AnomalyDashboard` 的四处面板空态用的是 `flex h-32 items-center justify-center`，
`h-32` 是**有意的**：图表区必须占住高度，否则数据到达的瞬间整块面板会往下弹。
直接换成 `InlineEmpty` 默认的 `py-10` 是回退，所以给了 `chart` 变体把这个意图
显式化 —— 原来它藏在四份复制的 class 串里。

### 4. `HostsPage` 的 12 行死代码

`:567` 已 `if (tableData.length === 0) return <EmptyState/>`，`:620` 又写了一遍
`tableData.length > 0 ? <表格> : <Card>暂无主机</Card>`。`tableData` 是 `:341` 的
`useMemo`，两处之间不重新赋值 —— else 分支永远渲染不到。删掉那 12 行并去掉外层三元。

副作用：同页两种矛盾文案（`:572`「还没有主机」vs `:646`「暂无主机」）随之消失。

### 5. 文案规则是既有的，不是要新立的

原以为「暂无X」/「还没有X」是漂移，逐处核对后发现是一条 **12:1 已成立的规则**：

- **带 CTA**（引导创建第一个）→ 「还没有X」：主机、设备、Plan
- **不带 CTA**（陈述当前没有）→ 「暂无X」：脚本、通知渠道/记录、执行记录、
  JIRA 草稿、提单记录、测试运行、定时任务、筛选后设备 —— 共 9 处

唯一违例是 `UsersPage` 手写空态：带 CTA 却用「暂无用户」。改为「还没有用户」后
规则 14:0 成立。**没有拉平文案** —— `PlanExecutePage`「暂无设备」与
`DevicesPage`「还没有设备」不是矛盾，是同一规则在"筛选无结果"与"首次为空"上的
正确区分（审查文档 §A1 把这条列为矛盾，是错的）。

### 6. 顺手消掉一个命名碰撞

`PlanStepInspector.tsx:126` 有个同名的局部 `function EmptyState()`，与 `ui/empty-state`
的导出撞名。它的形态（`PIPELINE_EDITOR.emptyState`）是编辑器专用的，不并入通用
组件，只改名为 `StepEmptyState`。这是审查时漏记的第五种画法。

## 放弃的备选

- **把四种画法全并进 `EmptyState`** —— 见 §1，会把整页组件塞进表格和小面板。
- **把 `AnomalyDashboard` 的 `h-32` 一并改成 `py-10`** —— 布局会在数据到达时跳动。
- **拉平「暂无」/「还没有」** —— 会抹掉一条已经成立且有意义的区分。
- **把 `PlanStepInspector` 的局部空态并入 `InlineEmpty`** —— 它是双行（标题+说明）
  且吃编辑器专属 token，并进来要加第三个变体，收益不抵复杂度。
- **给 `TableEmptyRow` 加图标** —— 一行灰字就是它存在的理由。

## 如何验证

```bash
cd frontend
npx vitest run                     # 79 files / 597 tests passed（+9）
npx tsc --noEmit                   # 0
npx eslint src --max-warnings 0    # 0
npm run build                      # 通过
```

新增 9 个用例：`empty-state.test.tsx` 7 个（三形态各自的判据 + 默认图标回落 +
`chart` 固定高度 + `bordered` 虚线）、`table.test.tsx` 2 个（`colSpan` 与兜底文案）。
**既有 588 个用例一行未改仍绿** —— 说明没有测试依赖被删掉的手写标记。

DOM 实测（Playwright + 登录控制面，只读 GET + 空响应拦截）：
用户页 / 主机页 / 审计页三处空态的图标底座实测 **64px×64px、圆角全等**；
主机页正常态 34 行表格照常渲染且无「暂无主机」残留。

**未能实测的一项**：`AnomalyDashboard` 四处面板空态在浏览器里没能构造出来 ——
抽样的 6 条 PlanRun 异常数据都非空，拦 `crash-details` 也没触发（空态由另一路
查询的字段决定）。该分支由 `__acceptance72.test.tsx:50` 的既有断言
（`/当前范围内未发现新增/`）+ `InlineEmpty chart` 单测覆盖，两者均绿。

## 何时重议

- **新增列表页时**：三形态的选择规则在 `ui/empty-state.tsx` 抬头，照它选，别再手写。
- **`EmptyState` 要加插图/动画时**：`[&>svg]:w-8` 这条子选择器会限制传入内容，
  届时改成显式 `iconSize` prop。
- **审查 §6 第 8 位（A2 加载态四套）落地时**：`SKELETON_BLOCK` 那四处
  `h-32 + h-64` 复制与本轮同源，`StatCardSkeleton` 等 4 个孤儿组件也该在那轮处理。
- **审查文档 §A1 的"文案不统一"一条已被本轮证否**，若后续复用该文档排期，
  以本 note §5 为准。
