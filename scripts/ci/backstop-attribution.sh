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
# 且同名 job 在新 attempt 里会拿到**全新 job id**。先重跑再取，等于把红灯证据擦掉。
#
# #3573（方案 v1.0，2026-10-03）：跨 attempt 归因曾有三处独立缺陷，任何一条都足以让
# 非 dry-run 路径拿不到结论；同一选择/聚合出口一并修复：
#   ① 固定 attempt：快照按 `runs/$CI_RUN_ID/attempts/$A/jobs` 读（分页合并），轮询固定
#      `attempts/$((A+1))/jobs`。不再读「最新」attempt——重跑后同名 job 的 id 全变，
#      旧 id 匹配必然落空；旧 id 只用于取旧日志。轮询期间发现 run attempt 超过 A+1
#      （并发重跑）即 unknown，不追随最新。
#   ② 稳定身份：目标集合 = 快照中 conclusion=failure 的 job **name**，按 API `.name`
#      精确相等匹配（本仓 ci.yml 无 matrix/自定义显示名，name 在一次 attempt 内唯一）。
#      禁用子串/正则；空目标、重复 name 不能经 jq 空集 all() 判成 success。
#   ③ 落定判定：目标未齐全、任一 status!=completed、conclusion=null 一律「未定」继续等；
#      只有全部 completed 才聚合（全绿=success、全红=failure、有绿有红=mixed）；
#      cancelled/skipped/neutral/action_required 等无法归因的结论明确 unknown，禁止笼统
#      else mixed。证据不足（空目标、重复身份、取数/解析异常、并发重跑、预算耗尽）
#      一律 unknown 且备注写真实原因。
#
# 用法（workflow 内）：
#   REPO=owner/name CI_RUN_ID=123 FIRST_CONCLUSION=failure \
#     bash scripts/ci/backstop-attribution.sh
# 输出：写 $GITHUB_OUTPUT（在场时，字段 failed_jobs_md / attribution_md / classification /
# rerun_conclusion / wait_budget）；stdout 打人类可读摘要（日志留痕）。
#
# wait_budget = 本次实际采用的重跑等待窗口（秒）。命中 SLOW_JOBS（默认 backend-test）
# 时为 WAIT_MAX_SLOW × WAIT_INTERVAL，否则 WAIT_MAX × WAIT_INTERVAL —— 把它作为
# output 暴露，是为了让「预算是否按 job 分级生效」可被测试断言、也可在兜底单正文里
# 直接看到这次等了多少（#3573）。
#
# 测试分两层：
# - DRY_RUN=1 快速测试只换**取数层**（fixture 直接给快照与重跑结论），覆盖分类出口、
#   预算分级与日志抽取；**不覆盖**真实路径的 job 选择 / 跨 attempt 匹配 / 落定判定；
# - tests/test_backstop_attribution_real_path_3573.py 以 DRY_RUN=0 + 临时 PATH 里的 gh 桩
#   执行真实调用程序（固定 attempt、分页、逐轮聚合），未知调用直接失败、绝不触网。
set -euo pipefail

REPO="${REPO:?REPO is required}"
CI_RUN_ID="${CI_RUN_ID:?CI_RUN_ID is required}"
FIRST_CONCLUSION="${FIRST_CONCLUSION:-failure}"
DRY_RUN="${DRY_RUN:-0}"

# 重跑后等待完成的预算。**只看目标红灯 job，不再等整个 run**（#3573 根因）。
#
# 原来的 `wait_conclusion` 轮询 run 的 `status/conclusion`，而 run 的 completed 要等
# **所有** job 落定，于是同批次里一个不相干的慢/超时 job 就能把分类吃掉。#2441 实测
# （run 35148237108 attempt 2）：目标 `frontend-check` **2m20s** 就出结论（failure），
# 但同批次 `backend-test` 撞上自身 `timeout-minutes: 60` 被 cancelled，run 直到
# 60 分钟后才 completed ⇒ 1h 预算在边界上被这个兄弟 job 吃掉，该类红灯永远拿不到分类。
#
# 现改为：① 轮询**目标 job 自身**的结论（全部落定即返回，见 wait_conclusion）；
# ② 预算按**目标集合里最慢的那个**分级——实测墙钟 backend-test 16~60min、
# frontend-check ≈2min，故 SLOW_JOBS 默认列 backend-test（曾按直觉写成
# frontend-check，见 #3573 演进记录）。
WAIT_INTERVAL="${WAIT_INTERVAL:-30}"
WAIT_MAX="${WAIT_MAX:-120}"
# 240×30s=2h，覆盖 backend-test 自身 60min timeout 的余量
WAIT_MAX_SLOW="${WAIT_MAX_SLOW:-240}"
# 逗号分隔的 job 名白名单；命中任一即走 WAIT_MAX_SLOW
SLOW_JOBS="${SLOW_JOBS:-backend-test}"
# 机械摘要不是日志倾倒：红灯 job 与失败用例名各设上限，够定位即可
MAX_JOBS="${MAX_JOBS:-20}"
MAX_CASES="${MAX_CASES:-20}"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# write_outputs 在多个提前返回路径复用，需要读取这两项；均在 main 中赋值。
STP_ATTEMPT=""
STP_WAIT_MAX="$WAIT_MAX"

# GHA 原始 job 日志每行形如 `2026-08-25T18:28:58.8735367Z <内容>`。用「Z + 空格 + 摘要词」
# 锚定失败摘要行，避免命中正文里出现的同名词；只取测试标识符，不带后面的错误文案
# （换行会破坏 issue 正文）。
#
# 摘要词两侧都是**一个或多个空格**（#3573 判据 3）——两个框架的实际形态不同：
#   pytest short summary：`…Z FAILED backend/tests/x.py::TestC::test_y`  ← Z 后单空格
#   vitest 失败清单：     `…Z  FAIL  src/x.test.ts > suite > name`     ← Z 后**双**空格
# 原先写死「Z + 恰好一个空格 + token + 恰好一个空格」，于是 vitest 侧**恒空**——
# 「未取到失败用例名」而日志里其实有。该缺失不是装饰性的：#2441 那一夜前端红灯就因取不到
# 用例名，在归因块里自述「不得作为前移评估的样本」。
# `[]...` 是 POSIX 字符类里「把 ] 放首位即字面量」的写法，故测试 id 的方括号可被吃下。
# vitest 用 ` > ` 分隔层级，不是标识符的一部分，故只取首段文件名。
_SUMMARY_TOKEN_RE='Z +(FAIL|FAILED|ERROR) +[][A-Za-z0-9_./:-]+'

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

# ── 取数层（dry-run 只换这一层，选择/聚合程序与真实路径共用）─────────────────

run_attempt() {
  if [ "$DRY_RUN" = "1" ]; then
    printf '%s\n' "${DRY_RUN_ATTEMPT:-1}"
    return 0
  fi
  gh api "repos/$REPO/actions/runs/$CI_RUN_ID" | jq -r '.run_attempt // 1'
}

# fetch_attempt_jobs <attempt> → stdout 合并分页后的 {"jobs":[...]}
# 失败（含 404）非零返回，gh/解析错误原因写 $WORK/gh_err——404 与否由调用方判定。
fetch_attempt_jobs() {
  local attempt="$1" page=1 payload count total acc_count=0
  local acc="$WORK/jobs_pages.jsonl"
  : > "$acc"
  while :; do
    if ! payload="$(gh api "repos/$REPO/actions/runs/$CI_RUN_ID/attempts/$attempt/jobs?per_page=100&page=$page" 2> "$WORK/gh_err")"; then
      return 1
    fi
    if ! printf '%s' "$payload" | jq -e '(.jobs | type == "array")' > /dev/null 2>&1; then
      printf '返回体不是预期的 jobs 数组（解析失败）。\n' > "$WORK/gh_err"
      return 1
    fi
    printf '%s' "$payload" | jq -c '.jobs[]' >> "$acc"
    count="$(printf '%s' "$payload" | jq -r '.jobs | length')"
    acc_count=$((acc_count + count))
    total="$(printf '%s' "$payload" | jq -r 'if (.total_count | type) == "number" then .total_count else empty end')"
    # 合并页后判断完整性：有 total_count 就累计到总量；没有才按「不足一页」收尾。
    if [ -n "$total" ] && [ "$acc_count" -ge "$total" ]; then
      break
    fi
    [ "$count" -eq 0 ] && break
    if [ -z "$total" ] && [ "$count" -lt 100 ]; then
      break
    fi
    page=$((page + 1))
    if [ "$page" -gt 50 ]; then
      printf '分页超过上限（>50 页）。\n' > "$WORK/gh_err"
      return 1
    fi
  done
  jq -s '{jobs: .}' "$acc"
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

# ── 重跑前的证据快照（固定 attempt A）─────────────────────────────────────
# 注：收集函数把结果写文件而不是用 `$(...)` 取——命令替换是子壳，函数内赋值传不回来。

snapshot_failed_jobs() { # <attempt> → $WORK/jobs_tsv（三要素①）、$WORK/failed_jobs_md
  local attempt="$1" payload
  if [ "$DRY_RUN" = "1" ]; then
    payload="$(cat "${DRY_RUN_JOBS_JSON:?DRY_RUN_JOBS_JSON required}")"
  elif ! payload="$(fetch_attempt_jobs "$attempt")"; then
    printf '首次失败 job 快照取数失败：%s\n' "$(first_line "$WORK/gh_err")" > "$WORK/unknown_reason"
    return 1
  fi
  # 结构异常（jobs 非数组 / 失败 job 缺合法 id、name）不入选择程序：直接 unknown。
  if ! printf '%s' "$payload" | jq -e '
        (.jobs | type == "array")
        and ([.jobs[] | select(.conclusion == "failure")]
             | all((.id | type == "number") and (.name | type == "string") and (.name | length > 0)))
      ' > /dev/null 2>&1; then
    printf '首次失败 job 快照结构异常（jobs 非数组，或失败 job 缺合法数字 id/非空 name）。\n' > "$WORK/unknown_reason"
    return 1
  fi
  printf '%s' "$payload" | jq -r '
    .jobs[] | select(.conclusion == "failure")
    | [.id, .name, ([.steps[]? | select(.conclusion == "failure") | .name] | join(", "))] | @tsv' > "$WORK/jobs_tsv"
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

# 按失败 job 集合选预算（#3573）：命中 SLOW_JOBS 任一即用 WAIT_MAX_SLOW。
# 输入是 snapshot 落盘的 jobs_tsv（`id<TAB>name<TAB>steps`），故重跑**之前**即可定，
# 不与「证据必须先于重跑」的硬约束冲突。
pick_wait_max() { # → 打印选中的 WAIT_MAX
  local name slow
  while IFS=$'\t' read -r _ name _; do
    [ -n "${name:-}" ] || continue
    IFS=',' read -r -a _slows <<< "$SLOW_JOBS"
    for slow in "${_slows[@]}"; do
      [ -z "$slow" ] && continue
      if [ "$name" = "$slow" ]; then
        printf '%s\n' "$WAIT_MAX_SLOW"
        return 0
      fi
    done
  done < "$WORK/jobs_tsv"
  printf '%s\n' "$WAIT_MAX"
}

target_names_json() { # 目标集合的身份=稳定 job name（重复由 main 在快照后拦截）
  cut -f2 "$WORK/jobs_tsv" | jq -Rsc 'split("\n") | map(select(length > 0))'
}

# ── 落定判定（固定新 attempt，逐轮聚合）─────────────────────────────────────

# poll_once <target_attempt> → 打印 pending|success|failure|mixed；
# 致命（并发重跑 / 取数解析异常 / 重复身份 / 无法归因结论）非零返回且原因写 $WORK/unknown_reason。
poll_once() {
  local target_attempt="$1" current payload line kind reason
  if ! current="$(run_attempt 2> "$WORK/gh_err")"; then
    printf '轮询读取 run attempt 失败：%s\n' "$(first_line "$WORK/gh_err")" > "$WORK/unknown_reason"
    return 1
  fi
  case "$current" in
    *[!0-9]* | '')
      printf '轮询读到非法 run attempt（%s）。\n' "$current" > "$WORK/unknown_reason"
      return 1
      ;;
  esac
  if [ "$current" -gt "$target_attempt" ]; then
    printf '检测到并发重跑（run attempt=%s > 固定目标 %s）——证据不确定，不追随最新 attempt。\n' \
      "$current" "$target_attempt" > "$WORK/unknown_reason"
    return 1
  fi
  if [ "$current" -lt "$target_attempt" ]; then
    printf 'pending\n' # 新 attempt 尚未出现：未定，预算内继续等
    return 0
  fi
  if ! payload="$(fetch_attempt_jobs "$target_attempt")"; then
    if grep -q '404' "$WORK/gh_err" 2>/dev/null; then
      printf 'pending\n' # 新 attempt 暂时 404：未定，预算内继续等
      return 0
    fi
    printf '轮询新 attempt（%s）job 列表失败：%s\n' "$target_attempt" "$(first_line "$WORK/gh_err")" > "$WORK/unknown_reason"
    return 1
  fi
  if ! line="$(printf '%s' "$payload" | jq -r --argjson targets "$(target_names_json)" --argjson att "$target_attempt" '
        .jobs as $jobs
        | ($targets | map(. as $t | [$jobs[] | select(.name == $t)])) as $tg
        | if ($tg | any(length > 1)) then "unknown\t新 attempt 中目标 job 名不唯一（疑似 matrix/显示名冲突）。"
          elif ($tg | any(length == 0)) then "pending"
          elif ($tg | any(.[][]; ((.run_attempt | type) != "number") or (.run_attempt != $att))) then "unknown\t新 attempt 返回的目标 job run_attempt 与固定 attempt 不符。"
          elif ($tg | any(.[][]; (.status // "") != "completed" or (.conclusion | type) != "string" or .conclusion == null)) then "pending"
          else ($tg | map(.[0].conclusion)) as $cs
            | if ($cs | all(. == "success")) then "success"
              elif ($cs | all(. == "failure" or . == "timed_out" or . == "startup_failure")) then "failure"
              elif ($cs | all(. == "success" or . == "failure" or . == "timed_out" or . == "startup_failure")) then "mixed"
              else "unknown\t目标 job 已全部 completed 但结论无法归因（"
                + ($cs | map(select(. != "success" and . != "failure" and . != "timed_out" and . != "startup_failure")) | join(", ")) + "）。"
              end
        end')"; then
    printf '新 attempt job 聚合解析失败。\n' > "$WORK/unknown_reason"
    return 1
  fi
  IFS=$'\t' read -r kind reason <<< "$line"
  case "$kind" in
    pending | success | failure | mixed)
      printf '%s\n' "$kind"
      return 0
      ;;
    unknown)
      printf '%s\n' "$reason" > "$WORK/unknown_reason"
      return 1
      ;;
    *)
      printf '聚合输出异常：%s\n' "$line" > "$WORK/unknown_reason"
      return 1
      ;;
  esac
}

wait_conclusion() { # <target_attempt> <budget> → 落定结论；非零 = 未能分类
  local target_attempt="$1" budget="$2" line
  for _ in $(seq 1 "$budget"); do
    # dry-run 不触网、不真实等待：夹具给什么取什么，给不出即视为未落定。
    if [ "$DRY_RUN" = "1" ]; then
      line="${DRY_RUN_RERUN_CONCLUSION:-}"
      case "$line" in
        success | failure | mixed)
          printf '%s\n' "$line"
          return 0
          ;;
      esac
      return 1
    fi
    if line="$(poll_once "$target_attempt")"; then
      case "$line" in
        success | failure | mixed)
          printf '%s\n' "$line"
          return 0
          ;;
      esac
      [ "$_" -lt "$budget" ] && sleep "$WAIT_INTERVAL"
    else
      return 1
    fi
  done
  return 1
}

# ── 输出（对外契约不变：classification / rerun_conclusion / wait_budget 与两个 md）──

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

write_outputs() { # <classification> <rerun_conclusion> <note>
  local classification="$1" rerun_conclusion="$2" note="$3"
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
  output wait_budget "$((STP_WAIT_MAX * WAIT_INTERVAL))"
  output_multi failed_jobs_md "$WORK/failed_jobs_md"
  output_multi attribution_md "$WORK/attribution_md"

  printf 'backstop_attribution attempt=%s classification=%s rerun_conclusion=%s wait_budget=%ss\n' \
    "$STP_ATTEMPT" "$classification" "${rerun_conclusion:-none}" "$((STP_WAIT_MAX * WAIT_INTERVAL))"
  cat "$WORK/attribution_md"
}

main() {
  local attempt confirm wait_max line dup_list

  : > "$WORK/unknown_reason"

  attempt="$(run_attempt 2> "$WORK/run_err")" || {
    write_outputs "unknown" "" "读取 run attempt 失败：$(first_line "$WORK/run_err")"
    return 0
  }
  case "$attempt" in *[!0-9]* | '') attempt=1 ;; esac
  STP_ATTEMPT="$attempt"

  # 证据先落盘再动重跑（否则重跑会重置 job 结论、同名 job 换新 id、旧日志端点指向新尝试）。
  if ! snapshot_failed_jobs "$attempt"; then
    write_outputs "unknown" "" "$(first_line "$WORK/unknown_reason")"
    return 0
  fi
  collect_cases
  # 预算同样在重跑前定：只取决于「哪些 job 红了」，与重跑结果无关。
  wait_max="$(pick_wait_max)"
  STP_WAIT_MAX="$wait_max"

  if [ ! -s "$WORK/jobs_tsv" ]; then
    write_outputs "unknown" "" "首次快照中没有 conclusion=failure 的目标 job——无法确定重跑目标（首次结论：${FIRST_CONCLUSION}）。"
    return 0
  fi
  dup_list="$(cut -f2 "$WORK/jobs_tsv" | sort | uniq -d | tr '\n' ' ' | sed -e 's/ $//')"
  if [ -n "$dup_list" ]; then
    write_outputs "unknown" "" "首次快照中失败 job 名不唯一（重复：${dup_list}）——无法按稳定 name 定位目标，疑似 matrix/显示名冲突。"
    return 0
  fi

  if [ "$attempt" -gt 1 ]; then
    # 该 run 已被重跑过（本 workflow 重入或人工重跑）：观测到的红灯就是重跑后的结论，
    # 直接判为确定性缺陷，且**不再重跑**——「自动 rerun 一次」的「一次」在此闭合。
    write_outputs "deterministic" "$FIRST_CONCLUSION" "该 run 已是第 ${attempt} 次尝试（此前已重跑），不再自动重跑。"
    return 0
  fi

  # 重跑前再次确认 attempt 未变化：变化即并发重跑，快照与新 attempt 的对应关系不可靠。
  confirm="$(run_attempt 2> "$WORK/run_err")" || {
    write_outputs "unknown" "" "重跑前读取 run attempt 失败：$(first_line "$WORK/run_err")"
    return 0
  }
  if [ "$confirm" != "$attempt" ]; then
    write_outputs "unknown" "" "重跑前检测到 run attempt 由 ${attempt} 变为 ${confirm}——并发重跑导致证据不确定，不做归因。"
    return 0
  fi

  if ! rerun_failed_jobs 2> "$WORK/rerun_err"; then
    write_outputs "unknown" "" "重跑请求失败：$(first_line "$WORK/rerun_err")"
    return 0
  fi

  # 轮询固定 A+1（不读 latest、不回落旧 attempt、不追加 rerun）。
  if line="$(wait_conclusion "$((attempt + 1))" "$wait_max")"; then
    case "$line" in
      success)
        write_outputs "flake" "success" ""
        ;;
      failure)
        write_outputs "deterministic" "failure" ""
        ;;
      mixed)
        # mixed＝目标 job 有转绿有仍红；仍红的那几个已构成确定性缺陷，按 deterministic
        # 处置，但要在备注里讲清，不能让读者误读成「全红」。
        write_outputs "deterministic" "mixed" "重跑后目标 job 结论不一致（部分转绿、部分仍红）——按确定性缺陷处置。"
        ;;
      *)
        write_outputs "unknown" "" "聚合输出异常：${line}"
        ;;
    esac
    return 0
  fi

  if [ -s "$WORK/unknown_reason" ]; then
    # 致命 unknown（并发重跑 / 取数解析异常 / 重复身份 / 无法归因结论）：真实原因优先。
    write_outputs "unknown" "" "$(first_line "$WORK/unknown_reason")"
  else
    write_outputs "unknown" "" "重跑未在预算内完成（${wait_max} × ${WAIT_INTERVAL}s = $((wait_max * WAIT_INTERVAL))s），无法分类。"
  fi
  return 0
}

main "$@"
