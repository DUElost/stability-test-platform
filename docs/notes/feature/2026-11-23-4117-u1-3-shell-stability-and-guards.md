# U1-3：公共外壳渲染稳定化、顶栏分隔收口与前端 UI 治理静态契约守卫

Status: implemented
Class: feature

## Decision

落实 `#4117` Wave 1 的 **U1-3** 外壳稳定化与静态契约守卫：

1. **`HeaderSlotContext` 读写拆分与去闪烁**：将 `HeaderSlotContext` 拆分为 `HeaderSlotValueContext`（读取端 `AppShell`）与引用恒定的 `HeaderSlotSetterContext`（写入端 `PageHeader` / `usePlanRunHeaderSlot` / `usePlanEditHeaderSlot`），消除 `setHeaderSlot` 触发子页面回环重渲染的问题；将卸载清空拆分为独立 unmount effect，避免同页 props 刷新时顶栏先置 `null` 闪烁。同步修复 `usePlanEditHeaderSlot` 中状态药丸缺 `shrink-0 whitespace-nowrap` 导致在 `1280×800` 压扁成三行椭圆的问题。
2. **`useSocketIO` 外壳降噪与 `online` 监听引用计数**：
   - 将全局 `window 'online'` 监听改为按 `_hookRefcount` 引用计数维护（首挂绑定、归零或 `disconnectDashSocket` 摘除），修复单个业务页卸载时误把 `AppShell` 共享断线重连监听摘掉的隐患；
   - 新增可选参数 `trackLastMessage?: boolean`（默认 `true` 保持兼容），`AppShell` 与 `useCrossClientSync` 传入 `false`，消除每次 Socket.IO 广播触发两次顶层外壳重渲染。
3. **`AppShell` 与 `Sidebar` 细节收口**：在 `AppShell` 顶栏业务槽位与右侧全局状态控件之间补齐垂直分隔线（`h-5 w-px bg-border`）；为 `Sidebar` 顶部固定 `AI 助手` 入口补齐 `onClick={onNavigate}`（移动端点击后收起抽屉）与桌面收起态 `title="AI 助手"` 提示。
4. **静态 UI 契约守卫（只减不增 allowlist）**：在既有 `tests/test_frontend_filter_workbench_guard.py` 中集中新增 6 条双向守卫（禁新增 `window.confirm/prompt/alert`、禁新增裸 `<table>`、禁新增无 `ClickableRow` 的 `<TableRow onClick>`（Refs `#3264`）、禁 `components/ui/` 外手写 `aria-modal`（存量基线为 0）、禁新增硬编码 Tailwind 调色板色阶、非认证路由页须用 `PageContainer`），存量白名单只减不增。

## Alternatives

- **新建独立的 `tests/test_ui_governance_guard.py` 文件**：按 `docs/development/dependencies-and-quality.md` 门禁收敛原则，同域前端静态守卫归拢至现有 `tests/test_frontend_filter_workbench_guard.py`，避免测试文件碎片化。
- **把 `useSocketIO` 的 `lastMessage` 默认关闭**：会破坏依赖 `lastMessage` 返回值的存量调用方（如测试或单页订阅）；采用默认 `true` + 壳层显式传 `trackLastMessage: false` 兼顾零破坏与外壳性能。

## Verification

- Vitest 定向回归：`Sidebar.test.tsx`、`AppShell.test.tsx`、`useSocketIO.test.ts`、`useSocketIO.disconnect.test.ts`、`useCrossClientSync.test.tsx`；
- 静态契约守卫：`.venv/bin/python -m pytest tests/test_frontend_filter_workbench_guard.py`；
- 前端类型检查与 lint：`npx tsc --noEmit`、`npx eslint --max-warnings 0`；
- 全量快速门禁：`.venv/bin/python scripts/run_gates.py check:quick`。

## Revisit

- Wave 2 `U2-1b` 完成后须从 `_CLICKABLE_TR_ALLOWLIST` 移除 `components/device/ExpandableDeviceTable.tsx`；Wave 3/4 每清理一页存量问题同步消减对应白名单直至归零。
