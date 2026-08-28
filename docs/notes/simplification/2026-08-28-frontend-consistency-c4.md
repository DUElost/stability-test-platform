# 前端一致性收敛第四批（C4/C5 收尾，C 类收官）

- **日期**：2026-08-28
- **关联**：`docs/reviews/FRONTEND_UI_REVIEW_2026-08-27.md` §4 C4 / C5
- **类型**：simplification（一致性收敛收尾）

## 决定了什么

### C5 — 错误消息粒度统一（带后端详情）
- `NotificationsPage`：channel/rule 的保存/删除 4 处 `toast.error('保存失败'/'删除失败')`
  → `toApiError(err).message`（与 handleTestChannel 既有行为一致）。
- `WifiPage`：create/update/delete 3 处 `onError` 泛化 → `toApiError(err).message`。
- 行为变化：错误 toast 从泛化文案变为后端详情（如 "boom"）——测试已同步更新断言。

### C4 — 表单 label 关联收尾
- 补齐 `NotificationsPage` channel/rule 表单 6 字段、`ScriptVersionDialog` 6 字段、
  `PlanRunHero` 中止原因 1 字段的 `htmlFor`/`id`。
- **包裹型 label 判定**：`<label className="flex items-center gap-2">` 内嵌 checkbox/radio/
  hidden-input 的（JiraSubmitPanel、MapModelsDialog、AuditLogPage 时间等）天然隐式关联，
  无需 htmlFor，不改。

## 如何验证

- `eslint` + `tsc --noEmit` 全过。
- vitest：`NotificationsPage.test.tsx` 31 用例全过（其中 1 处断言从'保存失败'更新为
  后端详情 'boom'，对应 C5 预期行为改进）。

## 何时重议

- **C 类一致性欠债（C1-C8）至此全部收官**（第一批 #481、第二批 #482、第三批 #487、
  本批）。剩余低危族：P1-P4（规模上线前）、A1-A3 / D1-D8 / E 内务（随手）。
- 若产品要求"保留泛化文案 + 详情折叠"，再重议 C5（当前直显后端详情）。
