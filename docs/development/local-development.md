# 本地开发指南

> 命令速查：根目录 [`AGENTS.md`](../../AGENTS.md)

---

## 1. 环境要求

| 组件 | 版本 |
|------|------|
| Python | 3.10+（推荐 3.11） |
| Node.js | 20+ |
| PostgreSQL | 生产/CI 用；本地可 `ALLOW_SQLITE_TESTS=1` 仅跑部分测试 |
| Redis | SAQ/派发需要；`TESTING=1` 时 lifespan 跳过 |

---

## 2. 控制平面（Linux-first）

### 默认：Docker Compose 开发隔离

开发环境默认使用 Docker Compose 容器运行 backend、frontend、PostgreSQL 与 Redis；生产 / 预发布控制平面则使用 Linux 宿主机 systemd + Nginx，不复用开发 Compose。

```bash
cp .env.server.example .env.server
docker compose up --build
```

默认映射端口：

| 服务 | 端口 |
|------|------|
| 前端 | `15173` |
| 后端 | `18000` |
| PostgreSQL | `15432` |
| Redis | `16379` |

约束：

- 建议在**独立 checkout** 中运行 Compose，不要在生产 checkout 内直接执行。
- Compose 开发环境不得复用生产 `STP_NFS_ROOT`、AEE、本地日志或挂载点。
- 若与生产同机并存，开发流量与生产流量必须使用不同端口和不同目录。

### 兼容入口：宿主机手动启动

仅用于本地排障或历史兼容，不作为当前默认开发路径，也不作为生产部署方式。

```bash
pip install -r backend/requirements.txt
cd backend && python -m alembic upgrade head && cd ..
uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload

cd frontend && npm install && npm run dev
```

### 兼容入口：Windows 批处理脚本

```bash
start-backend.bat
start-frontend-windows.bat
```

### 必配 env（开发）

完整变量表见 [`environment-variables.md`](./environment-variables.md)。

```bash
# backend/.env（宿主机手动启动时）
DATABASE_URL=postgresql+psycopg://stability:stability@localhost:5432/stability
STP_SCRIPT_ROOT=/absolute/path/to/repo/backend/agent/scripts
# STP_AGENT_MIN_VERSION  rollout 期请留空，见 operations/agent-version-and-hot-update.md
```

Compose 开发环境请优先使用根目录 `.env.server`，并显式保持以下路径独立：

```bash
STP_NFS_ROOT=/var/lib/stp-dev/nfs
STP_AEE_NFS_ROOT=/var/lib/stp-dev/aee-nfs
STP_AEE_LOCAL_ROOT=/var/lib/stp-dev/aee-local
```

---

## 3. Agent（Linux / WSL）

### 开发模式（仓库根目录）

```bash
export API_URL="http://127.0.0.1:8000"
export STP_SCRIPT_ROOT="$(pwd)/backend/agent/scripts"
python -m backend.agent.main
```

若后端跑在 Compose 开发隔离环境中，改为：

```bash
export API_URL="http://127.0.0.1:18000"
export STP_SCRIPT_ROOT="$(pwd)/backend/agent/scripts"
python -m backend.agent.main
```

### WSL 联调要点

| 项 | 值 |
|----|-----|
| `API_URL` | `http://127.0.0.1:8000` |
| `ANDROID_ADB_SERVER_PORT` | `5039` |
| 代码同步 | 勿在 `/mnt/` 下直接安装；rsync 到 WSL 本地 |

详述：[`wsl-linux-agent-setup.md`](../wsl-linux-agent-setup.md)、[`backend/agent/DEPLOY.md`](../../backend/agent/DEPLOY.md)

### 生产式安装

`backend/agent/install_agent.sh` → systemd `stability-test-agent`

---

## 4. 脚本入库

```bash
# 设 STP_SCRIPT_ROOT 后
curl -X POST http://localhost:8000/api/v1/scripts/scan -H "Cookie: ..."
```

WSL 跨机：另设 `STP_SCRIPT_RUNTIME_ROOT=/opt/stability-test-agent/scripts`。

---

## 5. 常用端口

| 服务 | 端口 |
|------|------|
| 宿主机后端 | 8000 |
| 宿主机前端 dev | 5173 |
| Compose 后端 | 18000 |
| Compose 前端 | 15173 |
| Compose PostgreSQL | 15432 |
| Compose Redis | 16379 |

运行日志：实时经 SocketIO → 控制面；事后经 `POST /api/v1/agent/logs`（SSH）。

---

## 6. 相关文档

- 测试：[`testing.md`](./testing.md)  
- 主机连通：[`host-connectivity-verification.md`](../host-connectivity-verification.md)  
- 系统架构：[`design/00-system-overview.md`](../design/00-system-overview.md)
