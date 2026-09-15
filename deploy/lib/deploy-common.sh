#!/usr/bin/env bash
# 一站式部署三个入口（preflight.sh / install.sh / agent/install.sh）的公共前置。
#
# 只做三件事：定位仓库根、确定可用的解释器、把选择暴露成统一变量。
# 业务逻辑一律在 `python -m tools.site_config` 与 `tools/release/build_bundle.py`
# 里，这里复制一份就会与那条路径漂移（曾因 bindings 目录不一致导致 S5 解析失败）。
#
# 用法（在脚本里）：
#   source "$(dirname "${BASH_SOURCE[0]}")/lib/deploy-common.sh"   # 或下方 relative path
#   deploy_require_root
#   deploy_ensure_python
#   "$DEPLOY_PYTHON" -m tools.site_config ...

set -euo pipefail

deploy_repo_root() {
    local here
    here="$(cd "$(dirname "${BASH_SOURCE[1]}")" && pwd)"
    # deploy/lib/ → 仓库根；深一层是 deploy/agent/install.sh
    while [ "$here" != "/" ]; do
        if [ -f "$here/tools/site_config/__main__.py" ]; then
            printf '%s\n' "$here"
            return 0
        fi
        here="$(dirname "$here")"
    done
    echo "deploy: cannot locate the repository root (expected tools/site_config/__main__.py)" >&2
    return 1
}

deploy_require_root() {
    if [ "$(id -u)" -ne 0 ]; then
        echo "deploy: run with sudo — the installer creates accounts, services and mounts." >&2
        exit 1
    fi
}

# 站点输入与运行状态的位置：三个脚本必须一致，否则 install 与 agent install 会
# 各自指向不同站点。全部可用环境变量覆盖。
deploy_defaults() {
    DEPLOY_REPO_ROOT="${DEPLOY_REPO_ROOT:-$(deploy_repo_root)}"
    STP_SITE_FILE="${STP_SITE_FILE:-/etc/stp/site.yaml}"
    STP_BINDINGS_DIR="${STP_BINDINGS_DIR:-/etc/stp/bindings}"
    STP_STATE_DIR="${STP_STATE_DIR:-/var/lib/stp}"
    STP_BUNDLE="${STP_BUNDLE:-/srv/stp-bundle}"
    STP_AGENTS_INVENTORY="${STP_AGENTS_INVENTORY:-${HOME:-/root}/hosts.ini}"
    STP_SITE_ID="${STP_SITE_ID:-city-b}"
    export DEPLOY_REPO_ROOT STP_SITE_FILE STP_BINDINGS_DIR STP_STATE_DIR STP_BUNDLE
    export STP_AGENTS_INVENTORY STP_SITE_ID
}

deploy_find_python() {
    # 只选不建：preflight 承诺零写入，缺依赖要作为一条 FAIL 报出来。
    local target="${STP_TOOL_VENV:-/opt/stp-tool}"
    if [ -x "$target/bin/python" ]; then
        DEPLOY_PYTHON="$target/bin/python"
    elif [ -x "$DEPLOY_REPO_ROOT/.venv/bin/python" ]; then
        DEPLOY_PYTHON="$DEPLOY_REPO_ROOT/.venv/bin/python"
    else
        DEPLOY_PYTHON="$(command -v python3 || true)"
    fi
    if [ -z "$DEPLOY_PYTHON" ]; then
        echo "deploy: no python3 interpreter found" >&2
        exit 1
    fi
    export DEPLOY_PYTHON
}

deploy_ensure_python() {
    if [ "${DRY_RUN:-0}" -eq 1 ]; then
        # --dry-run 承诺零写入：不建 venv，只用现有解释器（缺依赖就如实报错）。
        deploy_find_python
        return 0
    fi
    local target="${STP_TOOL_VENV:-/opt/stp-tool}"
    if [ -x "$target/bin/python" ]; then
        DEPLOY_PYTHON="$target/bin/python"
    else
        echo "deploy: creating the installer environment at $target"
        python3 -m venv "$target"
        "$target/bin/pip" install -q --disable-pip-version-check pydantic pyyaml "psycopg[binary]"
        DEPLOY_PYTHON="$target/bin/python"
    fi
    export DEPLOY_PYTHON
}

deploy_stp() {
    cd "$DEPLOY_REPO_ROOT"
    "$DEPLOY_PYTHON" -m tools.site_config "$@"
}

# 从 site.yaml 读站点标识与目标主机（仅两个标量；语义校验由 validate/install 负责）。
deploy_site_identity() {
    cd "$DEPLOY_REPO_ROOT"
    "$DEPLOY_PYTHON" - "$STP_SITE_FILE" <<'PY'
import sys

import yaml

with open(sys.argv[1], encoding="utf-8") as handle:
    data = yaml.safe_load(handle)
print(data["site"]["id"])
print(data["control_plane"]["target"])
PY
}

deploy_ensure_state_dir() {
    [ "${DRY_RUN:-0}" -eq 1 ] && return 0
    install -d -m 0700 -o root -g root "$STP_STATE_DIR"
}

deploy_ensure_bindings_dir() {
    [ "${DRY_RUN:-0}" -eq 1 ] && return 0
    install -d -m 0700 -o root -g root "$STP_BINDINGS_DIR"
}

deploy_next_steps() {
    cat <<EOF

Next steps
  1. Add Agent hosts to the inventory (one line each):
       sudo \${EDITOR:-vi} $STP_AGENTS_INVENTORY
  2. Onboard them through this site's own API:
       sudo ./deploy/agent/install.sh
  3. Run the controlled acceptance chain (S6):
       sudo ./deploy/install.sh verify
  4. Turn the run into P1 acceptance evidence:
       sudo $DEPLOY_PYTHON -m tools.site_config verify --config $STP_SITE_FILE \\
           --bindings-dir $STP_BINDINGS_DIR --json > $STP_STATE_DIR/verify-report.json
       sudo ./deploy/install.sh handover
EOF
}
