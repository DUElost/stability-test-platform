# A4 全量收尾：最后三张原生表迁 ui/table（issue #372）

Status: implemented
Class: simplification

依据：issue #372（UI 审查 §6 第 17 位）。**范围演变的实况记录**：审查
时的「六张原生表」，其中 audit / schedules / results 三张已被并行线
（#366 控件统一轮起）陆续迁完 `ui/table`；本 PR 只剩最后三张——且
审查标注的最大风险「DeviceTablePanel 虚拟滚动」已不存在（该组件现
为分页渲染，无 useVirtualizer），迁移降级为纯机械替换。

## Decision

- **DispatchCockpit**（选机第三步容量表）：外层手写
  `overflow-x-auto rounded-lg border` → `rounded-lg border` 卡壳 +
  `Table`（自带 overflow-auto）；`th/td` → TableHead/TableCell
  （显式 `h-auto px-3 py-2/3` 保原密度，覆盖默认 h-10）；
  `divide-y` 删除（TableRow 自带 border-b + hover）。
- **DeviceTablePanel**（选机表格视图）：同上模式；**sticky 表头与
  bg-muted/95 迁至 TableRow**（`sticky top-0 z-10 bg-muted/95
  hover:bg-muted/95`，#351 的「/95 是 sticky 必要条件」注释随迁）；
  排序表头（th 内 button + 方向图标）原样保留。
- **DeviceOverview**（PlanRun 详情表格视图，11 列）：`text-[12px]`
  密度经 Table className 保留；外层无框容器删除（内嵌面板语义）。

## 迁移不变量（DOM 实测逐条验证）

三处表格均在 `div.table-scrollbar`（ui/table 自带容器）内；
DeviceTablePanel 表头 `position: sticky`、底色 alpha 0.95 保留；
三处列头文本与迁移前一致；密度（px/py）逐格显式保留——**本轮是
底座统一，不是密度变更**（密度决策归 #392/B7）。

## Alternatives

- **DeviceTablePanel 排除在外**（否决）：其虚拟滚动前提已不存在，
  无剩余豁免理由。
- **顺手统一三表密度到 py-1.5**（否决）：面板表（选机工作台/详情
  内嵌）不在 #392 三主表范围，密度变更需单独决策。

## Verification

- 门禁：tsc / eslint（3 改动文件 `--max-warnings 0`）/ 全量 vitest
  602 / build 全绿（相关测试按 testid/文本断言，不依赖裸标签）。
- DOM 4/4（`/tmp/ui-shot-rig/verify-372.js`）：DeviceTablePanel
  sticky + oklab(…/0.95) 底 + ui/table 容器；DispatchCockpit 容量
  表在容器内（经「全选就绪 → 3 发起」链路触达）；DeviceOverview
  表视图在容器内（经 testid 切换触达）。

## Revisit

- 至此全库裸 `<table>` 清零，`ui/table` 是唯一表格底座；未来密度
  调整（如面板表跟进 py-1.5）在该底座单点改。
- 审查 §7「ui/table 全量迁移启动时 A4 窄修会被覆盖」已兑现：本 PR
  覆盖了 #351 的两处 overflow-x-auto 手写容器（由 Table 自带容器
  取代），sticky /95 注释完成搬家。
