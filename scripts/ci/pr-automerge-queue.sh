#!/usr/bin/env bash
# FIFO auto-merge queue: only the oldest eligible open PR may have auto-merge enabled.
set -euo pipefail

REPO="${GITHUB_REPOSITORY:?}"

is_excluded_ref() {
  local ref="$1"
  [[ "$ref" == dependabot/npm_and_yarn/frontend/frontend-major-* ]] && return 0
  [[ "$ref" == dependabot/github_actions/* ]] && return 0
  return 1
}

# updatePullRequestBranch 带 expectedHeadOid：并发 reconcile 中落败方会被拒
# （GraphQL: head sha didn't match the current head ref，见 run 33269782605）；
# 与 auto-merge 合入竞争时（查状态那一刻 PR 还 open，随即被合掉）会报
# Cannot update PR branch due to conflicts（run 33306014644）。两者都意味着本轮
# 目标已达成或已无意义，按无害处理，别把竞态刷成红 X。其余失败原样返回非零。
update_branch_tolerant() {
  local num="$1" out rc=0 state
  out="$(gh pr update-branch "$num" --repo "$REPO" 2>&1)" || rc=$?
  printf '%s\n' "$out"
  if [ "$rc" -eq 0 ]; then
    return 0
  fi
  if printf '%s' "$out" | grep -qi "didn't match the current head ref"; then
    echo "update-branch on #${num} lost the head-sha race; another run already updated it."
    return 0
  fi
  # 真冲突（#906 实测）：队首 PR 与 main 冲突时 update-branch 必失败，队列被它
  # 合法阻塞直到人工解冲突——这是可处置状态而非本 job 故障。原先只靠下面
  # 「PR 已不在 open 态」兜底，而冲突 PR 仍 open → 落到 return rc，把每轮
  # cron 与每个 PR 的 reconcile-queue 刷成红 X（reconcile-queue 不在 required
  # checks 内，不阻塞合入，但污染信号）。显式识别并绿退，附人工动作指引。
  if printf '%s' "$out" | grep -qiE "cannot update pr branch due to conflicts"; then
    echo "PR #${num} has merge conflicts with main; queue head blocked until resolved manually."
    return 0
  fi
  # workflow scope 缺口（#1783，run 34689776040 实测）：队首 PR 改过
  # .github/workflows/* 时，gh pr update-branch 被 GitHub 拒绝——
  # "refusing to allow a Personal Access Token to create or update workflow
  #  `.github/workflows/x.yml` without `workflow` scope"。这是**凭据配置**的
  # 可处置状态（AUTO_MERGE_PAT 缺 workflow scope），不是本 job 故障：原先落到
  # return rc，整 job 红 + 队首 rebase 停摆（实测 28 个 PR 全部 behind）。
  # 绿退并给人工动作指引；根因（补 PAT scope = 允许改 CI 定义）属安全面扩张，
  # 由 owner 单列决策（issue #1783 路径 B），本分支只做无害化。
  if printf '%s' "$out" | grep -qiE "without .?workflow.? scope"; then
    echo "PR #${num} touches .github/workflows/* and the queue token lacks 'workflow' scope;"
    echo "  GitHub refused update-branch. Rebase it manually with a workflow-scoped"
    echo "  credential (or merge main into the PR branch) to unblock the queue."
    return 0
  fi
  # 按 PR 状态判定而非再堆一条报错文案匹配：合入与 update-branch 的竞态
  # 不只有一种报错形态，而「PR 已不在 open 态」是唯一稳定的判据。
  state="$(gh pr view "$num" --repo "$REPO" --json state --jq .state 2>/dev/null || echo UNKNOWN)"
  if [ "$state" = "MERGED" ] || [ "$state" = "CLOSED" ]; then
    echo "PR #${num} is already ${state}; update-branch no longer applies."
    return 0
  fi
  return "$rc"
}

# PR 含 .github/workflows/ 变更时，pull_request 触发的 run 会停在 action_required；
# reconcile 与 pull_request_target workflow 代为批准（见 approve-pending-workflow-runs.sh）。
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
approve_pending_workflow_runs() {
  bash "${SCRIPT_DIR}/approve-pending-workflow-runs.sh" "$1"
}

# ── #1246：队首停摆告警（ci/queue-blocked，去重 + 恢复自动关闭）──────────────
# 告警只承载可见性，不参与合入决策：任何 issue 操作失败都不得中断或染红
# reconcile（队列行为与告警成败解耦）。issue 操作用独立 token（workflow 注入
# 的 ALERT_TOKEN=GITHUB_TOKEN）——AUTO_MERGE_PAT 是 fine-grained PAT，若未含
# Issues 权限，用它发告警会静默失效。
QUEUE_BLOCKED_LABEL="ci/queue-blocked"

issue_gh() {
  if [ -n "${ALERT_TOKEN:-}" ]; then
    GH_TOKEN="$ALERT_TOKEN" gh "$@"
  else
    gh "$@"
  fi
}

find_open_queue_blocked_issue() {
  # #384：REST issues 端点混返 PR——过滤带 pull_request 的条目，防误评论/误关
  issue_gh api "repos/${REPO}/issues?labels=ci%2Fqueue-blocked&state=open&per_page=20" \
    --jq '[.[] | select(.pull_request == null)] | first | .number // empty' 2>/dev/null || true
}

# 队首停摆：开/更新去重 issue。指纹（队首+失败集）未变则零写入，避免每小时刷屏。
alert_queue_blocked() {
  local head="$1" ref="$2" failed="$3"
  local fingerprint existing existing_body body author pr_url
  fingerprint="head=#${head} failed=${failed}"
  pr_url="https://github.com/${REPO}/pull/${head}"
  author="$(gh pr view "$head" --repo "$REPO" --json author --jq .author.login 2>/dev/null || echo unknown)"

  # #1549：逐行用 `printf '%s\n'` 输出，变量在**参数**里由 shell 展开。
  # 此前的写法把整行（含 %s）当参数、只把 '%s\n' 当格式串，于是 %s 全部按
  # 字面量输出——正文变成 "...[#%s](%s)..."，连指纹行也成了字面
  # `<!-- queue-blocked-fingerprint: %s -->`，使下面第 113 行的 grep 永不命中，
  # 「同指纹零写入」的反刷屏去重彻底失效（当时每次 reconcile 都重写一遍 issue）。
  body="$(printf '%s\n' \
    "## FIFO 队首停摆（ci/queue-blocked 自动告警）" \
    "" \
    "- 队首 PR：[#${head}](${pr_url})（\`${ref}\`，作者 @${author}）" \
    "- 未通过 required check：${failed}" \
    "- 队列影响：其后所有 PR 无法合入；分支更新已跳过（不对红队首自动重基）" \
    "- 判读工具：\`python -m tools.dev.queue_head_telemetry\`" \
    "- 处置（人工，择一）：修复该 check / 解冲突 / 让位（关闭或改 draft）；不要手动 Merge" \
    "" \
    "<!-- queue-blocked-fingerprint: ${fingerprint} -->")"

  if ! issue_gh label create "$QUEUE_BLOCKED_LABEL" --repo "$REPO" --color d73a4a \
      --description "FIFO 队首停摆自动告警（#1246）" >/dev/null 2>&1; then
    : # label 已存在属常态
  fi

  existing="$(find_open_queue_blocked_issue)"
  if [ -z "$existing" ]; then
    if issue_gh issue create --repo "$REPO" --title "FIFO 队首停摆：PR #${head} 的 required check 未通过" \
        --label "$QUEUE_BLOCKED_LABEL" --body "$body" >/dev/null 2>&1; then
      echo "Opened ci/queue-blocked alert for head #${head}."
    else
      echo "queue-blocked alert could not be created (issue 权限/网络)；继续，不中断 reconcile。" >&2
    fi
    return 0
  fi

  existing_body="$(issue_gh issue view "$existing" --repo "$REPO" --json body --jq .body 2>/dev/null || true)"
  if printf '%s' "$existing_body" | grep -qF "queue-blocked-fingerprint: ${fingerprint}"; then
    echo "ci/queue-blocked alert #${existing} unchanged (fingerprint match)."
    return 0
  fi
  if issue_gh issue edit "$existing" --repo "$REPO" --body "$body" >/dev/null 2>&1; then
    echo "Updated ci/queue-blocked alert #${existing} (head/失败集变化)."
  fi
  return 0
}

# 恢复：队首通过 required checks（或队列空）时关闭存量告警 issue。
resolve_queue_blocked() {
  local existing
  existing="$(find_open_queue_blocked_issue)"
  [ -n "$existing" ] || return 0
  issue_gh issue comment "$existing" --repo "$REPO" \
    --body "队列已恢复：队首不再被 required check 阻塞。自动关闭。" >/dev/null 2>&1 || true
  if issue_gh issue close "$existing" --repo "$REPO" >/dev/null 2>&1; then
    echo "Closed ci/queue-blocked alert #${existing} (recovered)."
  fi
  return 0
}

owner="${REPO%/*}"
name="${REPO#*/}"

mapfile -t eligible < <(
  gh pr list --repo "$REPO" --state open --limit 100 \
    --json number,url,headRefName,createdAt,isDraft,isCrossRepository,autoMergeRequest \
    --jq '
      sort_by(.createdAt)
      | .[]
      | select(.isDraft == false)
      | select(.isCrossRepository == false)
      | @json
    '
)

head_number=""
filtered=()
for row in "${eligible[@]}"; do
  ref="$(jq -r '.headRefName' <<<"$row")"
  if is_excluded_ref "$ref"; then
    continue
  fi
  filtered+=("$row")
  if [ -z "$head_number" ]; then
    head_number="$(jq -r '.number' <<<"$row")"
  fi
done

if [ -z "$head_number" ]; then
  echo "No eligible PRs in auto-merge queue."
  # #1246：队列空 = 无停摆；关闭存量告警（失败容忍）
  resolve_queue_blocked
  exit 0
fi

echo "Queue head: PR #${head_number}"

head_ref=""
for row in "${filtered[@]}"; do
  if [ "$(jq -r '.number' <<<"$row")" = "$head_number" ]; then
    head_ref="$(jq -r '.headRefName' <<<"$row")"
    break
  fi
done
if [ -n "$head_ref" ]; then
  for row in "${filtered[@]}"; do
    approve_pending_workflow_runs "$(jq -r '.headRefName' <<<"$row")"
  done
fi

# mergeMethod 预检只为日志区分「已启用/新启用」（c5d8d21e 起），enable 调用
# 本身幂等（对已启用 PR 再次 enablePullRequestAutoMerge 同样成功）。
# GraphQL 偶发 5xx（run 33508879071：E05B "Something went wrong while
# executing your query"）会经 set -e 把整个 reconcile 刷成红 X，而 reconcile
# 每小时 cron + 每个 PR 事件都会重跑、本轮跳过无累积损害——查询重试 3 次
# 仍败就放弃预检、直接走幂等 enable，让真实失败（权限/冲突）由 enable
# 调用自己暴露。
head_merge_method() {
  local num="$1" attempt out
  for attempt in 1 2 3; do
    out="$(
      gh api graphql \
        -f query='query($owner:String!,$repo:String!,$number:Int!){repository(owner:$owner,name:$repo){pullRequest(number:$number){autoMergeRequest{mergeMethod}}}}' \
        -F owner="$owner" -F repo="$name" -F number="$num" \
        --jq '.data.repository.pullRequest.autoMergeRequest.mergeMethod // ""'
    )" && { printf '%s' "$out"; return 0; }
    # #851：sleep 只落在重试之间——第 3 次（末次）失败后直接回退，不再空转 5s
    [ "$attempt" -eq 3 ] || sleep 5
  done
  echo "mergeMethod query failed after 3 attempts on #${num}; falling back to idempotent enable." >&2
  return 1
}

for row in "${filtered[@]}"; do
  num="$(jq -r '.number' <<<"$row")"
  url="$(jq -r '.url' <<<"$row")"
  has_auto="$(jq -r 'if .autoMergeRequest then "yes" else "no" end' <<<"$row")"

  if [ "$num" = "$head_number" ]; then
    method="$(head_merge_method "$num" || true)"
    if [ "$method" = "MERGE" ]; then
      echo "Auto-merge already enabled on #${num}"
    else
      gh pr merge "$url" --auto --merge
      echo "Enabled auto-merge on #${num}"
    fi
  elif [ "$has_auto" = "yes" ]; then
    gh pr merge "$url" --disable-auto || true
    echo "Disabled auto-merge on #${num} (waiting in queue)"
  fi
done

# 队首换档后常无新 CI → workflow_run 不会触发 pr-update-branch；reconcile 后
# 主动检查队首：已挂 auto-merge + required 全 SUCCESS + behind_by>0 → update。
REQUIRED=(
  lint
  CodeQL
  pr-typecheck
  pr-compileall
  pr-agent-tests
  pr-migrate-empty-db
)

if [ -z "$head_ref" ]; then
  echo "Queue head #${head_number} head ref not found; skip head update."
  exit 0
fi

head_json="$(
  gh pr view "$head_number" --repo "$REPO" \
    --json autoMergeRequest,statusCheckRollup,headRefName
)"
auto_method="$(jq -r '.autoMergeRequest.mergeMethod // ""' <<<"$head_json")"
if [ -z "$auto_method" ]; then
  echo "Queue head #${head_number} has no auto-merge; skip head update."
  exit 0
fi

# 分类 required checks：#1761 + #1792——「进行中」「失败」「中性」必须分开。
#
# #1761：原实现只读 .conclusion，检查进行中时该字段为空串 → 被渲染成 "missing" 并
# **触发告警**。后果：每个 PR 在正常 CI 期间都开一条 ci/queue-blocked（实测 83 分钟
# 20 条、历史 100 条中 99 条自动关闭），真实停摆被淹没、告警通道失效。
#
# #1792：`CodeQL` 是 GitHub 默认 setup 的**聚合 check**，其子分析
# （Analyze (actions)/(javascript-typescript)/(python)）未全部完成时，父 check 为
# `COMPLETED` + **`NEUTRAL`**。原判据「COMPLETED 且 != SUCCESS → failed」把它当成
# 失败，导致 #1764 合入后仍继续误报（08:20:58–08:41:12 六条）。
#
# 判据（与分支保护的实际裁决对齐——**不自作更严**）：
#   - conclusion == SUCCESS        → 通过
#   - conclusion == NEUTRAL        → 通过（#1792）。实证：strict=true 的分支保护要求
#     CodeQL，而 #1772 在 CodeQL=COMPLETED/NEUTRAL 下**被 GitHub 允许合入**，
#     即对方视 NEUTRAL 为满足态。若我们判它失败，就会出现「GitHub 认为可合入、
#     我们的 FIFO 却拒绝 update-branch」的队首伪停摆。
#   - status != COMPLETED          → pending：仅日志，不告警（机器在跑，人无需动作）
#   - 注册表里根本没有该 check     → missing：**仅在启动窗口之外**才告警（见下）
#   - status == COMPLETED 且 conclusion 为 FAILURE/CANCELLED/TIMED_OUT/… → failed：告警
#
# #1792（第三类）：`MISSING` 不能无条件告警。`CodeQL` 是**独立 workflow**，其 check 条目
# 晚于 CI job 注册进 rollup——实测 #1855 lint 启动 11:14:42、CodeQL 启动 11:15:15
# （+33s），而告警发生在 11:14:49（比 CodeQL 注册早 26s）。该窗口内条目**确实不存在**，
# 但那是**每次 PR 都会出现的正常启动时序**，不是「check 未注册/未上报」的异常。
#
# 判据：用 rollup **自身已有的时间戳**判断是否仍在启动窗口——无需跨轮持久化
# （reconcile 是无状态 job），也无需 check→workflow 映射的额外 API 调用。规则：
#   - 该 PR 尚无任何带 startedAt 的 check → 极早期，视为启动窗口，不告警；
#   - 最早 check 启动至今 < MISSING_GRACE_SECONDS → 启动窗口，不告警；
#   - 超过宽限仍缺条目 → 真 missing，告警（workflow 被禁用/改名/审批卡住等）。
MISSING_GRACE_SECONDS=600  # 10 分钟：远大于实测 20-38s 注册延迟，又不至于长期静默

failed_checks=""
pending_checks=""
missing_checks=""

# 启动窗口基准：本 PR rollup 中最早的 startedAt（无则视为极早期）
earliest_started="$(
  jq -r '[.statusCheckRollup[]? | .startedAt? // empty] | map(select(. != null)) | sort | first // ""' <<<"$head_json"
)"
in_startup_window="false"
if [ -z "$earliest_started" ]; then
  in_startup_window="true"
else
  started_epoch="$(date -u -d "$earliest_started" +%s 2>/dev/null || echo "")"
  now_epoch="$(date -u +%s)"
  if [ -n "$started_epoch" ] && [ "$((now_epoch - started_epoch))" -lt "$MISSING_GRACE_SECONDS" ]; then
    in_startup_window="true"
  fi
fi

for check in "${REQUIRED[@]}"; do
  # 同时取 status 与 conclusion：前者区分「在跑」与「已定论」。
  #
  # status 缺失时的兜底（`has("status")` 判定）：GitHub 的 statusCheckRollup 条目
  # 一般带 status，但**无 status 而有 conclusion** 意味着「已有定论」——按 COMPLETED
  # 处理，否则会把失败误判成 pending 而静默（把 #1761 的修复变成反向缺陷）。
  # 两者皆无 → 该 check 在注册表中不存在 → MISSING。
  read -r status conclusion <<<"$(
    jq -r --arg name "$check" '
      [.statusCheckRollup[]? | select(.name == $name)] | first
      | if . == null then "MISSING "
        elif has("status") then "\(.status) \(.conclusion // "")"
        elif (.conclusion // "") != "" then "COMPLETED \(.conclusion)"
        else "MISSING "
        end
    ' <<<"$head_json"
  )"

  # 通过态：SUCCESS 或 NEUTRAL（#1792，与分支保护裁决一致）
  if [ "$status" = "COMPLETED" ] && { [ "$conclusion" = "SUCCESS" ] || [ "$conclusion" = "NEUTRAL" ]; }; then
    continue
  fi

  # 注册表里没有该 check（无条目）——区分「启动窗口内尚未注册」与「真 missing」：
  # 前者是正常时序（不告警），后者需人工排查（告警）。
  if [ "$status" = "MISSING" ]; then
    if [ "$in_startup_window" = "true" ]; then
      missing_checks="${missing_checks:+${missing_checks}, }${check}:not-yet-registered"
      echo "Queue head #${head_number}: ${check} not yet registered (startup window); skip head update."
    else
      echo "Queue head #${head_number}: ${check} not reported (missing); skip head update."
      failed_checks="${failed_checks:+${failed_checks}, }${check}:missing"
    fi
    continue
  fi

  if [ "$status" != "COMPLETED" ]; then
    # 进行中/排队中（IN_PROGRESS/QUEUED/…）：机器所有，不告警（#1761）
    pending_checks="${pending_checks:+${pending_checks}, }${check}:${status}"
    echo "Queue head #${head_number}: ${check} still ${status}; skip head update."
    continue
  fi

  # status == COMPLETED 但非 SUCCESS（FAILURE/CANCELLED/TIMED_OUT/…）
  label="${conclusion:-failure}"
  echo "Queue head #${head_number}: ${check} not SUCCESS (${label}); skip head update."
  failed_checks="${failed_checks:+${failed_checks}, }${check}:${label}"
done
if [ -n "$failed_checks" ]; then
  # #1246：停摆不再只是日志一行——去重告警（可见性通道，失败不阻断 reconcile）
  alert_queue_blocked "$head_number" "$head_ref" "$failed_checks"
  exit 0
fi
if [ -n "$pending_checks" ] || [ -n "$missing_checks" ]; then
  # 仅有 pending（#1761）或启动窗口内尚未注册的 check（#1792 第三类）：不告警，
  # 也**不** resolve 存量告警——CI 还没跑完，此刻既不该新增噪音，也不该宣布
  # 「已恢复」（真实失败可能紧随其后）。下一轮 reconcile 会重新判定；一旦
  # pending 转 failed、或 missing 超出启动窗口，则走上面的告警分支。
  echo "Queue head #${head_number}: awaiting pending/unregistered checks: ${pending_checks}${pending_checks:+ }${missing_checks}"
  exit 0
fi

# 队首通过全部 required checks：恢复关闭存量告警
resolve_queue_blocked

behind="$(
  gh api "repos/${REPO}/compare/main...${head_ref}" --jq '.behind_by // 0'
)"
if [ "$behind" -eq 0 ]; then
  echo "Queue head #${head_number} is up to date with main."
  exit 0
fi

echo "Queue head #${head_number} is ${behind} commit(s) behind main; updating branch."
update_branch_tolerant "$head_number"
