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
│   ├── heartbeat_thread.py
│   ├── device_discovery.py
│   ├── system_monitor.py
│   ├── job_runner.py                   # 替代旧 task_executor
│   ├── pipeline_engine.py              # lifecycle 执行引擎
│   ├── pipeline_runner.py
│   ├── adb_wrapper.py
│   ├── registry/                       # 本地注册表
│   │   ├── local_db.py                 # SQLite WAL
│   │   └── script_registry.py          # script:<name> 解析
│   ├── watcher/                        # ADR-0018 设备日志监控
│   │   └── ...
│   └── scripts/                        # 可执行脚本（扁平布局）
│       └── <name>/v<version>/<entry>.py
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
- 只部署 `agent/` 包代码（清理测试文件）
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
# - 远程 Linux 主机：使用 Linux 控制平面的固定域名或固定 IP
# - WSL 环境（同机开发联调）：使用 127.0.0.1（安装脚本自动检测并设置）
API_URL=http://172.21.10.5:8000

# 主机 ID（生产/预发布默认使用固定 hosts.id）
HOST_ID=12
AUTO_REGISTER_HOST=false

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

> **生产建议**
>
> - 预发布 / 生产环境下请显式配置固定 `HOST_ID`，并保证与后端 `hosts.id` 一致。
> - `AUTO_REGISTER_HOST=true` 仅保留给临时实验环境或旧 agent 兼容，不应作为默认部署方式。

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

### ModuleNotFoundError after upgrade

**原因**：部署目录中仍保留旧代码，或本地 fork 引用了当前 agent 包中不存在的模块。

**修复**：重新同步 agent 目录，并用 `--delete` 删除目标机上的过期文件：
```bash
rsync -av --delete <source>/backend/agent/ /tmp/agent-install/
sed -i 's/\r$//' /tmp/agent-install/install_agent.sh
cd /tmp/agent-install && sudo bash install_agent.sh
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

### Job 卡在 PENDING（设备租约残留）

Job 异常终止后可能遗留 ACTIVE 租约（`device_leases.status = 'ACTIVE'`），导致后续 Job 无法被 claim。

**自动恢复**：Reconciler（15s 间隔）会自动处理过期租约——先将关联 Job 标为 UNKNOWN，grace 到期后释放租约并标 FAILED。

**手动诊断**：
```sql
-- 查看所有 ACTIVE 租约（含过期）
SELECT dl.device_id, dl.job_id, dl.status, dl.expires_at,
       d.serial, d.status AS device_status
FROM device_leases dl
JOIN device d ON d.id = dl.device_id
WHERE dl.status = 'ACTIVE'
ORDER BY dl.expires_at;

-- 紧急手动释放（仅当 Reconciler 不可用时使用）
UPDATE device_leases SET status = 'RELEASED', released_at = now()
WHERE device_id = <device_id> AND status = 'ACTIVE';
```

> **注意**：数据库运行在 Windows 侧，连接串为 `postgresql://stability:stability@localhost:5432/stability`。

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

## Ansible 运维入口

从 2026-04-20 起，Linux agent host 的推荐运维入口为 `tools/ansible/` 下的 Ansible playbook。

- 首次部署：`tools/ansible/playbooks/install_agent.yml`
- 热更新：`tools/ansible/playbooks/update_agent.yml`
- 服务管理：`tools/ansible/playbooks/service_agent.yml`
- 状态检查：`tools/ansible/playbooks/check_agent.yml`

详细命令见 `tools/ansible/README.md`。

---

如需部署到多台主机：

```bash
#!/bin/bash
# batch_deploy.sh
# 用法: ./batch_deploy.sh

HOSTS=("192.168.1.101" "192.168.1.102" "192.168.1.103")
AGENT_SRC="backend/agent"
API_URL="http://172.21.10.5:8000"

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

已部署主机无需重新安装。当前提供两条互补入口，**保留 `.env` 和所有数据目录不变**：

| 入口 | 适用场景 |
|---|---|
| 前端「主机管理」页面的「热更新」按钮 | 单台一键热更新；后端 `POST /api/v1/hosts/{host_id}/hot-update` 通过 SSH+rsync 推送代码并重启服务 |
| `tools/ansible/playbooks/update_agent.yml` | 线下批量热更新；统一回写 `API_URL` / `AGENT_SECRET` |

详细 Ansible 命令见 `tools/ansible/README.md`。

---

## 方案 C 存储与环境变量（ADR-0025）

> 详述：[`docs/design/2026-plan-c-storage-and-access.md`](../../docs/design/2026-plan-c-storage-and-access.md)

| 变量 | 默认值 / 说明 |
|------|----------------|
| `STP_AEE_LOCAL_ROOT` | AEE 设备日志 HDD 第一落点（默认 `/mnt/hdd/aee_events`） |
| `STP_AEE_CIFS_ROOT` | 15.4 CIFS 挂载根（HDD 溢出上送、Sprint 4 按需上送） |
| `STP_WATCHER_AEE_SUBDIR_LAYOUT` | 默认 `stp`（`mobilelog/`、`bugreport/`）；`correlated` 为旧布局 |

**行为变更**：

- 运行日志仅保留在 Agent SSD `logs/runs/{job_id}/`，**不上送** 15.4
- **访问**：执行中经 SocketIO 推送到控制面（UI 实时控制台 / `GET /api/v1/logs/query`）；事后经控制面 `POST /api/v1/agent/logs` SSH 读取 Agent 磁盘
- 不再向控制面注册 `run_log_bundle` JobArtifact
- LogArchiver 仅做 SSD prune（grace 后删除本地目录）
- HDD 将满时 `HddSpillMonitor` 溢出最旧 AEE 事件目录到 15.4 `devices/`

---

## 下一步

部署完成后，可以：

1. 在服务器端查看主机状态
2. 分配测试任务
3. 查看设备监控数据
4. 配置告警规则

---

*文档版本: 1.4.0*
*最后更新: 2026-06-21*
