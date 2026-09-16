#!/bin/bash
# Rendered by the site installer for <deploy-root> — do not edit by hand.
# stp-mem-top — 采集宿主机进程"匿名内存"Top-N，供 node_exporter textfile collector 暴露。
#
# 背景：2026-09-14 09:14 控制面整机卡死（~43GB 匿名内存压垮 24GB 机器），事后无
# per-process 记录可归因。本脚本由 systemd timer 周期运行，落两份数据：
#   1) {TEXTFILE_DIR}/stp_hostproc.prom  Prometheus 指标（按 comm+cgroup unit 聚合 Top-N）
#   2) {LOG_FILE}                        追加式 TSV（含 pid 与完整命令行，供人工回溯）
#
# 安装（控制面宿主机，root）：
#   install -m 0755 deploy/control-plane/node-exporter/stp-mem-top.sh /usr/local/sbin/stp-mem-top
#   install -m 0644 deploy/control-plane/systemd/stp-mem-top.service /etc/systemd/system/
#   install -m 0644 deploy/control-plane/systemd/stp-mem-top.timer   /etc/systemd/system/
#   systemctl daemon-reload && systemctl enable --now stp-mem-top.timer
#
# 展示：控制面健康页（/storage → "进程内存 Top 10"）经后端代理 Prometheus 读取，
# 见 backend/services/file_server_monitor.py。
#
# 环境变量（可选）：STP_MEM_TOP_TEXTFILE_DIR / STP_MEM_TOP_LOG / STP_MEM_TOP_N /
#                   STP_MEM_TOP_LOG_N / STP_MEM_TOP_MAX_LOG_BYTES
set -u
LC_ALL=C
export LC_ALL

TEXTFILE_DIR="${STP_MEM_TOP_TEXTFILE_DIR:-/var/lib/prometheus/node-exporter}"
PROM_FILE="${TEXTFILE_DIR}/stp_hostproc.prom"
LOG_FILE="${STP_MEM_TOP_LOG:-/var/log/stp-mem-top.tsv}"
TOP_N="${STP_MEM_TOP_N:-10}"
LOG_TOP_N="${STP_MEM_TOP_LOG_N:-20}"
MAX_LOG_BYTES="${STP_MEM_TOP_MAX_LOG_BYTES:-20000000}"

# 控制字符规范化（#2016）：`comm`/`unit`/`cmdline` 全部取自外部——进程可用
# prctl(PR_SET_NAME) 自设 15 字节名字（含 \n 也合法），异常 cgroup 名同理。裸换行会
# 同时污染两处 sink：Prometheus exposition（一行被截断 → textfile collector 判整个
# stp_hostproc.prom 解析失败，**全部** stp_hostproc_* 序列消失）与本脚本的 TSV 日志
# （一行变多行 + `read -r key anon` 在首个 TAB 处错切列）。在**取值处**统一压成空格，
# 两处输出都不必各自再处理；Prometheus 侧的 `\` 与 `"` 转义保持原样（那是值内合法字符）。
# 就地改写命名变量而非 `$(stp_sanitize ...)`：本函数每个 pid 调 4 次，热循环里不 fork。
stp_sanitize() {
    local __name=$1 __value=${!1}
    __value=${__value//$'\n'/ }
    __value=${__value//$'\r'/ }
    __value=${__value//$'\t'/ }
    printf -v "$__name" '%s' "$__value"
}

ts=$(date '+%F %T')
declare -A group_anon=()
declare -a procs=()
total=0

for d in /proc/[0-9]*; do
    [ -r "$d/smaps_rollup" ] || continue
    anon=$(awk '/^Anonymous:/{print $2; exit}' "$d/smaps_rollup" 2>/dev/null)
    case ${anon:-} in ''|*[!0-9]*) continue ;; esac
    [ "$anon" -gt 0 ] || continue
    comm=$(cat "$d/comm" 2>/dev/null) || continue
    stp_sanitize comm
    [ -n "${comm:-}" ] || continue
    unit=$(awk -F: '/^0::/{n=split($3,a,"/"); print a[n]; exit}' "$d/cgroup" 2>/dev/null)
    stp_sanitize unit
    [ -n "${unit:-}" ] || unit="-"
    cmdline=$(tr '\0' ' ' < "$d/cmdline" 2>/dev/null | cut -c1-160)
    stp_sanitize cmdline
    # 指标标签用 argv0 基名（node 进程的 comm 常被线程名占用显示为 MainThread），
    # 拿不到 argv0 时回退 comm；日志里两者都留（另附完整命令行）。
    name=$(awk '{n=split($1,a,"/"); print a[n]}' <<< "$cmdline")
    stp_sanitize name
    [ -n "${name:-}" ] || name="$comm"
    key="${name}|${unit}"
    group_anon[$key]=$(( ${group_anon[$key]:-0} + anon ))
    total=$(( total + anon ))
    procs+=("$(printf '%s\t%s\t%s\t%s\t%s' "${d#/proc/}" "$anon" "$comm" "$unit" "$cmdline")")
done

# ---- 1) Prometheus textfile（原子替换，0644 供 prometheus 用户读取）----
tmp=$(mktemp "${PROM_FILE}.XXXXXX") || exit 1
{
    printf '# HELP stp_hostproc_anon_bytes Top-N 进程组（comm+cgroup unit）的匿名内存\n'
    printf '# TYPE stp_hostproc_anon_bytes gauge\n'
    for key in "${!group_anon[@]}"; do
        printf '%s\t%s\n' "$key" "${group_anon[$key]}"
    done | sort -t$'\t' -k2,2nr | head -n "$TOP_N" | while IFS=$'\t' read -r key anon; do
        comm=${key%%|*}
        unit=${key#*|}
        comm=${comm//\\/\\\\}; comm=${comm//\"/\\\"}
        unit=${unit//\\/\\\\}; unit=${unit//\"/\\\"}
        printf 'stp_hostproc_anon_bytes{comm="%s",unit="%s"} %s\n' \
            "$comm" "$unit" "$((anon * 1024))"
    done
    printf '# HELP stp_hostproc_anon_total_bytes 全机进程匿名内存合计\n'
    printf '# TYPE stp_hostproc_anon_total_bytes gauge\n'
    printf 'stp_hostproc_anon_total_bytes %s\n' "$((total * 1024))"
} > "$tmp"
chmod 0644 "$tmp"
mv -f "$tmp" "$PROM_FILE"

# ---- 2) 追加式 TSV（Top-LOG_TOP_N；含 pid/完整命令行，便于人工回溯）----
{
    awk -v ts="$ts" '/^(MemTotal|MemAvailable|SwapTotal|SwapFree|AnonPages|Shmem):/{
        printf "%s\tMEM\t%s\t%s\n", ts, $1, $2
    }' /proc/meminfo
    for line in "${procs[@]}"; do
        printf '%s\tPROC\t%s\n' "$ts" "$line"
    done | sort -t$'\t' -k4,4nr | head -n "$LOG_TOP_N"
} >> "$LOG_FILE"

size=$(stat -c%s "$LOG_FILE" 2>/dev/null || echo 0)
if [ "$size" -gt "$MAX_LOG_BYTES" ]; then
    tail -c "$MAX_LOG_BYTES" "$LOG_FILE" > "${LOG_FILE}.tmp" && mv "${LOG_FILE}.tmp" "$LOG_FILE"
fi

exit 0
