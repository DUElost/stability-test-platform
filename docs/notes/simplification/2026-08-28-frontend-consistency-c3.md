# 前端一致性收敛第三批（C2 页签组件 / C3 可点击行）

- **日期**：2026-08-28
- **关联**：`docs/reviews/FRONTEND_UI_REVIEW_2026-08-27.md` §4 C2 / C3
- **类型**：simplification（组件抽取 + 逐页对齐）

## 决定了什么

### C2 — 状态型页签组件 `StateTabs`（`components/ui/state-tabs.tsx`）
- 两种视觉变体：`underline`（tabLinkClass，IssueTracker 风格）/ `segmented`（SEGMENTED，
  Notifications 风格），替代各页手写按钮。
- 无障碍语义：`role="tablist"` + `role="tab"` + `aria-selected` + `aria-controls`（可选
  `panelId`），支持 `testId`/`title`/`ariaLabel`。
- 接入：`IssueTrackerPage`（underline，保留 `data-testid="issue-tracker-tabs"`）、
  `NotificationsPage`（segmented，label 含动态计数）。

### C3 — 可点击表格行 `ClickableRow`（`components/ui/clickable-row.tsx`）
- 与 ClickableCard 同思路：`role="link"`/`tabIndex={0}` + Enter/Space 触发 + 焦点样式。
- 接入：`ResultsPage` 运行列表行、`ProjectDetailPage` 运行记录行。
- `ProjectsPage` 项目卡在 #477/476 改版时已具备 role/tabIndex/Enter 处理，无需改。

## 放弃的备选

- FileServerPage 的 tablist 作为样板，但其 `tabpanel` 用显式 id 关联；StateTabs 用
  `panelId`（可选）支持，消费方自行关联，避免组件内 useId 与消费方 id 无法对齐。
- ClickableRow 用 `role="button"`：导航语义用 `link` 更准确（行点击是跳转）。

## 如何验证

- `eslint` + `tsc --noEmit` 全过。
- vitest：`IssueTrackerPage.test.tsx` + `NotificationsPage.test.tsx` 36 用例全过
  （含 tab 切换、testId 断言，接入 StateTabs 后行为保持）。

## 何时重议

- C4/C5 剩余（Notifications/Wifi 错误、其余表单）归"表单基础设施"轮。
- 若未来需要路由型页签，用既有 `UnderlineTabs`（NavLink 版）；`StateTabs` 专责状态型。
