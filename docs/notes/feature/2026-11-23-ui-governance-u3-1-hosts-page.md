# U3-1：HostsPage 同标尺落地（右侧抽屉 / 虚拟滚动 / 密度收敛 / #3193）与 Wave 2 实图微调

Status: implemented
Class: feature

## Decision

按前端治理总台账 `#4117` Wave 3（`U3-1`）与 Wave 2 签收后的 3 条实图验收细则，以 `DevicesPage` 标杆模板同标尺改造 `HostsPage`，并顺带收口 `#3193` 与 `DeviceDetailDrawer`/搜索框的轻量打磨：

1. **`HostsPage` 信息密度与标杆架构（D2 + D5 Phase 1）**：
   - 将主按钮「添加主机」与进度面板重开入口归位至 `PageHeader.action`，收敛 `PageContainer` 垂直留白；
   - 合并顶部 4 张高大 KPI 卡与快捷视图为 `FilterWorkbench` 顶行单排状态摘要条（保留 `Agent 已对齐 X/Y · N 台待热更新` 口径与全部状态筛选无障碍标签）；
   - 修复「健康异常」快捷视图漏掉 `UNSCHEDULABLE` 的语义缺陷：新增组合态 `health=unhealthy`（同时命中 `DEGRADED` 与 `UNSCHEDULABLE` 并同步 URL）；
   - 删除 `ExpandableHostTable` 中约 140 行未接线的 `scriptPresenceSummary` 死代码；
   - 「更多筛选」内代码同步下拉迁至 `SearchableSelect`（对齐 D1）；
   - 表格启用 `@tanstack/react-virtual` 虚拟滚动、6 列客户端排序、将 `<TableRow onClick>` 迁至标准 `ClickableRow`（静态守卫 `_CLICKABLE_TR_ALLOWLIST` 从 3 降至 2），并对齐 `资源` 居中表头与 `1280×800` 窄列宽使核心列免横滚可见；
   - 新建标准右侧 `HostDetailDrawer`（`ui/sheet.tsx` `size="lg"`，支持 `?drawer=` URL 持久化、上一台/下一台切换、IP 一键复制、资源/Watch 开关、补齐 `mount_status` 呈现并链往 `/storage`、Agent 版本对齐、USB/Hub 对账诊断、按需懒加载脚本在位并链往 `/script-management`、底栏 AI 助手上下文深链）。
2. **消除原生弹窗与修复 `#3193`**：
   - 新建 `RetireHostDialog`（`ui/dialog.tsx`）替换 `HostsPage` 中的 `window.prompt`，静态守卫 `_NATIVE_DIALOG_ALLOWLIST` 从 4 降至 3；
   - 对齐 `startFlashPrereqsBatch` 的 `CANCELED` / `lost` 终态语义至 `canceled`（不再报红），并将顶部进度重开按钮的「进行中」判据并上 `flashPrereqsPending`（Closes `#3193`）。
3. **吸收 Wave 2 实图验收 3 条微调细则**：
   - `HostDetailDrawer` 与 `DeviceDetailDrawer` 内核心标识/长文本/路径/异常摘要默认允许自动换行完整展示，不再二次 `truncate`；
   - 放宽 `DevicesPage` / `HostsPage` 搜索框宽度并精简 `DevicesPage` placeholder，避免文字贴边 `/` 快捷键徽标；
   - 压实 `HostsPage` `1280×800` 列宽并修复 `资源` 列头居中对齐。

## Alternatives

- **保留表内展开行、仅加虚拟化**：展开行跨列高度动态突变会破坏虚拟列表行高估算并沿用 5 卡宽屏挤压问题，且违背 D2（`#465`）全站高密列表统一抽屉范式，故不采纳。
- **把 `DeviceDetailDrawer` 微调另拆单独 PR**：改动仅 20 余行且属本次 Wave 2 签收放行附带口径，随 Wave 3 同批完成并在 `stp-ui-review` 一次复核最省集成队列开销。

## Verification

- Vitest 定向回归：`ExpandableHostTable.test.tsx`、`HostsPage.test.tsx`、`useHostOperations.test.ts`、`DevicesPage.test.tsx`、`DeviceDetailDrawer.test.tsx`；
- 静态契约守卫：`pytest tests/test_frontend_filter_workbench_guard.py`（确认 `_NATIVE_DIALOG_ALLOWLIST` 4→3、`_CLICKABLE_TR_ALLOWLIST` 3→2）；
- 类型/Lint/综合门禁：`tsc --noEmit`、`eslint --max-warnings 0`、`npm run check:quick`；
- 隔离实机预览：`stp-ui-review` 下 `1440×900` 与 `1280×800` × 亮/暗双主题逐图验收（空闲态 / 右侧主机抽屉 / 筛选下拉 / 批量栏 / `DeviceDetailDrawer` 换行复核）。

## Revisit

- Phase 2 接入后端主机列表真服务端分页与筛选聚合（`#496`）时，只需替换 `HostsPage` 数据获取层，本轮解耦后的 `FilterWorkbench` URL 契约、虚拟化主表与 `HostDetailDrawer` 无需返工。
