#!/usr/bin/env bash
# #2333：兜底单的**归因输入**——红灯时对失败 job 自动 rerun 一次，并按「重跑是否转绿」
# 机械分类为 flake / 确定性缺陷；重跑仍红时附失败用例名（机械 grep，不引入 LLM 分诊，
# 见 docs/design/2026-08-governance-surface-protection.md §6）。
#
# 为什么需要它：`#1525` 的前移规则是「同类夜间红灯 ≥2 次 → 评估前移为 PR 侧检查」，
# 其隐含前提是「红灯 = 确定性缺陷」。而 `backend-test` 类红灯里混有 flake（#2333 事实 3），
# 对 flake 前移等于把 flakiness 引进合入路径。本脚本补的就是「缺陷 / flake」这一维输入。
#
# **顺序是硬约束**：红灯 job 清单与失败用例名都必须在 **rerun 之前**取——
# `gh run rerun` 会把 job 结论重置（转绿后 `.conclusion` 不再是 failure，清单会变空），
# job 日志端点也随手指向**新**一次尝试。先重跑再取，等于把红灯证据擦掉。
#
# 用法（workflow 内）：
#   REPO=owner/name CI_RUN_ID=123 FIRST_CONCLUSION=failure \
#     bash scripts/ci/backstop-attribution.sh
# 输出：写 $GITHUB_OUTPUT（在场时，字段 failed_jobs_md / attribution_md / classification /
# rerun_conclusion）；stdout 打人类可读摘要（日志留痕）。
#
# 测试用 dry-run：DRY_RUN=1 时不触网，取数由 fixture 提供
# （DRY_RUN_JOBS_JSON / DRY_RUN_LOG_FILE / DRY_RUN_ATTEMPT / DRY_RUN_RERUN_CONCLUSION /
# DRY_RUN_RERUN_OK）；fixture 缺失即复现「取不到日志」的真实失败语义。
set -euo pipefail

REPO="${REPO:?REPO is required}"
CI_RUN_ID="${CI_RUN_ID:?CI_RUN_ID is required}"
FIRST_CONCLUSION="${FIRST_CONCLUSION:-failure}"
DRY_RUN="${DRY_RUN:-0}"

# 重跑后等待完成的预算（30s × 120 = 1h；全量 CI 含 backend/frontend/docker）
WAIT_INTERVAL="${WAIT_INTERVAL:-30}"
WAIT_MAX="${WAIT_MAX:-120}"
# 机械摘要不是日志倾倒：红灯 job 与失败用例名各设上限，够定位即可
MAX_JOBS="${MAX_JOBS:-20}"
MAX_CASES="${MAX_CASES:-20}"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# GHA 原始 job 日志每行形如 `2026-08-25T18:28:58.8735367Z <内容>`。用「Z + 空格 + 摘要词」
# 锚定 pytest 的 short summary（`FAILED path::test`）与 vitest 的 `FAIL path`，避免命中正文里
# 出现的同名词；只取测试标识符，不带后面的错误文案（换行会破坏 issue 正文）。
# `[]...` 是 POSIX 字符类里「把 ] 放首位即字面量」的写法，故测试 id 的方括号可被吃下。
_SUMMARY_TOKEN_RE='Z (FAIL|FAILED|ERROR) [][A-Za-z0-9_./:-]+'

# 先剥 ANSI CSI 色码再 grep：日志里 `FAILED` 可能被自身着色包住（`ESC[31mFAILED`），
# 不剥则锚点 `Z FAIL` 匹配不上——那会静默变成「未取到失败用例名」，而日志其实在。
strip_ansi() { sed -e $'s/\033\\[[0-9;]*[A-Za-z]//g'; }

output() { # 单行 output
  [ -n "${GITHUB_OUTPUT:-}" ] || return 0
  printf '%s=%s\n' "$1" "$2" >> "$GITHUB_OUTPUT"
}

output_multi() { # output_multi <name> <file>
  [ -n "${GITHUB_OUTPUT:-}" ] || return 0
  local delim="__STP_${1}_EOF__"
  { printf '%s<<%s\n' "$1" "$delim"; cat "$2"; printf '%s\n' "$delim"; } >> "$GITHUB_OUTPUT"
}

first_line() { # <file> → 首行；空/缺失一律回落「原因未知」（不留半句空话）
  local line
  line="$(head -n 1 "$1" 2>/dev/null || true)"
  printf '%s\n' "${line:-原因未知}"
}

# ── 取数层（dry-run 换源，其余逻辑与真实路径完全一致）──────────────────────

run_attempt() {
  if [ "$DRY_RUN" = "1" ]; then
    printf '%s\n' "${DRY_RUN_ATTEMPT:-1}"
    return 0
  fi
  gh api "repos/$REPO/actions/runs/$CI_RUN_ID" | jq -r '.run_attempt // 1'
}

failed_jobs_tsv() { # `id<TAB>name<TAB>失败步骤名` 行
  local payload
  if [ "$DRY_RUN" = "1" ]; then
    payload="$(cat "${DRY_RUN_JOBS_JSON:?DRY_RUN_JOBS_JSON required}")"
  else
    payload="$(gh api "repos/$REPO/actions/runs/$CI_RUN_ID/jobs?per_page=100")"
  fi
  printf '%s' "$payload" | jq -r '
    .jobs[] | select(.conclusion == "failure")
    | [.id, .name, ([.steps[]? | select(.conclusion == "failure") | .name] | join(", "))] | @tsv'
}

job_log() { # <job_id> → 日志文本；取不到时把原因写 $WORK/log_err 并非零退出
  if [ "$DRY_RUN" = "1" ]; then
    # 不给 `return 0`：fixture 不存在时函数**必须**失败，否则「取不到日志」这条
    # 分支在 dry-run 下永远测不到（真实路径的失败语义要靠它复现）。
    cat "${DRY_RUN_LOG_FILE:?DRY_RUN_LOG_FILE required}"
    return
  fi
  # 必须带 --allow-escape-sequences：日志含 ANSI 色码，gh 默认拒绝输出（exit 1、
  # stdout 0 字节），会被误读成「接口无数据」——正是 #2333 当初误判的那一点。
  gh api --allow-escape-sequences "repos/$REPO/actions/jobs/$1/logs"
}

rerun_failed_jobs() {
  if [ "$DRY_RUN" = "1" ]; then
    [ "${DRY_RUN_RERUN_OK:-1}" = "1" ]
    return
  fi
  gh run rerun "$CI_RUN_ID" --repo "$REPO" --failed
}

wait_conclusion() { # 重跑后的 run 结论；未在预算内完成则非零退出
  if [ "$DRY_RUN" = "1" ]; then
    printf '%s\n' "${DRY_RUN_RERUN_CONCLUSION:-}"
    return 0
  fi
  local status="" conclusion="" line=""
  for _ in $(seq 1 "$WAIT_MAX"); do
    line="$(gh api "repos/$REPO/actions/runs/$CI_RUN_ID" | jq -r '"\(.status) \(.conclusion // "")"')" \
      || return 1
    status="${line%% *}"
    conclusion="${line#* }"
    [ "$status" = "completed" ] && break
    sleep "$WAIT_INTERVAL"
  done
  [ "$status" = "completed" ] || return 1
  printf '%s\n' "$conclusion"
}

# ── 重跑前的证据快照 ──────────────────────────────────────────────────────
# 注：两个收集函数把结果写文件而不是用 `$(...)` 取——命令替换是子壳，函数内赋值传不回来。

snapshot_failed_jobs() { # → $WORK/jobs_tsv、$WORK/failed_jobs_md（三要素①）
  failed_jobs_tsv > "$WORK/jobs_tsv"
  # 限行**在 awk 内**做，不要 `awk ... | head -n N`：pipefail 下 head 提前退出会让 awk
  # 吃 SIGPIPE、整条管线非零，set -e 直接终止脚本（#389 同一形态）。
  awk -F'\t' -v max="$MAX_JOBS" '
    NR > max { exit }
    { printf "- **%s** → 失败步骤: %s\n", $2, ($3 == "" ? "(未取到失败步骤名)" : $3) }
  ' "$WORK/jobs_tsv" > "$WORK/failed_jobs_md"
}

collect_cases() { # → $WORK/cases（去重、截断）、$WORK/log_notes（取不到日志的原因）
  : > "$WORK/cases"
  : > "$WORK/log_notes"
  local jid jname _steps
  while IFS=$'\t' read -r jid jname _steps; do
    [ -n "${jid:-}" ] || continue
    if job_log "$jid" > "$WORK/one_log" 2> "$WORK/log_err"; then
      strip_ansi < "$WORK/one_log" | grep -aoE "$_SUMMARY_TOKEN_RE" | sed -e 's/^Z //' >> "$WORK/cases" || true
    else
      printf '  - job %s（%s）：未取到日志——%s\n' \
        "$jname" "$jid" "$(first_line "$WORK/log_err")" >> "$WORK/log_notes"
    fi
  done < "$WORK/jobs_tsv"
  sort -u "$WORK/cases" -o "$WORK/cases"
  head -n "$MAX_CASES" "$WORK/cases" > "$WORK/cases_capped"
  mv "$WORK/cases_capped" "$WORK/cases"
}

to_md_list() { sed -e 's/^/  - `/; s/$/`/' "$1"; }

verdict_line() { # <classification> → 处置去向（与 #1525 前移规则同一口径）
  case "$1" in
    flake)
      printf '%s\n' '- 缺陷 / flake 分类: 重跑**转绿** → **flake**——进「去 flake」队列；**不进入** `#1525` 的前移评估（对 flake 前移会把 flakiness 引进合入路径）。'
      ;;
    deterministic)
      printf '%s\n' '- 缺陷 / flake 分类: 重跑**仍红** → **确定性缺陷**——才进入 `#1525` 的前移评估。'
      ;;
    *)
      printf '%s\n' '- 缺陷 / flake 分类: **未能分类**——按确定性缺陷处置，但**不得**作为前移评估的样本。'
      ;;
  esac
}

main() {
  local attempt rerun_conclusion classification note=""

  attempt="$(run_attempt)"
  case "$attempt" in *[!0-9]* | '') attempt=1 ;; esac

  # 证据先落盘，再动重跑（否则重跑会重置 job 结论、日志端点也改指新尝试）。
  snapshot_failed_jobs
  collect_cases

  if [ "$attempt" -gt 1 ]; then
    # 该 run 已被重跑过（本 workflow 重入或人工重跑）：观测到的红灯就是重跑后的结论，
    # 直接判为确定性缺陷，且**不再重跑**——「自动 rerun 一次」的「一次」在此闭合。
    rerun_conclusion="$FIRST_CONCLUSION"
    classification="deterministic"
    note="该 run 已是第 ${attempt} 次尝试（此前已重跑），不再自动重跑。"
  elif rerun_failed_jobs 2> "$WORK/rerun_err"; then
    if rerun_conclusion="$(wait_conclusion)"; then
      case "$rerun_conclusion" in
        success) classification="flake" ;;
        failure | timed_out | startup_failure) classification="deterministic" ;;
        *) classification="unknown" ;;
      esac
    else
      rerun_conclusion=""
      classification="unknown"
      note="重跑未在预算内完成（${WAIT_MAX} × ${WAIT_INTERVAL}s），无法分类。"
    fi
  else
    rerun_conclusion=""
    classification="unknown"
    note="重跑请求失败：$(first_line "$WORK/rerun_err")"
  fi

  {
    printf -- '- 首次结论: `%s`\n' "$FIRST_CONCLUSION"
    if [ -n "$rerun_conclusion" ]; then
      printf -- '- 重跑结论: `%s`\n' "$rerun_conclusion"
    else
      printf -- '- 重跑结论: （无）\n'
    fi
    verdict_line "$classification"
    if [ -s "$WORK/cases" ]; then
      printf -- '- 失败用例名（首次尝试，机械 grep `FAIL|FAILED|ERROR`，未去噪）:\n'
      to_md_list "$WORK/cases"
    else
      printf -- '- 失败用例名: 未取到失败用例名（首次尝试日志中无 `FAIL|FAILED|ERROR` 摘要行）\n'
    fi
    if [ -s "$WORK/log_notes" ]; then
      printf -- '- 日志取用情况:\n'
      cat "$WORK/log_notes"
    fi
    if [ -n "$note" ]; then
      printf -- '- 备注: %s\n' "$note"
    fi
  } > "$WORK/attribution_md"

  if [ ! -s "$WORK/failed_jobs_md" ]; then
    printf '(未取到 job 明细——疑似基础设施错误或运行已被清理)\n' > "$WORK/failed_jobs_md"
  fi

  output classification "$classification"
  output rerun_conclusion "$rerun_conclusion"
  output_multi failed_jobs_md "$WORK/failed_jobs_md"
  output_multi attribution_md "$WORK/attribution_md"

  printf 'backstop_attribution attempt=%s classification=%s rerun_conclusion=%s\n' \
    "$attempt" "$classification" "${rerun_conclusion:-none}"
  cat "$WORK/attribution_md"
}

main "$@"
