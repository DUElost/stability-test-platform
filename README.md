# Stability Test Platform — 稳定性测试管理平台

**版本**：1.0.0
**最后更新**：2026-06-21

中心化 Android 设备稳定性测试管理平台：Linux-first 控制平面运行 FastAPI 后端与 React 前端，Linux Agent 集群通过 ADB 连接设备执行 Plan 编排任务，支持实时监控、日志采集与报告生成。Windows / WSL 仅保留开发联调与兼容入口，不再作为默认生产基线。

详细架构与模块说明见 [`CLAUDE.md`](./CLAUDE.md)；AI/自动化开发约定见 [`AGENTS.md`](./AGENTS.md)。

---

## 架构概览

```
┌─────────────────────────────────────────────────────────────┐
│  控制平面（Linux-first，Windows/WSL 仅兼容）               │
│  FastAPI :8000  ·  React :5173  ·  APScheduler  ·  SAQ     │
│  PostgreSQL  ·  Redis（SAQ 队列）  ·  SocketIO 实时推送      │
└──────────────────────────┬──────────────────────────────────┘
                           │ HTTP / SocketIO
         ┌─────────────────┼─────────────────┐
         ▼                 ▼                 ▼
   ┌──────────┐     ┌──────────┐      ┌──────────┐
   │ React    │     │ Agent 1  │  …   │ Agent N  │
   │ Dashboard│     │ (Linux)  │      │ (Linux)  │
   └──────────┘     └────┬─────┘      └────┬─────┘
                         │ ADB             │ ADB
                    Android 设备       Android 设备
```

**核心概念**（ADR-0020）：`Plan` + `PlanStep` 编排 → `PlanRun` 执行 → 按设备扇出 `JobInstance`；脚本通过 `script:<name>` action 在 Agent 侧运行。

---

## 快速启动

生产 / 预发布控制平面按 Linux 宿主机部署（systemd + Nginx + PostgreSQL + Redis）；开发环境默认使用 Docker Compose 容器隔离。下面的批处理脚本和宿主机手动启动仅作为历史兼容或排障入口。

### 方式一：Docker Compose 开发隔离（默认开发方式）

```bash
# 建议在独立 checkout 中执行，避免与生产宿主机目录混用
cp .env.server.example .env.server
docker compose up --build

# 前端: http://127.0.0.1:15173
# 后端: http://127.0.0.1:18000
# PostgreSQL: 127.0.0.1:15432
# Redis: 127.0.0.1:16379
```

Compose 后端启动时会执行 dev-only 初始化脚本，按当前 ORM schema 创建本地开发库，并使用 `.env.server` 中的 `STP_ADMIN_USER` / `STP_ADMIN_PASSWORD` 创建或更新管理员账号。默认可用账号：

```text
admin / admin123
```

常用检查与日志：

```bash
docker compose ps
curl http://127.0.0.1:18000/health
docker compose logs -f server     # 后端 API / CSRF / 登录错误
docker compose logs -f frontend   # 前端容器 Nginx 访问日志
```

如果登录提示 `CSRF check failed`，优先确认浏览器访问地址是否为 `http://127.0.0.1:15173` 或 `http://localhost:15173`，且 `.env.server` / Compose 中的 `CORS_ORIGINS` 包含对应 Origin。

> Docker Compose 仅用于开发环境。不要在生产 checkout 内直接启动，也不要把开发容器挂到生产 NFS / AEE / 日志目录。

### 方式二：批处理脚本（Windows / WSL 兼容入口）

```bash
# 终端 1 — 后端（自动 alembic upgrade + 启动）
cd stability-test-platform
start-backend.bat          # http://localhost:8000

# 终端 2 — 前端
start-frontend-windows.bat  # http://localhost:5173
```

首次启动若缺少 `backend/.env`，脚本会基于 `backend/.env.example` 生成本地模板。

> **实机验证**：`start-backend.bat` 默认不开启热重载。仅本地开发时：
> ```powershell
> $env:STP_BACKEND_RELOAD = "1"
> .\start-backend.bat
> ```

### 方式三：手动启动（Linux / 本地排障）

**后端**

```bash
cd stability-test-platform
pip install -r backend/requirements.txt
cd backend && python -m alembic upgrade head && cd ..
uvicorn backend.main:app --host 0.0.0.0 --port 8000
# 开发热重载：加 --reload
```

**前端**

```bash
cd frontend
npm install
npm run dev                # http://localhost:5173
```

**Agent（Linux / WSL）**

```bash
# 开发模式（从仓库根目录）
export API_URL="http://<控制平面IP>:8000"
export STP_SCRIPT_ROOT="<repo>/backend/agent/scripts"   # 后端扫描用
python -m backend.agent.main

# 若后端跑在 docker compose 开发隔离环境：
# export API_URL="http://127.0.0.1:18000"

# 生产模式：backend/agent/install_agent.sh → systemd stability-test-agent
# WSL 联调须设 ANDROID_ADB_SERVER_PORT=5039，详见 CLAUDE.md
```

---

## 环境要求

| 组件 | 要求 |
|------|------|
| 后端 | Python 3.10+（推荐 3.11），PostgreSQL，Redis |
| 前端 | Node.js 20+，npm 10+ |
| Agent | Linux，Python 3.10+，ADB，SSH 访问 |

---

## 关键环境变量

### 控制平面（后端）

| 变量 | 默认值 / 说明 |
|------|--------------|
| `DATABASE_URL` | PostgreSQL 连接串 |
| `REDIS_URL` | `redis://localhost:6379/0` — SAQ 任务队列 |
| `JWT_SECRET_KEY` | JWT 签名密钥（生产必改） |
| `AGENT_SECRET` | Agent SocketIO 认证（生产必改，与 Agent 侧一致） |
| `STP_ADMIN_USER` / `STP_ADMIN_PASSWORD` | Docker Compose 开发初始化管理员账号；生产不要使用默认值 |
| `STP_SCRIPT_ROOT` | 脚本扫描根；**开发环境必须显式设为** `<repo>/backend/agent/scripts` |
| `STP_NFS_ROOT` | NFS 存储根（脚本/日志/产物） |
| `ENV` | `development` / `production` — 生产触发多项 guard |
| `AUTH_COOKIE_SECURE` | 生产须 `1`（HTTPS Cookie） |
| `AUTH_COOKIE_SAMESITE` | 生产须 `lax` 或 `strict` |
| `STP_CSRF_ENABLED` | 浏览器 CSRF 校验（生产须开启） |
| `CORS_ORIGINS` | 前端域名白名单 |
| `STP_ALLOW_REGISTER` | 公开注册开关；**生产默认关闭**，显式设 `1` 才允许 |
| `STP_METRICS_AUTH_REQUIRED` | 生产建议 `1`；`/metrics` 需 Bearer token 或 `X-Agent-Secret` |
| `STP_ENABLE_INPROCESS_SAQ` | 进程内 SAQ Worker（生产建议 `1`） |
| `STP_WATCHER_ENABLED` | Agent 侧 Watcher 灰度开关（默认 `true`） |
| `STP_AEE_LOCAL_ROOT` | Agent HDD AEE 根（方案 C，默认 `/mnt/hdd/aee_events`） |
| `STP_AEE_CIFS_ROOT` | Agent 上送 15.4 的 CIFS 挂载根（HDD spill / Sprint 4 upload） |

### Job / 租约超时（`backend/core/job_timeout_config.py`）

| 变量 | 生产默认 | 说明 |
|------|---------|------|
| `DISPATCHED_TIMEOUT_SECONDS` | 120 | PENDING Job 未被认领 → FAILED |
| `RUNNING_HEARTBEAT_TIMEOUT_SECONDS` | 900 | RUNNING Job 心跳丢失 → UNKNOWN |
| `PATROL_RUNNING_HEARTBEAT_TIMEOUT_SECONDS` | 300（dev 180） | patrol 阶段 RUNNING 独立超时窗口（init 等非 patrol 仍用 `RUNNING_HEARTBEAT_TIMEOUT_SECONDS`） |
| `UNKNOWN_GRACE_SECONDS` | 300 | UNKNOWN 宽限期后释放租约并 FAILED |

兼容旧名：`RUN_DISPATCHED_TIMEOUT_SECONDS` / `RUN_HEARTBEAT_TIMEOUT_SECONDS`。

### Agent

| 变量 | 说明 |
|------|------|
| `API_URL` | 控制平面地址 |
| `HOST_ID` | 主机 ID（须与 DB `host.id` 对齐，不可为 `0`） |
| `POLL_INTERVAL` | 轮询间隔（秒，默认 10） |
| `ADB_PATH` | ADB 可执行路径 |
| `ANDROID_ADB_SERVER_PORT` | WSL 联调须 `5039` |
| `STP_AEE_LOCAL_ROOT` | 方案 C：AEE 设备日志 HDD 根（默认 `/mnt/hdd/aee_events`） |

完整变量列表见 `backend/.env.example`、[`CLAUDE.md`](./CLAUDE.md) 与 [`docs/design/2026-plan-c-storage-and-access.md`](./docs/design/2026-plan-c-storage-and-access.md)。

---

## 测试

```bash
# 后端（需 TEST_DATABASE_URL 或 ALLOW_SQLITE_TESTS=1）
pytest backend/tests/

# Agent 单元测试
pytest backend/agent/tests/

# 前端
cd frontend && npx vitest run && npx tsc --noEmit

# 语法检查
python -m compileall backend
```

CI 流程见 `.github/workflows/ci.yml`（compileall → pytest → tsc → build）。

---

## 文档索引

| 文档 | 说明 |
|------|------|
| **[`docs/README.md`](./docs/README.md)** | **文档中心（推荐入口）** |
| [`docs/DOC-MAP.md`](./docs/DOC-MAP.md) | 文档分层、设计索引、阅读顺序 |
| [`docs/DOC-RETIREMENT.md`](./docs/DOC-RETIREMENT.md) | 待归档/删除文档清单 |
| [`docs/design/`](./docs/design/) | 技术设计（系统/后端/前端/Agent/数据模型） |
| [`docs/prd/`](./docs/prd/) | 产品需求 |
| [`docs/acceptance/`](./docs/acceptance/) | 验收矩阵 |
| [`docs/development/`](./docs/development/) | 本地开发与测试 |
| [`docs/operations/README.md`](./docs/operations/README.md) | 运维与部署索引 |
| [`CLAUDE.md`](./CLAUDE.md) | 项目百科：端点表、数据模型、FAQ、Changelog |
| [`AGENTS.md`](./AGENTS.md) | 开发命令与约定 |
| [`docs/adr/README.md`](./docs/adr/README.md) | 架构决策记录（ADR） |
| [`docs/project-vision.md`](./docs/project-vision.md) | 项目愿景 |
| [`docs/production-minimum-deployment-checklist.md`](./docs/production-minimum-deployment-checklist.md) | 生产最小部署 |
| [`docs/preprod-drill-runbook.md`](./docs/preprod-drill-runbook.md) | 预发布验收 |
| [`backend/agent/DEPLOY.md`](./backend/agent/DEPLOY.md) | Agent 安装与热更新 |

---

## 近期改进（主链路加固，至 f0ec89d）

2026-05 主链路脆弱性专项收口，重点包括：

- **派发门禁统一**：SCHEDULE/CHAIN 改走 sync dispatch gate；precheck 失败可手动 retry-dispatch；派发失败显式 FAILED + 审计
- **PlanRun 聚合加固**：行锁 + terminal guard；链式触发 flag 回滚防撞；abort 留痕
- **Job 超时集中化**：`job_timeout_config.py` 统一默认值；patrol 阶段 RUNNING 心跳超时可独立配置
- **Agent 可靠性**：subprocess 进程组隔离；log_signal outbox 死信闭环 + backlog 指标；step_trace_cache 防 SQLite 膨胀
- **生产就绪**：读 API 鉴权；SocketIO 同源；SAQ 503 降级；浏览器 HttpOnly Cookie + CSRF + refresh 黑名单（ADR-0024）
- **安全门禁**：生产默认关闭公开注册（`STP_ALLOW_REGISTER`）；`/metrics` 可选 Bearer/Agent-Secret 鉴权
- **可观测性**：dispatch gate / patrol / CSRF / outbox 等 Prometheus 指标；AlertManager 规则草案（`deploy/prometheus/alerts-stability-platform.yml`）

完整变更记录见 [`CLAUDE.md`](./CLAUDE.md) Changelog 与各 ADR。

---

## 生产就绪要点

上线前请阅读 [`docs/production-minimum-deployment-checklist.md`](./docs/production-minimum-deployment-checklist.md)，核心检查项：

1. **访问入口**：内网 HTTP 正式环境用 `ENV=internal` + `AUTH_COOKIE_SECURE=0`；HTTPS 环境才用 `ENV=production` + `AUTH_COOKIE_SECURE=1`
2. **单实例后端**：避免多进程重复调度（APScheduler / SAQ 均在进程内）
3. **数据库迁移**：`alembic upgrade head`（含 refresh token 黑名单等表）
4. **Agent 配置**：每台唯一 `HOST_ID`；`AGENT_SECRET` 与后端一致
5. **前端构建**：`VITE_API_BASE_URL=`（空）实现同源；Nginx 反代 `/api/`、`/health` 与 `/socket.io/`
6. **Metrics 鉴权**：生产建议 `STP_METRICS_AUTH_REQUIRED=1`；如需额外收口，可叠加 Nginx IP 白名单
7. **冒烟验收**：控制平面健康 → Agent ONLINE → 创建 PlanRun → 任务完整流转至终态 → 设备锁释放

预发布逐条执行：[`docs/preprod-drill-runbook.md`](./docs/preprod-drill-runbook.md)

### 生产环境运行与排障

生产 / 预发布控制平面按 Linux 宿主机方式运行，不使用根目录 `docker-compose.yml`，也不会执行 Docker Compose 的 dev-only 数据库初始化脚本。内网 HTTP 正式环境属于业务生产，但技术 profile 应使用 `ENV=internal`，避免 `ENV=production` 的 HTTPS Cookie guard 阻断浏览器登录。

生产数据库迁移由 systemd `ExecStartPre` 或人工命令执行：

```bash
cd /opt/stability-test-platform/backend
../venv/bin/python -m alembic upgrade head
```

生产登录不要使用开发默认账号 `admin/admin123`。首次管理员账号应通过受控的生产用户初始化 / 管理流程创建；生产环境也不要保留 `STP_ADMIN_PASSWORD=admin123` 一类默认值。

生产常用日志位置：

```bash
sudo systemctl status stability-backend --no-pager
sudo journalctl -u stability-backend -f
sudo tail -f /opt/stability-test-platform/logs/backend.log
sudo tail -f /opt/stability-test-platform/logs/backend_error.log
sudo tail -f /var/log/nginx/access.log
sudo tail -f /var/log/nginx/error.log
```

生产若出现 `CSRF check failed`，优先检查浏览器访问的实际 Origin 是否与 `.env.backend` 中 `CORS_ORIGINS` 完全一致。内网 HTTP 正式环境通常应同时满足：

```env
ENV=internal
AUTH_COOKIE_SECURE=0
AUTH_COOKIE_SAMESITE=lax
STP_CSRF_ENABLED=1
STP_ALLOW_REGISTER=0
STP_METRICS_AUTH_REQUIRED=1
CORS_ORIGINS=http://<控制平面内网IP或主机名>
```

HTTPS 生产环境才使用：

```env
ENV=production
AUTH_COOKIE_SECURE=1
AUTH_COOKIE_SAMESITE=lax
STP_CSRF_ENABLED=1
CORS_ORIGINS=https://<你的前端域名>
```

---

## 开发环境约定（EOL 防御）

项目曾出现 IDE/插件 CRLF 污染（单次 diff 1100+ 空行）。当前四层防御：

1. **`.gitattributes`** — 入库统一 LF
2. **`.editorconfig`** — 编辑器强制 LF
3. **`.vscode/settings.json`** — 关闭 formatOnSave 防误格式化
4. **`.githooks/pre-commit`** — 拦截纯空行污染与 CRLF

克隆后执行一次：

```bash
git config core.hooksPath .githooks
```

清地雷：`tools/dev/normalize-eol.sh`（加 `--apply` 修复，`--check` 用于 CI）。

---

## 常见问题

**Q: 开发环境脚本扫描路径怎么设？**
A: 后端启动前设 `STP_SCRIPT_ROOT=<repo>/backend/agent/scripts`，然后 `POST /api/v1/scripts/scan`。WSL 联调还需 `STP_SCRIPT_RUNTIME_ROOT=/opt/stability-test-agent/scripts`。

**Q: Agent 心跳正常但拉不到任务？**
A: 检查 `HOST_ID` 是否与 DB `host.id` 一致（不可为 `0`）。

**Q: 如何热更新 Agent？**
A: 前端「主机管理」页点击「热更新」，或 `tools/ansible/playbooks/update_agent.yml` 批量更新。

**Q: 测试需要 PostgreSQL 吗？**
A: 后端 pytest 默认需要；本地可设 `ALLOW_SQLITE_TESTS=1` 跳过。

更多 FAQ 见 [`CLAUDE.md`](./CLAUDE.md)。

---

*维护者请参考 [`CLAUDE.md`](./CLAUDE.md) 保持文档同步。*
