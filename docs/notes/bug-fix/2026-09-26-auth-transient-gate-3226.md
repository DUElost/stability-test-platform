# 会话探活的瞬时故障不再被路由守卫判为未登录（#3226）

Status: implemented
Class: bug-fix

日期：2026-09-26 ｜ 归属：前端 / 认证与门控 ｜ 类型：bug-fix

## Decision

`ProtectedRoute` / `AdminRoute` 各自独立发起 `/auth/me`，却只看 `isSuccess`：

```tsx
return sessionQ.isSuccess ? <Outlet /> : <Navigate to="/login" ... />;
```

`isSuccess === false` 同时覆盖「确实没登录」与「探活失败了」两种相反的事实；加上
`useAuthSession` 的 `retry` 关闭，一次接口抖动就会把**会话仍然有效**的用户弹回登录页并要求重输口令。

做法：

1. 抽出纯函数 `router/authGate.ts::resolveAuthGate()`，把会话查询映射为四态
   `loading | transient | anonymous | authorized`，**判据直接复用仓内既有的
   `classifyAuthFailure()`**（`utils/auth.ts:26-38`，#703/#1039 已确立的"超时/5xx/断网 ≠ 未授权"三分）；
2. 两条路由门都改为消费该函数；`transient` 渲染 `AuthGateTransient`
   （复用 `InlineError` + 「重试」，文案明示"**会话未失效**"），不跳转、不丢深链；
3. `useAuthSession` 的重试由"完全不重试"改为 **1 次**：真 401 的终态语义不变
   （`classifyAuthFailure` 仍按 status 判），只是不再让单次抖动即定论。

## Alternatives

- **只在 `client.ts` 拦截器里修**：否。#703 当年修的就是拦截器那条路，本单的成因恰恰是
  **守卫另起的一次独立探活**没走拦截器——这也是为什么判据已在、却没生效。
- **给守卫加 `if (sessionQ.isError)` 就地写分支**（不抽函数）：否。会留下第三种形状，
  且这条判据可被真值表穷举（5xx/429/断网/超时/401/403），抽出来才能测全。
- **把 `retry` 提到 2~3 次**：否。抖动窗口里徒增后端负载；1 次足以跨过单请求尖峰，
  而持续故障本就该由 transient 门显式告诉用户，而不是靠静默重试硬扛。
- **`PublicRoute` 一并改**：**未做**。它在 `isSuccess` 时跳首页、否则渲染登录表单；探活失败时
  多显示一次登录页，**不产生错误事实**，改它属于扩大范围。已在 Revisit 登记。

## Verification

- 新增 `router/authGate.test.ts`：**22 passed**（本文件 8 条 + 既有 4 个 router 测试文件回归）：
  - 真值表：502 / 断网(无 response) / `ECONNABORTED` / 503·504·429 → `transient`；401·403 → `anonymous`；
    成功 → `authorized`；加载中 → `loading`；访客冷启动（未发请求）→ `anonymous`；
  - **结构性守卫**（沿用 #2360 `adminRoutes.test.ts` 的源码判据风格）：两条门都必须
    `resolveAuthGate`，且**不许再写回裸 `isSuccess ? … : <Navigate to="/login"`**。
    加这条的理由：本单根因是"判据只落了一半"，只测纯函数拦不住将来新增第三条门又自己写二分支。
- 一处**我自己的测试缺陷**在提交前被抓出并修掉：源码断言 `/retry:\s*false/` 被我新写的
  解释注释里的同名字面串命中（假红）。改的是注释措辞而不是放宽断言——放宽会把这个守卫 permanently 削弱。
- `tsc --noEmit` 与 `tsc --noEmit -p tsconfig.node.json` 均退出码 0；`eslint` 4 个改动文件 0；
  `run_gates.py check:quick` 17 gates 全过。
- **未做真实浏览器复验**：#3226 的原始运行时证据（注入 `/auth/me`=500/503/断网 → 落 `/login`，
  撤掉注入后 `/auth/me` 立刻 200）来自 dev 隔离栈；同码第二套栈 `stp-dev2` 已按承诺回收。
  本次改动本身是纯决策函数 + 分支选择，jsdom 层可断言且已断言。

## Revisit

- 若将来出现第三条路由门（例如 project-scoped 门），必须走 `resolveAuthGate`——结构守卫会红，
  但**只覆盖 `ProtectedRoute`/`AdminRoute` 两个函数名**；改名或新增别的名字需要扩那条测试。
- `PublicRoute` 的 transient 体验（已登录用户遇到抖动会看到一个登录页）留待有人真正反馈再处理。
- 与 **#3259**（对话框关闭后焦点不归位）同属"守卫/组件吃掉了失败信息"的族；#3259 的修法在共享
  `ui/dialog.tsx` 里接管 `onCloseAutoFocus`，与本单无耦合。
- 真 401 之外的登出路径（`registerAuthFailureHandler` 的 clear cache + 硬跳 `?next=`）未改；
  它现在的触发面比改动前更小（transient 不再误触发），值得在 #2959 这类容量事件复发时回看。
