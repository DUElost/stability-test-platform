#!/bin/bash
#
# Stability Test Platform Agent 安装脚本
# 用法: sudo ./install_agent.sh
#
# 此脚本将：
# 1. 创建专用用户和目录
# 2. 设置 Python 虚拟环境
# 3. 安装依赖
# 4. 配置 systemd 服务
#

set -e

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# 配置
INSTALL_DIR="/opt/stability-test-agent"
SERVICE_NAME="stability-test-agent"
USER="stability-test"
GROUP="stability-test"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

echo_info() { echo -e "${GREEN}[INFO]${NC} $1"; }
echo_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
echo_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# 检查是否为 root
if [ "$EUID" -ne 0 ]; then
    echo_error "请使用 root 权限运行此脚本: sudo $0"
    exit 1
fi

# 获取脚本所在目录
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo_info "========================================="
echo_info "Stability Test Platform Agent 安装"
echo_info "========================================="

# 0. 检查并安装依赖
echo_info "检查系统依赖..."

# 检测操作系统类型
if [ -f /etc/debian_version ]; then
    # Debian/Ubuntu
    PKG_MANAGER="apt"
    if ! dpkg -l | grep -q python3-venv; then
        echo_warn "需要安装 python3-venv"
        apt update -qq
        apt install -y python3-venv python3-pip
    fi
elif [ -f /etc/redhat-release ]; then
    # RHEL/CentOS/Fedora
    PKG_MANAGER="yum"
    if ! rpm -q python3-venv &>/dev/null; then
        echo_warn "需要安装 python3-venv"
        yum install -y python3-venv python3-pip
    fi
fi

# 检查 Python 版本
PYTHON_VERSION=$(python3 --version 2>&1 | awk '{print $2}')
echo_info "Python 版本: $PYTHON_VERSION"

# 1. 创建用户和组
echo_info "创建专用用户..."
if ! id "$USER" &>/dev/null; then
    useradd -r -s /bin/false -d "$INSTALL_DIR" "$USER"
    echo_info "用户 $USER 已创建"
else
    echo_warn "用户 $USER 已存在"
fi

# 2. 创建目录结构
echo_info "创建目录结构..."
mkdir -p "$INSTALL_DIR"/{agent,logs,tmp,venv}

# 3. 复制 Agent 代码
echo_info "复制 Agent 代码..."
cp -r "$SCRIPT_DIR"/* "$INSTALL_DIR/agent/" 2>/dev/null || true
find "$INSTALL_DIR/agent/" -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true

# 4. 设置权限
echo_info "设置文件权限..."
chown -R "$USER:$GROUP" "$INSTALL_DIR"
chmod 750 "$INSTALL_DIR"
chmod 640 "$INSTALL_DIR/agent/"*.py 2>/dev/null || true

# 5. 创建 Python 虚拟环境
echo_info "创建 Python 虚拟环境..."
if [ ! -d "$INSTALL_DIR/venv/bin" ]; then
    python3 -m venv "$INSTALL_DIR/venv"
fi

# 6. 安装依赖
echo_info "安装 Python 依赖..."
if [ -f "$INSTALL_DIR/agent/requirements.txt" ]; then
    "$INSTALL_DIR/venv/bin/pip" install -r "$INSTALL_DIR/agent/requirements.txt" -q
else
    # 基础依赖
    "$INSTALL_DIR/venv/bin/pip" install requests -q
fi

# 7. 创建 .env 文件
echo_info "创建配置文件..."
if [ ! -f "$INSTALL_DIR/.env" ]; then
    if [ -f "$INSTALL_DIR/agent/.env.example" ]; then
        cp "$INSTALL_DIR/agent/.env.example" "$INSTALL_DIR/.env"
    else
        cat > "$INSTALL_DIR/.env" << EOF
# Stability Test Platform Agent 配置
API_URL=http://127.0.0.1:8000
HOST_ID=1
POLL_INTERVAL=10
MOUNT_POINTS=
ADB_PATH=adb
LOG_LEVEL=INFO
EOF
    fi
    echo_warn "请编辑 $INSTALL_DIR/.env 配置文件，设置正确的 API_URL 和 HOST_ID"
fi

chmod 640 "$INSTALL_DIR/.env"

# 8. 安装 systemd 服务
echo_info "安装 systemd 服务..."
if [ -f "$INSTALL_DIR/agent/stability-test-agent.service" ]; then
    cp "$INSTALL_DIR/agent/stability-test-agent.service" "$SERVICE_FILE"
else
    # 创建默认服务文件
    cat > "$SERVICE_FILE" << 'EOF'
[Unit]
Description=Stability Test Platform Agent
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=stability-test
Group=stability-test
WorkingDirectory=/opt/stability-test-agent
EnvironmentFile=-/opt/stability-test-agent/.env
ExecStart=/opt/stability-test-agent/venv/bin/python -m agent.main
Restart=always
RestartSec=10
StandardOutput=append:/opt/stability-test-agent/logs/agent.log
StandardError=append:/opt/stability-test-agent/logs/agent_error.log
SyslogIdentifier=stability-test-agent

[Install]
WantedBy=multi-user.target
EOF
fi

systemctl daemon-reload

# 9. 创建管理脚本
echo_info "创建管理脚本..."
cat > "$INSTALL_DIR/agentctl" << 'EOCTL'
#!/bin/bash
case "$1" in
    start)   systemctl start stability-test-agent ;;
    stop)    systemctl stop stability-test-agent ;;
    restart) systemctl restart stability-test-agent ;;
    status)  systemctl status stability-test-agent ;;
    logs)    journalctl -u stability-test-agent -f ;;
    enable)  systemctl enable stability-test-agent ;;
    disable) systemctl disable stability-test-agent ;;
    *)       echo "用法: $0 {start|stop|restart|status|logs|enable|disable}" ;;
esac
EOCTL
chmod +x "$INSTALL_DIR/agentctl"
ln -sf "$INSTALL_DIR/agentctl" /usr/local/bin/agentctl 2>/dev/null || true

echo_info "========================================="
echo_info "安装完成！"
echo_info "========================================="
echo ""
echo_info "后续步骤："
echo_info "1. 编辑配置文件: nano $INSTALL_DIR/.env"
echo_info "2. 启动服务: agentctl start"
echo_info "3. 查看日志: agentctl logs"
echo_info "4. 开机自启: agentctl enable"
echo ""
echo_warn "请先配置 .env 文件中的 API_URL 和 HOST_ID"
echo ""
