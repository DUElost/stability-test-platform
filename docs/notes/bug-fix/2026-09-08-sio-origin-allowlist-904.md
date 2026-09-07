# Socket.IO Origin 白名单：引擎 CORS 对齐 + 服务端强制校验

Status: implemented
Class: bug-fix

## Decision

#904（R02-F05）：`create_sio_server` 配置 `cors_allowed_origins="*"`，而
dashboard 握手接受 Cookie 自动认证；FastAPI 的 CORS/CSRF 中间件挂在
`_fastapi_app`，不覆盖 ASGI 外层 Socket.IO 通道——same-site 不同 origin 页面
可借受害者会话连接读取实时数据。两层修复：

1. **引擎层对齐**：`cors_allowed_origins="*"` → `get_cors_allowed_origins()`
   （`backend/core/cors.py` 同源配置：显式来源、拒绝通配符、credentials=True）
   ——与 FastAPI CORSMiddleware 一处配置、一处校验。
2. **服务端强制**：dashboard `on_connect` 开头经 `_origin_allowed(environ)`
   校验 `HTTP_ORIGIN`——外来 Origin 在认证前拒绝（`Origin not allowed`）。
   必要性：engineio 的 CORS 只生成响应头、由浏览器执行，非浏览器客户端可
   无视；而 Cookie 自动附带握手必带 Origin，故应用层校验才构成服务端边界。
   缺 Origin（脚本/测试携 token）不在此拦，交由既有认证分支（#281 P0
   匿名规则 + R02-D3 token 校验面）。

`/agent` namespace 不加应用层 Origin 校验：agent_secret 是显式凭据（非浏览器
自动附带），same-site 页面无法伪造；引擎层 CORS 收紧已覆盖其浏览器侧握手。

**部署面注意**：dashboard 页面 origin 必须在 `CORS_ORIGINS` 内——与 REST API
同一配置且 Socket.IO 与 REST 同 ASGI 部署（页面能访问 API 即已在白名单），
现网无新增配置要求；`CORS_ORIGINS` 配置非法（通配符/空）时启动期 fail-fast，
与 FastAPI 侧行为一致。

## Alternatives

- 仅靠 engineio `cors_allowed_origins=list`：不够——CORS 头由浏览器执行，
  非浏览器客户端可绕过，验收「外来 Origin + Cookie 握手被拒绝」要求服务端
  拒绝，必须应用层校验。
- `/agent` 也加 Origin 强制：放弃——agent_secret 为显式凭据，Cookie 自动
  附带攻击面不存在；加校验反而可能误伤无 Origin 的合法 Agent 客户端。
- CSRF 中间件扩展覆盖 Socket.IO 通道：放弃——CSRF guard 的写操作语义与
  连接建立不同，Origin 白名单在 on_connect 更精准；跨 ASGI 挂载改动违反
  硬不变量（入口形态锁定）。

## Verification

- `python -m pytest backend/tests/realtime/test_dashboard_auth.py`：13 passed
  （存量 10 + 新增 3：外来 Origin+有效 token 拒绝 / 白名单 Origin+Cookie
  放行 / 无 Origin 走既有 token 路径）；
- ruff 干净；存量测试无 `cors_allowed_origins="*"` 依赖（grep 零命中）；
- Registry：fix-904-sio-origin-allowlist 全程登记（--issue 904）。

## Revisit

- #46（生产 HTTPS）落地后：`CORS_ORIGINS` 生产值复核（https origin），
  本修复的白名单机制不变；
- 实际浏览器跨站验证（issue 建议第 3 条）不在本单范围，待真机窗口。
