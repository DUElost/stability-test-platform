# 前端一致性收敛第二批（C1-Schedules / C5 / C4）

- **日期**：2026-08-28
- **关联**：`docs/reviews/FRONTEND_UI_REVIEW_2026-08-27.md` §4 C 类一致性欠债
- **类型**：simplification（一致性收敛第二批）

## 决定了什么

1. **C1 SchedulesPage 迁 react-query**（`pages/schedules/SchedulesPage.tsx`）：
   手写 `useState+useEffect+useCallback` → `useQuery`（schedules + plans 两个 query）；
   CRUD 后 `qc.invalidateQueries(scheduleKeys.list())`；新增 `scheduleKeys` 到 queryKeys.ts。
   loading/loadError 由 query 状态派生。至此 C1（数据获取范式）全部页面统一为 react-query。
2. **C5 错误粒度**：
   - `SchedulesPage` delete/toggle 的 `toast.error('删除失败'/'切换失败')` 改为
     `toApiError(err).message`（带后端详情，与 handleSave 一致）。
   - `LoginPage` 错误横幅 `STATUS_CHIP.destructive` → `ALERT_BOX.destructive`（与
     RegisterPage/ChangePasswordPage 统一）。
3. **C4 表单 label 关联**：`SchedulesPage` 表单 5 个字段与 `ChangePasswordPage` 3 个字段
   补 `htmlFor`/`id`（WifiPage 第一批已补）。

## 放弃的备选

- C4 一次性把全站原生 input 表单统一到封装 `Input`：涉及页面多、与 C2/C3 组件轮
  纠缠，拆到后续"表单基础设施"轮。
- C5 把 NotificationsPage/WifiPage 全部错误也改 toApiError：这两页是 `onError` 回调
  风格，改动分散，随 C5 后续轮。

## 如何验证

- `eslint` + `tsc --noEmit` 全过。
- vitest：`SchedulesPage.test.tsx` 2 用例全过（迁移后行为保持）。

## 何时重议

- C2/C3（页签组件、可点击行键盘）为下一批，需先抽组件。
- C4/C5 剩余页面（NotificationsPage/WifiPage 错误、其余表单）归"表单基础设施"轮。
