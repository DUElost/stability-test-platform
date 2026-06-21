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

## 2. 控制平面（Windows / Linux）

### 快速启动（Windows）

```bash
start-backend.bat    # alembic upgrade + uvicorn :8000
start-frontend-windows.bat
```

开发热重载：`$env:STP_BACKEND_RELOAD=1` 后运行 `start-backend.bat`。

### 手动启动

```bash
pip install -r backend/requirements.txt
cd backend && python -m alembic upgrade head && cd ..
uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload

cd frontend && npm install && npm run dev
```

### 必配 env（开发）

```bash
# backend/.env（可由 .env.example 生成）
DATABASE_URL=postgresql+psycopg://stability:stability@localhost:5432/stability
STP_SCRIPT_ROOT=/absolute/path/to/repo/backend/agent/scripts
```

---

## 3. Agent（Linux / WSL）

### 开发模式（仓库根目录）

```bash
export API_URL="http://127.0.0.1:8000"
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
| 后端 | 8000 |
| 前端 dev | 5173 |
| PostgreSQL | 5432 |
| Redis | 6379 |
| Agent 运行日志 HTTP | 8900（方案 C） |

---

## 6. 相关文档

- 测试：[`testing.md`](./testing.md)  
- 主机连通：[`host-connectivity-verification.md`](../host-connectivity-verification.md)  
- 系统架构：[`design/00-system-overview.md`](../design/00-system-overview.md)
