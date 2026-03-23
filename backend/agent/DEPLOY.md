# Agent 服务部署指南

本文档介绍如何在 Linux 主机上部署和运行 Stability Test Platform Agent。

说明：
- 本文档仅覆盖 Agent 部署，安装入口在 `backend/agent/install_agent.sh`。
- 控制平面（后端+前端+Nginx）模板位于 `deploy/control-plane/`。

---

## 标准目录结构

部署后的目录布局：

```
/opt/stability-test-agent/              # INSTALL_DIR（可通过 AGENT_INSTALL_DIR 环境变量覆盖）
├── .env                                # 环境配置
├── agent/                              # Agent Python 包（唯一代码目录）
│   ├── __init__.py
│   ├── main.py                         # 入口：python -m agent.main
│   ├── config.py                       # 集中路径常量
│   ├── heartbeat.py
│   ├── device_discovery.py
│   ├── system_monitor.py
│   ├── task_executor.py
│   ├── adb_wrapper.py
│   ├── tool_discovery.py
│   ├── test_framework.py
│   ├── test_stages.py
│   ├── aimonkey_aee.py
│   ├── aimonkey_risk.py
│   ├── aimonkey_stages.py
│   └── tools/                          # 测试工具插件
│       ├── __init__.py
│       ├── monkey_test.py
│       ├── aimonkey_test.py
│       └── ...
├── resources/                          # 测试资源文件
│   └── aimonkey/                       # AIMONKEY 二进制与配置
├── logs/                               # 所有日志统一目录
│   ├── agent.log                       # systemd stdout
│   ├── agent_error.log                 # systemd stderr
│   └── runs/                           # 每次任务执行的独立日志
│       └── {run_id}/
├── tmp/                                # 临时文件
└── venv/                               # Python 虚拟环境
```

---

## 前提条件

- Linux 操作系统（Ubuntu 20.04+, CentOS 7+, 或其他主流发行版）
- Python 3.8+
- root 或 sudo 权限
- ADB 工具（如需管理 Android 设备）

---

## 快速部署

只需同步 `backend/agent/` 目录到目标主机，运行安装脚本即可。不需要复制 `backend/` 其他模块。

### 1. 同步代码到目标主机

**远程 Linux 主机**：
```bash
# 从 Windows 开发机同步（通过 SSH）
rsync -av --delete backend/agent/ user@target-host:/tmp/agent-install/

# 或使用 scp
scp -r backend/agent/* user@target-host:/tmp/agent-install/
```

**WSL（同机模拟）**：
```bash
# 必须先复制到 WSL 本地文件系统，不能直接在 /mnt/ 下运行安装脚本
# 原因：/mnt/ 是 Windows 文件系统 (drvfs)，存在 CRLF 换行符和权限映射问题
rsync -av --delete /mnt/f/stability-test-platform/backend/agent/ /tmp/agent-install/
```

### 2. 运行安装脚本

```bash
cd /tmp/agent-install

# 修复 CRLF 换行符（从 Windows 同步过来的文件可能包含 \r\n）
sed -i 's/\r$//' install_agent.sh

# 运行安装
sudo bash install_agent.sh
```

安装脚本交互提示：
- **API_URL**：WSL 环境直接回车（自动检测使用 `127.0.0.1`）；远程主机输入中心服务器 IP
- **HOST_ID**：直接回车使用建议值，或输入 `auto` 自动注册

安装脚本将自动完成：
- 创建安装目录 `/opt/stability-test-agent`
- 只部署 `agent/` 包代码（清理测试文件，保留 `test_framework.py` 和 `test_stages.py`）
- 配置 Python 虚拟环境并安装依赖
- 安装 systemd 服务（以 `python -m agent.main` 模块模式启动）
- 创建管理脚本 `agentctl`

### 3. 配置环境变量

安装脚本已根据交互输入生成 `.env` 文件。如需调整：

```bash
sudo nano /opt/stability-test-agent/.env
```

关键配置项：

```bash
# 服务器地址（必填）
# - 远程 Linux 主机：使用 Windows 中心服务器的局域网 IP
# - WSL 环境（同机）：使用 127.0.0.1（安装脚本自动检测并设置）
API_URL=http://172.21.10.15:8000

# 主机 ID（推荐 auto，首次心跳时自动注册）
HOST_ID=auto
AUTO_REGISTER_HOST=true

# 心跳间隔（秒）
POLL_INTERVAL=10

# ADB 路径
ADB_PATH=adb

# 日志级别
LOG_LEVEL=INFO

# AIMONKEY 资源目录（可选，默认 /opt/stability-test-agent/resources/aimonkey）
# AIMONKEY_RESOURCE_DIR=/opt/stability-test-agent/resources/aimonkey
```

> **WSL 环境注意事项**
>
> - `API_URL` 必须使用 `http://127.0.0.1:8000`，不能使用 Windows 局域网 IP
> - WSL 的 localhost 自动转发到 Windows 宿主，但局域网 IP 会被 Windows 防火墙拦截
> - 安装脚本会自动检测 WSL 环境（通过 `/proc/version` 包含 `microsoft`）并设置默认值

### 4. 启动服务

```bash
# 重新加载 systemd 配置（安装脚本已执行，更新配置后需重新执行）
sudo systemctl daemon-reload

# 启动服务
sudo systemctl start stability-test-agent

# 查看状态（确认 Active: active (running)）
sudo systemctl status stability-test-agent

# 查看日志
tail -f /opt/stability-test-agent/logs/agent_error.log
```

验证成功标志：
- `systemctl status` 显示 `active (running)`
- 日志中出现 `agent_started` 和 `heartbeat_success`
- 后端 Dashboard 中出现该主机

---

## 管理命令

使用 `agentctl` 管理服务：

```bash
# 启动服务
agentctl start

# 停止服务
agentctl stop

# 重启服务
agentctl restart

# 查看状态
agentctl status

# 查看实时日志
agentctl logs

# 启用开机自启
agentctl enable

# 禁用开机自启
agentctl disable

# 健康检查
agentctl health
```

---

## 服务特性

### 自动重启

Agent 服务配置了自动重启：
- 任何异常退出后 10 秒自动重启
- 300 秒内最多重启 5 次（防止无限重启循环）

### 日志管理

日志文件位置：
- 标准输出：`/opt/stability-test-agent/logs/agent.log`
- 错误输出：`/opt/stability-test-agent/logs/agent_error.log`
- systemd 日志：`journalctl -u stability-test-agent`

### 资源限制

服务配置了以下资源限制：
- 最大文件描述符数：65536
- 最大进程数：4096

---

## 故障排查

### 服务无法启动

```bash
# 查看详细状态
sudo systemctl status stability-test-agent

# 查看错误日志（优先检查这个文件）
sudo tail -50 /opt/stability-test-agent/logs/agent_error.log

# 查看 systemd 日志
sudo journalctl -u stability-test-agent -n 50 --no-pager

# 手动前台运行以观察完整输出
cd /opt/stability-test-agent
sudo PYTHONPATH=/opt/stability-test-agent /opt/stability-test-agent/venv/bin/python -m agent.main
```

### ModuleNotFoundError: No module named 'agent.test_stages'

**原因**：部署时 `test_framework.py` 或 `test_stages.py` 被误删。这两个是生产模块（非测试文件）。

**修复**：重新同步并安装：
```bash
rsync -av --delete <source>/backend/agent/ /tmp/agent-install/
sed -i 's/\r$//' /tmp/agent-install/install_agent.sh
cd /tmp/agent-install && sudo bash install_agent.sh

# 确认这两个文件存在
sudo ls /opt/stability-test-agent/agent/test_framework.py
sudo ls /opt/stability-test-agent/agent/test_stages.py
```

### Shell 脚本报错 `$'\r': command not found`

**原因**：从 Windows 同步的 `.sh` 文件包含 CRLF 换行符（`\r\n`），Linux 无法解析。

**修复**：
```bash
sed -i 's/\r$//' /tmp/agent-install/install_agent.sh
```

### 无法连接服务器

```bash
# 检查网络连接
curl http://your-server-ip:8000/health

# 检查防火墙
sudo firewall-cmd --list-all  # CentOS
sudo ufw status                # Ubuntu
```

### ADB 设备无法发现

```bash
# 检查 ADB 是否可用
adb devices

# 检查 ADB 路径和端口配置
grep -E "^ADB|^ANDROID" /opt/stability-test-agent/.env

# 健康检查（会显示端口和已识别设备数）
agentctl health
```

**WSL 环境 ADB 端口冲突**：若默认 5037 端口被占用导致切换为 5039，但 agent 发现设备为 0，
原因是 `sudo` 运行时会剥离 shell 环境变量。修复方法：

```bash
# 在 .env 中固化端口配置（一次性操作）
echo 'ANDROID_ADB_SERVER_PORT=5039' >> /opt/stability-test-agent/.env
sudo systemctl restart stability-test-agent

# 验证
agentctl health  # 应显示 "端口: 5039" 和 "已识别设备: N 台"

# 手动验证 ADB 连接（必须指定端口）
ANDROID_ADB_SERVER_PORT=5039 adb devices
```

### Job 卡在 PENDING（设备锁残留）

Job 异常终止后可能遗留设备锁（`device.lock_run_id` 不为空，`device.status = BUSY`），导致后续 Job 无法被 claim。

**诊断**：
```bash
# 通过 API 查看设备状态
curl -s http://localhost:8000/api/v1/devices | python3 -m json.tool | grep -A2 '"status": "BUSY"'
```

**修复**（在 Windows 侧或能连接 PostgreSQL 的环境执行）：
```python
import psycopg
conn = psycopg.connect('postgresql://stability:stability@localhost:5432/stability')
conn.autocommit = True
cur = conn.cursor()
# 查看锁状态
cur.execute('SELECT id, lock_run_id, lock_expires_at, status FROM device WHERE status = %s', ('BUSY',))
for row in cur.fetchall(): print(row)
# 清理指定设备的锁
cur.execute('UPDATE device SET lock_run_id = NULL, lock_expires_at = NULL, status = %s WHERE id = %s', ('ONLINE', <device_id>))
conn.close()
```

> **注意**：数据库运行在 Windows 侧，连接串为 `postgresql://stability:stability@localhost:5432/stability`，表名为单数形式（`device`、`host`、`job_instance`）。

---

## 手动测试

在启动 systemd 服务前，可以手动前台运行 Agent 观察输出：

```bash
# 方式 1：部署目录中运行（部署模式）
cd /opt/stability-test-agent
sudo PYTHONPATH=/opt/stability-test-agent \
  /opt/stability-test-agent/venv/bin/python -m agent.main

# 方式 2：从项目源码运行（开发模式，需项目依赖已安装）
cd /path/to/stability-test-platform
python -m backend.agent.main
```

---

## 卸载

```bash
# 停止并禁用服务
sudo agentctl stop
sudo agentctl disable

# 删除服务文件
sudo rm /etc/systemd/system/stability-test-agent.service
sudo systemctl daemon-reload

# 删除安装目录
sudo rm -rf /opt/stability-test-agent

# 删除用户
sudo userdel stability-test
```

---

## 多主机部署

如需部署到多台主机：

```bash
#!/bin/bash
# batch_deploy.sh
# 用法: ./batch_deploy.sh

HOSTS=("192.168.1.101" "192.168.1.102" "192.168.1.103")
AGENT_SRC="backend/agent"
API_URL="http://172.21.10.15:8000"

for HOST in "${HOSTS[@]}"; do
    echo "部署到 $HOST..."

    # 同步代码
    rsync -av --delete "$AGENT_SRC/" root@$HOST:/tmp/agent-install/

    # 远程安装（修复 CRLF + 非交互执行）
    ssh root@$HOST << EOF
        sed -i 's/\r$//' /tmp/agent-install/install_agent.sh
        cd /tmp/agent-install && bash install_agent.sh <<< "$API_URL"
        systemctl daemon-reload
        systemctl start stability-test-agent
        systemctl status stability-test-agent --no-pager
EOF
done
```

---

## 热更新（已部署主机的代码同步）

已部署主机无需重新安装，使用 `sync_agent.sh` 只推送代码变更，**保留 `.env` 和所有数据目录不变**。

### 快速同步

```bash
# 进入 WSL（如从 Windows 开发机操作）
wsl

# WSL 本机
bash /mnt/f/stability-test-platform/backend/agent/sync_agent.sh wsl

# 单台远程主机
bash /mnt/f/stability-test-platform/backend/agent/sync_agent.sh root@172.21.15.10

# 批量（多个 IP）
bash /mnt/f/stability-test-platform/backend/agent/sync_agent.sh 172.21.15.10 172.21.15.11 172.21.15.12
```

### 从 Windows 命令行快速同步（无需进入 WSL shell）

```powershell
# PowerShell / Git Bash / Windows Terminal
wsl -u root -- bash /mnt/f/stability-test-platform/backend/agent/sync_agent.sh wsl
```

### 批量同步（hosts.txt）

在 `backend/agent/` 创建 `hosts.txt`：

```
# Linux Agent 主机列表
172.21.15.10
172.21.15.11
android@172.21.15.12
```

然后一键同步全部：

```bash
bash /mnt/f/stability-test-platform/backend/agent/sync_agent.sh --all
```

### sync_agent.sh 与 install_agent.sh 的区别

| | `install_agent.sh` | `sync_agent.sh` |
|---|---|---|
| 用途 | 首次部署 | 代码热更新 |
| 交互提示 | 需要输入 API_URL / HOST_ID | 无 |
| `.env` | 创建（若不存在） | **不修改** |
| venv / logs | 重建 | **不修改** |
| systemd 服务 | 安装/更新 service 文件 | daemon-reload + restart |

---

## 下一步

部署完成后，可以：

1. 在服务器端查看主机状态
2. 分配测试任务
3. 查看设备监控数据
4. 配置告警规则

---

*文档版本: 1.3.0*
*最后更新: 2026-03-23*
