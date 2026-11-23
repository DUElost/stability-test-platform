# U1-2：收敛 SearchableSelect / FilterBar / Button / 语义 Token 与状态色

Status: implemented
Class: feature

## Decision

落实 `#4117` Wave 1 的 U1-2 基础组件与设计 token 收敛：

1. **`SearchableSelect` 升级（D1）**：浮层改用 `createPortal(..., document.body)` + `position: fixed` 动态定位与视口翻转，避免在带 `overflow-hidden` 或滚动容器中裁切；补齐 `ArrowDown` / `ArrowUp` / `Home` / `End` / `Enter` / `Escape` 完整键盘导航与关闭焦点归位；将暗色下失效的 `ring-black/5` 替换为 `ring-border/50`。
2. **`FilterSearchInput` 与 `MoreFiltersPopover` 修复**：修正 `FilterSearchInput` 中 `cn()` 参数顺序，使 `pl-9 pr-8 bg-muted/30` 生效、不再被 `FORM.inputSm` 的 `pl-7 pr-2 bg-background` 覆写导致搜索文字与放大镜/快捷键 `/` 重叠；同步将 `MoreFiltersPopover` 的 `ring-black/5` 替换为 `ring-border/50`。
3. **`Button` 小图标选择器修复（#3487）**：将 `[&_svg:not([class*='size-'])]:size-4` 扩展为 `[&_svg:not([class*='size-']):not([class*='h-']):not([class*='w-'])]:size-4`，使已有显式 `h-3.5 w-3.5` 等尺寸类的小图标不再被强行拉大为 16px。
4. **语义色与状态口径收敛**：将 `TEXT.destructive`、`ALERT_BANNER.destructive`、`ALERT_BOX.destructive`、`FORM.error`、`RISK_RATING_TEXT.S` 及 `InlineError` 收敛到 `text-destructive-text`（暗色模式 AA 可读）；将 `RISK_RATING_TEXT.B` 对齐 `StatusBadge kind="risk"` 的 `text-success`；将 `StatusBadge` 中 `JOB_STATUS_CONFIG.ABORTED` 从 `destructive` 收敛到 `secondary`（`已中止`），与 `CANCELED` 一致，不与 `FAILED` 混淆。

## Alternatives

- **使用 Radix Popover 重写 `SearchableSelect`**：会改变现有 DOM 结构与测试中对 `role="listbox"` / `input` 的查询约定；采用轻量 `createPortal` + 手动定位 + 键盘导航即可无侵入解决裁切与键盘可用性，保持 100% 既有 props 兼容。
- **全站将 `RUNNING` 状态色改为 `info`**：经核对现有测试（`status-badge.test.tsx`）与全站习惯，`RUNNING` 明确约定为 `warning`（amber），仅 `ABORTED` 存在与 `FAILED` 混淆的真实缺陷，因此只收敛 `JOB.ABORTED` 与 `RISK_RATING_TEXT.B`。

## Verification

- Vitest 定向回归：`searchable-select.test.tsx`、`filter-bar.test.tsx`、`status-badge.test.tsx`、`DevicesPage.test.tsx`、`HostsPage.test.tsx`、`PlanRunListPage.test.tsx`；
- 后端静态漂移守卫：`.venv/bin/python -m pytest tests/test_status_vocabulary_drift.py tests/test_frontend_filter_workbench_guard.py`；
- 前端类型检查与 lint：`npx tsc --noEmit`、`npx eslint --max-warnings 0`；
- 全量快速门禁：`.venv/bin/python scripts/run_gates.py check:quick`。

## Revisit

- Wave 2 实机浏览器验收（`stp-ui-review`）时，实测验证 `FilterSearchInput` 计算态 `paddingLeft === 36px` / `paddingRight === 32px` 以及 `SearchableSelect` 在 `DevicesPage` 表格容器顶部打开不被裁切。
