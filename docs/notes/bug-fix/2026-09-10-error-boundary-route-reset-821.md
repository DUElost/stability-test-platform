# 路由层 ErrorBoundary 随路由切换复位（#821，R12-F08）

Status: implemented
Class: bug-fix

## Decision

`AppShell` 内容区的 `<ErrorBoundary fullscreen={false}>` 常驻包裹
`<Suspense><Outlet/></Suspense>`，无复位机制：某页一次 transient 渲染崩溃
（如后端字段形状异常）后 `hasError=true` 持续渲染错误屏；SPA 切路由只换
Outlet 内容、边界实例不重挂载 → 去任何页面都是同一旧错误屏，只能整页刷新。

修正（选「复位键」而非 `key` 重挂载）：

- `ErrorBoundary` 新增可选 `resetKey?: string` + `getDerivedStateFromProps`：
  resetKey 变化即清除错误态并重新渲染子树；不传时行为完全不变（App.tsx
  顶层边界不受影响）。
- `AppShell` 传 `resetKey={location.pathname}`：切路由即复位；错误屏上点任意
  侧栏/顶栏入口即恢复。

为什么不用 `key={pathname}`：key 会让边界连同子树在每次 pathname 变化时整体
重挂载——对同路由参数导航（如 `/plan-runs/1 → /plan-runs/2`）改变现有页面
实例复用语义；resetKey 只在出错后复位状态，非错误路径零行为变化。

## Alternatives

- `key={useLocation().pathname}`（issue 首推）：一行改动，但扩大重挂载语义
  （同路由参数变化也重挂）；未采纳。
- 用 `location.key` 作复位键：搜索参数变化也复位——错误屏上无法操作搜索，
  收益为零且更宽。
- 在 ErrorBoundary 内监听 history/location：组件无路由上下文，需额外注入；
  不如由使用方传 resetKey。

## Verification

- `npx vitest run src/components/ErrorBoundary.test.tsx` → 5/5（新增 3 例：
  resetKey 变化恢复 / resetKey 不变错误态保持 / 复位后新内容再崩仍显示新错误）
- 红绿：未修复实现上 2 条新用例失败（guard 用例恒过）
- 全量前端套件 / type-check / eslint / build / `check:quick` 通过

## Revisit

- 若希望「同一路径原地重试」也可恢复（如后端恢复后重试），可在错误屏加
  「重试本页」按钮并复用 resetKey 机制；当前错误屏已提供整页刷新出口。
