#!/usr/bin/env bash
# 只读体检：在任何写入之前，把「缺什么、怎么补」逐项列出来。
#
#   sudo ./deploy/preflight.sh                       # 本机就绪度
#   sudo ./deploy/preflight.sh --db-url <DSN>        # 连目标库是否可达且为空一并探测
#   sudo ./deploy/preflight.sh --redis-url redis://127.0.0.1:6379/1
#   sudo ./deploy/preflight.sh --bundle /srv/stp-bundle
#
# 本脚本零写入（不建 venv、不落文件、不改服务）。退出码：0=无 FAIL；1=有 FAIL。
# BLOCKED 表示「没给它输入所以没验证」，不算失败，但也不会被当成通过。
#
# 附加参数原样透传给 `python -m tools.site_config preflight`。

set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/deploy-common.sh"

if [ "${1:-}" = "--help" ] || [ "${1:-}" = "-h" ]; then
    sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'
    exit 0
fi

deploy_defaults
deploy_require_root
deploy_find_python

echo "preflight: host readiness for a stability-test-platform site (read-only)"
echo "preflight: repository=$DEPLOY_REPO_ROOT interpreter=$DEPLOY_PYTHON"

args=(preflight)
if [ -f "$STP_SITE_FILE" ]; then
    # 已有站点输入：把它的绑定目录、发布物与部署根一并探测，便于重跑前确认。
    args+=(--bindings-dir "$STP_BINDINGS_DIR")
    if [ -d "$STP_BUNDLE" ]; then
        args+=(--bundle "$STP_BUNDLE")
    fi
fi

set +e
deploy_stp "${args[@]}" "$@"
status=$?
set -e

if [ "$status" -eq 2 ]; then
    exit 2
fi
if [ "$status" -ne 0 ]; then
    echo
    echo "preflight: at least one item FAILed — align it with its Fix line, then re-run."
    echo "preflight: this command wrote nothing."
    exit "$status"
fi

echo
echo "preflight: all checks passed. Install with: sudo ./deploy/install.sh"
exit 0
