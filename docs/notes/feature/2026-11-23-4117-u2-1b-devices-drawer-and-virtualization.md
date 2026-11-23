# U2-1b：DevicesPage 右侧标准详情抽屉、虚拟滚动、ClickableRow 与列排序

Status: implemented
Class: feature

## Decision

落实 `#4117` Wave 2（1 号标杆页 `DevicesPage`）后半程 **U2-1b**（对应 D2 `#465` / D5 Phase 1 `#496` / `#3264`）：

1. **右侧标准 `DeviceDetailDrawer`（D2 / `#465`）**：
   - 新增 `frontend/src/components/device/DeviceDetailDrawer.tsx`，基于 `ui/sheet.tsx`（`size="lg"`）承载设备信息与归属、按需挂载的 `DeviceReturnPanel`（含欠账 `source_job_id` 直达 `/jobs/:jobId/report` 深链）与 2×2 实时遥测卡片；
   - 顶栏提供一键复制串号、状态/生命周期徽标与筛选序列内「上一台 / 下一台」连续巡检导航；底栏提供 D6 上下文跳转 AI 助手深链（`/assistant?prompt=...`）；
   - `DevicesPage` 同步 URL `?drawer=<deviceId>`，支持直达某台设备详情与刷新保持。
2. **表格虚拟滚动与键盘可达行（D5 Phase 1 / `#3264`）**：
   - 彻底移除 `ExpandableDeviceTable` 行内展开 DOM 与展开箭头列，改用 `@tanstack/react-virtual` 在固定视口高度的滚动容器内虚拟化渲染数据行，配合真实 `sticky top-0` 表头与无割裂感的左侧 sticky 悬浮态背景；
   - 将 `<TableRow onClick={...}>` 替换为标准 `<ClickableRow role="button" ...>`，同步在 `ClickableRow` 支持 `data-state` / `style` 透传并防止子控件按键冒泡，并从 `tests/test_frontend_filter_workbench_guard.py` 的 `_CLICKABLE_TR_ALLOWLIST` 移除 `components/device/ExpandableDeviceTable.tsx`（白名单 4 → 3）。
3. **客户端列排序**：为 `设备`、`状态`、`电量`、`温度`、`所属主机`、`最后活跃` 6 列提供无侵入客户端排序（点击循环 `asc → desc → 默认`），无需额外后端改动。

## Alternatives

- **使用 `<div>` Grid 替代 `<table>` 做虚拟滚动**：会破坏现有语义化表格结构、列宽对齐与无障碍表头语义；采用 `<tbody>` 顶部/底部 padding 占位行 + `@tanstack/react-virtual` 既保持真实 `<table>` 与 sticky 表头/左列，又显著压低 DOM 节点数。
- **把 `ExpandableDeviceTable` 文件重命名为 `DeviceTable`**：会影响现有导入路径与 `test_frontend_filter_workbench_guard.py` 的既有 `SourceGuard` 锚点；保留组件名与文件路径、专注交付行为升级可最小化无关变更。

## Verification

- Vitest 定向回归：`DeviceDetailDrawer.test.tsx`、`DevicesPage.test.tsx`、`ExpandableDeviceTable.test.tsx`、`DeviceBulkActionBar.test.tsx`、`DeviceReturnPanel.test.tsx`；
- 后端静态守卫：`.venv/bin/python -m pytest tests/test_frontend_filter_workbench_guard.py`（验证 `_CLICKABLE_TR_ALLOWLIST` 缩减通过）；
- 前端类型检查与 lint：`npx tsc --noEmit`、`npx eslint --max-warnings 0`；
- 全量快速门禁：`.venv/bin/python scripts/run_gates.py check:quick`；
- 实机浏览器验收：在 `stp-ui-review` 隔离环境执行 `1440×900` / `1280×800` × `light` / `dark` 双视口双主题截图与 DOM 几何/键盘焦点实测。

## Revisit

- 完成 `stp-ui-review` 实机截图对比并经 Owner 验收定调后，再按同一标杆模板推进 Wave 3（`U3-1` `HostsPage`）与 Wave 4。
