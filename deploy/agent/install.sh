#!/usr/bin/env bash
# 批量接入 Agent：读仓库外的 inventory（默认 ~/hosts.ini），逐台交给本站 API。
#
#   sudo ./deploy/agent/install.sh                 # 用 ~/hosts.ini
#   sudo ./deploy/agent/install.sh --inventory /etc/stp/hosts.ini
#   sudo ./deploy/agent/install.sh --dry-run       # 只校验 inventory 与绑定，不接
#
# 首次运行会写出 inventory 模板并停下——填完再跑一次即可。Host 始终由本站 API
# 分配，脚本不直接写 Agent 机器上的任何配置（那一步由 Agent 安装包自己完成）。
#
# 凭据来源是 inventory 内的 ansible_password / ansible_ssh_private_key_file；
# 未逐台覆盖时共用一个绑定（默认 agent_ssh），落在 $STP_BINDINGS_DIR（0700/0600）。

set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/lib/deploy-common.sh"

DRY_RUN=0
INVENTORY=""
PASSTHROUGH=()

while [ "$#" -gt 0 ]; do
    case "$1" in
        --inventory|-i) INVENTORY="${2:?--inventory needs a file}"; shift 2 ;;
        --inventory=*) INVENTORY="${1#*=}"; shift ;;
        --dry-run) DRY_RUN=1; shift ;;
        --help|-h) sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) PASSTHROUGH+=("$1"); shift ;;
    esac
done

deploy_defaults
deploy_require_root
INVENTORY="${INVENTORY:-$STP_AGENTS_INVENTORY}"

if [ ! -f "$INVENTORY" ]; then
    echo "agent install: no inventory at $INVENTORY"
    if [ "$DRY_RUN" -eq 1 ]; then
        echo "agent install: dry run; the template was not written."
        exit 0
    fi
    if [ ! -d "$(dirname "$INVENTORY")" ]; then
        echo "agent install: $(dirname "$INVENTORY") does not exist; create it or pass --inventory <path>." >&2
        exit 1
    fi
    deploy_ensure_python
    umask 077
    temporary="$(dirname "$INVENTORY")/.$(basename "$INVENTORY").$$"
    trap 'rm -f "$temporary"' EXIT
    ( cd "$DEPLOY_REPO_ROOT" && "$DEPLOY_PYTHON" -c \
        'from tools.site_config.inventory import TEMPLATE; print(TEMPLATE, end="")' ) >"$temporary"
    mv -f "$temporary" "$INVENTORY"
    cat <<EOF
agent install: wrote an inventory template to $INVENTORY (0600).

The file documents the accepted keys and the shared-credential default: fill in
one line per Agent host, then re-run:

  sudo ./deploy/agent/install.sh
EOF
    exit 0
fi

if [ ! -f "$STP_SITE_FILE" ]; then
    echo "agent install: $STP_SITE_FILE is missing — install the control plane first:" >&2
    echo "agent install:   sudo ./deploy/install.sh" >&2
    exit 1
fi
if [ ! -d "$STP_BUNDLE" ]; then
    echo "agent install: release bundle $STP_BUNDLE is missing; re-run sudo ./deploy/install.sh" >&2
    exit 1
fi

deploy_ensure_python
deploy_ensure_state_dir
deploy_ensure_bindings_dir

identity="$(deploy_site_identity)"
mapfile -t identity_lines <<<"$identity"
SITE_ID="${identity_lines[0]}"
TARGET="${identity_lines[1]}"

install_flags=(--through-agents)
if [ "$DRY_RUN" -eq 1 ]; then install_flags+=(--dry-run); fi

echo "agent install: onboarding the hosts in $INVENTORY through site '$SITE_ID'"
deploy_stp install --config "$STP_SITE_FILE" --bindings-dir "$STP_BINDINGS_DIR" \
    --state-dir "$STP_STATE_DIR" --confirm-site "$SITE_ID" --confirm-target "$TARGET" \
    --agents-inventory "$INVENTORY" "${install_flags[@]}" "${PASSTHROUGH[@]}"

if [ "$DRY_RUN" -eq 1 ]; then
    echo
    echo "agent install: dry run finished; nothing was written."
    exit 0
fi

cat <<EOF

agent install: finished. Confirm each host reports ONLINE with a matched digest:
  sudo ./deploy/install.sh verify
EOF
