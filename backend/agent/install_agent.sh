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
USER="android"
GROUP="android"
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

# 函数：获取下一个可用的 HOST_ID
generate_unique_host_id() {
    local api_url="$1"
    local default_id="$2"

    # 如果无法连接 API，使用基于 MAC 地址的哈希生成唯一 ID
    if ! curl -s "$api_url/api/v1/hosts" > /dev/null 2>&1; then
        # 使用 MAC 地址生成唯一 ID (1-10000 范围)
        local mac_hash=$(cat /sys/class/net/*/address 2>/dev/null | head -1 | sha256sum | cut -c1-8)
        local id=$((1 + 0x$mac_hash % 9999))
        echo "$id"
        return
    fi

    # 从 API 获取已存在的主机列表，找到最大 ID + 1
    local max_id=$(curl -s "$api_url/api/v1/hosts" 2>/dev/null | python3 -c "
import sys, json
try:
    hosts = json.load(sys.stdin)
    ids = [h.get('id', 0) for h in hosts]
    print(max(ids) + 1 if ids else 1)
except:
    print('$default_id')
" 2>/dev/null || echo "$default_id")

    echo "$max_id"
}

# 提示用户输入 API_URL
echo_info "请输入中心服务器的 API 地址 (默认: http://172.21.10.15:8000)"
read -r -p "API_URL: " api_url_input
API_URL="${api_url_input:-http://172.21.10.15:8000}"

# 获取本机信息用于生成唯一标识
HOSTNAME=$(hostname)
IP_ADDR=$(hostname -I | awk '{print $1}')

# 生成唯一的 HOST_ID
DEFAULT_HOST_ID=$(generate_unique_host_id "$API_URL" "1")

echo_info "检测到以下主机信息:"
echo_info "  主机名: $HOSTNAME"
echo_info "  IP地址: $IP_ADDR"
echo_info "  建议的 HOST_ID: $DEFAULT_HOST_ID"

# 提示用户确认或修改 HOST_ID
read -r -p "请输入 HOST_ID (默认: $DEFAULT_HOST_ID): " host_id_input
HOST_ID="${host_id_input:-$DEFAULT_HOST_ID}"

if [ ! -f "$INSTALL_DIR/.env" ]; then
    cat > "$INSTALL_DIR/.env" << EOF
# Stability Test Platform Agent 配置
# 生成时间: $(date '+%Y-%m-%d %H:%M:%S')
# 主机信息: $HOSTNAME ($IP_ADDR)

API_URL=$API_URL
HOST_ID=$HOST_ID
POLL_INTERVAL=10
MOUNT_POINTS=
ADB_PATH=adb
LOG_LEVEL=INFO
EOF
    echo_info "配置文件已创建: $INSTALL_DIR/.env"
else
    # 更新现有配置文件
    sed -i "s|^API_URL=.*|API_URL=$API_URL|" "$INSTALL_DIR/.env"
    sed -i "s|^HOST_ID=.*|HOST_ID=$HOST_ID|" "$INSTALL_DIR/.env"
    echo_info "配置文件已更新: $INSTALL_DIR/.env"
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
User=android
Group=android
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
# Stability Test Platform Agent 管理脚本

show_help() {
    echo "用法: sudo $0 {start|stop|restart|status|logs|enable|disable|help}"
    echo ""
    echo "命令:"
    echo "  start     启动 Agent 服务"
    echo "  stop      停止 Agent 服务"
    echo "  restart   重启 Agent 服务"
    echo "  status    查看服务状态"
    echo "  logs      查看服务日志 (journalctl)"
    echo "  logfile   查看 Agent 错误日志文件"
    echo "  enable    设置开机自启"
    echo "  disable   取消开机自启"
    echo "  help      显示此帮助信息"
    echo ""
    echo "或者直接使用 systemctl 命令:"
    echo "  sudo systemctl start stability-test-agent"
    echo "  sudo systemctl stop stability-test-agent"
    echo "  sudo systemctl restart stability-test-agent"
    echo "  sudo systemctl status stability-test-agent"
    echo ""
    echo "查看日志:"
    echo "  sudo cat /opt/stability-test-agent/logs/agent_error.log"
    echo "  sudo tail -f /opt/stability-test-agent/logs/agent.log"
}

case "$1" in
    start)   sudo systemctl start stability-test-agent ;;
    stop)    sudo systemctl stop stability-test-agent ;;
    restart) sudo systemctl restart stability-test-agent ;;
    status)  sudo systemctl status stability-test-agent ;;
    logs)    sudo journalctl -u stability-test-agent -f ;;
    logfile) sudo cat /opt/stability-test-agent/logs/agent_error.log ;;
    enable)  sudo systemctl enable stability-test-agent ;;
    disable) sudo systemctl disable stability-test-agent ;;
    help|--help|-h) show_help ;;
    *)       echo "未知命令: $1"; show_help; exit 1 ;;
esac
EOCTL
chmod +x "$INSTALL_DIR/agentctl"

# 创建全局命令链接
if [ -d "/usr/local/bin" ]; then
    ln -sf "$INSTALL_DIR/agentctl" /usr/local/bin/agentctl
    echo_info "已创建全局命令: agentctl"
fi

# 设置权限，确保所有用户都可以执行
chmod 755 "$INSTALL_DIR/agentctl"

echo_info "========================================="
echo_info "安装完成！"
echo_info "========================================="
echo ""
echo_info "配置信息:"
echo_info "  API_URL:  $API_URL"
echo_info "  HOST_ID:  $HOST_ID"
echo_info "  主机名:   $HOSTNAME"
echo_info "  IP地址:   $IP_ADDR"
echo ""
echo_info "后续步骤："
echo_info "1. 查看/编辑配置:  nano $INSTALL_DIR/.env"
echo_info "2. 启动服务:       sudo systemctl start stability-test-agent"
echo_info "   或使用:         agentctl start"
echo_info "3. 查看日志:       sudo systemctl status stability-test-agent"
echo_info "   或查看日志文件: sudo cat $INSTALL_DIR/logs/agent_error.log"
echo_info "4. 开机自启:       sudo systemctl enable stability-test-agent"
echo_info "5. 帮助信息:       agentctl help"
echo ""
echo_warn "重要提示:"
echo_warn "  - 修改 .env 配置后必须重启服务才能生效"
echo_warn "  - 重启命令: sudo systemctl restart stability-test-agent"
echo_warn "  - 或:       agentctl restart"
echo ""

# 检查是否为重复 HOST_ID
existing_hosts=$(curl -s "$API_URL/api/v1/hosts" 2>/dev/null | python3 -c "
import sys, json
try:
    hosts = json.load(sys.stdin)
    same_id = [h for h in hosts if h.get('id') == $HOST_ID]
    print(len(same_id))
except:
    print('0')
" 2>/dev/null || echo "0")

if [ "$existing_hosts" -gt 0 ] 2>/dev/null; then
    echo_warn "警告: HOST_ID=$HOST_ID 可能已被其他主机使用"
    echo_warn "如果这是新主机，建议重新运行安装并选择不同的 HOST_ID"
    echo ""
fi
