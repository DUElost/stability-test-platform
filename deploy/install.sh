#!/usr/bin/env bash
# 控制面一站式安装：构建发布物 → 生成站点输入 → 校验/计划 → S0–S4 安装。
#
#   sudo ./deploy/install.sh              # 缺什么补什么；只回答 ≤4 个问题
#   sudo ./deploy/install.sh --yes        # 全部取探测默认（非交互，适合脚本/CI）
#   sudo ./deploy/install.sh --dry-run    # 只报计划，一个字节都不写
#   sudo ./deploy/install.sh --database stp_b --public-url http://192.0.2.5
#                                         # 站点输入项：--display-name/--public-url/
#                                         # --database/--redis-index/--storage-mount/
#                                         # --data-disk/--admin-username/--bundle
#   sudo ./deploy/install.sh verify       # 装完之后跑 S6 受控验收
#   sudo ./deploy/install.sh handover     # 汇总 MS-01/02/04/05/06/10/13 证据
#
# 任一步失败即停，并保留上一步的产物——重跑从缺的那一步继续（不重新提问、不覆盖）。
# 宿主机写操作（建 venv、建库、挂盘、fstab）默认执行并逐项回显；--no-fix 退化为只报命令。
#
# 覆盖位置（环境变量）：STP_SITE_FILE / STP_BINDINGS_DIR / STP_STATE_DIR / STP_BUNDLE /
# STP_TOOL_VENV / STP_SITE_ID。发布物已由发布渠道备好时用 STP_BUNDLE 指向它即可跳过构建。

set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/deploy-common.sh"

SUBCOMMAND="install"
FIX=1
DRY_RUN=0
ASSUME_YES=0
THROUGH_AGENTS=0
AGENTS_INVENTORY=""
PASSTHROUGH=()
INIT_OPTIONS=()

while [ "$#" -gt 0 ]; do
    case "$1" in
        verify|handover) SUBCOMMAND="$1"; shift ;;
        --no-fix) FIX=0; shift ;;
        --dry-run) DRY_RUN=1; shift ;;
        --yes|-y) ASSUME_YES=1; shift ;;
        --through-agents) THROUGH_AGENTS=1; shift ;;
        --agents-inventory) AGENTS_INVENTORY="${2:?--agents-inventory needs a file}"; shift 2 ;;
        --agents-inventory=*) AGENTS_INVENTORY="${1#*=}"; shift ;;
        # 站点输入项转给 init；其余安装期选项透传给 install
        --reset-db-password) INIT_OPTIONS+=(--reset-db-password); shift ;;
        --display-name|--public-url|--database|--redis-index|--storage-mount|--data-disk|--admin-username|--bundle)
            if [ -z "${2:-}" ]; then echo "install: $1 needs a value" >&2; exit 2; fi
            INIT_OPTIONS+=("$1" "$2"); shift 2 ;;
        --display-name=*|--public-url=*|--database=*|--redis-index=*|--storage-mount=*|--data-disk=*|--admin-username=*|--bundle=*)
            INIT_OPTIONS+=("${1%%=*}" "${1#*=}"); shift ;;
        --help|-h) sed -n '2,19p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) PASSTHROUGH+=("$1"); shift ;;
    esac
done

deploy_defaults

# 参数级守卫放在任何宿主机写入之前：错误的调用不该留下 venv/目录
if [ "$SUBCOMMAND" = "verify" ] && [ "$DRY_RUN" -eq 1 ]; then
    # verify 没有 dry-run 语义（它本来就要驱动受控链）：静默照跑等于违背 --dry-run
    echo "install: verify has no --dry-run; it always drives the controlled chain." >&2
    echo "install: bound it with --run-timeout / --device-serial, or check readiness with ./deploy/preflight.sh." >&2
    exit 2
fi
if [ "$SUBCOMMAND" != "install" ] && [ ! -f "$STP_SITE_FILE" ]; then
    echo "install: $SUBCOMMAND needs an installed site, but $STP_SITE_FILE is missing." >&2
    echo "install: run sudo ./deploy/install.sh first." >&2
    exit 1
fi

deploy_require_root
deploy_ensure_python
deploy_ensure_state_dir
deploy_ensure_bindings_dir

# ── 装完之后的两条命令：纯透传，不复制任何检查逻辑 ────────────────────────
if [ "$SUBCOMMAND" = "verify" ]; then
    deploy_stp verify --config "$STP_SITE_FILE" --bindings-dir "$STP_BINDINGS_DIR" "${PASSTHROUGH[@]}"
    cat <<EOF

verify: to let handover consume this run, save the same report as JSON:
  sudo $DEPLOY_PYTHON -m tools.site_config verify --config $STP_SITE_FILE \\
      --bindings-dir $STP_BINDINGS_DIR --json > $STP_STATE_DIR/verify-report.json
EOF
    exit 0
fi
if [ "$SUBCOMMAND" = "handover" ]; then
    handover_flags=()
    if [ -f "$STP_STATE_DIR/verify-report.json" ]; then
        handover_flags+=(--verify-report "$STP_STATE_DIR/verify-report.json")
    fi
    if [ "$DRY_RUN" -eq 1 ]; then handover_flags+=(--dry-run); fi
    deploy_stp handover --config "$STP_SITE_FILE" --state-dir "$STP_STATE_DIR" \
        "${handover_flags[@]}" "${PASSTHROUGH[@]}"
    exit 0
fi

# ── 1. 发布物（R2 未交付前的本地实现；发布渠道就绪后把 STP_BUNDLE 指向产物即可）──
# 必须先于站点输入：init 会从清单读 expected_release，顺序颠倒会永久不匹配。
if [ ! -f "$STP_BUNDLE/release-manifest.json" ]; then
    if [ "$DRY_RUN" -eq 1 ]; then
        echo "install: would build the release bundle at $STP_BUNDLE"
    else
        echo "install: building the release bundle at $STP_BUNDLE"
        ( cd "$DEPLOY_REPO_ROOT" && "$DEPLOY_PYTHON" tools/release/build_bundle.py --out "$STP_BUNDLE" )
    fi
else
    echo "install: reusing existing release bundle $STP_BUNDLE"
fi

# ── 2. 站点输入 ───────────────────────────────────────────────────────────
init_flags=()
if [ "$FIX" -eq 0 ]; then init_flags+=(--no-fix); fi
if [ "$DRY_RUN" -eq 1 ]; then init_flags+=(--dry-run); fi
if [ "$ASSUME_YES" -eq 1 ]; then init_flags+=(--non-interactive); fi

if [ ! -f "$STP_SITE_FILE" ]; then
    if [ "$DRY_RUN" -eq 0 ] && [ "$ASSUME_YES" -eq 0 ] && [ ! -t 0 ]; then
        echo "install: stdin is not a terminal and $STP_SITE_FILE does not exist yet." >&2
        echo "install: re-run with --yes to accept the probed defaults, or run it interactively." >&2
        exit 2
    fi
    echo "install: no site input at $STP_SITE_FILE — probing this host and asking four questions"
    deploy_stp init --output "$STP_SITE_FILE" --bindings-dir "$STP_BINDINGS_DIR" \
        --site-id "$STP_SITE_ID" --bundle "$STP_BUNDLE" "${INIT_OPTIONS[@]}" "${init_flags[@]}"
    if [ "$DRY_RUN" -eq 1 ]; then
        echo
        echo "install: dry run finished; neither the site input nor anything else was written."
        exit 0
    fi
else
    echo "install: reusing existing site input $STP_SITE_FILE"
fi

# ── 3. 校验 + 计划（先看清再动手）──────────────────────────────────────────
echo "install: validating and planning"
deploy_stp validate --config "$STP_SITE_FILE"
deploy_stp plan --config "$STP_SITE_FILE"

# ── 4. S0–S4 安装（+ 可选 S5 Agent 接入）─────────────────────────────────
identity="$(deploy_site_identity)"
mapfile -t identity_lines <<<"$identity"
SITE_ID="${identity_lines[0]}"
TARGET="${identity_lines[1]}"

install_flags=()
if [ "$DRY_RUN" -eq 1 ]; then install_flags+=(--dry-run); fi
if [ "$THROUGH_AGENTS" -eq 1 ]; then install_flags+=(--through-agents); fi
if [ -n "$AGENTS_INVENTORY" ]; then install_flags+=(--agents-inventory "$AGENTS_INVENTORY"); fi

steps="S0–S4"
if [ "$THROUGH_AGENTS" -eq 1 ]; then steps="S0–S5"; fi
echo "install: running $steps for site '$SITE_ID' on '$TARGET'"
deploy_stp install --config "$STP_SITE_FILE" --bindings-dir "$STP_BINDINGS_DIR" \
    --state-dir "$STP_STATE_DIR" --confirm-site "$SITE_ID" --confirm-target "$TARGET" \
    "${install_flags[@]}" "${PASSTHROUGH[@]}"

if [ "$DRY_RUN" -eq 1 ]; then
    echo
    echo "install: dry run finished; nothing was written."
    exit 0
fi

deploy_next_steps
