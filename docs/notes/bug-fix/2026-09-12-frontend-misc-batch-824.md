# Agent Note: 前端杂项批修复（#824）

Status: implemented
Class: bug-fix
Issue: #824

## Decision

1. **useConfirm**：新 confirm 打开前将未决 Promise resolve(false)，避免 resolveRef 覆盖导致永久挂起。
2. **EditProjectDialog**：改名时先 `onSubmit` 其它字段，再 `onRename`。
3. **NotificationsPage**：单条标已读失败 catch + toast。
4. **AuditLogPage**：`datetime-local` 经 `datetimeLocalInputToIso` 转 UTC ISO 再发 API。
5. **formatDurationSeconds**：`precise` 且 h>0 时保留秒（3723s → `1h 2m 3s`）。
6. **CaseEditDialog + create_case**：新建不传 ordinal；后端 `ordinal is None` 时取 max+1。

LiveConsole 回放竞态为 issue 疑似项，本单未纳入。

## Alternatives

- useConfirm 队列化多弹窗：过重，当前 UI 无并发 confirm 产品需求。
- 审计过滤改后端接受 naive+时区参数：前端本地→UTC 转换更小 diff。

## Verification

- `npm run test -- useConfirm.test.tsx format.test.ts`
- `pytest backend/tests/api/test_suites.py -k ordinal`（若有对应用例则跑全文件）
- `python scripts/run_gates.py check:quick`

## Revisit

- `TestCaseIn.ordinal` 更新路径仍要求显式 ordinal；编辑流已满足。
