# Access Cookie 过期后 Socket.IO 触发有界刷新（#1119）

Status: implemented
Class: bug-fix

## Decision

`connect_error` 将 `Authentication required` 与 `Invalid token` 一并视为可恢复，
调用 `refreshAccessToken` 后重连；用 `_authRecoveryAttempts`（上限 2）约束，
刷新失败或超限进入 `error`，`Origin not allowed` 等不可恢复错误不刷新。
服务端缺 token 仍拒绝，仅注释说明消息语义，不放宽认证。

涉及：`frontend/src/hooks/useSocketIO.ts`、`backend/realtime/socketio_server.py`；
测试见 `useSocketIO.test.ts`。

## Alternatives

- 仅改服务端消息文案：客户端仍只认 `Invalid token`，不解决缺 cookie。
- 无限刷新：Socket.IO 无限重连会打爆 refresh。
- 放宽服务端匿名握手：明确禁止。

## Verification

- `vitest --run src/hooks/useSocketIO.test.ts`
- 覆盖 Authentication required 恢复 / 刷新失败有界 / Origin 不刷新

## Revisit

若握手错误改为结构化 `data.code`，客户端可改为码表而非文案匹配。
