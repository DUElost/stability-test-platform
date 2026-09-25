# #3297 core 中间件迁出 api/middleware：C4 基线清零（6→0）+ C1 删 core.audit→models.audit

Status: implemented
Class: architecture

关联：[#3297](https://github.com/DUElost/stability-test-platform/issues/3297)（本单）、
[#3291](https://github.com/DUElost/stability-test-platform/pull/3291)（`.importlinter` 基线来源）、
[#3295](https://github.com/DUElost/stability-test-platform/pull/3336)（同波次的 C2 清零——本单不动那批 agent_* 文件）、
#281（生产类环境 secure cookie/SameSite/CSRF 护栏，AGENTS.md 硬不变量）、
#2778/#3017（审计守卫，本单同步其模块判据）。

## Decision

`backend/core` 回到「最底层、零 web 框架」定义，C4 的 6 条基线一次清零并按 issue
验收**删除该合约的 ignore_imports 段**（此后 core→fastapi/starlette 直接 import 恒红）：

- **csrf / request_metrics 整文件** `git mv` → `backend/api/middleware/`（保留 rename 历史）。
- **limiter 拆分**：`RateLimiter` / `RateLimitMiddleware` / `get_client_ip` /
  `get_rate_limit_info` 与限额常量 → `backend/api/middleware/limiter.py`；
  纯客户端 IP 解析（`resolve_client_ip` / `get_trusted_proxies` / `_parse_networks` /
  `DEFAULT_TRUSTED_PROXIES` / `UNKNOWN_CLIENT`）**留在 `backend/core/limiter.py`**。
  留在 core 的原因是分层而非习惯：审计写入（下沉后在 services 层）与限流两侧都要用
  它，services→api 是 C1 上引。XFF 可信代理解析语义逐字未动。
- **audit 拆分**：`backend/core/audit.py` 只留零依赖词表与归并函数
  （`AUDIT_RESOURCE_TYPE(S/ALIASES)`、`canonical/expand_*`）；`record_audit` /
  `record_audit_async` / `_audit_client_ip` → 新模块
  `backend/services/audit_writer.py`——这正是 C1 里 S4 基线注释登记的出口
  （「审计记录写入下沉到 services 或 models 侧」），因此 C1 的
  `core.audit → models.audit` ignore 行同 PR 删除。
  audit_writer 对 request 只做**结构化取用**（读 `.client.host` / `.headers`，
  类型标 `Any`），不 import fastapi/starlette：C4 消除的是 web 框架依赖，此判据满足。
  为什么不把 Request 处理拆到 api 层：`record_audit` 的调用方横跨
  api.routes / services / scheduler / scripts（实测 ~117 个调用点），services 与
  scheduler 够不着 api 层；把 request→ip 提取单独放 api 会让这些调用点无处安放，
  故写入与其提取逻辑同放 services。93 处 `request=request` 传参形态保持不变。
- **security 拆分**：`set_auth_cookies` / `clear_auth_cookies`（唯二的 `Response` 操作）
  → 新模块 `backend/api/auth_cookies.py`；core/security.py 删 `Response` import，
  token/哈希/环境护栏/`extract_cookie_token` 等纯函数留 core。生产类环境启动
  fail-fast（`validate_production_auth_cookie_settings`）位置与语义不变。
- **main.py** 三个中间件 import 改指 `backend.api.middleware.*`；
  `endpoint_label` 随 request_metrics 迁移同步改点。
- **`.importlinter` 顺带收紧**（与「基线只删不增」同向）：
  C1 layers 第二层补入 `backend.api.middleware`；C2 forbidden 补入
  `backend.api.middleware` / `backend.api.auth_cookies` / `backend.api.error_handlers`
  ——堵住的正是本单新落点被 services 反向引用的可能；现基线 19 行不变。
- **守卫同步**：`test_audit_resource_type_guard.py` 与
  `test_audit_action_retention_guard_3017.py` 的模块识别从
  `endswith("core.audit")` 扩为 `endswith(("core.audit", "audit_writer"))`，
  自检 fixture 改用新导入路径（守卫的 #2879 设计本就要求「改名导入可识别」）；
  `test_rate_limiter.py` / `test_request_metrics_middleware.py`（含 patch 字符串）/
  `test_csrf_origin_middleware.py` 改指新模块。
  调用点计数下限 `_CALL_SITES_BASELINE=117` 不动——本单只改导入源，不减调用。

## Alternatives

- **record_audit 原地留 core、request 参数改 `Any`**：消掉 C4（fastapi import）但
  C1 的 `core.audit→models.audit` 仍无解——写入必须碰 ORM，grimp 连函数体内 import
  都计入（`.importlinter` 头注），懒 import 不算出口。被否。
- **request→ip 提取放 api 层、record_audit 收 `ip_address`**：需改 93 个调用点，且
  services/scheduler 侧的 4 个服务调用者会转而需要 api 层的提取器（C1 上引）或各自
  复制提取逻辑（发散）。被否。
- **limiter 整文件迁 api（含 resolve_client_ip）**：audit_writer（services）够不着
  api，要么 services→api 违反 C1，要么在 services 复制 XFF 可信代理解析——后者正是
  #281 CR Major 修的伪造面，不能有两套。被否，故按「中间件走、网络原语留」切。
- **把 RateLimiter 纯类留 core、只迁中间件**：RateLimiter 仅被中间件与测试使用，
  没有 core 侧消费者；留 core 只是把「HTTP 配额语义」藏在最底层。被否。

## Verification

- `lint-imports`：C1 KEPT(1 ignored，仅剩 S3)、C4 KEPT **无 ignored**、C2 19 行不变、
  C3/C5 不动——merge 最新 main（a2c4c95e）后复跑仍绿。
- `ruff check backend/ tests/` 全绿；`run_gates check:quick` 通过（tool-manifest、
  god-files 等全绿；曾红一次系本地 ref 落后 main 的基线假象，merge 后消失）。
- 套件（testcontainers PG 隔离库，套 6G cgroup 硬顶）：
  `backend/tests/api` 1311 passed（含 `test_auth_cookie_session` 等 cookie/CSRF
  行为不变守卫）、`backend/tests/services`+`scheduler` 1501 passed、
  `backend/tests` 其余 902 passed、根 `tests/` 1843 passed/18 skipped；
  merge 后定向复跑守卫+行为套件 185 passed；merge 后全量 `backend/tests` 复跑见 PR。
- 行为不变的结构性保证：三个中间件与 cookie 写入是**同代码换址**（git mv / 原样
  搬运），`record_audit` 的日志串、降级分支、`strict` 语义逐字未动。
- CI required checks：**pending**（合入前以 FIFO auto-merge 结果为准）。

## Revisit

- `resolve_client_ip` 现住 `core/limiter.py` 但已无「limiter」内容——若后续再有
  消费者，可考虑改名 `core/client_ip.py`（一次 `git mv` + sed，本单不为改名扩大 diff）。
- C2 剩余 19 行（agent 域之外的 plan_run_*/project_* 等）沿 #3295 的领域异常路径
  分批清；`audit_writer` 的结构化 request 取用会随那批收口自然显形。
- 若 socketio 侧（`realtime`）未来要写审计：realtime 在 services 之下，import
  `services.audit_writer` 会是 C1 上引——届时按 #3293 端口注入先例办，不要下沉
  audit_writer 到 models 层凑层次。
