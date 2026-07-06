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

    if adb version >/dev/null 2>&1; then
        echo -e "  ADB: ${GREEN}可用${NC} ($(adb version | sed -n '1p'))"
    else
        echo -e "  ADB: ${YELLOW}不可用${NC}"
    fi

    if command -v adb >/dev/null 2>&1; then
        devices="$(adb devices 2>/dev/null | awk 'NR > 1 && NF {count++} END {print count + 0}')"
        echo -e "  已识别设备: ${GREEN}${devices} 台${NC}"
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

restart_service() {
    sudo systemctl restart "$SERVICE_NAME"
}

main() {
    if [ $# -lt 1 ]; then
        echo "Usage: agentctl <health|restart>" >&2
        exit 2
    fi

    case "$1" in
        health) health_check ;;
        restart) restart_service ;;
        *) echo "Unknown: $1" >&2; exit 2 ;;
    esac
}

main "$@"
