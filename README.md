# Stability Test Platform — 稳定性测试管理平台

**版本**：1.0 · **文档更新**：2026-07-15

中心化 Android 设备稳定性测试管理平台：Linux-first 控制平面（FastAPI + React）编排执行，Linux Agent 经 ADB 驱动设备跑 Plan；支持实时监控、Watcher/AEE 采集、去重归档与通知。

| 入口 | 说明 |
|------|------|
| [`docs/README.md`](./docs/README.md) | **文档中心**（分层索引） |
| [`AGENTS.md`](./AGENTS.md) | 开发命令与 AI 约定 |
| [`CLAUDE.md`](./CLAUDE.md) | 架构不变量、FAQ、Changelog |

---

## 架构概览

```
┌─────────────────────────────────────────────────────────────┐
│  控制平面（Linux-first）                                     │
│  FastAPI :8000  ·  React  ·  APScheduler  ·  SAQ            │
│  PostgreSQL  ·  Redis（仅 SAQ）  ·  SocketIO                │
└──────────────────────────┬──────────────────────────────────┘
                           │ HTTP / SocketIO
         ┌─────────────────┼─────────────────┐
         ▼                 ▼                 ▼
   Dashboard          Agent 1 … N（Linux）
                           │ ADB
                      Android 设备
```

**主链**（ADR-0020）：`Plan` / `PlanStep` → `PlanRun`（`plan_snapshot`）→ 按设备扇出 `JobInstance` → Agent `script:<name>` 执行 → `/complete` 终态 ACK → 聚合。

细则见 [`docs/design/01-execution-pipeline.md`](./docs/design/01-execution-pipeline.md) 与 [`docs/design/07-execution-protocol.md`](./docs/design/07-execution-protocol.md)。

---

## 快速启动

### 开发（推荐：Docker Compose）

```bash
cp .env.server.example .env.server
docker compose up --build
# 前端 http://127.0.0.1:15173  ·  后端 http://127.0.0.1:18000
# 默认管理员见 .env.server（STP_ADMIN_*）；勿用于生产
```

完整本地步骤、WSL / Agent 联调：[`docs/development/local-development.md`](./docs/development/local-development.md)。

### 生产 / 预发布

Linux 宿主机 systemd + Nginx + PostgreSQL + Redis（**不用**根目录 Compose）。上线清单：

- [`docs/production-minimum-deployment-checklist.md`](./docs/production-minimum-deployment-checklist.md)
- [`docs/preprod-drill-runbook.md`](./docs/preprod-drill-runbook.md)
- [`docs/operations/README.md`](./docs/operations/README.md)

### Agent

开发：`API_URL=... python -m backend.agent.main`  
生产：[`backend/agent/DEPLOY.md`](./backend/agent/DEPLOY.md) / [`docs/linux-agent-ansible-runbook.md`](./docs/linux-agent-ansible-runbook.md)

---

## 环境要求

| 组件 | 要求 |
|------|------|
| 控制平面 | Python 3.10+（推荐 3.11），PostgreSQL，Redis，Node.js 20+ |
| Agent | Linux，Python 3.10+，ADB |

环境变量完整表见 [`docs/development/environment-variables.md`](./docs/development/environment-variables.md)（含 `STP_AGENT_MIN_VERSION`、超时、方案 C 路径）。模板：`backend/.env.example`、`backend/agent/.env.example`。

---

## 测试

```bash
# 推荐包装脚本（可选加载 .env.test）
./scripts/run_pytest.sh backend/agent/tests/ -q

# Agent（无 PG，优先日常验证）
python -m pytest backend/agent/tests/

# 控制面（用 Docker testcontainers；生产机勿把 TEST_DATABASE_URL 指生产库）
unset TEST_DATABASE_URL
JWT_SECRET_KEY=test-secret python -m pytest backend/tests/ -q

# 前端
cd frontend && npx vitest run && npx tsc --noEmit
```

约束与 CI 映射：[`docs/development/testing.md`](./docs/development/testing.md)、[`AGENTS.md`](./AGENTS.md)。

---

## 近期能力（摘要）

| 主题 | 要点 | 文档 |
|------|------|------|
| 执行协议 | 终态仅 `/complete`；abort 保租约等 ACK；UNKNOWN 围栏；PlanRun 不再生产 DEGRADED | [07-execution-protocol](./docs/design/07-execution-protocol.md) |
| 派发 / 链 | Stage 2 物化自 `plan_snapshot`；链触发可补偿；preflight + schema 硬化 | 同上 + alembic `c8d9…` |
| Agent 版本 | 热更新携带 code revision；`STP_AGENT_MIN_VERSION` **显式开启**才门禁 claim | [环境变量](./docs/development/environment-variables.md) · [主机运维](./docs/operations/agent-version-and-hot-update.md) |
| 通知 | `notification_logs` + 前端铃铛 / 历史页 | [`design/03-frontend.md`](./docs/design/03-frontend.md) |
| 主机 UI | 紧凑主机表、浮动批量栏、单机热更新 | 同上 |

Changelog：[`CLAUDE.md`](./CLAUDE.md)。

---

## 文档地图（精简）

| 路径 | 内容 |
|------|------|
| [`docs/DOC-MAP.md`](./docs/DOC-MAP.md) | 分层、阅读顺序 |
| [`docs/design/`](./docs/design/) | 与代码对齐的技术设计 |
| [`docs/prd/`](./docs/prd/) · [`docs/acceptance/`](./docs/acceptance/) | 需求与验收 |
| [`docs/adr/`](./docs/adr/) | 架构决策 |
| [`docs/development/`](./docs/development/) | 本地开发、测试、env |
| [`docs/operations/`](./docs/operations/) | 部署与运维 |

冲突时以**代码与测试**为准，并回写对应 `docs/` 子文档。
