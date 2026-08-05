#!/usr/bin/env bash
set -uo pipefail

INSTALL_DIR="${STP_INSTALL_DIR:-/opt/stability-test-agent}"
SERVICE_NAME="${STP_AGENT_SERVICE:-stability-test-agent}"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

check_server_connection() {
    local api_url="${1:-}"
    if [ -z "$api_url" ]; then
        return 1
    fi
    curl -fsS --max-time 5 "${api_url%/}/health" >/dev/null 2>&1 \
        || curl -fsS --max-time 5 "${api_url%/}/" >/dev/null 2>&1
}

health_check() {
    local exit_code=0
    local env_file="${INSTALL_DIR}/.env"
    local API_URL=""
    local ADB_PORT=""

    echo -e "${BLUE}健康检查:${NC}"
    echo ""

    if systemctl -q is-active "$SERVICE_NAME" 2>/dev/null; then
        echo -e "  服务状态: ${GREEN}active${NC}"
    else
        echo -e "  服务状态: ${RED}inactive${NC}"
        exit_code=1
    fi

    if systemctl -q is-enabled "$SERVICE_NAME" 2>/dev/null; then
        echo -e "  开机自启: ${GREEN}enabled${NC}"
    else
        echo -e "  开机自启: ${YELLOW}disabled${NC}"
        exit_code=1
    fi

    if [ -f "$env_file" ]; then
        echo -e "  配置文件: ${GREEN}存在${NC}"
        API_URL="$(grep "^API_URL=" "$env_file" 2>/dev/null | cut -d= -f2- || true)"
        HOST_ID="$(grep "^HOST_ID=" "$env_file" 2>/dev/null | cut -d= -f2- || true)"
        ADB_PORT="$(grep "^ANDROID_ADB_SERVER_PORT=" "$env_file" 2>/dev/null | cut -d= -f2- | tr -d '"' || true)"
        [ -n "$API_URL" ] && echo "    API_URL: $API_URL"
        [ -n "$HOST_ID" ] && echo "    HOST_ID: $HOST_ID"
    else
        echo -e "  配置文件: ${RED}缺失${NC}"
        exit_code=1
    fi

    if python3 --version >/dev/null 2>&1; then
        echo -e "  Python 环境: ${GREEN}正常${NC}"
    else
        echo -e "  Python 环境: ${RED}异常${NC}"
        exit_code=1
    fi

    if [ -z "$ADB_PORT" ]; then
        ADB_PORT="5037"
    fi

    if adb version >/dev/null 2>&1; then
        echo -e "  ADB: ${GREEN}可用${NC} ($(adb version | sed -n '1p'))"
    else
        echo -e "  ADB: ${YELLOW}不可用${NC}"
    fi

    if command -v adb >/dev/null 2>&1; then
        devices="$(ANDROID_ADB_SERVER_PORT="$ADB_PORT" adb devices 2>/dev/null | awk 'NR > 1 && NF {count++} END {print count + 0}')"
        echo -e "  已识别设备: ${GREEN}${devices} 台${NC} (ADB 端口 ${ADB_PORT})"
    fi

    # #160: 多 ADB fork-server 并存会把 USB 设备拆分到不同 server
    if command -v pgrep >/dev/null 2>&1; then
        local server_ports=""
        local server_conflict=0
        while IFS= read -r proc_line; do
            [ -z "$proc_line" ] && continue
            local proc_port=""
            proc_port="$(printf '%s\n' "$proc_line" | sed -n 's/.*-L tcp:\([0-9][0-9]*\).*/\1/p')"
            if [ -z "$proc_port" ]; then
                proc_port="$(printf '%s\n' "$proc_line" | sed -n 's/.*-P \([0-9][0-9]*\).*/\1/p')"
            fi
            [ -z "$proc_port" ] && proc_port="?"
            server_ports="${server_ports:+$server_ports, }$proc_port"
            if [ "$proc_port" != "$ADB_PORT" ]; then
                server_conflict=1
            fi
        done < <(pgrep -u "$(id -u)" -af 'adb.*fork-server server' || true)

        if [ -n "$server_ports" ]; then
            if [ "$server_conflict" -eq 1 ]; then
                echo -e "  ADB server: ${RED}冲突${NC} (期望 ${ADB_PORT}，实际: ${server_ports})"
                exit_code=1
            else
                echo -e "  ADB server: ${GREEN}单一${NC} (端口: ${server_ports})"
            fi
        else
            echo -e "  ADB server: ${YELLOW}未运行${NC}"
        fi
    fi

    if [ -z "$API_URL" ]; then
        echo -e "  服务器连接: ${RED}未配置${NC}"
        exit_code=1
    elif check_server_connection "$API_URL"; then
        echo -e "  服务器连接: ${GREEN}正常${NC}"
    else
        echo -e "  服务器连接: ${YELLOW}无法连接${NC}"
        exit_code=1
    fi

    return "$exit_code"
}

repair_adb() {
    if [ ! -x "${INSTALL_DIR}/venv/bin/python" ]; then
        echo "Agent venv 不存在: ${INSTALL_DIR}/venv/bin/python" >&2
        return 1
    fi
    (
        cd "$INSTALL_DIR" && "${INSTALL_DIR}/venv/bin/python" -m agent.device_discovery repair
    )
}

restart_service() {
    sudo systemctl restart "$SERVICE_NAME"
}

main() {
    if [ $# -lt 1 ]; then
        echo "Usage: agentctl <health|repair-adb|restart>" >&2
        exit 2
    fi

    case "$1" in
        health) health_check ;;
        repair-adb) repair_adb ;;
        restart) restart_service ;;
        *) echo "Unknown: $1" >&2; exit 2 ;;
    esac
}

main "$@"
