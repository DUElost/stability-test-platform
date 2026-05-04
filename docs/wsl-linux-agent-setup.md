# WSL 本地 Linux Host Agent 配置教程

用于在 Windows WSL 环境中部署 Linux Host Agent，验证 AIMONKEY 功能。

## 前提条件

- Windows 10/11 已启用 WSL2
- 已安装 Ubuntu 或其他 Linux 发行版
- Windows 已配置 ADB 并能识别设备

## 步骤 1: WSL 环境准备

### 1.1 启动 WSL 并更新系统

```bash
wsl -d Ubuntu

# 更新系统包
sudo apt update && sudo apt upgrade -y
```

### 1.2 安装必要依赖

```bash
# 安装 Python 3.8+ 和 pip
sudo apt install -y python3 python3-pip python3-venv

# 安装 ADB
sudo apt install -y android-tools-adb

# 安装其他工具
sudo apt install -y git curl vim
```

### 1.3 配置 ADB 连接

WSL 需要访问 Windows 主机的 ADB 服务：

```bash
# 在 Windows PowerShell (管理员) 中执行
adb kill-server
adb -a -P 5037 nodaemon server

# 在 WSL 中设置 ADB 服务器地址
export ADB_SERVER_SOCKET=tcp:$(cat /etc/resolv.conf | grep nameserver | awk '{print $2}'):5037

# 验证连接
adb devices
```

## 步骤 2: 部署 Linux Host Agent

### 2.1 创建应用目录

```bash
# 创建应用目录
sudo mkdir -p /opt/stability-test-agent
sudo chown $USER:$USER /opt/stability-test-agent
cd /opt/stability-test-agent
```

### 2.2 复制项目文件

从 Windows 项目目录复制到 WSL：

```bash
# 在 WSL 中执行，将 Windows 路径的项目复制到 WSL
rsync -av --exclude='.git' --exclude='node_modules' --exclude='__pycache__' \
    /mnt/d/Tinno_auto/Stability-Tools/stability-test-platform/backend/ \
    /opt/stability-test-agent/
```

或使用 Windows PowerShell：

```powershell
# 在 PowerShell 中执行
wsl -d Ubuntu -e bash -c "mkdir -p /opt/stability-test-agent"

# 复制文件
wsl -d Ubuntu -e cp -r /mnt/d/Tinno_auto/Stability-Tools/stability-test-platform/backend/* /opt/stability-test-agent/
```

### 2.3 创建 Python 虚拟环境

```bash
cd /opt/stability-test-agent

# 创建虚拟环境
python3 -m venv venv

# 激活虚拟环境
source venv/bin/activate

# 升级 pip
pip install --upgrade pip
```

### 2.4 安装 Python 依赖

```bash
# 创建 requirements.txt
cat > requirements.txt << 'EOF'
requests>=2.28.0
fastapi>=0.95.0
uvicorn>=0.21.0
pydantic>=1.10.0
python-dotenv>=1.0.0
EOF

# 安装依赖
pip install -r requirements.txt
```

## 步骤 3: 配置 AIMONKEY 资源

### 3.1 创建资源目录

```bash
# 创建 AIMONKEY 资源目录
mkdir -p /opt/stability-test-agent/resources/aimonkey
```

### 3.2 复制 Monkey 资源文件

```bash
# 从 Windows 项目复制 AIMonkey 资源
rsync -av /mnt/d/Tinno_auto/Stability-Tools/Monkey_test/AIMonkeyTest_2025mtk/ \
    /opt/stability-test-agent/resources/aimonkey/

# 确保文件可执行
chmod +x /opt/stability-test-agent/resources/aimonkey/aim
chmod +x /opt/stability-test-agent/resources/aimonkey/aimwd
```

### 3.3 验证资源文件

```bash
ls -la /opt/stability-test-agent/resources/aimonkey/

# 应该包含以下文件:
# - aim
# - aimwd
# - aim.jar
# - aimonkey.apk
# - blacklist.txt
# - arm64-v8a/ (目录)
# - armeabi-v7a/ (目录)
```

## 步骤 4: 配置环境变量

### 4.1 创建环境变量文件

```bash
cat > /opt/stability-test-agent/.env << 'EOF'
# Agent 配置
AGENT_ID=wsl-local-agent
AGENT_NAME=WSL Local Agent
API_BASE_URL=http://localhost:8000
LOG_LEVEL=INFO

# AIMONKEY 资源路径
AIMONKEY_RESOURCE_DIR=/opt/stability-test-agent/resources/aimonkey

# ADB 配置
ADB_PATH=/usr/bin/adb
ADB_SERVER_SOCKET=tcp:172.17.0.1:5037
EOF
```

### 4.2 配置自动激活脚本

```bash
cat >> ~/.bashrc << 'EOF'

# Stability Test Agent 配置
export AIMONKEY_RESOURCE_DIR=/opt/stability-test-agent/resources/aimonkey
export ADB_SERVER_SOCKET=tcp:$(cat /etc/resolv.conf | grep nameserver | awk '{print $2}'):5037
export PATH="/opt/stability-test-agent/venv/bin:$PATH"
EOF
```

## 步骤 5: 启动 Agent

### 5.1 手动启动方式

```bash
cd /opt/stability-test-agent

# 激活虚拟环境
source venv/bin/activate

# 启动 Agent
python -m agent.main
```

### 5.2 使用 systemd 服务（推荐）

```bash
# 创建 systemd 服务文件
sudo tee /etc/systemd/system/stability-test-agent.service > /dev/null << 'EOF'
[Unit]
Description=Stability Test Agent
After=network.target

[Service]
Type=simple
User=$USER
WorkingDirectory=/opt/stability-test-agent
Environment=AIMONKEY_RESOURCE_DIR=/opt/stability-test-agent/resources/aimonkey
Environment=PYTHONPATH=/opt/stability-test-agent
ExecStart=/opt/stability-test-agent/venv/bin/python -m agent.main
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

# 替换 $USER 为实际用户名
sudo sed -i "s/\$USER/$USER/g" /etc/systemd/system/stability-test-agent.service

# 重新加载 systemd
sudo systemctl daemon-reload

# 启动服务
sudo systemctl start stability-test-agent

# 查看状态
sudo systemctl status stability-test-agent

# 设置开机自启
sudo systemctl enable stability-test-agent
```

## 步骤 6: 验证 AIMONKEY 功能

### 6.1 检查 Agent 状态

```bash
# 检查服务状态
sudo systemctl status stability-test-agent

# 查看日志
sudo journalctl -u stability-test-agent -f
```

### 6.2 运行单元测试

```bash
cd /opt/stability-test-agent

# 激活虚拟环境
source venv/bin/activate

# 运行 AIMONKEY 测试
python -m agent.test_aimonkey
```

### 6.3 手动执行 AIMONKEY 任务

创建测试脚本：

```bash
cat > /tmp/test_aimonkey.py << 'EOF'
import sys
sys.path.insert(0, '/opt/stability-test-agent')

from agent.task_executor import TaskExecutor
from agent.adb_wrapper import AdbWrapper

# 创建执行器
adb = AdbWrapper()
executor = TaskExecutor(adb)

# 检查设备
import subprocess
result = subprocess.run(['adb', 'devices'], capture_output=True, text=True)
print("设备列表:")
print(result.stdout)

# 获取第一个设备
serial = None
for line in result.stdout.strip().split('\n')[1:]:
    if line.strip() and '\tdevice' in line:
        serial = line.split('\t')[0]
        break

if not serial:
    print("未找到设备")
    sys.exit(1)

print(f"使用设备: {serial}")

# 执行 AIMONKEY 设置测试
params = {
    "runtime_minutes": 1,  # 测试1分钟
    "throttle_ms": 500,
    "enable_fill_storage": False,
    "enable_clear_logs": False,
}

try:
    print("开始设备配置...")
    executor._aimonkey_setup(serial, params)
    print("✓ 设备配置成功")

    print("开始启动 Monkey...")
    pid = executor._aimonkey_start_monkey(serial, params)
    print(f"✓ Monkey 启动成功, PID: {pid}")

    # 停止 Monkey
    executor._aimonkey_stop_monkey(serial, pid)
    print("✓ Monkey 已停止")

except Exception as e:
    print(f"✗ 错误: {e}")
    import traceback
    traceback.print_exc()
EOF

# 运行测试
source /opt/stability-test-agent/venv/bin/activate
python /tmp/test_aimonkey.py
```

## 常见问题

### Q1: WSL 无法连接 Windows ADB

**解决**: 确保 Windows ADB 服务器以 `-a` 参数启动监听所有接口：

```powershell
# Windows PowerShell (管理员)
adb kill-server
adb -a -P 5037 nodaemon server
```

### Q2: 权限错误

**解决**: 确保资源文件有执行权限：

```bash
chmod +x /opt/stability-test-agent/resources/aimonkey/aim*
```

### Q3: Python 模块导入错误

**解决**: 确保 PYTHONPATH 包含项目目录：

```bash
export PYTHONPATH=/opt/stability-test-agent:$PYTHONPATH
```

### Q4: 找不到 AIMONKEY 资源

**解决**: 检查环境变量：

```bash
echo $AIMONKEY_RESOURCE_DIR
ls -la $AIMONKEY_RESOURCE_DIR
```

## 快速命令参考

```bash
# 启动服务
sudo systemctl start stability-test-agent

# 停止服务
sudo systemctl stop stability-test-agent

# 重启服务
sudo systemctl restart stability-test-agent

# 查看日志
sudo journalctl -u stability-test-agent -f

# 查看 Agent 帮助
cd /opt/stability-test-agent && source venv/bin/activate && python -m agent --help
```

## 下一步

完成部署后，你可以：

1. 通过 API 提交 AIMONKEY 任务
2. 在 Platform Web 界面监控任务执行
3. 查看收集的日志文件在 `/opt/stability-test-agent/logs/`
