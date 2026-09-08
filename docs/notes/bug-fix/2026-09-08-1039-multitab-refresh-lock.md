# #1039 修复：refresh 跨标签锁 + 刷新失败探活，消除多标签 rotation 无辜登出

Status: implemented
Class: bug-fix

## Decision

#1016（D4 消费即吊销 rotation）落地后，同会话多标签在 access token 过期边界并发
refresh 时，输家拿到 401（旧 jti 已吊销）即被前端强制登出，其 401 响应的
`clear_auth_cookies` 还可能覆盖赢家刚写入的新 cookie，把无辜登出扩大为全会话。
修复全部在前端，后端会话语义零改动：

1. **跨标签 refresh 互斥**（`frontend/src/utils/auth.ts`）：`refreshAccessToken`
   经 Web Locks（`navigator.locks`，锁名 `stp:auth:refresh`）串行化——后到标签
   入队等待，拿到锁时 cookie jar 已被先到的标签更新，其 refresh 直接成功。
   单标签内 `_refreshInFlight` 防抖语义不变（跨标签锁之上仍合一并发调用）。
   `navigator.locks` 不可用（旧浏览器）退回原单标签防抖行为。
2. **刷新失败先探活再登出**（`frontend/src/utils/api/client.ts`）：401 拦截器在
   refresh 失败后、全局登出前，用裸 axios 探活 `/auth/me`（不经拦截器，防递归）；
   会话仍有效（赢家新 cookie 在 jar）则置位 `__probeRetry` 后重放原请求，探活
   也失败才执行 `_authFailureHandler`。`__probeRetry` 保证最多一次探活重放，
   有限次尝试；`shouldSkipRefresh` 覆盖的 auth 端点不探活；`/login` 路径维持
   原有早退（防死循环注释不变）。

## Alternatives

- **服务端 rotation 短复用宽限**（issue 候选 1）——放弃：宽限即重放窗口回归，
  动摇 #901 的「刷新后旧 refresh 立即失效」验收，属方向级安全语义变更、需设计
  note/ADR 裁决；跨标签锁在不触碰安全语义的前提下完整消除竞态，无需此代价。
- **输家 401 不清 cookie**（issue 候选 2）——放弃：锁生效后已不存在输家请求，
  现代浏览器下无收益；保留后端 `_refresh_unauthorized` 现状，PR 零后端 diff。
- **仅探活、不加锁**——放弃：探活是条件缓解（赢家 Set-Cookie 已落地的时序才
  有效），锁才是消除竞态的机制，两者互补而非互替。

## Verification

- 目标回归新增 6 用例：探活成功重放原请求且不登出、refresh+探活双失败才登出、
  重放再 401 有限次尝试不循环、`shouldSkipRefresh` 端点跳过探活、持锁未放不
  POST、锁上仍保留单标签防抖——`vitest run src/utils/api.test.ts
  src/utils/client_strict.test.ts src/utils/api/client.test.ts` **27 passed**；
- 全量前端 `vitest run` **89 files / 658 passed**；`npm run build`（tsc + vite）
  通过；`eslint src` 通过；
- `python scripts/run_gates.py check:quick` 全绿（见 PR 检查）。

## Revisit

- 不支持 Web Locks 的旧浏览器（Chrome <69 / Safari <15.4）仍走历史行为，存在
  残余竞态窗口；内网工具链浏览器受控，暂不为其引入服务端宽限。若实测出现该
  群体，再议「rotation 短宽限 + 输家不清 cookie」组合（需设计 note 修订）；
- 探活为真实请求：会话真死的路径多一次 `/auth/me` 401，量级可忽略；
- useSocketIO 的 refresh 复用同函数（`@/utils/auth` mock 的既有测试不受影响），
  自动重连与 API 401 恢复天然共用同一把锁。
