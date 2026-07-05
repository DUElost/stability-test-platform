# 预发布演练 Runbook（逐条执行）

目标：在 1 台控制平面主机 + 1 台 Linux Agent 上跑通完整闭环：  
`任务创建 -> 自动分发 -> Agent 执行 -> 完成回传 -> UI 可见`

预发布按生产形态演练：控制平面运行在 Linux 宿主机（systemd + Nginx + PostgreSQL + Redis），不使用根目录 Docker Compose；Docker Compose 仅用于开发隔离环境。

---

## 0. 变量约定（先改成你的实际值）

在控制平面主机执行：

```bash
export CONTROL_IP="172.21.10.15"
export CONTROL_BASE_URL="http://$CONTROL_IP"
export CONTROL_DIR="/opt/stability-test-platform"
export DEPLOY_USER="$USER"
```

若预发布已启用 HTTPS，将 `CONTROL_BASE_URL` 改为 `https://<控制平面域名>`。

---

## 1. 控制平面部署（主 Linux Host）

### 1.1 准备环境

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip nginx curl
```

Node 20（nvm）：

```bash
curl -fsSL https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.3/install.sh | bash
source ~/.nvm/nvm.sh
nvm install 20
nvm use 20
```

### 1.2 同步代码

```bash
sudo mkdir -p "$CONTROL_DIR"
sudo chown -R "$DEPLOY_USER:$DEPLOY_USER" "$CONTROL_DIR"
cd "$CONTROL_DIR"
# 方式1：git clone 到该目录
# 方式2：从当前开发机 rsync 到该目录
```

### 1.3 后端安装与服务化

```bash
cd "$CONTROL_DIR"
python3 -m venv venv
source venv/bin/activate
pip install -r backend/requirements.txt
cd backend && ../venv/bin/python -m alembic upgrade head && cd ..

python3 tools/prepare_env.py --template deploy/control-plane/env/.env.backend.example --target "$CONTROL_DIR/.env.backend"
mkdir -p "$CONTROL_DIR/logs"

cp deploy/control-plane/systemd/stability-backend.service /tmp/stability-backend.service
sed -i "s|<deploy-user>|$DEPLOY_USER|g" /tmp/stability-backend.service
sudo cp /tmp/stability-backend.service /etc/systemd/system/stability-backend.service

sudo systemctl daemon-reload
sudo systemctl enable stability-backend
sudo systemctl restart stability-backend
sudo systemctl status stability-backend --no-pager
```

### 1.4 前端构建与 Nginx

```bash
cd "$CONTROL_DIR/frontend"
npm install
VITE_API_BASE_URL= npm run build

sudo cp "$CONTROL_DIR/deploy/control-plane/nginx/stability-platform.conf" /etc/nginx/sites-available/stability-platform
# 如已具备证书，优先改用 stability-platform-https.conf
sudo ln -sf /etc/nginx/sites-available/stability-platform /etc/nginx/sites-enabled/stability-platform
sudo nginx -t
sudo systemctl restart nginx
sudo systemctl status nginx --no-pager
```

### 1.5 控制平面健康检查

```bash
curl -s http://127.0.0.1:8000/
curl -s "http://$CONTROL_IP/api/v1/hosts"
```

---

## 2. 接入 1 台 Linux Agent（灰度）

在 Agent 主机执行：

### 2.1 安装 Agent

```bash
mkdir -p /tmp/agent-install
cd /tmp/agent-install
# 拷贝 backend/agent/* 与 install_agent.sh 到此目录
chmod +x install_agent.sh
sudo ./install_agent.sh
```

### 2.2 配置 Agent

```bash
sudo cp /opt/stability-test-agent/.env /opt/stability-test-agent/.env.bak.$(date +%F-%H%M%S) || true
sudo tee /opt/stability-test-agent/.env > /dev/null << EOF
API_URL=$CONTROL_BASE_URL
HOST_ID=<填写与后端 hosts.id 对齐的正整数>
AUTO_REGISTER_HOST=false
AGENT_SECRET=<填写与控制平面 AGENT_SECRET 一致的值>
POLL_INTERVAL=10
MOUNT_POINTS=
ADB_PATH=adb
LOG_LEVEL=INFO
EOF
```

`stability-backend.service` 默认只监听 `127.0.0.1:8000`，远端 Agent 应访问 Nginx 对外入口（HTTP/HTTPS 域名或 IP），不要直接指向控制平面的 `:8000` 端口，除非你明确额外开放了 backend 端口。

### 2.3 启动验证

```bash
sudo systemctl restart stability-test-agent
sudo systemctl status stability-test-agent --no-pager
sudo journalctl -u stability-test-agent -n 80 --no-pager
```

---

## 3. HOST_ID 对齐检查（控制平面）

在控制平面主机执行：

```bash
curl -s "http://127.0.0.1:8000/api/v1/hosts"
```

按 Agent 主机 IP 找到 `id`，确保 Agent `.env` 的 `HOST_ID` 与该 `id` 一致。  
若不一致，Agent 会出现“心跳正常但拉不到任务”。

同时确认 Agent `.env` 的 `AGENT_SECRET` 与控制平面 `.env.backend` 一致；不一致时 Agent API / SocketIO 会被拒绝。

`AUTO_REGISTER_HOST=true` 仅保留给旧 agent 或临时实验环境，本 Runbook 的预发布与上线前验收一律按固定 `HOST_ID` 执行。

---

## 4. E2E 闭环演练

### 4.0 主链路 smoke 脚本（推荐，Plan/PlanRun 全路径）

在**控制平面主机**（后端已启动、至少 1 台 Agent ONLINE、至少 1 台设备 ONLINE）执行：

```bash
cd "$CONTROL_DIR"
source venv/bin/activate
# 开发环境：可在仓库根目录 .env 中设置 STP_ADMIN_PASSWORD / STP_SMOKE_ORIGIN / STP_ADMIN_USER，
# 脚本启动时会自动加载（不覆盖 shell 已 export 的变量）。生产/CI 仍建议显式 export。
export STP_ADMIN_PASSWORD='<your-password>'
# 可选：与 CORS 白名单一致（默认 http://localhost:5173）
export STP_SMOKE_ORIGIN='http://localhost:5173'
# 可选：默认 admin；若库里 admin 用户名不同须与此一致
export STP_ADMIN_USER=admin

# 开发库首次 smoke 或 401 Incorrect username or password 时（DEV ONLY）：
# 按 .env 创建/重置 admin 密码（不打印密码）：
python backend/scripts/reset_dev_admin_password.py
# 等价 SQL（仅本地排障）：UPDATE users SET role='admin', is_active='Y' WHERE username='admin';
# 密码须用 get_password_hash 写入，故优先用上面的脚本。

python backend/scripts/seed_and_smoke.py \
  --backend "http://127.0.0.1:8000" \
  --timeout 600
```

**开发环境**可省略 `--device-id` 与 `--target-host-id`：脚本登录后会 `GET /api/v1/devices`，自动选用 **status=ONLINE** 且已关联 host 的设备，并将 `host_id` 同步用于热更新（若该 host 下无 ONLINE 设备，则尝试第一个 ONLINE host 下的设备）。预发布/生产仍建议显式传入，避免误选环境。

显式指定设备示例（预发布/多机环境）：

```bash
python backend/scripts/seed_and_smoke.py \
  --backend "http://127.0.0.1:8000" \
  --target-host-id "<hosts.id>" \
  --device-id <device.id> \
  --timeout 600
```

常用变体：

| 场景 | 命令 |
|------|------|
| 跳过 Agent 热更新（SSH 未配或仅验 API） | 加 `--no-hot-update` |
| 只触发不等待终态 | 加 `--no-wait` |
| 延长轮询 | `--timeout 900` |

**同名 Plan 复跑**：脚本会先尝试 `DELETE` 旧 `smoke-plan-001`；若该 Plan 已有历史 PlanRun（开发库常见），后端返回 `409`，脚本自动 **PUT 更新步骤定义** 后继续触发，无需手工清库。

**通过标准**（与 §5 一致）：脚本退出码 `0`；PlanRun 终态为 `SUCCESS`（或业务可接受的 `PARTIAL_SUCCESS`）；Job 为 `COMPLETED`；`step_traces` 含 init/patrol/teardown 步骤。

**CI 说明**：GitHub Actions 默认跑 **集成级 smoke**（mock Agent RPC，无真实设备）；本脚本用于**预发布/上线前人工验收**，须在 checklist 勾选。

---

### 4.1 UI 侧操作

1. 打开 `http://<控制平面IP>/`
2. 确认 Dashboard 中目标 Host 为 `ONLINE`
3. 创建一个绑定目标设备的任务（必须选定设备）

### 4.2 后端日志观察（控制平面）

```bash
sudo journalctl -u stability-backend -f
```

预期看到：
- `POST /api/v1/heartbeat`（来自 Agent IP）
- `GET /api/v1/agent/runs/pending?host_id=<n>`
- `POST /api/v1/agent/runs/{id}/heartbeat`
- `POST /api/v1/agent/runs/{id}/complete`

### 4.3 Agent 日志观察（Agent 主机）

```bash
sudo journalctl -u stability-test-agent -f
```

预期看到：
- `heartbeat_success`
- `pending_runs_fetched`
- `run_start`
- `run_complete`

---

## 5. 验收标准（全部满足才通过）

- [ ] **主链路 smoke**：`seed_and_smoke.py` 已执行且退出码为 0（§4.0；或等价 UI 全路径手动验收并记录 plan_run_id）

1. Job 状态完整流转：`PENDING -> RUNNING -> COMPLETED|FAILED|ABORTED`；心跳丢失 / patrol stall 时符合 `RUNNING -> UNKNOWN -> RUNNING|FAILED`
2. `task.target_device_id == run.device_id`（目标设备不漂移）
3. 任务终态后设备锁释放（设备可再次被分发）
4. Dashboard 状态在 1-2 秒内有增量更新
5. 无 `HOST_ID=0` Agent 在线

---

## 5.1 Job / 租约超时（分级 heartbeat）

recycler 对 RUNNING Job 按阶段选用不同心跳丢失阈值（见 `backend/core/job_timeout_config.py`）：

| 变量 | 生产默认 | 说明 |
|------|---------|------|
| `RUNNING_HEARTBEAT_TIMEOUT_SECONDS` | 900 | init / teardown 等非 patrol 活跃阶段 |
| `PATROL_RUNNING_HEARTBEAT_TIMEOUT_SECONDS` | 300（dev 180） | 已进入 patrol 周期（`patrol_cycle_count > 0` 或 patrol 心跳/步骤信号） |
| `UNKNOWN_GRACE_SECONDS` | 300 | UNKNOWN 宽限期；UI `grace_remaining_seconds` 与此同步 |
| `DISPATCHED_TIMEOUT_SECONDS` | 120 | PENDING 未被认领；UI `pending_claim_remaining_seconds` 与此同步 |

**排查**：Agent 假 RUNNING 时，先看 Job 是否处于 patrol（PlanRun devices 矩阵 / Drawer 的 `current_stage`）；patrol 场景下约 300s 无 patrol 心跳会 → UNKNOWN，再经 grace 释放租约。调整 patrol 窗口时须评估 `plan.patrol_interval_seconds`，避免短于巡检间隔导致误杀。

---

## 6. 回滚步骤（失败时立即执行）

控制平面主机：

```bash
sudo systemctl stop stability-backend
sudo systemctl stop nginx
# 如果有旧版本配置，恢复旧 service/nginx 配置并重启
```

Agent 主机：

```bash
sudo cp /opt/stability-test-agent/.env.bak.* /opt/stability-test-agent/.env 2>/dev/null || true
sudo systemctl restart stability-test-agent
```

---

## 7. 演练通过后下一步

1. 扩展到 2-3 台 Agent 做并发验证（30-60 分钟）
2. 增加 logrotate 与基础告警（心跳超时/任务失败率）
3. 进入小规模生产灰度
