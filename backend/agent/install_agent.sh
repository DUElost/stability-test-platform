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
# 5. 安装运行时工件：Pipeline schema 与版本标识（#1247）
# 6. 安装提权边界 wrapper 并生成受控 sudoers（#1250）
# 7. 安装后自检（样例 Pipeline 校验，脱离开发仓库目录）
#

set -e

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# 配置（可通过环境变量覆盖）
INSTALL_DIR="${AGENT_INSTALL_DIR:-/opt/stability-test-agent}"
SERVICE_NAME="stability-test-agent"
USER="${AGENT_USER:-android}"
GROUP="${AGENT_GROUP:-android}"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
LOCK_FILE="/var/lock/${SERVICE_NAME}-install.lock"

echo_info() { echo -e "${GREEN}[INFO]${NC} $1"; }
echo_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
echo_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# 检查是否为 root
if [ "$EUID" -ne 0 ]; then
    echo_error "请使用 root 权限运行此脚本: sudo $0"
    exit 1
fi

# 单实例守卫（任务 #4）：同一时间只允许一个安装实例运行，
# 防止并发执行相互覆盖 $INSTALL_DIR / systemd 服务文件 / sudoers。
# flock 语义：fd 9 持锁到进程退出，Ctrl-C / 失败退出时自动释放。
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
    echo_error "检测到另一个安装实例正在运行（$LOCK_FILE 被占用）"
    echo_error "请等待其完成后重试，或用 'lsof $LOCK_FILE' 排查残留进程"
    exit 1
fi
echo_info "获取安装锁成功（单实例守卫）"

# 获取脚本所在目录
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# 运行时工件解析（#1247）：schema 与 VERSION 不属于 backend/agent/ 源码目录，
# 必须与源码同批落盘到 INSTALL_DIR，否则 pipeline_validator 取不到 schema。
# schema 来源两种布局：
#   1) 仓库/暂存树同构布局：<script_dir>/../schemas/pipeline_schema.json
#   2) Ansible 暂存布局：  <script_dir>/stp_schemas/pipeline_schema.json
resolve_pipeline_schema() {
    local script_dir="$1" candidate
    for candidate in \
        "$script_dir/../schemas/pipeline_schema.json" \
        "$script_dir/stp_schemas/pipeline_schema.json"; do
        if [ -f "$candidate" ]; then
            echo "$candidate"
            return 0
        fi
    done
    return 1
}

# 版本标识：优先取调用方注入（Ansible 传控制面仓库 HEAD），
# 其次从脚本所在 git 仓库派生；都不可得时留空（不阻断安装）。
resolve_code_version() {
    local script_dir="$1"
    if [ -n "${AGENT_CODE_VERSION:-}" ]; then
        echo "$AGENT_CODE_VERSION"
        return 0
    fi
    git -C "$script_dir" rev-parse --short HEAD 2>/dev/null || true
}

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

# 1.1 sudo 权限在步骤 2.1 由提权 wrapper bootstrap 生成（#1250 / ADR-0037）

# 2. 创建目录结构
echo_info "创建目录结构..."
mkdir -p "$INSTALL_DIR"/{agent,logs,tmp,venv,resources/aimonkey}

# 2.1 安装提权边界 wrapper（#1250 / ADR-0037）
# NOPASSWD 只授 wrapper 一条命令（另加固定 systemctl 服务管理）；wrapper 位于
# /usr/local/sbin（root:root，不在 Agent 可写的 INSTALL_DIR 内），参数与路径
# 校验在 wrapper 内部完成。旧的 rsync/cp/chmod/chown/ln 免密规则不再生成。
echo_info "安装提权 wrapper..."
WRAPPER_SRC="$SCRIPT_DIR/stp_agent_priv.py"
if [ ! -f "$WRAPPER_SRC" ]; then
    echo_error "缺少 wrapper 源文件: $WRAPPER_SRC"
    exit 1
fi
install -D -m 0755 -o root -g root "$WRAPPER_SRC" /usr/local/sbin/stp-agent-priv
if ! python3 /usr/local/sbin/stp-agent-priv bootstrap \
        --install-dir "$INSTALL_DIR" --user "$USER" --group "$GROUP" \
        --service "$SERVICE_NAME"; then
    echo_error "提权 wrapper bootstrap 失败，安装中止（不产出无受控 sudo 的主机）"
    exit 1
fi
if ! /usr/local/sbin/stp-agent-priv selftest; then
    echo_error "提权 wrapper 自检失败，安装中止"
    exit 1
fi
echo_info "sudo 权限已配置: /etc/sudoers.d/${SERVICE_NAME}（wrapper 模式）"

# 3. 复制 Agent 代码
echo_info "复制 Agent 代码..."
# 只复制 agent 目录（Agent 运行时不依赖 backend/ 其他模块）；
# 但 backend/schemas/pipeline_schema.json 是运行时工件，单独安装（3.1）
cp -r "$SCRIPT_DIR"/* "$INSTALL_DIR/agent/" 2>/dev/null || true
# 清理测试文件和安装辅助文件
rm -f "$INSTALL_DIR/agent/test_agent"*.py 2>/dev/null || true
rm -f "$INSTALL_DIR/agent/test_aimonkey"*.py 2>/dev/null || true
rm -f "$INSTALL_DIR/agent/test_main"*.py 2>/dev/null || true
rm -rf "$INSTALL_DIR/agent/tests" 2>/dev/null || true
rm -rf "$INSTALL_DIR/agent/stp_schemas" 2>/dev/null || true
rm -f "$INSTALL_DIR/agent/install_agent.sh" 2>/dev/null || true
rm -f "$INSTALL_DIR/agent/agentctl.sh" 2>/dev/null || true
rm -f "$INSTALL_DIR/agent/DEPLOY.md" 2>/dev/null || true
rm -f "$INSTALL_DIR/agent/.env.example" 2>/dev/null || true
rm -f "$INSTALL_DIR/agent/stability-test-agent.service" 2>/dev/null || true
rm -f "$INSTALL_DIR/agent/stp_agent_priv.py" 2>/dev/null || true
find "$INSTALL_DIR/" -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true

# 3.1 安装 Pipeline schema（运行时校验必需；与 API 热更新同一目标路径）
SCHEMA_SRC="$(resolve_pipeline_schema "$SCRIPT_DIR")" || {
    echo_error "缺少 Pipeline schema：既不在 $SCRIPT_DIR/../schemas/，也不在 $SCRIPT_DIR/stp_schemas/（#1247）"
    echo_error "  schema 是 Agent 运行时校验必需工件：手工安装请连同 backend/schemas/ 一起同步，"
    echo_error "  Ansible 安装请使用 install_agent.yml（会先暂存 schema）。"
    exit 1
}
mkdir -p "$INSTALL_DIR/schemas"
install -m 0644 "$SCHEMA_SRC" "$INSTALL_DIR/schemas/pipeline_schema.json"
echo_info "Pipeline schema 已安装: $INSTALL_DIR/schemas/pipeline_schema.json"

# 3.2 写入版本标识（与热更新的 agent/VERSION 同语义）
CODE_VERSION="$(resolve_code_version "$SCRIPT_DIR")"
if [ -n "$CODE_VERSION" ]; then
    echo "$CODE_VERSION" > "$INSTALL_DIR/agent/VERSION"
    echo_info "版本标识已写入: $CODE_VERSION"
fi

# 4. 设置权限
echo_info "设置文件权限..."
chown -R "$USER:$GROUP" "$INSTALL_DIR"
chmod 750 "$INSTALL_DIR"
chmod 640 "$INSTALL_DIR/agent/"*.py 2>/dev/null || true

# 4b. flashtool 二进制可执行 + udev 规则（自动刷机功能依赖）
FLASHTOOL_DIR="$INSTALL_DIR/agent/resources/flashtool"
if [ -d "$FLASHTOOL_DIR" ]; then
    echo_info "配置 flash_tool 可执行权限..."
    find "$FLASHTOOL_DIR" -maxdepth 3 -type f \( -name "flash_tool" -o -name "flash_tool.sh" -o -name "modemmanagercmd.sh" \) -exec chmod +x {} \; 2>/dev/null || true

    # 部署 udev 规则：MTK preloader / ttyACM 设备权限
    UDEV_SRC=$(find "$FLASHTOOL_DIR" -maxdepth 3 -name "99-ttyacms.rules" -type f 2>/dev/null | head -n 1)
    if [ -n "$UDEV_SRC" ] && [ -d /etc/udev/rules.d ]; then
        cp "$UDEV_SRC" /etc/udev/rules.d/99-ttyacms.rules 2>/dev/null && \
            udevadm control --reload-rules 2>/dev/null && \
            udevadm trigger 2>/dev/null && \
            echo_info "udev 规则已部署: 99-ttyacms.rules" || \
            echo_warn "udev 规则部署失败，刷机可能需要 sudo 才能访问 USB"
    fi
fi

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
    "$INSTALL_DIR/venv/bin/pip" install requests python-dotenv "websockets>=12.0" -q
fi

# 6.5 安装后自检：脱离开发仓库目录，用安装产物校验样例 Pipeline（#1247）
# cwd 固定为 INSTALL_DIR：python -m 会把 cwd 加入 sys.path，若沿用调用方 cwd
# （Ansible 场景是暂存源码树）会导入开发布局而非安装产物，自检即失真。
echo_info "校验安装后的 Pipeline schema..."
if (cd "$INSTALL_DIR" && sudo -u "$USER" env PYTHONPATH="$INSTALL_DIR" \
        "$INSTALL_DIR/venv/bin/python" -m agent.install_selfcheck); then
    echo_info "Pipeline schema 自检通过"
else
    echo_error "安装后 Pipeline 自检失败，安装中止（避免带病上线）"
    echo_error "  排查: cd $INSTALL_DIR && sudo -u $USER env PYTHONPATH=$INSTALL_DIR $INSTALL_DIR/venv/bin/python -m agent.install_selfcheck"
    exit 1
fi

# 7. 创建 .env 文件
echo_info "创建配置文件..."

# 函数：基于节点 IPv4 生成可读 HOST_ID（198.51.100.6 → 198-51-100-6）
# 若该 ID 被其他 IP 占用则追加短后缀；同 IP 复用已有 ID。
# 无可用 IPv4 时回退 auto-<hex>。
generate_unique_host_id() {
    local api_url="$1"
    local ip_addr="$2"

    local base_id=""
    if [[ "$ip_addr" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]]; then
        base_id="${ip_addr//./-}"
    fi

    if [ -z "$base_id" ]; then
        echo "auto-$(head -c 6 /dev/urandom | xxd -p 2>/dev/null || date +%s)"
        return
    fi

    # 无法连 API 时直接用 IP 派生 ID
    if ! curl -s --max-time 3 "$api_url/api/v1/hosts" > /dev/null 2>&1; then
        echo "$base_id"
        return
    fi

    local candidate
    candidate=$(curl -s --max-time 5 "$api_url/api/v1/hosts" 2>/dev/null | python3 -c '
import json, sys, uuid
base = sys.argv[1]
ip_addr = sys.argv[2]
try:
    payload = json.load(sys.stdin)
    if isinstance(payload, list):
        hosts = payload
    elif isinstance(payload, dict) and isinstance(payload.get("items"), list):
        hosts = payload["items"]
    else:
        raise ValueError("unexpected hosts response")
except Exception:
    print(base)
    raise SystemExit(0)

# 平台预创建 / 旧库存主机应沿用数据库中的真实 ID，不能把同一 IP
# 误判为冲突后生成随机后缀，否则 Agent claim/socket 身份会与 DB 脱节。
for host in hosts:
    if not isinstance(host, dict):
        continue
    existing_ip = str(host.get("ip") or host.get("ip_address") or "").strip()
    existing_id = str(host.get("id") or "").strip()
    if existing_ip == ip_addr and existing_id:
        print(existing_id)
        raise SystemExit(0)

taken = {
    str(host.get("id", ""))
    for host in hosts
    if isinstance(host, dict)
}
if base not in taken:
    print(base)
else:
    for _ in range(8):
        alt = f"{base}-{uuid.uuid4().hex[:4]}"
        if alt not in taken:
            print(alt)
            break
    else:
        print(f"auto-{uuid.uuid4().hex[:12]}")
' "$base_id" "$ip_addr" 2>/dev/null || echo "$base_id")

    echo "$candidate"
}

# 提示用户输入 API_URL
# WSL 环境检测：WSL 中 Agent 访问同机开发后端应使用 127.0.0.1
DEFAULT_API_URL=""
if grep -qi microsoft /proc/version 2>/dev/null; then
    DEFAULT_API_URL="http://127.0.0.1:8000"
    echo_warn "检测到 WSL 环境，默认使用 127.0.0.1 访问同机 Windows 后端"
fi

echo_info "请输入中心服务器的 API 地址${DEFAULT_API_URL:+ (默认: $DEFAULT_API_URL)}"
read -r -p "API_URL: " api_url_input
API_URL="${api_url_input:-$DEFAULT_API_URL}"
if [ -z "$API_URL" ]; then
    echo_error "API_URL 不能为空：部署必须提供真实控制面地址（脚本不再内置默认值）"
    exit 1
fi

# 获取本机信息用于生成唯一标识
HOSTNAME=$(hostname)
IP_ADDR=$(hostname -I | awk '{print $1}')

# 生成唯一的 HOST_ID
DEFAULT_HOST_ID=$(generate_unique_host_id "$API_URL" "$IP_ADDR")

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
AUTO_REGISTER_HOST=false
POLL_INTERVAL=10
MOUNT_POINTS=
ADB_PATH=adb
# ADB 服务端口（默认 5037）。WSL 环境若默认端口被占用需切换（如 5039）：
# ANDROID_ADB_SERVER_PORT=5037
LOG_LEVEL=INFO
AGENT_SECRET=${AGENT_SECRET:-}

# AIMONKEY 资源目录（热更新路径，与 agent/resources/aimonkey 一致）
# AIMONKEY_RESOURCE_DIR=$INSTALL_DIR/agent/resources/aimonkey
EOF
    echo_info "配置文件已创建: $INSTALL_DIR/.env"
else
    # 更新现有配置文件
    sed -i "s|^API_URL=.*|API_URL=$API_URL|" "$INSTALL_DIR/.env"
    sed -i "s|^HOST_ID=.*|HOST_ID=$HOST_ID|" "$INSTALL_DIR/.env"
    sed -i "s|^AUTO_REGISTER_HOST=.*|AUTO_REGISTER_HOST=false|" "$INSTALL_DIR/.env"
    if ! grep -q "^AGENT_SECRET=" "$INSTALL_DIR/.env"; then
        echo "AGENT_SECRET=${AGENT_SECRET:-}" >> "$INSTALL_DIR/.env"
    else
        sed -i "s|^AGENT_SECRET=.*|AGENT_SECRET=${AGENT_SECRET:-}|" "$INSTALL_DIR/.env"
    fi
    echo_info "配置文件已更新: $INSTALL_DIR/.env"
fi

chmod 640 "$INSTALL_DIR/.env"
# #1251：.env 由 root 创建，显式归属 agent 用户/组——否则 Agent 进程
# load_dotenv 因权限不足失败（独立安装路径无 Ansible 的后续属主修复）
chown "$USER:$GROUP" "$INSTALL_DIR/.env"

# 7.5 STP_AEE_LOCAL_ROOT 静态守门（#78 子任务 3）
# 防止 #72 类 .env 错配（路径指向 android 用户无权写的目录）安装上线；
# 校验目标：路径非空 + 父目录可写 + 子目录可创建 + adb 拉取可写入。
# 失败则警告但不强制中止安装（运维可手动修复 .env 后重启 Agent）。
AEE_LOCAL_ROOT="$(grep -E '^STP_AEE_LOCAL_ROOT=' "$INSTALL_DIR/.env" | tail -n 1 | cut -d= -f2- | tr -d '\"' || true)"
if [ -z "$AEE_LOCAL_ROOT" ]; then
    echo_warn "STP_AEE_LOCAL_ROOT 未配置 — AEE Reconciler 主路径不会启动（仅 inotifyd 兜底）"
    echo_warn "  ADR-0025 设计需该路径指向 1TB HDD 的可写目录；典型值：/mnt/hdd/aee_events 或 /data/hdd/aee_events"
else
    echo_info "校验 STP_AEE_LOCAL_ROOT=$AEE_LOCAL_ROOT 可写性 (#78 守门)"
    PARENT_DIR="$(dirname "$AEE_LOCAL_ROOT")"
    if [ ! -d "$PARENT_DIR" ]; then
        echo_warn "  父目录 $PARENT_DIR 不存在 — 请先挂载 HDD 并 mkdir"
    elif ! sudo -u "$USER" test -w "$PARENT_DIR"; then
        echo_error "  父目录 $PARENT_DIR 不可写 (user=$USER) — AEE Reconciler 会启动崩溃 (#72 根因)"
        echo_error "  排查：ls -ld $PARENT_DIR / mount | grep $PARENT_DIR"
        echo_error "  修复后再 systemctl restart $SERVICE_NAME"
    else
        if ! sudo -u "$USER" mkdir -p "$AEE_LOCAL_ROOT" 2>/dev/null; then
            echo_error "  $AEE_LOCAL_ROOT 创建失败 — 请检查权限/挂载状态"
        else
            PROBE_FILE="$AEE_LOCAL_ROOT/.install_probe_$$"
            if sudo -u "$USER" touch "$PROBE_FILE" 2>/dev/null; then
                sudo -u "$USER" rm -f "$PROBE_FILE"
                echo_info "  STP_AEE_LOCAL_ROOT 可写性 OK"
            else
                echo_error "  $AEE_LOCAL_ROOT 不可写入 (user=$USER) — AEE Reconciler 会启动崩溃"
                echo_error "  修复后再 systemctl restart $SERVICE_NAME"
            fi
        fi
    fi
fi

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
Environment="PYTHONPATH=/opt/stability-test-agent"
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
if [ ! -f "$SCRIPT_DIR/agentctl.sh" ]; then
    echo_error "缺少管理脚本: $SCRIPT_DIR/agentctl.sh"
    exit 1
fi

install -m 755 "$SCRIPT_DIR/agentctl.sh" "$INSTALL_DIR/agentctl"
chown "$USER:$GROUP" "$INSTALL_DIR/agentctl"

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
    payload = json.load(sys.stdin)
    if isinstance(payload, list):
        hosts = payload
    elif isinstance(payload, dict) and isinstance(payload.get('items'), list):
        hosts = payload['items']
    else:
        hosts = []
    target = sys.argv[1]
    current_ip = sys.argv[2]
    conflicts = []
    for host in hosts:
        if not isinstance(host, dict) or str(host.get('id', '')) != target:
            continue
        existing_ip = str(host.get('ip') or host.get('ip_address') or '').strip()
        if not existing_ip or existing_ip != current_ip:
            conflicts.append(host)
    print(len(conflicts))
except Exception:
    print('0')
" "$HOST_ID" "$IP_ADDR" 2>/dev/null || echo "0")

if [ "$existing_hosts" -gt 0 ] 2>/dev/null; then
    echo_warn "警告: HOST_ID=$HOST_ID 可能已被其他主机使用"
    echo_warn "如果这是新主机，建议重新运行安装并选择不同的 HOST_ID"
    echo ""
fi
