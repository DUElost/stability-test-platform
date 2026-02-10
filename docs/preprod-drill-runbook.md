# 预发布演练 Runbook（逐条执行）

目标：在 1 台控制平面主机 + 1 台 Linux Agent 上跑通完整闭环：  
`任务创建 -> 自动分发 -> Agent 执行 -> 完成回传 -> UI 可见`

---

## 0. 变量约定（先改成你的实际值）

在控制平面主机执行：

```bash
export CONTROL_IP="172.21.10.15"
export CONTROL_DIR="/opt/stability-test-platform"
export DEPLOY_USER="$USER"
```

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

cp deploy/control-plane/env/.env.backend.example "$CONTROL_DIR/.env.backend"
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
npm run build

sudo cp "$CONTROL_DIR/deploy/control-plane/nginx/stability-platform.conf" /etc/nginx/sites-available/stability-platform
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
API_URL=http://$CONTROL_IP:8000
HOST_ID=<填写与后端 hosts.id 对齐的正整数>
POLL_INTERVAL=10
MOUNT_POINTS=
ADB_PATH=adb
LOG_LEVEL=INFO
EOF
```

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

---

## 4. E2E 闭环演练

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

1. 任务状态完整流转：`PENDING -> QUEUED -> RUNNING -> COMPLETED|FAILED|CANCELED`
2. `task.target_device_id == run.device_id`（目标设备不漂移）
3. 任务终态后设备锁释放（设备可再次被分发）
4. Dashboard 状态在 1-2 秒内有增量更新
5. 无 `HOST_ID=0` Agent 在线

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
