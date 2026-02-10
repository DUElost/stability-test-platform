# Agent 服务部署指南

本文档介绍如何在 Linux 主机上部署和运行 Stability Test Platform Agent。

说明：
- 本文档仅覆盖 Agent 部署，安装入口在 `backend/agent/install_agent.sh`。
- 控制平面（后端+前端+Nginx）模板位于 `deploy/control-plane/`。

---

## 前提条件

- Linux 操作系统（Ubuntu 20.04+, CentOS 7+, 或其他主流发行版）
- Python 3.8+
- root 或 sudo 权限
- ADB 工具（如需管理 Android 设备）

---

## 快速部署

### 1. 准备安装文件

将 Agent 目录复制到目标主机：

```bash
# 在目标主机上创建临时目录
mkdir -p /tmp/agent-install
cd /tmp/agent-install

# 复制以下文件到当前目录
# - agent/ 目录（包含所有 Python 代码）
# - install_agent.sh
```

### 2. 运行安装脚本

```bash
chmod +x install_agent.sh
sudo ./install_agent.sh
```

安装脚本将自动完成：
- 创建专用用户 `stability-test`
- 设置安装目录 `/opt/stability-test-agent`
- 配置 Python 虚拟环境
- 安装 systemd 服务
- 创建管理脚本 `agentctl`

### 3. 配置环境变量

编辑配置文件：

```bash
sudo nano /opt/stability-test-agent/.env
```

关键配置项：

```bash
# 服务器地址（必填）
API_URL=http://your-server-ip:8000

# 主机 ID（必填，从服务器获取）
HOST_ID=1

# 心跳间隔（秒）
POLL_INTERVAL=10

# 挂载点检查（可选）
MOUNT_POINTS=/mnt/data,/mnt/logs

# ADB 路径（如需管理设备）
ADB_PATH=adb

# 日志级别
LOG_LEVEL=INFO
```

### 4. 启动服务

```bash
# 启动服务
sudo agentctl start

# 查看状态
sudo agentctl status

# 查看日志
sudo agentctl logs
```

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

# 查看错误日志
sudo journalctl -u stability-test-agent -n 50 --no-pager

# 检查配置文件
sudo cat /opt/stability-test-agent/.env

# 检查 Python 环境
sudo /opt/stability-test-agent/venv/bin/python --version
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

# 检查 ADB 路径配置
grep ADB_PATH /opt/stability-test-agent/.env
```

---

## 手动测试

在启动服务前，可以手动测试 Agent：

```bash
# 切换到安装目录
cd /opt/stability-test-agent

# 激活虚拟环境
source venv/bin/activate

# 手动运行（前台模式，方便调试）
python -m agent.main
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

如需部署到多台主机，可以使用以下脚本：

```bash
#!/bin/bash
# batch_deploy.sh

HOSTS=("192.168.1.101" "192.168.1.102" "192.168.1.103")

for i in "${!HOSTS[@]}"; do
    HOST="${HOSTS[$i]}"
    HOST_ID=$((i + 1))

    echo "部署到 $HOST (HOST_ID=$HOST_ID)..."

    # 复制文件
    scp -r agent/ install_agent.sh root@$HOST:/tmp/agent-install/

    # 远程执行安装
    ssh root@$HOST << EOF
        cd /tmp/agent-install
        sed -i "s/^HOST_ID=.*/HOST_ID=$HOST_ID/" .env.example
        sudo ./install_agent.sh
        systemctl start stability-test-agent
EOF
done
```

---

## 下一步

部署完成后，可以：

1. 在服务器端查看主机状态
2. 分配测试任务
3. 查看设备监控数据
4. 配置告警规则

---

*文档版本: 1.0.0*
*最后更新: 2026-01-22*
