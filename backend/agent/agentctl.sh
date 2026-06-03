#!/usr/bin/env bash
set -euo pipefail
INSTALL_DIR="${STP_INSTALL_DIR:-/opt/stability-test-agent}"

cmd_health() {
    echo -e "\033[0;34mHealth Check:\033[0m"; echo ""
    systemctl -q is-active stability-test-agent 2>/dev/null && echo "  Service: \033[0;32mactive\033[0m" || { echo "  Service: \033[0;31minactive\033[0m"; return 1; }
    systemctl -q is-enabled stability-test-agent 2>/dev/null && echo "  Auto-start: \033[0;32menabled\033[0m"
    [ -f "${INSTALL_DIR}/.env" ] && echo "  Config: \033[0;32mfound\033[0m" || echo "  Config: \033[0;31mmissing\033[0m"
    [ -f "${INSTALL_DIR}/.env" ] && { grep -q "^API_URL=" "${INSTALL_DIR}/.env" && echo "    API_URL: $(grep "^API_URL=" ${INSTALL_DIR}/.env | cut -d= -f2-)"; }
    [ -f "${INSTALL_DIR}/logs/agent_error.log" ] && echo "  Error log: \033[0;32mfound ($(stat -c%s ${INSTALL_DIR}/logs/agent_error.log) bytes)\033[0m" || echo "  Error log: \033[0;31mmissing\033[0m"
    python3 --version >/dev/null 2>&1 && echo "  Python: \033[0;32mOK\033[0m" || echo "  Python: \033[0;31mFAIL\033[0m"
    adb version >/dev/null 2>&1 && echo "  ADB: \033[0;32mOK ($(adb version | head -1))\033[0m" || echo "  ADB: \033[0;31mFAIL\033[0m"
    ndevices=$(adb devices 2>/dev/null | tail -n +2 | grep -v "^$" | wc -l)
    echo "  Devices: \033[0;32m${ndevices}\033[0m"
    api_url=$(grep "^API_URL=" "${INSTALL_DIR}/.env" 2>/dev/null | cut -d= -f2- || true)
    if [ -n "${api_url}" ]; then curl -s --max-time 5 "${api_url}/" >/dev/null 2>&1 && echo "  Server: \033[0;32mreachable\033[0m" || echo "  Server: \033[0;31munreachable (${api_url})\033[0m"; else echo "  Server: \033[0;31mnot configured\033[0m"; fi
}
cmd_restart() { sudo systemctl restart stability-test-agent; }
main() { [ $# -lt 1 ] && { echo "Usage: agentctl <health|restart>" >&2; exit 2; }; case "$1" in health) cmd_health ;; restart) cmd_restart ;; *) echo "Unknown: $1" >&2; exit 2 ;; esac; }
main "$@"
