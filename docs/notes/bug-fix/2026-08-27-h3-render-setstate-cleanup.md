# H3 修复：render 期 setState 反模式收敛（6 处）

- **日期**：2026-08-27
- **关联**：`docs/reviews/FRONTEND_UI_REVIEW_2026-08-27.md` H3（复核轮定级：代码气味，非 bug）
- **类型**：bug-fix（代码清洁 / 反模式收敛）

## 决定了什么

6 处"render 期 setState"分两类处理：

1. **NotificationsPage（真副作用）**：`hasLogs` 异步数据到达后的一次性自动切 tab——
   迁 `useEffect`，块级豁免 `react-hooks/set-state-in-effect`（`tabAutoDetected` 守卫
   防循环，`findByText` 测试兼容）。
2. **其余 5 处（官方推荐模式）**：**保留** render 期 "adjust state when prop changes"
   模式（React 官方 `react.dev/learn/you-might-not-need-an-effect` 推荐），不迁 useEffect
   ——因为 `react-hooks/set-state-in-effect` 是 error 级 lint 门禁，明令反对 effect 内
   setState；原模式才是官方认可写法。真正的易错点是**对象引用比较**：
   - `UserModal` / `AddHostModal`：`prevModal.editing !== editUser`（对象引用）→ 改为
     `prevOpen` + `prevEditingId`（`id` 稳定标识）比较，消除"父组件每次重渲染传新引用
     误触 reset"的隐患。
   - `ScriptVersionDialog` / `PlanExecutePage`×2：resetKey 本就是稳定字符串比较
     （`open|script.id` / `JSON.stringify(filters)`），无需改动，仅补注释说明。

## 放弃的备选

- **6 处全迁 useEffect**：被 lint 规则否定（`react-hooks/set-state-in-effect` error 级），
  且与 React 官方推荐相悖——"统一迁移"的直觉方案在此是错的方向。

## 如何验证

- 改动文件 `eslint`（含 `react-hooks/exhaustive-deps`、`set-state-in-effect`）全过；
  `tsc --noEmit` 全过。
- vitest：`NotificationsPage.test.tsx` + `HostsPage.test.tsx` + `PlanExecutePage.test.tsx`
  3 文件 96 用例全过（含"自动切 tab 只发生一次"、"无通知记录停留在渠道页签"、
  "URL 指定页签不被覆盖"等行为断言）。
- 涉及表单初始化回归点：UserModal / AddHostModal 打开/切换编辑对象时预填与清空。

## 何时重议

- 无需重议（纯代码清洁）。若未来 React lint 对 render 期 adjust-state 模式出新规，
  按新规再统一。
