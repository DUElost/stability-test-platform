# H5' 修复：ErrorBoundary 下沉到路由层

- **日期**：2026-08-28
- **关联**：`docs/reviews/FRONTEND_UI_REVIEW_2026-08-27.md` H5'（独立保留项，结构弱点）
- **类型**：bug-fix（错误边界粒度）

## 决定了什么

`App.tsx:12` 的 ErrorBoundary 包裹整个 App —— 任何页面 render-throw 都会**整页无壳**
（侧栏/顶栏同崩）。改为双层：

1. **页面级兜底（新增）**：`AppShell.tsx` 的 `<main>` 区域包 `ErrorBoundary fullscreen={false}`，
   包裹 `<Suspense><Outlet /></Suspense>`。单页 render-throw 只崩内容区，侧栏/顶栏
   （AppShell 外壳）保住，用户仍可导航离开。
2. **应用级最后防线（保留）**：`App.tsx` 顶层 ErrorBoundary 不变（Provider / AppShell
   本身崩溃时兜底）。
3. `ErrorBoundary` 组件新增 `fullscreen?: boolean` prop（默认 `true` 保持顶层全屏行为）：
   `fullscreen` → `min-h-screen`；紧凑模式 → `h-full min-h-64`（适配内容区高度，不溢出）。

## 放弃的备选

- 只在路由定义层给每个 element 包 ErrorBoundary：侵入所有路由定义，且漏包风险高；
  在 AppShell 的 Outlet 出口统一包裹更内聚。
- 移除 App.tsx 顶层 ErrorBoundary：Provider / AppShell 本身崩溃时无兜底，保留。

## 如何验证

- `eslint` + `tsc --noEmit` 全过。
- vitest：`ErrorBoundary.test.tsx`（2 用例，断言文本/刷新/chunk 恢复不受 fullscreen 影响）
  + `PlanRunDetailPage.test.tsx`（21 用例，含 AppShell 集成的 ErrorBoundary spy）共 23 用例全过。
- B 轨目视验证（可选）：任意页面抛错时侧栏/顶栏应保留、内容区显示错误态。

## 何时重议

- 无。若产品希望错误态带"返回上一页"按钮（当前只有刷新），再扩展 ErrorBoundary 动作区。
