# Agent Note: Shell Top-Bar Slimming, Workbench Action Placement (Option A) & Sidebar Refinement (#4117)

Status: implemented
Class: feature
Related: #4117

## Decision

1. **契约 §3.1 同步修订（`docs/design/2026-11-23-frontend-ui-governance-contract.md`）**：
   - 将 `AppShell` 顶栏定为全站唯一紧凑单行页头（`48px`），明确禁止列表页在顶栏堆叠说明书式静态副标题；
   - 采纳 Owner 裁决（方案 A）：在 `/devices` 与 `/hosts` 高密列表工作台页中，将实体创建与运维进度等页面级业务操作下沉至页面内容区 `FilterWorkbench` 右侧操作区，不与顶栏右侧全局壳层工具混排；其余未迁移页保留 `PageHeader.action` 兼容；
   - 规定 `AppShell` 实时连接态常态静音、侧栏折叠态持久化与去中部悬浮圆钮、以及核心导航组默认展开。
2. **顶栏与壳层瘦身（`AppShell.tsx`、`PageHeader.tsx`、`UserMenu.tsx`）**：
   - 顶栏高度由 `h-20`（`80px`）压至 `h-12`（`48px`），每页直接释放 `32px` 垂直空间给数据表首屏；
   - `PageHeader` 改为单行紧凑布局，未迁移页若仍传 `subtitle` 则在 `xl` 及以上宽度同行轻展示，避免裁切；
   - 实时连接态常态收敛为紧凑绿点状态指示器（`ws-connection-status`），仅断线时展开红色「已断开」警示徽标（对齐「常态静音、异常着色」）；
   - `UserMenu` 触发器同步收紧至 `h-9`、`w-7 h-7` 头像，居中贴合 `48px` 顶栏。
3. **方案 A 页面业务按钮归位（`DevicesPage.tsx`、`HostsPage.tsx`）**：
   - `/devices` 与 `/hosts` 移除静态说明书副标题与顶栏 `action`；
   - 「+ 添加设备」「+ 添加主机」及「安装 / 热更新 / 刷机前置进度」按钮收紧为 `h-7` 紧凑样式并放入对应 `ExpandableDeviceTable` / `ExpandableHostTable` 的 `extraHeaderRight`（`FilterWorkbench` 右侧工具区）。
4. **左侧常驻边栏轻量收敛（`Sidebar.tsx`、`navItems.ts`、`AppShell.tsx`、`bulk-action-bar.tsx`）**：
   - 桌面宽度由 `224px / 72px` 收敛至 `208px / 56px`（`BULK_BAR_OUTER_CLASS` 同步对齐 `lg:left-56`），Logo 栏同步压至 `h-12` 与顶栏齐平；
   - 仅「更多功能」标记 `defaultCollapsed: true`，核心高频三组（工作区 / 分析报告 / 资源）默认展开，消除运维跨组跳转额外点击；
   - 持久化 `sidebarCollapsed` 至 `localStorage`（`stp_sidebar_collapsed`），移除收起态悬浮在屏幕中线遮挡表格左列的 `fixed left-[60px] top-1/2` 圆钮。

## Alternatives

- **方案 B（将「添加设备 / 添加主机」继续留在瘦身后的顶栏）**：虽可保持旧契约字面不变，但在 `1280px` 视口下仍会与全局壳层工具及动态运维进度按钮竞争顶栏水平空间，且手动添加设备/主机属低频操作，挂在全局顶栏视觉权重偏高；Owner 已明确选择方案 A。
- **改为顶部横向导航栏替代左侧常驻边栏**：14 个业务模块在横向导航下会触发二级下拉或折行，反增跳转成本；在左侧边栏内做宽度收敛、核心组默认展开与折叠持久化是成本最低、体验收益最高的方案。

## Verification

- Vitest（`AppShell.test.tsx`、`Sidebar.test.tsx`、`DevicesPage.test.tsx`、`HostsPage.test.tsx`、`ExpandableDeviceTable.test.tsx`、`ExpandableHostTable.test.tsx`、`bulk-action-bar.test.tsx`）：**7 files / 137 passed**。
- 前端契约守卫 pytest（`test_frontend_filter_workbench_guard.py`、`test_frontend_bulk_selection_guard_2614.py`、`test_frontend_sticky_header_opacity.py`）：**17 passed**。
- 静态与治理门禁：`scripts/run_gates.py check:quick` **16/16 gates passed**。
- `stp-ui-review` 隔离预览实机 Playwright 巡检与 5 张双视口 × 双主题截图人工审阅（`1440×900` 亮色 / `1280×800` 暗色 + 侧栏收起刷新持久化）：
  - 顶栏高度 `49px`（`48px` 内容 + `1px` 底边），较原 `81px` 释放 `32px`，主表可用视口高度由 `633px` 增至 `663px`（`1440×900`）；
  - 实时连接常态为紧凑绿点（`wsQuietDotOk=true`），顶栏无静态副标题，无业务创建按钮；
  - 「添加设备」「添加主机」位于工作台右侧（`addBtnOutsideHeader=true`、`addBtnBelowHeader=true`）；
  - 侧栏展开 `208px`（核心三组默认展开，仅「更多功能」默认折叠），收起后刷新页面仍保持 `56px`（`persistedKey="1"`）且无中部悬浮按钮（`floatingMiddleBtnCount=0`）；
  - 全场景 `docOverflow=0`、`outerOverflowY=0`、`footerFullyInViewport=true`、`footerDidNotShift=true`、`theadStickyOk=true`。

## Revisit

- 其余列表页（如 Plan 管理、用例套件、脚本库）可按后续波次逐步对齐方案 A（去静态副标题并将创建操作归位到工作台），在此之前 `PageHeader` 保留 `action` 与行内 `subtitle` 兼容。
