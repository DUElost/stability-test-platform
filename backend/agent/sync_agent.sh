#!/bin/bash
#
# sync_agent.sh - Agent 热更新脚本
#
# 仅更新 Python 代码和管理脚本，保留 .env / venv / logs / resources 不变。
# 适用场景：代码修复、功能迭代后推送到已部署的 Agent 主机。
#
# 用法:
#   ./sync_agent.sh wsl                      # 更新本机 WSL Agent
#   ./sync_agent.sh root@172.21.15.10        # 更新单台远程 Agent
#   ./sync_agent.sh 172.21.15.10             # 同上（默认 root 用户）
#   ./sync_agent.sh 172.21.15.10 172.21.15.11 ...  # 批量更新
#   ./sync_agent.sh --all                    # 读取同目录 hosts.txt 批量更新
#
# hosts.txt 格式（每行一个 [user@]host，# 开头为注释）：
#   172.21.15.10
#   android@172.21.15.11
#   # 这是注释
#

set -e

INSTALL_DIR="${AGENT_INSTALL_DIR:-/opt/stability-test-agent}"
SERVICE_NAME="stability-test-agent"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo_info()  { echo -e "${GREEN}[INFO]${NC}  $1"; }
echo_warn()  { echo -e "${YELLOW}[WARN]${NC}  $1"; }
echo_error() { echo -e "${RED}[ERROR]${NC} $1"; }
echo_step()  { echo -e "${BLUE}[>>>]${NC}  $1"; }
echo_sep()   { echo -e "${BLUE}$(printf '─%.0s' {1..50})${NC}"; }

# ─────────────────────────────────────────────
# 定位源代码目录，处理 Windows drvfs 路径
# ─────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLEANUP_TMP=0

if [[ "$SCRIPT_DIR" == /mnt/* ]]; then
    echo_warn "检测到 Windows drvfs 路径 ($SCRIPT_DIR)"
    echo_warn "自动复制到 WSL 本地临时目录以避免 CRLF 和 I/O 问题..."
    TMP_SRC=$(mktemp -d /tmp/agent-sync-XXXXX)
    rsync -a --delete "$SCRIPT_DIR/" "$TMP_SRC/"
    # 修复 CRLF：在子 shell 中切换到 HOME，避免 find 因无法恢复
    # 当前工作目录（如 /opt/stability-test-agent 的 root 权限目录）而报错
    (cd "$HOME" && find "$TMP_SRC" -type f \( -name "*.py" -o -name "*.sh" \) \
        -exec sed -i 's/\r$//' {} +) 2>/dev/null || true
    SCRIPT_DIR="$TMP_SRC"
    CLEANUP_TMP=1
fi

cleanup() {
    if [ "$CLEANUP_TMP" -eq 1 ] && [ -d "$TMP_SRC" ]; then
        rm -rf "$TMP_SRC"
    fi
}
trap cleanup EXIT

# ─────────────────────────────────────────────
# 同步时的排除规则（两套，用途不同）
# ─────────────────────────────────────────────

# 本地更新 & 推送到远端 agent/ 目录时的排除规则（排除脚本和文档）
INSTALL_EXCLUDES=(
    "--exclude=__pycache__/"
    "--exclude=test_agent*.py"
    "--exclude=test_aimonkey*.py"
    "--exclude=test_main*.py"
    "--exclude=tests/"
    "--exclude=install_agent.sh"
    "--exclude=sync_agent.sh"
    "--exclude=agentctl.sh"
    "--exclude=DEPLOY.md"
    "--exclude=.env.example"
    "--exclude=stability-test-agent.service"
    "--exclude=hosts.txt"
)

# 推送到远端临时目录时的排除规则（保留 agentctl.sh，远端 cp 步骤会用到）
REMOTE_TEMP_EXCLUDES=(
    "--exclude=__pycache__/"
    "--exclude=tests/"
)

# ─────────────────────────────────────────────
# 本地更新（WSL 同机）
# ─────────────────────────────────────────────
do_local_update() {
    echo_step "本地更新 $INSTALL_DIR ..."

    if [ ! -d "$INSTALL_DIR" ]; then
        echo_error "Agent 未安装：$INSTALL_DIR 不存在，请先运行 install_agent.sh"
        return 1
    fi

    # 1. 同步 Python 代码（sudo 无需当前用户拥有目录写权限）
    sudo rsync -av --delete "${INSTALL_EXCLUDES[@]}" \
        "$SCRIPT_DIR/" "$INSTALL_DIR/agent/"

    # 2. 更新 agentctl（从 agentctl.sh 生成，去掉 .sh 后缀）
    sudo cp -f "$SCRIPT_DIR/agentctl.sh" "$INSTALL_DIR/agentctl"
    sudo chmod 755 "$INSTALL_DIR/agentctl"
    sudo ln -sf "$INSTALL_DIR/agentctl" /usr/local/bin/agentctl 2>/dev/null || true

    # 3. 修复整个安装目录的所有权
    #    必须 chown 整个 INSTALL_DIR（而非仅 agent/），否则 systemd 以服务用户身份
    #    chdir 进入 WorkingDirectory 时会报 200/CHDIR
    #    使用 sudo stat 确保即使当前用户无读权限也能取到目录属主
    AGENT_USER=$(sudo stat -c '%U' "$INSTALL_DIR" 2>/dev/null || echo "android")
    sudo chown -R "$AGENT_USER:$AGENT_USER" "$INSTALL_DIR"

    # 4. 重载 systemd 并重启服务
    sudo systemctl daemon-reload
    sudo systemctl restart "$SERVICE_NAME"

    echo_info "本地更新完成，等待服务启动..."
    sleep 2
    sudo systemctl status "$SERVICE_NAME" --no-pager -l | head -15
    echo_info "运行 'agentctl health' 验证设备识别"
}

# ─────────────────────────────────────────────
# 远程 NOPASSWD sudo 初始化（一次性，幂等）
# ─────────────────────────────────────────────
bootstrap_nopasswd() {
    local TARGET="$1"
    local REMOTE_USER="${TARGET%%@*}"

    # 快速通道：NOPASSWD 已配置则直接返回
    if ssh "$TARGET" "sudo -n true" 2>/dev/null; then
        return 0
    fi

    echo_warn "[$REMOTE_USER@${TARGET#*@}] NOPASSWD sudo 未配置，进行一次性初始化"
    echo_warn "  请在下方提示处输入 $TARGET 的 sudo 密码..."

    # -t 分配伪 TTY，允许 sudo 交互式读取密码
    # printf 写入 sudoers 规则，避免 heredoc + TTY 的干扰问题
    local RULES
    RULES="${REMOTE_USER} ALL=(ALL) NOPASSWD: /bin/systemctl, /usr/bin/systemctl
${REMOTE_USER} ALL=(ALL) NOPASSWD: /usr/bin/rsync, /bin/rsync
${REMOTE_USER} ALL=(ALL) NOPASSWD: /usr/bin/cp, /bin/cp
${REMOTE_USER} ALL=(ALL) NOPASSWD: /usr/bin/chmod, /bin/chmod
${REMOTE_USER} ALL=(ALL) NOPASSWD: /usr/bin/chown, /bin/chown
${REMOTE_USER} ALL=(ALL) NOPASSWD: /usr/bin/ln, /bin/ln
${REMOTE_USER} ALL=(ALL) NOPASSWD: /usr/bin/stat, /bin/stat"

    ssh -t "$TARGET" "printf '%s\n' '$RULES' | sudo tee /etc/sudoers.d/stability-test-agent > /dev/null \
        && sudo chmod 440 /etc/sudoers.d/stability-test-agent \
        && echo '[OK] sudoers 写入完成'"

    # 验证是否生效
    if ssh "$TARGET" "sudo -n true" 2>/dev/null; then
        echo_info "[$REMOTE_USER@${TARGET#*@}] NOPASSWD sudo 配置成功，后续 sync 无需密码"
    else
        echo_error "[$REMOTE_USER@${TARGET#*@}] NOPASSWD sudo 配置后验证失败，请手动检查"
        return 1
    fi
}

# ─────────────────────────────────────────────
# 远程更新（SSH）
# ─────────────────────────────────────────────
do_remote_update() {
    local TARGET="$1"

    # 补全 user@（默认 root）
    if [[ "$TARGET" != *@* ]]; then
        TARGET="root@$TARGET"
    fi

    local HOST="${TARGET#*@}"
    echo_step "远程更新 $TARGET ..."

    # 预检：确保 NOPASSWD sudo 已配置（首次会交互式初始化）
    bootstrap_nopasswd "$TARGET" || return 1

    # 1. rsync 到目标机临时目录（保留 agentctl.sh，远端 cp 步骤会用到）
    rsync -av --delete \
        "${REMOTE_TEMP_EXCLUDES[@]}" \
        "$SCRIPT_DIR/" "$TARGET:/tmp/agent-update-$$/"

    # 2. 远程执行热更新
    ssh "$TARGET" bash <<REMOTE
set -e
INSTALL_DIR="${INSTALL_DIR}"
SERVICE_NAME="${SERVICE_NAME}"
SRC="/tmp/agent-update-$$"

if [ ! -d "\$INSTALL_DIR" ]; then
    echo "[ERROR] Agent 未安装：\$INSTALL_DIR 不存在，请先运行 install_agent.sh"
    rm -rf "\$SRC"
    exit 1
fi

# 修复 CRLF（防止 Windows 来源文件污染）
find "\$SRC" -type f \( -name "*.py" -o -name "*.sh" \) \
    -exec sed -i 's/\r$//' {} + 2>/dev/null || true

# 同步代码到 agent/ 目录（排除脚本和文档）
sudo rsync -av --delete \
    --exclude='__pycache__/' \
    --exclude='test_agent*.py' \
    --exclude='test_aimonkey*.py' \
    --exclude='test_main*.py' \
    --exclude='tests/' \
    --exclude='agentctl.sh' \
    --exclude='install_agent.sh' \
    --exclude='sync_agent.sh' \
    --exclude='DEPLOY.md' \
    --exclude='.env.example' \
    --exclude='stability-test-agent.service' \
    --exclude='hosts.txt' \
    "\$SRC/" "\$INSTALL_DIR/agent/"

# 更新 agentctl（agentctl.sh 已在临时目录中）
sudo cp -f "\$SRC/agentctl.sh" "\$INSTALL_DIR/agentctl"
sudo chmod 755 "\$INSTALL_DIR/agentctl"
sudo ln -sf "\$INSTALL_DIR/agentctl" /usr/local/bin/agentctl 2>/dev/null || true

# 修复整个安装目录所有权（必须覆盖 INSTALL_DIR 本身，否则 systemd 200/CHDIR）
AGENT_USER=\$(sudo stat -c '%U' "\$INSTALL_DIR" 2>/dev/null || echo "android")
sudo chown -R "\$AGENT_USER:\$AGENT_USER" "\$INSTALL_DIR"

# 重启服务
sudo systemctl daemon-reload
sudo systemctl restart "\$SERVICE_NAME"

echo "[INFO] 等待服务启动..."
sleep 2
sudo systemctl status "\$SERVICE_NAME" --no-pager -l | head -15

# 清理临时文件
rm -rf "\$SRC"
REMOTE

    echo_info "$HOST 更新完成"
}

# ─────────────────────────────────────────────
# 帮助信息
# ─────────────────────────────────────────────
show_help() {
    cat << 'EOF'
用法:
  sync_agent.sh wsl                          # 更新本机 WSL Agent
  sync_agent.sh root@172.21.15.10            # 更新单台远程 Agent
  sync_agent.sh 172.21.15.10                 # 同上（默认 root 用户）
  sync_agent.sh 172.21.15.10 172.21.15.11   # 批量更新多台
  sync_agent.sh --all                        # 读 hosts.txt 批量更新

hosts.txt 格式（每行一个 [user@]host，# 开头为注释）:
  172.21.15.10
  android@172.21.15.11
  # 已下线的机器

注意:
  - 此脚本只更新代码，不会修改 .env 配置
  - 若需修改 ADB 端口，手动编辑目标机 .env 后重启即可：
      echo 'ANDROID_ADB_SERVER_PORT=5039' >> /opt/stability-test-agent/.env
      sudo systemctl restart stability-test-agent
EOF
}

# ─────────────────────────────────────────────
# 主逻辑
# ─────────────────────────────────────────────
if [ $# -eq 0 ]; then
    show_help
    exit 1
fi

# 解析 --all
TARGETS=()
if [ "$1" = "--all" ]; then
    HOSTS_FILE="$(dirname "${BASH_SOURCE[0]}")/hosts.txt"
    if [ ! -f "$HOSTS_FILE" ]; then
        echo_error "hosts.txt 不存在: $HOSTS_FILE"
        echo_info "请创建 hosts.txt，每行一个 [user@]host"
        exit 1
    fi
    while IFS= read -r line; do
        line="${line%%#*}"        # 去掉注释
        line="${line//[[:space:]]/}"  # 去掉空白
        [ -n "$line" ] && TARGETS+=("$line")
    done < "$HOSTS_FILE"
    echo_info "从 hosts.txt 读取 ${#TARGETS[@]} 台主机"
else
    TARGETS=("$@")
fi

ERRORS=0
TOTAL=${#TARGETS[@]}

for i in "${!TARGETS[@]}"; do
    TARGET="${TARGETS[$i]}"
    echo_sep
    echo_info "[$((i+1))/$TOTAL] 目标: $TARGET"
    echo_sep

    if [ "$TARGET" = "wsl" ] || [ "$TARGET" = "local" ]; then
        do_local_update || { echo_error "本地更新失败"; ERRORS=$((ERRORS+1)); }
    else
        do_remote_update "$TARGET" || { echo_error "$TARGET 更新失败"; ERRORS=$((ERRORS+1)); }
    fi
    echo ""
done

echo_sep
if [ $ERRORS -eq 0 ]; then
    echo_info "全部 $TOTAL 台主机更新成功"
else
    echo_error "$ERRORS / $TOTAL 台主机更新失败"
    exit 1
fi
