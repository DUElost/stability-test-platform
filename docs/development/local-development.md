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

### 开发期「假 Agent」夹具（`tools/dev/fake_agent.py`，#2402）

job / step 级实时面在 dev 里是可测的——前提是别再手搓夹具。**它只注册与回报，
绝不执行脚本**（不 import subprocess/pty、不碰 ADB），并且有三条硬红线：

| 红线 | 判据 | 谁守 |
|---|---|---|
| 不执行任何东西 | AST 级守卫：不得 import `subprocess`/`pty`/`shutil`/`asyncio`，不得出现 `os.system` 类调用 | `tests/test_dev_fake_agent.py` |
| 只打 dev 栈 | 默认 `127.0.0.1:18000`；指向 `:8000`（本机生产控制面）**直接拒绝**，需 `--allow-non-dev-target` 显式越过 | 同上（断言「被拒时一个 HTTP 都不发」） |
| 凭据不外泄 | `AGENT_SECRET` 只从环境读；`fencing_token` 由 `fencing_token_for()` 一处取用，**永不打印** | 同上（断言输出里不含 token 值，只报「有没有」） |

反向同样成立：**不得把真机 Agent 指向 dev 的 `:18000`** —— 那会把真机的设备/Job
事实写进 dev 库，两边的事实源都被污染。

```bash
export AGENT_SECRET=<与 dev server 容器同值>        # 工具不会打印它
# 1) 造主机与设备（host_id="0" 是按 IP 自动注册的哨兵）
docker compose exec -T server python /app/tools/dev/fake_agent.py heartbeat --count 3
# 2) 常驻：注册 /agent + 周期心跳（compose exec -d 起的进程会随会话回收，故用 setsid）
docker compose exec -d server sh -c \
  'setsid nohup python /app/tools/dev/fake_agent.py serve --lifetime 900 </dev/null >/tmp/fa.log 2>&1 &'
# 3) 跑一步 job 生命周期（token 自动从 device_leases 取，不打印）
docker compose exec -T server python /app/tools/dev/fake_agent.py claim --capacity 4
docker compose exec -T server python /app/tools/dev/fake_agent.py step --job <ID> \
  --step step_init_1 --status RUNNING
docker compose exec -T server python /app/tools/dev/fake_agent.py complete --job <ID>
# 4) 反向造推送：只允许 agent→server 的白名单事件
docker compose exec -T server python /app/tools/dev/fake_agent.py inject \
  --event step_log --data '{"job_id":<ID>,"line":"from fixture"}'
```

**dev 冒烟要含 job 级 WS 渲染**（这条是 #2402 的验收点）：起假 Agent → 跑 1 台设备
1 个 step 的 Plan → 在**不刷新** `/execution/plan-runs/<id>` 的前提下断言页面反映了
RUNNING→终态。只测 REST/UI 面的「冒烟」覆盖不到这条链。

两个已知差异，不要当 bug 查：

- **transport**：生产 Agent 按 #1121 走 websocket-only；dev 镜像里没有
  `websocket-client`，所以夹具在 `--transport auto` 下会退回 **polling** 并自带
  自愈重连（polling 会话约 5 分钟掉一次）。多实例拓扑下这**不等价**于生产，
  涉及会话亲和的改动仍须按 #1121 的口径验。
- **容器内 `/app` 只读**：夹具日志默认落 `/tmp/stp-fake-agent.log`。

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
