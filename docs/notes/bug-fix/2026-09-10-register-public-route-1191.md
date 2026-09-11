# 公开注册页冷启动不再被 401 拦截送回登录页（#1191，R12-F04）

Status: implemented
Class: bug-fix

## Decision

`/register` 与 `/login` 同属公开路由（`PublicRoute`：会话探测失败即渲染 Outlet），
但 401 拦截器把终端 401 一律当作会话失效执行全局登出副作用
（`clearAppQueryCache` + `disconnectDashSocket` + `window.location.href = '/login'`），
且只豁免 `pathname === '/login'`。于是未登录冷启动/刷新 `/register` 时，
`useAuthSession` 的 `/auth/me` 探活 401 → 被送回登录页，无法注册。修正：

- 新增 `AUTH_PUBLIC_PATHS = ['/login', '/register']` + `isAuthPublicPath()`（尾斜杠
  归一），拦截器对公开认证页跳过全局登出副作用（与 /login 既有豁免合并为同一判断）；
- `shouldSkipRefresh` 增补 `/auth/register`：公开注册端点的 401 不尝试 refresh/探活，
  错误直接交还表单展示（与 login/token/refresh/logout 同口径）；
- 受保护路由行为不变：终端 401 仍执行全局登出跳转（既有用例覆盖）。

涉及：`frontend/src/utils/api/client.ts` + `api.test.ts`；`PublicRoute`
（`router/index.tsx`）本身语义正确，未改动。

## Alternatives

- 只豁免 `/auth/me`：探活只发生在拦截器内部；公开页上任意接口的终端 401 都会触发
  全局登出，同族问题仍在（且 `/auth/me` 不是唯一探测源）。
- 在 `useAuthSession` 里改用裸 axios 探测：绕过拦截器可解注册页，但会话探测是全站
  行为，改动面更大且丢失去重/刷新链路。
- 公开页禁用全部 401 处理（连 refresh 也跳过）：登录态过期用户打开 /login 时无法
  借 refresh 恢复会话——保持现状（先 refresh/探活，失败后才走页面豁免）。

## Verification

- `npx vitest run src/utils/api.test.ts`：新增「/register 终端 401 不触发
  auth-failure handler」与「/auth/register 跳过 refresh + 探活」两用例；既有
  「/login 豁免」「受保护路由仍登出」用例保持通过
- `npm run type-check`、`eslint src` 通过

## Revisit

若未来公开路由增多（如 /forgot-password），改为从路由配置派生公开路径清单，避免在
拦截器硬编码数组。
