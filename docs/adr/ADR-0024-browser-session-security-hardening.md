# ADR-0024: 浏览器 Web 会话安全化

- 状态：Accepted
- 优先级：P0
- 目标里程碑：M3.2
- 日期：2026-05-21
- 决策者：平台研发组
- 标签：认证, Web 安全, CSRF, refresh token, 可观测

## 背景

ADR-0019 之前的认证假设是 **Bearer token + localStorage**:前端登录拿 access/refresh,塞 localStorage,所有 HTTP 调用走 `Authorization: Bearer`。这套模式在 2026-05 暴露了三个互相牵连的缺口:

1. **XSS 即 token 泄露**。localStorage 任何同源脚本可读,只要前端有一个未转义 sink 就能把会话整包外送。本项目前端有富文本编辑器(`PipelineEditor` / `PlanLifecycleEditor`)和大量 markdown 渲染面板(Report / JIRA Draft),供应链 + 用户输入双重攻击面持续扩张。

2. **登出无实质效果**。`POST /api/v1/auth/logout` 之前只是前端清 localStorage,服务端不感知。refresh token 30 天有效期内,只要任何一个副本(浏览器 backup / 备份 / 调试拷贝 / XSS 偷走的拷贝)还在,就能继续换 access。无法响应"管理员封号 / 用户改密 / 单点退出"等安全事件。

3. **缺少观测**。没有任何指标能告诉运维"上一小时有多少跨站请求被拦",所有 4xx 都被 nginx 吞了。攻击面和误配置面都是黑盒。

CLAUDE.md / ADR-0001 / ADR-0011 都未明确认证侧的安全基线;此次收口需要一份显式 ADR 把决策、影响面、回滚路径锁死。

## 决策

把浏览器会话从 **localStorage Bearer** 切到 **HttpOnly Cookie + CSRF 双层防御 + 服务端可吊销**,并补上观测:

### 1. 会话载体:HttpOnly Cookie(`f26eb43`)

- `POST /api/v1/auth/login` 不再返回 token 给前端,而是 `Set-Cookie: stp_access_token=...; HttpOnly; SameSite=lax`(以及对应的 `stp_refresh_token`),前端代码完全摸不到 token。
- 保留 `POST /api/v1/auth/token` 走老路径返回 Bearer,**仅用于 Swagger / 脚本 / CI**,前端走 cookie 路径。
- `get_current_user` 改双模式:优先读 Authorization header,缺失时回退 cookie。Agent / server-to-server 不受影响。
- 生产强校验:`ENV=production` 启动时 `validate_production_auth_cookie_settings()` 拒绝 `AUTH_COOKIE_SECURE!=1` 与 `SAMESITE=none`,启动期硬失败而不是上线后被发现。

### 2. CSRF 防御:Origin/Referer 同源白名单中间件(`6caae14`)

`SameSite=lax` 防住跨站表单提交,但对 same-site 跨子域 / 富文本编辑器自动请求 / 旧浏览器无效。在 `/api/v1/*` 写操作前置 `CSRFOriginMiddleware`:

```
SAFE_METHODS → 放行
Authorization: Bearer → 放行(浏览器不会跨站自动重放)
X-Agent-Secret → 放行(server-to-server)
Origin in allowed_origins → 放行(严格 string match,不做子域 relax)
无 Origin 时,Referer normalized 在白名单 → 放行
否则 → 403
```

白名单复用 `get_cors_allowed_origins()`,**单一事实源**避免双维护漂移。中间件 LIFO 顺序固定为 `CORS(外) → RateLimit → CSRF(内)`,这样 4xx 也带 CORS 头(便于前端读 error.message),CSRF 又最贴近路由,中间件链上其他改动不会绕过。

降级开关 `STP_CSRF_ENABLED=0` 仅供排障;production guard 拒绝该降级与 cookie 强校验一起在 lifespan 启动期检测。

### 3. Refresh token 服务端可吊销:jti + PG 黑名单(`1388432`)

- `create_access_token` / `create_refresh_token` 注入 `jti = uuid4().hex`。
- 新表 `revoked_refresh_token(jti PK, revoked_at, expires_at, reason)` + `idx_revoked_refresh_token_expires_at`。
- `POST /auth/logout` 解出 cookie/body 里的 refresh,写黑名单(`ON CONFLICT DO NOTHING + RETURNING jti` 判断真实插入,幂等);无 token / 解码失败一律返回 200,不暴露内部状态给探测。
- `POST /auth/refresh` 解出 jti 后查黑名单,命中 401。
- APScheduler `revoked_token_cleanup` 每日清 expired 行,避免无限增长。
- **Grace 兼容**:本次提交之前签发的 refresh 没有 jti。refresh 路由对缺 jti 的 token 写 WARN 日志后放行,30 天后所有旧 token 自然过期,届时收紧分支为 401。日历提醒 **2026-06-21**。

### 4. 可观测性:CSRF 拒绝按 reason 分类(`a6a633d`)

`stability_csrf_rejected_total{reason}` Counter,reason 三档:

- `origin_not_allowed` — 有 Origin 但不在白名单(典型 CSRF 攻击征兆)
- `referer_not_allowed` — 无 Origin,Referer 也不可信(浏览器降级 / 隐私模式)
- `missing_origin_and_referer` — 两者都缺(curl / 脚本 / 探测)

放行路径不计数,只统计实际被拒。Prometheus 看板可直接 split 三类趋势,误配置(`referer_not_allowed` 突增)和攻击(`origin_not_allowed` 突增)可区分对待。

## 备选方案与权衡

| 维度 | 决策 | 备选 | 否决理由 |
|---|---|---|---|
| 会话载体 | HttpOnly Cookie | localStorage Bearer 保持 | XSS 即泄露,无法接受 |
| 会话载体 | HttpOnly Cookie | sessionStorage Bearer | 同样可读;且关闭 tab 即丢失,UX 倒退 |
| CSRF 防御 | Origin/Referer 同源白名单 | 双 cookie / CSRF token | 实现复杂、需要前端配合维护 hidden field;`SameSite=lax + Origin 白名单` 已能挡 99% 攻击面 |
| CSRF 防御 | 中间件统一拦截 | 每路由装饰器 | 易漏装(本项目 200+ 路由);中间件零侵入 |
| Refresh 黑名单存储 | PG 表 | Redis(SETEX) | PG 已是事实存储,事务一致(登出 + 写黑名单同事务),sync 路由零改动;Redis 需要把 auth 全改 async + 测试引 fakeredis |
| Refresh 黑名单存储 | PG 表 | in-memory dict | 单进程限定,水平扩展即失效 |
| 兼容策略 | 30 天 grace(无 jti 放行) | 立即强制 401 | 全员被踢一次,可见性中断,代价超过收益 |
| Metrics 维度 | reason 三档 | 单 counter 无 label | 攻击/误配置/探测无法区分;运维拿不出动作 |

## 影响

### API 行为变化

- `POST /api/v1/auth/login` 返回 `{"ok": true}` + Set-Cookie,**不再返回 token**。前端如有硬编码读 `response.access_token` 会立即破。
- `POST /api/v1/auth/logout` 增加 PG 写入;并发登出会被 `ON CONFLICT` 幂等吸收。
- `POST /api/v1/auth/refresh` 命中黑名单返回 401(WWW-Authenticate: Bearer);命中无 jti 兼容分支写 WARN 日志。
- 所有 `/api/v1/*` 非 SAFE 方法的 cookie 请求必须带 `Origin` 或 `Referer`,否则 403。Bearer 模式不受影响。

### 部署侧

- 生产 DB 必须 `alembic upgrade head` 应用 `f1a2b3c4d5e6`,否则 logout/refresh 5xx。
- 生产 `ENV=production` 必须满足:`AUTH_COOKIE_SECURE=1` / `AUTH_COOKIE_SAMESITE ∈ {lax, strict}` / `STP_CSRF_ENABLED ∈ {1, true, ...}`,否则启动期 `RuntimeError` 自杀。
- 新增 APScheduler job `revoked_token_cleanup`(默认每 24h),会出现在 `stability_apscheduler_job_*` 指标里。
- 新指标族 `stability_csrf_rejected_total{reason}`,建议在 Grafana / AlertManager 配:`rate(stability_csrf_rejected_total{reason="origin_not_allowed"}[5m]) > 10` 触发提醒。

### 灰度回退

- `STP_CSRF_ENABLED=0` 关 CSRF 中间件,production guard 同时禁用 → 需要在 `ENV` 非 production 时使用。
- HttpOnly Cookie 可通过 `AUTH_COOKIE_SECURE=0` 在非 HTTPS 调试,production guard 拒绝。
- 黑名单整体降级只能通过 revert `1388432`,DB 表保留即可。

### 兼容窗口

- **2026-05-21 ~ 2026-06-21**:无 jti 旧 refresh token 兼容期。期间日志中 `refresh_token_missing_jti` 是预期 WARN,不告警。
- **2026-06-21 之后**:把 `backend/api/routes/auth.py` 中 `else: logger.warning(...)` 分支替换为 `return _refresh_unauthorized("Invalid refresh token")`,grace 收口。

## 落地与后续动作

| 项 | 状态 | 关联 commit |
|---|---|---|
| 前端 QueryProvider 拆分 + 分层 staleTime + clearAppQueryCache | ✅ | `7193223` |
| HttpOnly Cookie 会话 + production guard | ✅ | `f26eb43` |
| CSRF Origin/Referer 中间件 + production guard | ✅ | `6caae14` |
| Refresh token 黑名单 + 每日清理 | ✅ | `1388432` |
| `.gitattributes` 收敛 EOL 噪声(扫除诊断噪声) | ✅ | `d5cc670` |
| `db_session` TRUNCATE RESTART IDENTITY 隔离(扫除假阳/假阴) | ✅ | `f000899` |
| `stability_csrf_rejected_total{reason}` 指标 | ✅ | `a6a633d` |
| 收紧无 jti grace 分支为 401 | ⏳ 2026-06-21 后 | — |
| 接入 Prometheus AlertManager 规则(`origin_not_allowed` 突增) | 待 ADR-0011 第二层 | — |

## 关联实现/文档

- `backend/core/csrf.py` — `CSRFOriginMiddleware`
- `backend/core/security.py` — cookie 工具 + `validate_production_auth_cookie_settings`
- `backend/api/routes/auth.py` — login / logout / refresh / me 双模式
- `backend/services/token_blacklist.py` — is_revoked / revoke / cleanup_expired
- `backend/models/token_blacklist.py` — RevokedRefreshToken
- `backend/alembic/versions/f1a2b3c4d5e6_add_revoked_refresh_token.py`
- `backend/scheduler/revoked_token_cleanup.py` + `app_scheduler.py:REVOKED_TOKEN_CLEANUP_INTERVAL`
- `backend/core/metrics.py:csrf_rejected_total`
- `frontend/src/components/QueryProvider.tsx` + `frontend/src/utils/api/client.ts` — cookie 模式 fetch
- `backend/tests/test_csrf_origin_middleware.py`(18 cases)
- `backend/tests/api/test_auth_cookie_session.py`(6 cases)
- `backend/tests/api/test_refresh_token_blacklist.py`(9 cases)
- `backend/tests/test_agent_secret_guards.py`(CSRF / cookie production guard)

依赖:无新增第三方库;Prometheus / passlib / PyJWT 均已在依赖树中。
