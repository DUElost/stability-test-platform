# 前端一致性收敛（C1/C6/C7/C8 第一批）

- **日期**：2026-08-28
- **关联**：`docs/reviews/FRONTEND_UI_REVIEW_2026-08-27.md` §4 C 类一致性欠债
- **类型**：simplification（一致性收敛，按规范表第一批）

## 决定了什么（第一批实施的 C 类项）

1. **C8 注册页走 api 客户端**（`pages/auth/RegisterPage.tsx`）：裸 `axios.post` → `api.auth.register`，
   与 Login 共享 withCredentials/token/错误规范化。
2. **C1 AuditLogPage 迁 react-query**（`pages/audit/AuditLogPage.tsx`）：手写
   `useState+useEffect+useCallback` → `useQuery`（queryKey 含 page/pageSize/filters）；
   非法时间区间用 `enabled: !invalidRange` 禁发请求，UI 静态错误（M3 语义保留）。
   **SchedulesPage 待迁**（下批，因其有测试且含分页语义）。
3. **C6 HostsPage 批量删除并发受控**（`pages/hosts/HostsPage.tsx`）：串行 `for...of` →
   5 并发 worker（与 DevicesPage 批量标签同模式），失败汇总而非逐条静默，最终 toast
   报"成功 X，失败 Y（前 3 项）"。
4. **C7 AddHostModal 提交中禁止关闭**（`pages/hosts/components/AddHostModal.tsx`）：
   X / overlay 路径统一 `if (!isSubmitting) onClose()`（与 AddDeviceModal 一致）；
   原"始终允许关闭"注释删除。**统一规则：提交中禁止所有关闭路径**。

## 放弃的备选

- C1 两页同批迁移：SchedulesPage 有测试 + 分页语义，拆批降低风险。
- C8 顺带补用户名前端校验：属 C4 表单统一轮，不在此批。
- C6 逐台 toast 失败详情：信息噪音大，改为汇总（失败列表前 3 项）。

## 如何验证

- `eslint` + `tsc --noEmit` 全过。
- vitest：`HostsPage.test.tsx` 13 用例全过（覆盖 AddHostModal / 批量操作相关）。
- AuditLogPage 无既有测试（纯数据层改写，三态断言靠人工核对）。

## 何时重议

- C1 的 SchedulesPage 迁移、C2/C3/C4/C5 属下一批（规范表先行再铺开）。
- 若统一"提交中禁止关闭"影响错误重试场景（提交失败后用户想关窗改数据），重议 C7。
