# 稳定性测试平台 — 生产就绪 / 上线前全面评估报告

> 生成日期：2026-05-23  
> 审查方式：仓库只读分析 + ADR/CI/部署配置核实  
> 关联：ADR-0024、ADR-0021、ADR-0020、ADR-0018、ADR-0011

**审查范围**：`F:/stability-test-platform` 全仓库只读分析（ADR、CI、部署模板、指标、未提交改动）  
**依据来源**：子代理 transcript `b1de09af-7fa2-487e-ad69-83e1b22ee517` + 仓库现场核实

---

## 执行摘要（最高优先级 5 条）

1. **生产网络与实时通道未闭环（P0）**：Nginx 模板（`deploy/control-plane/nginx/stability-platform.conf`、`deploy/nginx/frontend-docker.conf`）仅有 `/api/` 与 legacy `/ws/`，**缺少 `/socket.io/` 反代**；前端 SocketIO 默认连 `http(s)://<host>:8000`（`frontend/src/config/index.ts`），与 Nginx 80/443 同源架构冲突 → PlanRun 实时推送、Dashboard SocketIO 在生产极可能失效。**工作量：S**

2. **ADR-0024 后端已落地，生产 env 模板与部署文档滞后（P0）**：`validate_production_auth_cookie_settings()`（`backend/core/security.py`）要求 `ENV=production` + `AUTH_COOKIE_SECURE=1` + CSRF 开启，但 `deploy/control-plane/env/.env.backend.example` **未包含**上述变量；`docs/production-minimum-deployment-checklist.md` 仍描述过时状态。**工作量：S**

3. **大量敏感读 API 仍无鉴权（P0 公网 / P1 纯内网）**：`plan-runs/*` 聚合端点、`plans` GET、`/runs/*/report*`、`/stats/*`、`/schedules` GET、`/metrics` 等均可匿名访问。内网 MVP 可勉强接受，**公网或跨 VLAN 上线前必须补齐或网络隔离**。**工作量：L**

4. **未提交的登录页死循环修复必须纳入发布（P0）**：`frontend/src/utils/api/client.ts` 与 `frontend/src/utils/auth.ts` 修复了 Cookie 模式下 `/login` 页「校验登录状态中…」无限刷新（与 ADR-0024 直接相关），**当前仅工作区修改、未 commit**。**工作量：S**

5. **可观测「第二层」未落地（P1）**：Prometheus 指标 + Grafana 模板已有（ADR-0011 第一层 ✅），但 **AlertManager 规则、Loki、部署后 30 分钟观测 runbook 执行** 仍为 ⬜（`docs/adr/ADR-0011-observability-and-alerting-evolution.md`）。**工作量：M**

---

## P0 — 上线阻塞（必须做）

| # | 动作 | 依据 | 工作量 | 部分实现 |
|---|------|------|--------|----------|
| 1 | **Nginx 增加 `/socket.io/` WebSocket 反代**；构建时设 `VITE_API_BASE_URL=` 空或同源，使 SocketIO 走 443/80 而非 :8000 | `frontend/src/hooks/useSocketIO.ts:108-109` 连 `${API_BASE_URL}/dashboard`；`deploy/control-plane/nginx/stability-platform.conf` 无 socket.io | **S** | `/api/` 反代已有 |
| 2 | **HTTPS + 生产 env 全套**（`ENV=production`、`AUTH_COOKIE_SECURE=1`、`AUTH_COOKIE_SAMESITE=lax\|strict`、`STP_CSRF_ENABLED=1`） | ADR-0024；`backend/main.py:87` 启动 guard；`backend/tests/test_agent_secret_guards.py` | **M** | guard 代码 ✅ |
| 3 | **生产 DB 执行 `alembic upgrade head` → `f1a2b3c4d5e6`**（`revoked_refresh_token` 表） | ADR-0024；缺表则 logout/refresh **5xx** | **S** | migration 已存在 |
| 4 | **Commit 并发布 `client.ts` / `auth.ts` 登录死循环修复** | git diff 未提交；修复 `/login` 时跳过 `clearAppQueryCache` + redirect | **S** | 修复已完成 |
| 5 | **强密钥与非 placeholder 配置**：`JWT_SECRET_KEY`、`AGENT_SECRET`、`CORS_ORIGINS` 精确匹配前端 Origin | `backend/main.py` lifespan 校验 | **S** | 有 guard 测试 |
| 6 | **单实例 backend 约束**写入运维规程（禁止多 worker 无改造扩容） | APScheduler + 内存 RateLimit 进程内（`backend/scheduler/app_scheduler.py`） | **S** | 文档有提及 |
| 7 | **（公网）补齐读 API 鉴权或 Nginx IP 白名单** | 见下方鉴权缺口表 | **L** | 写 API mostly ✅ |
| 8 | **（公网）关闭或门控 `POST /api/v1/auth/register`** | `backend/api/routes/auth.py:194` 任意注册 | **S** | — |
| 9 | **更新 `.env.backend.example` 与 `production-minimum-deployment-checklist.md`** 纳入 ADR-0024 变量 | 模板当前仅 `ENV=development`，缺 Cookie/CSRF | **S** | — |

### P0 补充：Nginx `/socket.io/` 建议配置片段

```nginx
location /socket.io/ {
    proxy_pass http://127.0.0.1:8000/socket.io/;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

### P0 补充：生产 env 必配（guard 会校验）

```env
ENV=production
AUTH_COOKIE_SECURE=1
AUTH_COOKIE_SAMESITE=lax
STP_CSRF_ENABLED=1
JWT_SECRET_KEY=<强随机>
AGENT_SECRET=<非 placeholder>
CORS_ORIGINS=https://<你的前端域名>
```

---

## P1 — 强烈建议（上线后短期内）

| # | 动作 | 依据 | 工作量 |
|---|------|------|--------|
| 1 | 落地 ADR-0011 **AlertManager 默认规则**（心跳超时、dispatch 失败率、SAQ 积压、CSRF `origin_not_allowed`、apscheduler error） | ADR-0011 §49-50；ADR-0024 推荐 `rate(stability_csrf_rejected_total{reason="origin_not_allowed"}[5m]) > 10` | **M** |
| 2 | 导入 Grafana dashboard + Prometheus scrape `/metrics` | `docs/grafana/stability-platform-dashboard.json` | **S** |
| 3 | **2026-06-21** 收紧 refresh 无 jti grace → 401 | ADR-0024；`auth.py:268` WARN 后放行 | **S** |
| 4 | PlanRunDetailPage **接通导出报告**（复用 `GET /runs/{job_id}/report/export`） | 后端已有；UI 仍 `toast.info('导出报告 — 功能开发中')`（`PlanRunDetailPage.tsx:236`） | **S** |
| 5 | PostgreSQL **自动化备份** + 恢复演练 | `docs/production-minimum-deployment-checklist.md` 仅一句 pg_dump | **M** |
| 6 | 前端 **admin 路由门控**（users / notifications / audit / script 写操作） | 后端 `require_admin` 有；前端无路由级拦截 | **S** |
| 7 | 预发布按 `docs/preprod-drill-runbook.md` 跑通 + **30min 观测窗口** | ADR-0011 第三层 | **M** |
| 8 | 同步更新部署文档（鉴权、SocketIO、调度模型） | 多处与 ADR-0024 不一致 | **S** |
| 9 | Vitest 覆盖 login 401 不循环 | 未提交 fix 无专项测试 | **S** |
| 10 | 评估 **STP_WATCHER_ENABLED** 生产开启条件 | ADR-0018 默认 `false`（`backend/agent/main.py`） | **M** |

---

## P2 — 优化与增强

| 项 | 说明 | 依据 | 工作量 |
|----|------|------|--------|
| SocketIO Redis adapter | 多 backend 实例 room 广播不分裂 | CLAUDE.md「水平扩展」；当前无 RedisManager | **L** |
| Loki 集中日志 | 替代/补充 `log_writer.py` 本地文件 | CLAUDE.md「下一步」 | **M** |
| 移除 legacy `/ws/` | Nginx `/ws/` 块 + deprecated stub 端点 | ADR-0018 supersede ADR-0009 | **S** |
| Gunicorn + 外置 scheduler | 若需多 worker，须外置 APScheduler/SAQ | 当前单 uvicorn 设计 | **L** |
| MapReduce / Settings | 占位页真实功能或从导航隐藏 | `MapReducePage.tsx`、`SettingsPage.tsx` | **S/M** |
| 分布式 rate limit | Redis 替代进程内 300 req/min/IP | `backend/core/limiter.py` | **M** |
| ADR-0021 文档状态 | 头标 Proposed vs 正文已实施 | `docs/adr/ADR-0021-*` | **S** |
| DeviceMetricSnapshot 清理 | stats 设备历史返回空 | `backend/api/routes/stats.py` deprecated | **S** |
| Watcher CATCHUP | `backend/agent/watcher/manager.py:409` TODO | 灰度功能完善 | **M** |

---

## 建议新增功能（按业务价值排序）

| 排序 | 功能 | 业务价值 | 工作量 |
|------|------|----------|--------|
| 1 | **告警与值班闭环**（AlertManager → 钉钉/邮件，关联 PlanRun/Host 深链） | 直接降低 MTTR | **M** |
| 2 | **PlanRun 级批量报告导出**（PDF/zip，含 Watcher 摘要） | 测试经理日常刚需 | **M** |
| 3 | **Watcher 生产化**（默认开启 + log_signal 告警规则联动） | 稳定性测试核心价值 | **L** |
| 4 | **细粒度 RBAC**（只读 / 执行 / 运维 / 管理员） | 多人协作 | **L** |
| 5 | **集中日志检索**（Loki + Job/PlanRun 关联查询） | 排障效率 | **M** |
| 6 | **DB 备份监控与一键恢复 runbook 脚本** | 合规与容灾 | **M** |
| 7 | **Agent 批量升级看板**（Ansible 状态回写 UI） | 运维规模化 | **L** |

---

## 上线检查清单

### 基础设施
- [ ] PostgreSQL 就绪，`cd backend && alembic upgrade head`（head=`f1a2b3c4d5e6`）
- [ ] Redis 就绪（SAQ broker，`REDIS_URL`）
- [ ] 单实例 backend（uvicorn），`STP_ENABLE_INPROCESS_SAQ` 按架构设定
- [ ] Nginx：静态前端 + `/api/` + **`/socket.io/`** + HTTPS
- [ ] 防火墙：仅暴露 443/80；**不对公网暴露 8000**

### 安全（ADR-0024）
- [ ] `ENV=production`
- [ ] `AUTH_COOKIE_SECURE=1` + HTTPS
- [ ] `AUTH_COOKIE_SAMESITE=lax` 或 `strict`
- [ ] `STP_CSRF_ENABLED=1`
- [ ] `JWT_SECRET_KEY`、`AGENT_SECRET` 非 placeholder
- [ ] `CORS_ORIGINS` 精确匹配前端 Origin（无 wildcard）
- [ ] 前端已包含 **login 死循环 fix**（commit `client.ts` / `auth.ts`）
- [ ] 公网：读 API 鉴权或网络隔离；关闭 open register

### Agent
- [ ] 每台 Agent 唯一 `HOST_ID`（≠0，与 DB `host.id` 一致）
- [ ] `AGENT_SECRET` 与 backend 一致
- [ ] `STP_SCRIPT_ROOT` / NFS 路径一致；跨机时配置 `STP_SCRIPT_RUNTIME_ROOT`
- [ ] WSL：`ANDROID_ADB_SERVER_PORT=5039`

### 功能验收
- [ ] 登录 → HttpOnly Cookie 会话 → 登出 → refresh 黑名单拒登
- [ ] Plan 执行 → 派发门禁（ADR-0021）→ PlanRun 详情**实时更新**（SocketIO `job_status` / `plan_run_status` / `watcher_signal`）
- [ ] 热更新：无 Job 直通；有 Job 409 → `HostHotUpdateConfirmDialog` abort 路径（ADR-0021 C6）
- [ ] Job 终态 → 设备 lease 释放
- [ ] `/metrics` 被 Prometheus scrape

### 可观测
- [ ] Grafana dashboard 导入（`docs/grafana/stability-platform-dashboard.json`）
- [ ] （P1）AlertManager 规则生效
- [ ] 部署后 30 分钟盯：`stability_apscheduler_job_runs_total{status="error"}`、CSRF 三档、`stability_saq_queue_depth`

### 备份与日历
- [ ] pg_dump 定时任务 + 保留策略
- [ ] 日历提醒：**2026-06-21** refresh jti grace 收口（ADR-0024）

---

## 风险与已知技术债表

| ID | 风险 / 技术债 | 严重度 | 依据 | 缓解 | 工作量 |
|----|--------------|--------|------|------|--------|
| R1 | 生产 SocketIO 不可用 | **高** | nginx 无 `/socket.io/`；`config/index.ts` 非 localhost 指向 `:8000` | P0 #1 | S |
| R2 | 敏感数据匿名可读 | **高**（公网） | plan-runs/plans/runs/stats/schedules GET 无 auth | P0 #7 或内网隔离 | L |
| R3 | Cookie Secure 无 HTTPS 导致启动失败或 Cookie 不生效 | **高** | ADR-0024 生产 guard | P0 #2 | M |
| R4 | 未提交 login fix 导致登录页卡死 | **中** | git diff `client.ts`/`auth.ts` | P0 #4 | S |
| R5 | 单点 backend，无 HA | **中** | APScheduler 进程内设计 | 接受 MVP；文档化 | S |
| R6 | 无告警，故障靠人工发现 | **中** | ADR-0011 L2 ⬜ | P1 #1 | M |
| R7 | 无自动备份 | **中** | 仅文档提及 pg_dump | P1 #5 | M |
| R8 | refresh grace 窗口（无 jti 旧 token 仍可 refresh） | **低** | 至 2026-06-21 | 日历收口 | S |
| R9 | Watcher 默认关，异常检测价值未释放 | **低** | `STP_WATCHER_ENABLED=false` | P1 #10 | M |
| R10 | 部署文档过时误导运维 | **低** | checklist 仍写旧鉴权/WS 模型 | P1 #8 | S |
| R11 | 开放注册任意建号 | **中**（公网） | `auth.py:194` | P0 #8 | S |
| R12 | `/metrics` 公开暴露内部指标 | **低-中** | `backend/api/routes/metrics.py` 无 auth | Nginx ACL | S |
| R13 | PlanRun 导出 UI 占位 | **低** | `PlanRunDetailPage.tsx:236` | P1 #4 | S |
| R14 | MapReduce / Settings 占位页 | **低** | 导航可见但功能未实现 | P2 隐藏或实现 | S |
| R15 | 前端无 admin 路由门控 | **低-中** | 非 admin 进 /users 会 API 403 | P1 #6 | S |
| R16 | legacy `/ws/` Nginx 块无效 | **低** | 主路径已 SocketIO | P2 清理 | S |
| R17 | Rate limit 进程内，多实例无效 | **低** | `backend/core/limiter.py` | P2 Redis | M |
| R18 | `DeviceMetricSnapshot` deprecated | **低** | stats 设备历史空 | P2 清理或替代 | S |

---

## 附录：分维度审查摘要（证据索引）

### 1. 安全与会话（ADR-0024）

| 项 | 状态 | 依据 |
|----|------|------|
| HttpOnly Cookie 登录 | ✅ | `backend/api/routes/auth.py`；前端 `withCredentials: true` |
| CSRF Origin/Referer 中间件 | ✅ | `backend/core/csrf.py`；18 cases `backend/tests/test_csrf_origin_middleware.py` |
| Refresh jti 黑名单 | ✅ | 表 `revoked_refresh_token`；migration `f1a2b3c4d5e6`；9 cases `test_refresh_token_blacklist.py` |
| 生产 guard | ✅ | `backend/main.py:87` + `validate_production_auth_cookie_settings()` |
| Grace 窗口（无 jti 旧 refresh） | ⏳ 至 **2026-06-21** | `auth.py:268` |
| CSRF 指标 | ✅ | `stability_csrf_rejected_total{reason}` |
| 前端 auth/client 改动 | ⚠️ **未提交** | 修复 `/login` 401 死循环 |

**鉴权缺口（读 API）**：

| 端点族 | 鉴权 | 风险 |
|--------|------|------|
| `GET /api/v1/plan-runs/**`（chain/timeline/events/devices/watcher/summary） | ❌ | 泄露执行详情、设备 serial、Watcher 信号 |
| `GET /api/v1/plans`、`GET /plans/{id}` | ❌ | 泄露编排定义 |
| `POST /plans/{id}/run/preview` | ❌ | 可预览扇出设备列表 |
| `GET /api/v1/runs/*/report*`、`/jira-draft*` | ❌ | 泄露 Job 报告与 JIRA 草稿 |
| `GET /api/v1/stats/**` | ❌ | 仪表盘数据外泄 |
| `GET /api/v1/schedules` | ❌ | 定时任务配置外泄 |
| `GET /metrics` | ❌ | 指标面暴露 |
| `POST /api/v1/auth/register` | ❌ 公开 | 任意注册普通用户 |
| Agent `/api/v1/agent/**` | ✅ X-Agent-Secret | — |
| 写操作（hosts/scripts/plans run 等） | ✅ admin / active_user | — |

### 2. 可观测与告警（ADR-0011）

| 层级 | 状态 | 内容 |
|------|------|------|
| 第一层 指标 | ✅ | ~30 族 Prometheus 指标（`backend/core/metrics.py`） |
| `/metrics` 端点 | ✅ | `backend/api/routes/metrics.py` |
| Grafana 模板 | ✅ 部分 | `docs/grafana/stability-platform-dashboard.json` |
| 第二层 AlertManager | ⬜ | ADR-0011 §49-50 |
| 第三层运维闭环 | ⬜ | 部署后 30 分钟观测窗口 |
| Loki / 集中日志 | ⬜ | 当前 `log_writer.py` 写本地文件 |

**APScheduler jobs**（均埋点 `stability_apscheduler_job_*`）：`recycler`、`session_watchdog`、`device_lease_reconciler`、`cron_check`、`retention_cleanup`、`saq_queue_depth_poll`、`precheck_reaper`、`revoked_token_cleanup`。

### 3. 数据与迁移

- **Alembic head**：`f1a2b3c4d5e6`（ADR-0024 必需）
- **连接池默认**：`pool_size=30, max_overflow=60`（`backend/core/database.py`）
- **备份**：文档仅提及 pg_dump/WAL，**无自动化脚本**

### 4. 测试与质量

| 套件 | 规模 | CI | 缺口 |
|------|------|-----|------|
| `backend/tests/` | ~543 collected | ✅ PG 16 | 部分 `@pytest.mark.skipif(not IS_PG)` |
| `backend/agent/tests/` | ~371 collected | ✅ | Watcher E2E 部分 mock |
| `frontend` vitest | ~29 文件 / 138 cases | ✅ tsc + build | 无 auth 死循环专项测试 |

**CI**（`.github/workflows/ci.yml`）：backend compileall → pytest → agent pytest → frontend tsc/vitest/build → Docker build。**未覆盖**：Nginx 反代 E2E、HTTPS Cookie 集成。

### 5. 部署与运维

| 项 | 现状 |
|----|------|
| 进程模型 | 单 uvicorn（APScheduler + 内存 RateLimit 进程内） |
| Gunicorn | ❌ 未配置；多 worker 会与调度器冲突 |
| Nginx | HTTP only `:80`；缺 HTTPS、缺 `/socket.io/` |
| Redis/SAQ | ✅ lifespan 启动 |
| env 模板 | `.env.backend.example` 缺 ADR-0024 变量 |
| 健康检查 | ✅ `/health`（含 DB ping） |

### 6. 功能完整度（ADR-0020/0021/0022）

| 模块 | 状态 | 说明 |
|------|------|------|
| Plan/PlanRun 主线 | ✅ | 后端 + C5b/C5c/C6 UI |
| PlanRunDetailPage | ✅ | 设备矩阵 / Watcher / 时间线 / SocketIO |
| 导出报告 | ⚠️ 半完成 | 后端 `GET /runs/{id}/report/export` ✅；PlanRun 页占位 |
| MapReduce | ❌ 占位 | 「正在开发中」 |
| Settings | ⚠️ 静态展示 | 无真实配置写入 |
| Issue Tracker | ✅ 基础 | JIRA draft 列表 |
| Watcher | ⚠️ 灰度关 | 默认 `STP_WATCHER_ENABLED=false` |
| 热更新 409 | ✅ | `HostHotUpdateConfirmDialog`（ADR-0021 C6） |

### 7. 性能与扩展

- SocketIO 水平扩展：❌ 无 Redis adapter
- 大表索引：✅ 部分（`idx_step_trace_job_stage/status_ts`，migration `e8f9a0b1c2d3`）
- SAQ 队列深度指标：`stability_saq_queue_depth`

### 8. 合规与运维习惯

| 项 | 状态 |
|----|------|
| RBAC | 后端 `user`/`admin`；前端无路由级 admin 门控 |
| 审计日志 | ✅ `audit_log` 表 + admin 只读 API |
| 密钥 | placeholder 启动拒绝（非 TESTING） |
| 备份 | 仅文档，无 cron |
| HTTPS | 未配；与 `AUTH_COOKIE_SECURE=1` 强绑定 |

---

## 结论

平台核心编排链路（**Plan → PlanRun → Agent → 聚合 UI**）与 **ADR-0024 后端安全能力**（HttpOnly Cookie、CSRF、refresh 黑名单、生产 guard）已达可上线水准；**Plan/PlanRun 主线（ADR-0020/0021/0022）** 功能基本就绪。

但若不做以下三项，生产环境将出现典型故障：

1. **生产网络层**（HTTPS + SocketIO 反代 + 前端同源 URL）
2. **env/文档与 ADR-0024 guard 对齐**
3. **读 API 鉴权策略**（公网必须处理）

**建议路径**：按 P0 清单完成 → 执行 `docs/preprod-drill-runbook.md` 全链路演练 → 首批上线后一周内补齐 AlertManager 与备份自动化。

**最优先三项（P0 #1、#2、#4）**：SocketIO 反代 + HTTPS/Cookie env + 提交登录修复 — 这三项最容易造成「能登录但页面不刷新」或「登录页卡死」。
