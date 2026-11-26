# Agent Note: Lock Viewport Height & Pin Pagination Footers on `/devices` and `/hosts` (#4117)

Status: implemented
Class: bug-fix
Related: #4117

## Decision

1. **视口锁高消除双滚动条**：`DevicesPage` 与 `HostsPage` 列表主视图均改用 `<PageContainer width="wide" scrollable={false} className="py-2.5 lg:py-2.5">`，遵守 UI 治理契约 §3.1 规则 2（`flex h-full min-h-0 flex-col`，禁止 `calc(100vh-...)` 魔数）。
2. **卡片内弹性表格滚区 + 固定分页底栏**：
   - `ExpandableDeviceTable` 与 `ExpandableHostTable` 顶层改为 `flex flex-1 min-h-0 flex-col gap-2`，`FilterWorkbench` 包裹 `shrink-0`；
   - 主表卡片改为 `flex flex-1 min-h-0 flex-col overflow-hidden rounded-xl border border-border bg-card`；
   - `<Table containerClassName="flex-1 min-h-0 overflow-auto">` 移除原有的 `max-h-[calc(100vh-225px)]` / `max-h-[calc(100vh-235px)]` 硬编码高度，仅表格体内部垂直/横向滚动，表头保持 `sticky top-0`；
   - 分页底栏（`devices-pagination-bar` / `hosts-pagination-bar`）以 `shrink-0 border-t` 固定在表格卡片底部，常驻视口底端无需外层滚动即可直接查看页码区间与翻页；选中多行时卡片下方保留 `BulkBarSpacer`（`h-40 shrink-0`）将底栏顶出悬浮批量条覆盖带。
3. **统一两页底栏文案与按钮样式**：设备页与主机页统一采用 `第 X–Y / 共 N 台`、`每页 50/100/200` 与带左右箭头的 `上一页 / 下一页` 控件。

## Alternatives

- **继续调大 `calc(100vh-...)` 减数（如改为 `-310px`）**：当筛选工作台激活多条 chips 折行、或出现告警提示条、或后续顶栏瘦身（PR 2）改变顶栏高度时，硬编码减数会立刻再度失配或留下大块底部死白。纯 Flex 视口锁高可天然自适应任意视口高度与上方行政区变化。
- **使用 `position: sticky; bottom: 0` 吸底**：仍无法消除 `PageContainer` 外层滚动条与表格内部滚动条并存的双滚动条割裂体验。

## Verification

- Vitest 单测（`ExpandableDeviceTable.test.tsx`、`DevicesPage.test.tsx`、`ExpandableHostTable.test.tsx`、`HostsPage.test.tsx`）：**4 files / 126 passed**（修正 `<TableHeader sticky>` 后重跑 `ExpandableDeviceTable.test.tsx` **20 passed**）。
- 筛选与契约守卫：`tests/test_frontend_filter_workbench_guard.py` **8 passed**。
- 静态与治理门禁：`scripts/run_gates.py check:quick` **16/16 gates passed**。
- `stp-ui-review` 隔离预览实机 Playwright 巡检与截图人工审阅（`1440×900` 亮色 + 选中态、`1280×800` 暗色，`/devices` 与 `/hosts` 各 50 条分页夹具）：
  - 全场景 `docOverflow === 0`、`outerOverflowY === 0`（零外层滚动条）；
  - 分页底栏首屏即完整落于视口内（`1440×900`：`top=846, bottom=889`；`1280×800`：`top=746, bottom=789`），内部表格滚动 `400px` 后底栏位置零偏移（`footerDidNotShift=true`）且 `<thead>` 精确吸顶（`theadStickyOk=true`）；
  - 勾选行弹出底部悬浮 `BulkActionBar` 后，`BulkBarSpacer` 将底栏 cleanly 顶至 `bottom=721 <= bulkBarTop=821`（零遮挡）。

## Revisit

- PR 2 瘦身顶栏（`80px → 48px`）并下沉页面按钮后，因本 PR 已改用纯 Flex 弹性锁高，表格可用视口高度会自动无缝增加而无需再改任何高度常量。
