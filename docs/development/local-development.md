# 本地开发指南

> 本文是启动与迁移命令的权威位置。依赖分工见
> [`dependencies-and-quality.md`](./dependencies-and-quality.md)，测试与生产数据库边界见
> [`testing.md`](./testing.md)。

---

## 1. 环境要求

| 组件 | 版本 |
|------|------|
| Python | 3.10+（推荐 3.11） |
| Node.js | 20+ |
| PostgreSQL | 生产 / CI / 本地测试均需要；测试库由 `conftest` 拉起 testcontainers（[testing.md](./testing.md)） |
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

**浏览器入口必须用 `http://localhost:15173`，勿用 `127.0.0.1`**（#2330）：
compose 构建把 API/WS 基址烤成 `localhost:18000`，而 `127.0.0.1` 与 `localhost` 是两个
站点（site 只看 host），host-only `SameSite=Lax` 会话 cookie 跨站既不存储也不发送——
用 `127.0.0.1` 打开会「假登录」（接口 200 但无会话）且 Socket.IO 恒「已断开」。
生产前端经 `127.0.0.1` 直访同理会回退 `localhost:8000` 而失效（现实入口为 LAN IP/域名）。

约束：

- 建议在**独立 checkout** 中运行 Compose，不要在生产 checkout 内直接执行。
- Compose 开发环境不得复用生产 `STP_NFS_ROOT`、AEE、本地日志或挂载点。
- 若与生产同机并存，开发流量与生产流量必须使用不同端口和不同目录。

### dev 库的 schema 与字典 seed（#2381）

Compose 起的 PostgreSQL 由 `backend/scripts/init_dev_db.py` 初始化（`ENV=production`
时该脚本直接拒绝执行）。它按库的现状选路，并在输出里打印实际走的那条：

| 库现状 | 路径 | 输出 | 结果 |
|---|---|---|---|
| 空库 | `alembic upgrade head` | `dev_db_schema_ready path=alembic` | schema + **字典 seed** |
| 已有 `alembic_version` | 同上（正常增量） | 同上 | 同上 |
| 有表但无 `alembic_version` | `create_all` 兜底 | `path=create_all_legacy WARNING=...dictionary_seeds_not_applied` | 只有表，**没有 seed** |

`specialty`（专项）、`script`（脚本注册）这类静态字典的**唯一事实源是 seed 迁移**，没有
API 写端点。所以第三行那种库会「schema 看着成功、但新建 Plan 没有专项可选」——这就是
#2381。收养一个老 dev 库需要显式决策（不该由 dev 脚本顺手做掉）：先确认库内 schema 与
head 一致，再 stamp 后补链。

```bash
cd backend
python -m alembic current          # 核对现状，确认库内 schema 与 head 一致
python -m alembic stamp <revision>
python -m alembic upgrade head
```

嫌麻烦就直接删掉那个 dev 库，让 compose 重新起一个空库。管理员账号由同一脚本按
`STP_ADMIN_USER` / `STP_ADMIN_PASSWORD` upsert，缺任一则跳过并打印
`dev_db_admin_skipped`。

判据：`tests/test_dev_bootstrap_seed.py`（PR 路径，含「兜底必须自带标注」）与
`tests/test_alembic_upgrade.py::test_dev_bootstrap_from_empty_database_produces_seeded_schema`
（夜间容器，空库→seed 的地面真值）。

### 兼容入口：宿主机手动启动

仅用于本地排障或历史兼容，不作为当前默认开发路径，也不作为生产部署方式。

```bash
pip install -r backend/requirements.txt
cd backend && python -m alembic upgrade head && cd ..
uvicorn backend.main:app --host 0.0.0.0 --port 8000

# 仅本地调试需要时显式开启热重载（勿用于真机/生产）
uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload

cd frontend && npm install && npm run dev
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

## 6. 专属 worktree 里跑门禁（并行执行）

并行 Execution 用专属 worktree（约定位置 `<repo>/.wt/<name>`，见
[`repository-workflow.md`](./repository-workflow.md)）。**新建的 worktree 没有依赖**，
而 `scripts/run_gates.py check:quick` 会跑到前端门禁，缺依赖时停在
`eslint: command not found`。复用主检出已装好的依赖即可（符号链接，不复制）：

```bash
# 前端：链接建在 frontend/ 下（相对仓根 3 层）
ln -s ../../../frontend/node_modules frontend/node_modules
# Python：链接建在 worktree 根 `.wt/<name>/`（相对仓根 2 层）
ln -s ../../.venv .venv
```

`node_modules` / `.venv` 均被 `.gitignore` 覆盖，链接不会污染 `git status`。
若确实要独立安装依赖，按 [`dependencies-and-quality.md`](./dependencies-and-quality.md) 走。

> 门禁与跳过规则本身对 worktree 布局是透明的；历史上曾在 `.wt/` 下出现假红
> （跳过规则按绝对路径匹配 `.wt`，把整个 worktree 滤空），已由 #1978 修复。

---

## 7. 相关文档

- 测试：[`testing.md`](./testing.md)  
- WSL / Agent 联调：[`wsl-linux-agent-setup.md`](../wsl-linux-agent-setup.md)  
- 系统架构：[`design/00-system-overview.md`](../design/00-system-overview.md)
