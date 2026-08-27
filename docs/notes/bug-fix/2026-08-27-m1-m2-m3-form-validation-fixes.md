# M1 / M2 / M3 修复：表单校验、时间区间提示、死代码清理

- **日期**：2026-08-27
- **关联**：`docs/reviews/FRONTEND_UI_REVIEW_2026-08-27.md` M1（死代码，复核轮定性）、
  M2（Schedules 校验缺失）、M3（Audit 时间静默）
- **类型**：bug-fix

## 决定了什么

1. **M2 — SchedulesPage 表单校验补全**（`pages/schedules/SchedulesPage.tsx`）：
   - `handleSave` 补 `name` 非空、`cron_expr` 非空校验（原只校验 plan_id / device_ids，
     空名称/空 cron 直接提交，错误落到后端才报）。
   - `parseDeviceIds` 重构为返回 `{ ids, invalid }`：非法 token（如 `"1,abc"` 的 `abc`）
     不再静默丢弃，`handleSave` 中以 `toast.info` 提示"以下设备 ID 无效已忽略"。
2. **M3 — AuditLogPage 时间区间非法提示**（`pages/audit/AuditLogPage.tsx`）：
   `start_time > end_time` 不再静默 `return`，改为 `setError('起始时间不能晚于结束时间')`
   + 清空列表，复用页面既有 `InlineError` 渲染（带重试）。
3. **M1 — UsersPage 编辑弹窗死代码清理**：
   `UserModal` 的 `onSubmit` 改为可选；编辑弹窗不再传
   `onSubmit={(data) => createMutation.mutate(...)}`（复核轮定性：`isEditMode && onUpdate`
   恒优先，此路径恒不可达）。编辑态只传 `onUpdate`。

## 放弃的备选

- M2 非法 ID 用 `toast.error`：会被当作"操作失败"（10s 红条）；改用 `toast.info`（4s）
  表达"部分忽略"的非破坏语义。
- M1 把 `onSubmit` 留在编辑弹窗传 `() => {}`：不如"不传 + 可选化"干净，直接删。

## 如何验证

- `eslint` + `tsc --noEmit` 全过。
- vitest：`SchedulesPage.test.tsx` 2 用例全过。AuditLogPage / UsersPage 无既有测试
  （本次改动为纯表单校验/死代码删除，无行为回归面）。

## 何时重议

- 无。
