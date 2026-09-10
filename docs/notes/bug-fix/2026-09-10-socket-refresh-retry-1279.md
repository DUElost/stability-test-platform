# useSocketIO：刷新失败后有界重连 + 网络恢复即重连（#1279）

Status: implemented
Class: bug-fix

## Decision

`frontend/src/hooks/useSocketIO.ts`（#1119）在可恢复鉴权失败时先
`socket.disconnect()` 再刷新 cookie 会话；`disconnect()` 会取消 manager 的
`reconnectionAttempts: Infinity` 自动重连。原实现在
`refreshAccessToken()` 返回 `false` 时只上报 `error`、不再 `connect()`——
而 `utils/auth.ts` 的 refresh 捕获所有异常，**瞬时网络故障同样走 `false` 分支**，
于是实时更新停到整页刷新；`_authRecoveryAttempts` 也只在 connect 成功时归零，
预算耗尽后同样无自愈路径。

修复（两条互补路径，均保持有界）：

1. **有界指数退避重连**：刷新失败后 `_scheduleAuthRetry(socket)`——3s/6s/12s/24s
   共 4 次，每次重连前把 `_authRecoveryAttempts` 归零以允许重新走 cookie 刷新；
   `connect` 成功或 `disconnectDashSocket()`（登出）时取消并清零。
2. **网络恢复触发**：hook 挂载期间监听 `window` 的 `online` 事件 →
   `_retryDashConnectNow()` 立即 `connect()` 并重置重试预算，覆盖「退避预算
   已耗尽之后网络才恢复」的情形；unmount 时移除监听（不泄漏）。

## Alternatives

- **恢复 `reconnection: true` 的自动重连（不 disconnect）**——放弃：#1119 的
  disconnect 是为了在刷新期间不吃掉 reconnection 配额、且避免带过期 token 的
  握手刷屏；保留 disconnect + 自己安排重连更可控。
- **无限重试**——放弃：会话真正失效时会持续重连/刷新（服务端刷屏）；指数退避
  4 次 + online 触发已覆盖瞬时故障，且换网络/恢复联网有可靠信号。
- **区分「网络失败」与「会话失效」**——放弃：`refreshAccessToken()` 吞掉所有
  异常，返回值不含原因；改其契约属跨模块面，超出本单范围（见 Revisit）。

## Verification

- `npx vitest run src/hooks/useSocketIO.test.ts` → **8 passed**，新增：
  - `schedules a bounded reconnect when the refresh fails (#1279)`
  - `reconnects immediately when the browser goes back online (#1279)`
- 反事实：把 `useSocketIO.ts` 还原为 origin/main 后两条新用例**均失败**；恢复后全绿。
- `npx tsc --noEmit` 通过；`python scripts/run_gates.py check:quick` → **[OK]（7 gates）**。

## Revisit

若 `refreshAccessToken` 将来返回失败原因（网络 vs 会话失效），可把「网络类失败」
交给退避重连、「会话失效」直接 `disconnectDashSocket()` + 引导登录；届时本条
的重试上限可与 `_AUTH_RECOVERY_MAX` 合并为单一预算模型。
