# Socket.IO 重订阅误用 Map value（#1112）

Status: implemented  
Class: bug-fix

## Decision

`useSocketIO` 的 `connect` 重订阅改为 `for (const room of _activeRooms.keys())`，
不再 `Map.forEach(room => …)`（首参是引用计数）。后端
`DashboardNamespace.on_subscribe` 对非字符串 `room` 直接拒绝并打日志，避免
`_ROOM_PATTERN.fullmatch` TypeError；`on_unsubscribe` 同样要求 str。

涉及：`frontend/src/hooks/useSocketIO.ts`、`backend/realtime/socketio_server.py`；
测试见 `useSocketIO.test.ts`、`test_dashboard_subscribe.py`。

## Alternatives

- 仅改前端：后端仍可能被异常客户端打崩。
- 仅改后端：重连仍订阅错误房间，功能仍坏。
- 改用 `Array.from(_activeRooms.keys())`：等价，`for…of keys` 更直观。

## Verification

- `vitest --run src/hooks/useSocketIO.test.ts`
- `pytest backend/tests/realtime/test_dashboard_subscribe.py -q`
- `ruff check` on touched Python files

## Revisit

若后续引入非 Map 房间表，保持「迭代房间名」不变量即可。
