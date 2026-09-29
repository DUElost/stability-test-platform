# 通知事件类型 CHAIN_INCOMPLETE 的前后端同步（#3458，批次 B2 / G7）

Status: implemented
Class: bug-fix

关联：[#3458](https://github.com/DUElost/stability-test-platform/issues/3458)、[#3497](https://github.com/DUElost/stability-test-platform/issues/3497)（批次 B2 规划方案 §3 G7 / §1 F5）。不关单，生效随整批控制面部署（§5）。

## Decision

F5 形态：后端 `EventType` 已含 `CHAIN_INCOMPLETE`（[`notification.py:21`](../../../backend/models/notification.py)），
前端 `AlertRule.event_type` 联合（[`types.ts:798`](../../../frontend/src/utils/api/types.ts)）与展示映射 `EVENT_LABELS`
（[`NotificationsPage.tsx:47`](../../../frontend/src/pages/notifications/NotificationsPage.tsx)）未同步——
规则表单下拉里选不到该事件，已有规则的卡片只能原样透出英文枚举，违反「前端类型与后端 schema 同步」的硬不变量。

按 #3497 §3 G7 修，守卫分三层：

1. `AlertRule.event_type` 联合补 `'CHAIN_INCOMPLETE'`；
2. `EVENT_LABELS` 补 `CHAIN_INCOMPLETE: '链断'`，注解由 `Record<string, string>` 改为
   `satisfies Record<AlertRule['event_type'], string>`——键集由编译器穷尽约束，后端枚举再新增而这里漏配时
   `npm run type-check` 判红；
3. `tests/test_frontend_api_types_sync.py` 增跨语言单向断言：`backend/models/notification.py` 的
   `EventType` 成员 ⊆ `types.ts` 的 `AlertRule.event_type` 联合（纯文本解析、离线）。Python 不解析页面实现，
   前端内部映射的穷尽性留给 TypeScript（v1.1 明确的分层）。

三层各自独立判红：跨语言事实（后端 → 契约）由 pytest 守卫；前端契约内部一致性由编译器；
用户可见行为（下拉真实包含）由 Vitest。

## Alternatives

- **Python 同时解析 `EVENT_LABELS` 校验键完备**：否。同一事实两个解析器会漂移；`satisfies` 是更强、
  零维护的穷尽检查，规划方案 v1.1 明确不让 Python 解析页面实现。
- **`EVENT_LABELS` 保留 `Record<string, string>`、只补键**：否。注解宽于实际键集时，下次枚举新增
  不会有任何信号——正是本轮缺陷的成因。
- **顺手删掉 `EVENT_LABELS[rule.event_type] || rule.event_type` 的兜底**：否。后端未来若先行下发
  新枚举值，运行时兜底仍应保留；与 #3458 无关，不顺手改。

## Verification

- `cd frontend && npx vitest run src/pages/notifications/NotificationsPage.test.tsx` → **42 passed**
  （41 既有 + 1 新增：表单下拉含「链断」选项且 `value === 'CHAIN_INCOMPLETE'`）。
- `cd frontend && npm run type-check` → 退出码 0（正常实现）。
- 变异自证（#3497 §4 G7 的三层）：
  1. 临时从 `types.ts` 联合删去 `CHAIN_INCOMPLETE` → `pytest tests/test_frontend_api_types_sync.py -q`
     判红：`AssertionError: ... EventType 成员未同步到前端 AlertRule.event_type：['CHAIN_INCOMPLETE']`（exit 1）；
     恢复后 3 passed。
  2. 保留联合、临时从 `EVENT_LABELS` 删去 `CHAIN_INCOMPLETE: '链断'` → `npm run type-check` 判红：
     `TS1360 ... Property 'CHAIN_INCOMPLETE' is missing`（exit 2）；恢复后退出码 0。
  3. 正常实现下 Vitest 下拉断言绿（见上）。
- 通用门禁（#3497 §4）：`lint --max-warnings 0` / `type-check` / Vitest / `knip` / `check:quick` /
  `pytest tests/ -q` —— 结果见 PR 正文。

## Revisit

- 本批只收 `EventType`（§1 F5）；其余后端枚举与 `types.ts` 的同步情况按 §8 登记为证据，不进入本批承诺。
- `satisfies` + 跨语言单向守卫的组合可作为后续「后端枚举 → 前端映射」的参照；若再出现同类映射，
  按本单做法收敛，不预建通用抽象。
- 生效随整批控制面部署（前端换包）后按 §5 关单；本 PR 不部署、不操作队列。
