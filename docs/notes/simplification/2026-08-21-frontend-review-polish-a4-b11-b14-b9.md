# 前端 UI 审查打磨包：A4 窄修 + B11 + B14 + B9（B13 判不成立）

Status: implemented
Class: simplification

依据：`docs/reviews/FRONTEND_UI_REVIEW_2026-08-19.md` §6 第 12 位（A4 窄修）
+ 第 13 位（B11 + B14 + B13 + B9 打包）。按合入路径注意力预算合并为
单 PR 双 commit：commit 1 防御性加固（不可见）、commit 2 视觉打磨
（可见），两种性质可独立 revert。

## B13 先决核实结论：不成立，零代码

文件服务器页「资源趋势」卡头左侧的 存储/CPU/内存 是**纯静态彩色
`<span>` 图例**（无 onClick/无状态/无 button 语义），CapacityChart 三条
线无条件全画；右侧 6H/24H/7D 是卡上唯一控件（单选时间窗）。审查
§B13「同一张卡里两种分段控件」的前提不存在——与 B12「无二次确认」
同类：B 轨目视把图例当成了切换控件。

## Decision（commit 1 防御性加固）

- `AuditLogPage:138` / `SchedulesPage:264` 表格外层 `overflow-hidden`
  → `overflow-x-auto`（列数增加时的防溢出，对齐 ui/table 基准）。
- `DeviceTablePanel` 的 `bg-muted/95` 旁加注释：**/95 是 sticky 表头的
  必要条件**（行从表头下滚过需近乎不透明），勿当底色漂移改 /50——
  B 轨曾把它列进「三种深浅」，就是没查 sticky。

## Decision（commit 2 视觉打磨）

- `DispatchCockpit:159` `bg-muted/60` → `/50`（非 sticky，回归基准；
  三种深浅只剩这一处真漂移，1 字符）。
- **B11-a** `PlanSelectPhase`：删头部右侧「选中后展示」占位——正文
  「从左侧选择一个 Plan」已是引导载体，双份留一。
- **B11-b** `PlanStepInspector`：footer 改为 `{step && …}`——空态引导
  只在 StepEmptyState 卡里说一次；头部 mono「未选择步骤」是状态行
  非引导，保留。
- **B14** `AnomalyDashboard` 两张 Top 卡的 accent 按值降级：
  字段有值 → warning/destructive，空（显示「无」）→ `KPI_TONE.default`
  ——告警色不再表示无数据。
- **B9** 主机页首卡「Agent 已对齐 x/y」从条件第三行**并入标签行**
  （`主机总数 · Agent 已对齐 0/34`）：四卡文本块恒等高，基线确定性
  对齐；信息不丢（它是机队级汇总，与 Agent 列的逐行徽章不同源，
  不可删）。弃 min-h 方案：魔法数脆，单行常在是结构保证。

## Verification

- 门禁：tsc / eslint（8 改动文件 `--max-warnings 0`）/ 全量 vitest 633 /
  build 全绿（`PlanStepInspector.test` 的「未选择步骤」断言不受影响——
  保留的是头部状态行）。
- DOM 实测 7/7（`/tmp/ui-shot-rig/verify-polish.js`）：
  - B13：趋势卡 `role="group"` 唯一、图例三 span 均不在 button 内；
  - B9：四卡大数字 y 坐标全等（178px×4），首卡标签含内联对齐率；
  - A4：两页表容器 computed `overflow-x=auto`（schedules 空列表场景
    用拦截注入假数据才渲染出列表表）；
  - B14：拦截 watcher-summary 置空 top 字段 → 两张「无」卡值色为
    前景/ muted，非告警色。

## 陷阱存档：cn() 参数顺序即优先级

`PANEL.root` 自带 `overflow-hidden`。`cn(PANEL.root, 'overflow-x-auto')`
生效（后者胜）；`cn('overflow-x-auto', PANEL.root)` 被 token 的
overflow-hidden 压掉（twMerge 后者胜）——SchedulesPage 首版就栽在
照抄了原代码的参数序。DOM 复验抓到（computed 仍 hidden），磁盘与
模块供应都是新代码、唯独渲染旧值，定位绕了三圈。教训：**覆写 token
属性的类必须放 cn() 最后一位**，且此类改动只有 computed-style 断言
能拦住。

## Revisit

- 审查 §5 修正表可回写：B13 推翻（图例非控件）、A4「三种深浅」中
  /95 为 sticky 必要条件——是否回写审查文档由维护者定（本文已留档）。
- B7 按钩子等 60+ host；A7/A8/B4/A4 全量余 4 项。
