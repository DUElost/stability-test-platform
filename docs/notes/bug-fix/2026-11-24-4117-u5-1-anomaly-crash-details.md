# Agent Note: U5-1 修复 `AnomalyDashboard` 包名榜 `#6+` 报错设备明细断链并统一抽屉下钻（#4117）

Status: implemented
Class: bug-fix

## Decision

- 修复 `PlanRunDetailPage` 中「本次新增 · 包名榜」点击「查看全部」后无法查看第 6 名及以后包名 Crash 报错设备明细的交互断链：
  - 后端 `GET /api/v1/plan-runs/{run_id}/crash-details` 已支持任意 `package_name` 与 `time_scope` 查询并返回 `entry_origin`；
  - 前端 `api.planRuns.getCrashDetails` 新增透传 `time_scope` 参数；
  - 将 `PackageRankingDrawer` 升级为符合 UI 契约 D2 的 `size="lg"` 标准右侧 `Sheet`，内置包名搜索、共享 `PaginationBar` 与内置 `CrashDetailPanel`，无论从 Top 5「查看详情」还是从「查看全部」点击任意包名（含 `#6+` 及「运行前遗留」Tab），均可在抽屉内原地拉取并查看受影响设备与事件明细；
  - 按 `supports_origin_split` 与当前来源（`current` → `runtime` / `preexisting` → `baseline`）过滤 `entry_origin`，消除时间窗/来源口径错配；
  - 消除 Top 5 行内 `<button>` 嵌套 `<button>` 的语义缺陷；格式化事件时间戳；支持点击受影响设备串号直接打开 `PlanRunDetailPage` 的单机 `DeviceDetailDrawer` 或一键跳转单机日志（`/execution/plan-runs/:runId/logs?q=<serial>`）。

## Alternatives

- **在主卡片内动态插入被选中的第 6+ 包名行**：仍会导致小内联面板拥挤、无法连续浏览多个包名，且无法统一「运行前遗留」和搜索分页体验；因此将全部包名列表与 Crash 明细统一收口至 `size="lg"` 抽屉，同时保留主卡片 Top 5 直接点选筛饼图与直达详情的快捷体验。

## Verification

- Vitest 单测覆盖：`AnomalyDashboard.test.tsx`（含 `#6+` 抽屉内点击查明细、`time_scope` 与 `entry_origin` 过滤、运行前遗留详情、包名搜索与 `PaginationBar`、点击设备串号触发 `onSelectDeviceSerial`、错误态重试与零嵌套 `<button>`）。
- 本地静态门禁：`npx tsc --noEmit`、`npx eslint`、`scripts/run_gates.py check:quick`。

## Revisit

- 后续在 `U5-2`/`U5-3` 推进 `PlanRunDetailPage` 整体分层与 `DeviceDetailDrawer` 标杆对齐时，可进一步联动在 Minimap 上高亮当前查看包名所影响的设备集合。
