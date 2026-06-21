# Stability Test Platform 生产最小可用部署清单（MVP）

适用范围：当前代码基线（`backend/main.py` 启动内置调度器/回收器、`frontend` 为 Vite 构建产物、Agent 为 systemd 服务）。

目录命名说明：
- `deploy/control-plane/`：控制平面（前后端主机）部署模板
- `backend/agent/`：Linux Host Agent 安装文件（`install_agent.sh`）

推荐联动文档：
- 目标愿景：`docs/project-vision.md`
- 预发布逐条执行版：`docs/preprod-drill-runbook.md`

## 1. 目标拓扑

- 1 台主 Linux Host（控制平面）：
  - Backend API + WebSocket（`127.0.0.1:8000`，由 Nginx 反向代理）
  - Frontend 静态资源（Nginx 直接托管）
- N 台 Linux Host Agent（数据平面）：
  - 每台主机运行 `stability-test-agent.service`
  - 每台主机配置唯一 `HOST_ID`，心跳上报到控制平面 `API_URL`

## 2. 关键约束（必须遵守）

- 当前版本后端会在应用启动时启动调度线程和回收线程（`backend/main.py`）。
- 生产 MVP 必须使用单实例后端（`1` 个进程）运行，避免多进程重复调度。
- 不允许 Agent 使用 `HOST_ID=0`；每台 Agent 必须唯一且固定。
- 读 API 已要求登录（`get_current_active_user`）；`/metrics` 仍无鉴权，生产请用 Nginx IP 白名单限制。
- 前端 SocketIO 生产构建须设 `VITE_API_BASE_URL=`（空），Nginx 须反代 `/socket.io/`（见 §3.6）。

## 3. 控制平面部署清单（主 Linux Host）

### 3.1 系统准备

- 安装基础依赖：
```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip nginx curl
```
- 安装 Node.js 20（推荐 nvm）：
```bash
curl -fsSL https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.3/install.sh | bash
source ~/.nvm/nvm.sh
nvm install 20
nvm use 20
```
- 版本校验：
```bash
python3 --version   # >= 3.10
node --version      # >= 20
npm --version
```

### 3.2 部署目录与代码

```bash
sudo mkdir -p /opt/stability-test-platform
sudo chown -R $USER:$USER /opt/stability-test-platform
cd /opt/stability-test-platform
# 将当前仓库内容同步到该目录（git clone 或 rsync）
```

### 3.3 后端环境

Redis 与 SAQ 为**硬依赖**：`STP_ENABLE_INPROCESS_SAQ=1`（默认）时，启动期会对 `REDIS_URL` 执行 PING，失败则进程退出；SAQ worker 启动失败同样导致 lifespan 失败。`/health` 在 in-process 模式下返回 `saq_ready`（worker 已启动且 Redis 可达）。开发/测试可通过 `TESTING=1`（pytest）或 **非 production** 下 `STP_SKIP_INFRA_CHECK=1` 跳过 Redis PING **与 in-process SAQ 启动**（纯 API 调试；`/health` 的 `saq_ready` 将为 `false`）。

```bash
cd /opt/stability-test-platform
python3 -m venv venv
source venv/bin/activate
pip install -r backend/requirements.txt
```

创建后端环境文件 `/opt/stability-test-platform/.env.backend`：

```bash
python3 tools/prepare_env.py --template deploy/control-plane/env/.env.backend.example --target /opt/stability-test-platform/.env.backend
```

### 3.4 前端构建

```bash
cd /opt/stability-test-platform/frontend
npm install
npm run build
```

### 3.5 后端 systemd 服务

从模板生成 `/etc/systemd/system/stability-backend.service`（将 `<deploy-user>` 替换为实际部署用户）：

```bash
cp deploy/control-plane/systemd/stability-backend.service /tmp/stability-backend.service
sed -i 's|<deploy-user>|'"$USER"'|g' /tmp/stability-backend.service
sudo cp /tmp/stability-backend.service /etc/systemd/system/stability-backend.service
```

启用服务：

```bash
mkdir -p /opt/stability-test-platform/logs
sudo systemctl daemon-reload
sudo systemctl enable stability-backend
sudo systemctl start stability-backend
sudo systemctl status stability-backend --no-pager
```

### 3.6 Nginx（前端静态 + API / SocketIO 反向代理）

从模板生成 `/etc/nginx/sites-available/stability-platform`：

```bash
sudo cp deploy/control-plane/nginx/stability-platform.conf /etc/nginx/sites-available/stability-platform
```

模板已包含 `/api/`、`/socket.io/`（WebSocket 升级）与 legacy `/ws/`。

生产前端构建（同源，SocketIO 走 Nginx 443/80）：

```bash
cd /opt/stability-test-platform/frontend
VITE_API_BASE_URL= npm run build
```

构建后确认：`frontend/src/config/index.ts` 在非 localhost 且 `VITE_API_BASE_URL` 为空时，
`dashboardSocketUrl()` 返回相对路径 `/dashboard`（浏览器连 `wss://<域名>/socket.io/...`）。
Nginx 模板须同时反代 `/api/` 与 `/socket.io/`（见 `deploy/control-plane/nginx/stability-platform.conf`
与 Docker 版 `deploy/nginx/frontend-docker.conf`）。

生产后端 env 必配（ADR-0024 guard 会校验）：

```env
ENV=production
AUTH_COOKIE_SECURE=1
AUTH_COOKIE_SAMESITE=lax
STP_CSRF_ENABLED=1
JWT_SECRET_KEY=<强随机>
AGENT_SECRET=<非 placeholder>
CORS_ORIGINS=https://<你的前端域名>
STP_ENABLE_INPROCESS_SAQ=1
REDIS_URL=redis://...
```

Job / 租约超时（可选覆盖，默认见 `backend/core/job_timeout_config.py`）：

| 环境变量 | 生产默认 | 说明 |
|---------|---------|------|
| `DISPATCHED_TIMEOUT_SECONDS` | 120 | PENDING Job 未被 Agent 认领 → FAILED |
| `RUNNING_HEARTBEAT_TIMEOUT_SECONDS` | 900 | RUNNING Job 心跳丢失 → UNKNOWN |
| `PATROL_RUNNING_HEARTBEAT_TIMEOUT_SECONDS` | 300 | patrol 阶段 RUNNING 心跳丢失 → UNKNOWN |
| `UNKNOWN_GRACE_SECONDS` | 300 | UNKNOWN 宽限期后释放租约并 FAILED |
| `REDIS_PING_TIMEOUT` | 3 | 启动期 Redis PING 超时（秒） |

兼容旧名：`RUN_DISPATCHED_TIMEOUT_SECONDS` / `RUN_HEARTBEAT_TIMEOUT_SECONDS` 仍有效。
非 `production` 环境可通过上述变量单独调优，**不建议**在未评估前缩短生产默认值。

启用站点：

```bash
sudo ln -sf /etc/nginx/sites-available/stability-platform /etc/nginx/sites-enabled/stability-platform
sudo nginx -t
sudo systemctl restart nginx
sudo systemctl status nginx --no-pager
```

## 4. Agent 批量接入清单（每台 Linux Host）

### 4.1 安装 Agent

```bash
mkdir -p /tmp/agent-install
cd /tmp/agent-install
# 拷贝 backend/agent/* 与 install_agent.sh
chmod +x install_agent.sh
sudo ./install_agent.sh
```

### 4.2 配置 Agent（重点）

编辑 `/opt/stability-test-agent/.env`：

```env
# 可先复制模板：
# cp deploy/control-plane/env/agent.env.example /opt/stability-test-agent/.env
API_URL=http://<控制平面IP或域名>
HOST_ID=<唯一且非0的整数>
POLL_INTERVAL=10
MOUNT_POINTS=
ADB_PATH=adb
LOG_LEVEL=INFO
```

注意：
- `HOST_ID` 是逻辑主机标识，不是 IP。
- 同一 `HOST_ID` 不可被多台 Agent 复用。
- 不可使用 `HOST_ID=0`。
- `HOST_ID` 必须与后端数据库中的 `hosts.id` 对齐，否则会出现“心跳正常但拉不到任务”。

建议在控制平面执行以下查询后再填 Agent 配置：

```bash
curl -s -H "Authorization: Bearer <access_token>" http://127.0.0.1:8000/api/v1/hosts
```

按目标主机 IP 找到对应 `id`，把该 `id` 写入 Agent 的 `HOST_ID`。

### 4.3 启动与验证

```bash
sudo systemctl restart stability-test-agent
sudo systemctl status stability-test-agent --no-pager
sudo journalctl -u stability-test-agent -n 100 --no-pager
```

## 5. 上线前验收（最小闭环）

按以下顺序验收，全部通过才允许上线：

0. **主链路 smoke（Plan → PlanRun → Job 终态）** — 勾选后上线：

- [ ] 已在预发布环境执行 `backend/scripts/seed_and_smoke.py`（见 `docs/preprod-drill-runbook.md` §4.0），或完成等价手动验收
- [ ] 脚本/人工记录：`plan_run_id`、终态 `SUCCESS`（或已批准的 `PARTIAL_SUCCESS`）、关联 `device_id` / `host_id`
- [ ] CI 集成级 smoke 已通过（`backend-test` job 内 `main-chain-integration-smoke`；不替代本项）

```bash
# 控制平面主机示例（操作者自行 export STP_ADMIN_PASSWORD；需真实 ONLINE 设备）
export STP_ADMIN_PASSWORD='<your-password>'
python backend/scripts/seed_and_smoke.py \
  --backend http://127.0.0.1:8000 \
  --target-host-id <hosts.id> \
  --device-id <device.id> \
  --no-hot-update \
  --timeout 600
```

1. 控制平面健康检查：
```bash
curl -s http://127.0.0.1:8000/
curl -s http://<控制平面IP>/api/v1/hosts
```
2. 任意 Agent 主机可访问控制平面：
```bash
curl -v --max-time 5 http://<控制平面IP>:80/
curl -v --max-time 5 http://<控制平面IP>/api/v1/hosts
```
3. Dashboard 可见主机 `ONLINE` 与设备 `ONLINE/BUSY` 实时状态。
4. 创建一个绑定设备任务后，状态应完整经历：
`PENDING -> QUEUED -> RUNNING -> COMPLETED|FAILED|CANCELED`
5. 任务终态后设备锁释放（设备可再次调度）。

## 6. 迁移切换步骤（WSL -> 主 Linux Host）

1. 停止 WSL 内旧服务：
```bash
./stop-backend-wsl.sh
sudo systemctl stop stability-test-agent
```
2. 在主 Linux Host 启动后端与 Nginx。
3. 批量更新所有 Agent 的 `API_URL` 指向新控制平面地址。
4. 检查是否存在误配置 `HOST_ID=0` 的 Agent，发现即修复并重启。
5. 观察 30 分钟：心跳、任务分发、任务回收均正常后再开放业务使用。

## 7. 运维最小值班清单

- 日常巡检：
  - `systemctl status stability-backend`
  - `systemctl status nginx`
  - `journalctl -u stability-backend -n 100 --no-pager`
- 故障快速定位：
  - Agent 端：`journalctl -u stability-test-agent -f`
  - 后端连通性：`curl http://127.0.0.1:8000/api/v1/hosts`
- 备份：
  - PostgreSQL 日常备份（使用 pg_dump 或 WAL归档）

## 8. MVP 后续增强（建议排期）

- Agent 注册与 API Token 鉴权。
- Nginx HTTPS（证书管理）。
- 统一日志与监控告警（心跳超时、任务失败率、队列积压）。

详见 [生产就绪评估（2026-05-23 归档）](./archive/assessments/production-readiness-assessment-2026-05-23.md)；当前发版验收见 [`acceptance/00-platform-smoke.md`](./acceptance/00-platform-smoke.md)。
