#!/bin/bash
#
# Stability Test Platform Agent 管理脚本
# 用法: ./agentctl.sh {start|stop|restart|status|logs|enable|disable|health}
#

SERVICE_NAME="stability-test-agent"
INSTALL_DIR="/opt/stability-test-agent"
LOG_FILE="$INSTALL_DIR/logs/agent.log"

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# 检查是否已安装
check_installed() {
    if [ ! -d "$INSTALL_DIR" ]; then
        echo -e "${RED}错误: Agent 未安装在 $INSTALL_DIR${NC}"
        echo "请先运行 install_agent.sh 进行安装"
        exit 1
    fi
}

# 启动服务
start_service() {
    echo -e "${BLUE}启动 $SERVICE_NAME 服务...${NC}"
    systemctl start "$SERVICE_NAME"
    if [ $? -eq 0 ]; then
        echo -e "${GREEN}服务已启动${NC}"
        sleep 2
        status_service
    else
        echo -e "${RED}启动失败${NC}"
    fi
}

# 停止服务
stop_service() {
    echo -e "${BLUE}停止 $SERVICE_NAME 服务...${NC}"
    systemctl stop "$SERVICE_NAME"
    if [ $? -eq 0 ]; then
        echo -e "${GREEN}服务已停止${NC}"
    else
        echo -e "${RED}停止失败${NC}"
    fi
}

# 重启服务
restart_service() {
    echo -e "${BLUE}重启 $SERVICE_NAME 服务...${NC}"
    systemctl restart "$SERVICE_NAME"
    if [ $? -eq 0 ]; then
        echo -e "${GREEN}服务已重启${NC}"
        sleep 2
        status_service
    else
        echo -e "${RED}重启失败${NC}"
    fi
}

# 查看服务状态
status_service() {
    echo -e "${BLUE}服务状态:${NC}"
    systemctl status "$SERVICE_NAME" --no-pager

    # 显示最近的心跳信息
    if [ -f "$LOG_FILE" ]; then
        echo ""
        echo -e "${BLUE}最近的日志 (最后10行):${NC}"
        tail -10 "$LOG_FILE" 2>/dev/null || true
    fi
}

# 查看实时日志
logs_service() {
    echo -e "${BLUE}显示实时日志 (Ctrl+C 退出):${NC}"
    journalctl -u "$SERVICE_NAME" -f
}

# 启用开机自启
enable_service() {
    echo -e "${BLUE}启用开机自启...${NC}"
    systemctl enable "$SERVICE_NAME"
    if [ $? -eq 0 ]; then
        echo -e "${GREEN}已启用开机自启${NC}"
    else
        echo -e "${RED}启用失败${NC}"
    fi
}

# 禁用开机自启
disable_service() {
    echo -e "${BLUE}禁用开机自启...${NC}"
    systemctl disable "$SERVICE_NAME"
    if [ $? -eq 0 ]; then
        echo -e "${GREEN}已禁用开机自启${NC}"
    else
        echo -e "${RED}禁用失败${NC}"
    fi
}

# 健康检查
health_check() {
    echo -e "${BLUE}健康检查:${NC}"
    echo ""

    # 检查服务是否运行
    if systemctl is-active --quiet "$SERVICE_NAME"; then
        echo -e "  服务状态: ${GREEN}运行中${NC}"
    else
        echo -e "  服务状态: ${RED}已停止${NC}"
    fi

    # 检查开机自启
    if systemctl is-enabled --quiet "$SERVICE_NAME"; then
        echo -e "  开机自启: ${GREEN}已启用${NC}"
    else
        echo -e "  开机自启: ${YELLOW}已禁用${NC}"
    fi

    # 检查配置文件
    if [ -f "$INSTALL_DIR/.env" ]; then
        echo -e "  配置文件: ${GREEN}存在${NC}"

        # 显示关键配置
        API_URL=$(grep "^API_URL=" "$INSTALL_DIR/.env" | cut -d'=' -f2)
        HOST_ID=$(grep "^HOST_ID=" "$INSTALL_DIR/.env" | cut -d'=' -f2)
        echo -e "    API URL: $API_URL"
        echo -e "    Host ID: $HOST_ID"
    else
        echo -e "  配置文件: ${RED}不存在${NC}"
    fi

    # 检查日志文件
    if [ -f "$LOG_FILE" ]; then
        LOG_SIZE=$(du -h "$LOG_FILE" | cut -f1)
        echo -e "  日志文件: ${GREEN}存在 ($LOG_SIZE)${NC}"
    else
        echo -e "  日志文件: ${YELLOW}不存在${NC}"
    fi

    # 检查虚拟环境
    if [ -d "$INSTALL_DIR/venv/bin" ]; then
        echo -e "  Python 环境: ${GREEN}正常${NC}"
    else
        echo -e "  Python 环境: ${RED}异常${NC}"
    fi

    # 检查 ADB
    ADB_PATH=$(grep "^ADB_PATH=" "$INSTALL_DIR/.env" 2>/dev/null | cut -d'=' -f2)
    ADB_PATH=${ADB_PATH:-adb}
    if command -v "$ADB_PATH" &>/dev/null; then
        ADB_VERSION=$($ADB_PATH version | head -1)
        echo -e "  ADB: ${GREEN}可用${NC} ($ADB_VERSION)"
    else
        echo -e "  ADB: ${RED}不可用 ($ADB_PATH)${NC}"
    fi

    # 检查网络连接
    API_URL=$(grep "^API_URL=" "$INSTALL_DIR/.env" 2>/dev/null | cut -d'=' -f2)
    API_URL=${API_URL:-http://127.0.0.1:8000}
    if curl -s -o /dev/null -w "%{http_code}" "$API_URL/health" 2>/dev/null | grep -q "200\|404"; then
        echo -e "  服务器连接: ${GREEN}正常${NC}"
    else
        echo -e "  服务器连接: ${YELLOW}无法连接${NC}"
    fi

    echo ""
}

# 显示帮助
show_help() {
    echo "Stability Test Platform Agent 管理工具"
    echo ""
    echo "用法: $0 {start|stop|restart|status|logs|enable|disable|health}"
    echo ""
    echo "命令:"
    echo "  start    - 启动服务"
    echo "  stop     - 停止服务"
    echo "  restart  - 重启服务"
    echo "  status   - 查看服务状态"
    echo "  logs     - 查看实时日志"
    echo "  enable   - 启用开机自启"
    echo "  disable  - 禁用开机自启"
    echo "  health   - 健康检查"
    echo ""
}

# 主逻辑
check_installed

case "$1" in
    start)
        start_service
        ;;
    stop)
        stop_service
        ;;
    restart)
        restart_service
        ;;
    status)
        status_service
        ;;
    logs)
        logs_service
        ;;
    enable)
        enable_service
        ;;
    disable)
        disable_service
        ;;
    health)
        health_check
        ;;
    *)
        show_help
        exit 1
        ;;
esac

exit 0
