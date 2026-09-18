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

# 启用队首 auto-merge 的**容错**版本（#2646）。
#
# `gh pr merge --auto` 在队首 PR 改过 `.github/workflows/*` 时会被 GitHub 以
# 「refusing to allow a Personal Access Token to create or update workflow … without
# `workflow` scope」拒绝。这与 #1783 在 `updatePullRequestBranch` 上修的是**同一根因、
# 不同调用点**——而 enable 这条路径当时没有同样的容错，于是同一缺陷复发。
#
# 危害**远大于**「自己红一步」：`set -e` 让整 job 失败 → 队首始终拿不到 auto-merge →
# 下游 `if [ -z "$auto_method" ]; then … skip head update` 因「队首没有 auto-merge」
# 而**跳过分支更新** ⇒ enable 失败把「停摆」固定下来，队列**自持停摆且不自愈**
# （实测 2026-09-17T19:23:25Z → 2026-09-18T01:44:04Z 整队列零合入 6h20m，23 个 open PR）。
#
# 处置与 #1783 同构：识别该类拒绝后**绿退**并给人工动作指引。根因（补 `workflow` scope =
# 允许该凭据改 CI 定义）属**安全面扩张**，由 owner 单列决策；本函数只做无害化。
enable_auto_tolerant() {
  local num="$1" url="$2" out rc=0 method
  out="$(gh pr merge "$url" --auto --merge 2>&1)" || rc=$?
  printf '%s\n' "$out"
  if [ "$rc" -eq 0 ]; then
    echo "Enabled auto-merge on #${num}"
    return 0
  fi
  if printf '%s' "$out" | grep -qiE "without .?workflow.? scope"; then
    echo "PR #${num} touches .github/workflows/* and the queue token lacks 'workflow' scope;"
    echo "  GitHub refused to enable auto-merge. Enable it manually with a workflow-scoped"
    echo "  credential (or ask an owner to do so) to unblock the queue."
    echo "  NOTE: queue head update is skipped while the head has no auto-merge (#2646);"
    echo "  until it is enabled, this queue cannot self-heal."
    # 退出码 2 = 「绿退但队列被凭据卡住」——调用方据此发**可区分**的停摆告警
    # （既有 `failed=` 指纹表达不了「绿队首被凭据卡住」）。
    return 2
  fi
  # 「已被并发启用」不是故障：另一轮 reconcile / 人工刚挂上，报错形态不稳定，
  # 故按**结果**判定（复读 autoMergeRequest）而非再堆一条文案匹配——与
  # update_branch_tolerant 末尾「按 PR 状态判定而非匹配报错文案」同一取向。
  method="$(head_merge_method "$num" || true)"
  if [ -n "$method" ]; then
    echo "Auto-merge already enabled on #${num} (concurrent enable); nothing to do."
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

# ── #2556：区分「checks 从未创建」与「跑了但红」──────────────────────────────
# 判据读取走 ALERT_TOKEN（workflow 注入的 GITHUB_TOKEN，job 已授 actions: write）
# 而非默认 GH_TOKEN：AUTO_MERGE_PAT 的 scope 集不受本仓控制（#1783 已证它缺
# workflow scope），若它没有 Actions 读权限，判据会静默退化成「每轮交给人工」——
# 自愈等于没做。同 #1246「告警不依赖 PAT 是否含 Issues 权限」的取舍。
probe_gh() {
  if [ -n "${ALERT_TOKEN:-}" ]; then
    GH_TOKEN="$ALERT_TOKEN" gh "$@"
  else
    gh "$@"
  fi
}

# `ci.yml` 在某个 head sha 上的 run 数：`total_count == 0` 才是「GitHub 侧触发
# 丢失」（本仓无法预防、也没有可修的 check）；>0 则说明触发正常，缺条目是别的
# 成因（workflow 被禁用/改名/卡审批）。探测失败返回非零——调用方按「未知」处理，
# 不猜。
ci_run_total_for_sha() {
  local sha="$1" out
  out="$(probe_gh api "repos/${REPO}/actions/workflows/ci.yml/runs?head_sha=${sha}&per_page=1" \
    --jq '.total_count // 0' 2>/dev/null)" || return 1
  # 只接受纯数字：探测输出畸形时同样按「未知」处理，别把脏值喂给数值比较。
  case "$out" in '' | *[!0-9]*) return 1 ;; esac
  printf '%s' "$out"
}

# 冷却：reconcile 是无状态 job，「同一 (队首, head_sha) 至多自愈一次」的事实只能
# 落在跨轮持久的面上——复用 `ci/queue-blocked` 告警正文里的隐藏标记（与去重指纹
# 同处，不新增状态存储）。带行尾终止符匹配，理由同 #2075 的指纹比较。
self_heal_already_attempted() {
  local head="$1" sha="$2" existing body
  [ -n "$sha" ] || return 1
  existing="$(find_open_queue_blocked_issue)"
  [ -n "$existing" ] || return 1
  body="$(issue_gh issue view "$existing" --repo "$REPO" --json body --jq .body 2>/dev/null || true)"
  printf '%s' "$body" | grep -qF "queue-selfheal: head=#${head} sha=${sha} -->"
}

# 队首停摆：开/更新去重 issue。指纹（队首 + 失败集 + 自愈状态）未变则零写入，
# 避免每小时刷屏。
#
# #2556：`impact` 由调用方给出——停摆成因不同，给人看的「为什么没更新分支」必须
# 如实区分（判为红 / 已自续重基 / 冷却已用尽 / 无 base-change 可做）。缺省即原先
# 那句「不对红队首自动重基」。`selfheal_state` 与 `head_sha` 非空时，正文追加隐藏
# 标记，作为下次 reconcile 判定冷却的依据（见 self_heal_already_attempted）。
alert_queue_blocked() {
  local head="$1" ref="$2" failed="$3" impact="${4:-}" selfheal_state="${5:-}" head_sha="${6:-}"
  local reason_code="${7:-}"
  local fingerprint existing existing_body body author pr_url
  local -a lines=()
  fingerprint="head=#${head} failed=${failed}"
  # #2646：`reason_code` 进指纹——否则「绿队首被凭据卡住」（failed 为空）与
  # 「无失败项的普通停摆」会共用同一指纹，正文与去重标记互相覆盖，
  # 运维无法稳定区分两种处置（前者需补 workflow scope，后者需排查 CI）。
  if [ -n "$reason_code" ]; then
    fingerprint="${fingerprint} reason=${reason_code}"
  fi
  if [ -n "$selfheal_state" ]; then
    # sha 进指纹：换了 head（自愈后的新 sha）必须刷新正文与标记，否则冷却记录
    # 会停在旧 sha 上，新 sha 的第二次判定误判为「没试过」。
    fingerprint="${fingerprint} selfheal=${selfheal_state}:${head_sha:0:12}"
  fi
  pr_url="https://github.com/${REPO}/pull/${head}"
  author="$(gh pr view "$head" --repo "$REPO" --json author --jq .author.login 2>/dev/null || echo unknown)"
  if [ -z "$impact" ]; then
    impact="- 队列影响：其后所有 PR 无法合入；分支更新已跳过（不对红队首自动重基）"
  fi

  # #1549：逐行用 `printf '%s\n'` 输出，变量在**参数**里由 shell 展开。
  # 此前的写法把整行（含 %s）当参数、只把 '%s\n' 当格式串，于是 %s 全部按
  # 字面量输出——正文变成 "...[#%s](%s)..."，连指纹行也成了字面
  # `<!-- queue-blocked-fingerprint: %s -->`，使下面比较指纹的那条 grep 永不命中，
  # 「同指纹零写入」的反刷屏去重彻底失效（当时每次 reconcile 都重写一遍 issue）。
  lines=(
    "## FIFO 队首停摆（ci/queue-blocked 自动告警）"
    ""
    "- 队首 PR：[#${head}](${pr_url})（\`${ref}\`，作者 @${author}）"
    "- 未通过 required check：${failed:-（无——见下方原因码）}"
    "${impact}"
    "- 判读工具：\`python -m tools.dev.queue_head_telemetry\`"
    "- 处置（人工，择一）：修复该 check / 解冲突 / 让位（关闭或改 draft）；不要手动 Merge"
    ""
  )
  if [ -n "$reason_code" ]; then
    # #2646：原因码单列并给出**可执行**动作——「未通过 required check」为空时，
    # 读者必须能立即知道该做什么，而不是去猜「没失败项为何不合入」。
    lines+=("- **原因码**：\`${reason_code}\`")
    if [ "$reason_code" = "credential-scope" ]; then
      lines+=("  - 队首 required checks **全绿**，但队列凭据缺 \`workflow\` scope，"
              "GitHub 拒绝启用 auto-merge（该 PR 改过 \`.github/workflows/*\`）")
      lines+=("  - **人工动作**：用具备 \`workflow\` scope 的凭据为该 PR 启用 auto-merge，"
              "或让该 PR 让位（关闭/改 draft）")
      lines+=("  - **注意**：在队首拿到 auto-merge 之前，本脚本会跳过 head update ⇒ "
              "**队列不会自愈**，必须人工介入（#2646）")
    fi
    if [ "$reason_code" = "conflicting" ] || [[ "$reason_code" == conflicting-* ]]; then
      lines+=("  - 队首 required checks **全绿**，但与 \`main\` **冲突**（\`mergeable=CONFLICTING\`）"
              "⇒ 无法合入，其后所有 PR 停摆")
      lines+=("  - **人工动作**：把 \`main\` 合入该 PR 分支并解冲突（或让该 PR 让位：关闭/改 draft）")
      lines+=("  - **注意**：冲突队首**不走自动重基**（重基必失败）⇒ **队列不会自愈**（#2624）")
    fi
  fi
  if [ -n "$selfheal_state" ] && [ -n "$head_sha" ]; then
    lines+=("<!-- queue-selfheal: head=#${head} sha=${head_sha} -->")
  fi
  lines+=("<!-- queue-blocked-fingerprint: ${fingerprint} -->")
  body="$(printf '%s\n' "${lines[@]}")"

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
  # #2075：比较必须带行尾终止符 ` -->`。grep -qF 是**子串**匹配，而指纹是
  # `head=#N failed=<按 REQUIRED 顺序逗号连接>`——失败集**收敛**到旧值前缀
  # （前几项仍红、后面某项转绿）时，计算值正好是存量值的子串，会被误判为
  # unchanged 而**不刷新正文**，正文继续列着已经通过的 check。
  # 不加终止符时实测：存量 `failed=lint:FAILURE, pr-agent-tests:FAILURE` 对计算值
  # `failed=lint:FAILURE` 命中（漏刷新）。
  # 也不用 grep -qxF：正文行含 `<!-- ` 前缀，整行比较得把前缀一并写进匹配串，更脆。
  if printf '%s' "$existing_body" | grep -qF "queue-blocked-fingerprint: ${fingerprint} -->"; then
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

# #2646：队首因「凭据缺 workflow scope」而挂不上 auto-merge 时置位——循环后据此告警。
# 该状态**无法用既有 `failed=` 指纹表达**（队首 required checks 全绿、无失败项），
# 若不单列，运维只会看到「一切正常却没合入」。
head_enable_blocked=""
for row in "${filtered[@]}"; do
  num="$(jq -r '.number' <<<"$row")"
  url="$(jq -r '.url' <<<"$row")"
  has_auto="$(jq -r 'if .autoMergeRequest then "yes" else "no" end' <<<"$row")"

  if [ "$num" = "$head_number" ]; then
    method="$(head_merge_method "$num" || true)"
    if [ "$method" = "MERGE" ]; then
      echo "Auto-merge already enabled on #${num}"
    else
      # 区分三态：0=已启用/无碍；2=凭据受阻（绿退但队列被卡，需可区分告警）；
      # 其它=真故障，必须上抛。**不能**写 `|| head_enable_blocked=...`——那会把
      # 非 2 的失败一并吞掉（`||` 右侧赋值成功即整行成功），使真故障伪装成绿。
      enable_rc=0
      enable_auto_tolerant "$num" "$url" || enable_rc=$?
      if [ "$enable_rc" -eq 2 ]; then
        head_enable_blocked="$num"
      elif [ "$enable_rc" -ne 0 ]; then
        exit "$enable_rc"
      fi
    fi
  elif [ "$has_auto" = "yes" ]; then
    gh pr merge "$url" --disable-auto || true
    echo "Disabled auto-merge on #${num} (waiting in queue)"
  fi
done

# #2646：队首挂不上 auto-merge 且原因是**凭据缺 workflow scope** → 发可区分的停摆告警。
#
# 为何必须单列：既有指纹是 `head=#N failed=<失败项>`，而本状态的队首 required checks
# **全绿**、`failed` 为空 ⇒ 告警面表达不出「绿队首被凭据卡住」。实测该状态曾让整个
# FIFO 队列**零合入 6h20m**（23 个 open PR），且因下游「队首无 auto-merge 就跳过 head
# update」而**不自愈**——没有可区分告警，运维只能靠肉眼发现「一切正常却没合入」。
if [ -n "$head_enable_blocked" ]; then
  alert_queue_blocked "$head_enable_blocked" "$head_ref" "" \
    "- 队列影响：队首 required checks **全绿**但**无 auto-merge**，故本轮不会合入；且脚本在「队首无 auto-merge」时跳过 head update → **队列不自愈**（#2646）" \
    "" "" \
    "credential-scope"
  exit 0
fi

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
    --json autoMergeRequest,statusCheckRollup,headRefName,headRefOid,mergeable,mergeStateStatus
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
#   - 注册表里根本没有该 check     → missing：**仅在启动窗口之外**才处理（见下）；
#     而「超出窗口的 missing」还要再分两类（#2556，判据在循环之后）：
#       · ci.yml 在该 head sha 上 run 数 == 0 → **从未创建**：一次带冷却的自愈重基；
#       · run 数 > 0                          → 跑了但此 check 没上报 → 人工。
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
unreported_checks=""

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

  # 注册表里没有该 check（无条目）——区分「启动窗口内尚未注册」与「超出窗口」：
  # 前者是正常时序（不告警），后者**先不下结论**（#2556）——「从未创建」与
  # 「跑了没上报」处置相反，循环后用 ci.yml 的 run 数分辨。
  if [ "$status" = "MISSING" ]; then
    if [ "$in_startup_window" = "true" ]; then
      missing_checks="${missing_checks:+${missing_checks}, }${check}:not-yet-registered"
      echo "Queue head #${head_number}: ${check} not yet registered (startup window); skip head update."
    else
      echo "Queue head #${head_number}: ${check} not reported (missing); classified after the loop."
      unreported_checks="${unreported_checks:+${unreported_checks}, }${check}:missing"
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
  # 有 check 在跑时也不做自愈重基（#2556）：那会打断在途 run，等它落定。
  echo "Queue head #${head_number}: awaiting pending/unregistered checks: ${pending_checks}${pending_checks:+ }${missing_checks}"
  exit 0
fi

# ── #2556：required check「没上报」的两种相反成因 ──────────────────────────
# 原先 `missing` 与 FAILURE 同归「红队首」→ 一律不重基。但「GitHub 压根没在该 sha
# 上创建 run」（触发丢失，不可预防，也没有可修的 check）需要的动作与「跑了且红」
# 相反：前者的唯一自愈动作是**一次 base-change push**——新 head sha 会重新触发
# `pull_request`；后者重基只会反复烧队列。判据用 run 数（`ci.yml` 的
# `?head_sha=`，本仓可查、不需新权限），不是 rollup 里有 FAILURE/CANCELLED/STALE。
#
# 自愈有界：同一 (队首 PR, head_sha) 至多一次（标记落在告警正文，见
# self_heal_already_attempted）。同一 sha 第二次仍无 run 就如实交回人工——不做
# 「missing ↔ 重基」循环。换个 head sha（自愈后的新提交）则是一次全新判定。
if [ -n "$unreported_checks" ]; then
  head_sha="$(jq -r '.headRefOid // ""' <<<"$head_json")"
  ci_runs=""
  if [ -n "$head_sha" ]; then
    ci_runs="$(ci_run_total_for_sha "$head_sha" || true)"
  fi
  if [ -z "$head_sha" ] || [ -z "$ci_runs" ]; then
    echo "Queue head #${head_number}: ci.yml run count unavailable (sha='${head_sha}'); no self-heal."
    alert_queue_blocked "$head_number" "$head_ref" "$unreported_checks" \
      "- 队列影响：其后所有 PR 无法合入；未能探明 ci.yml 是否创建过 run（探测失败/无 head sha），未自动重基 → 需人工核实"
    exit 0
  fi
  if [ "$ci_runs" != "0" ]; then
    echo "Queue head #${head_number}: ${ci_runs} ci.yml run(s) on ${head_sha} but a required check never reported; no self-heal."
    alert_queue_blocked "$head_number" "$head_ref" "$unreported_checks" \
      "- 队列影响：其后所有 PR 无法合入；该 sha 上 CI 已触发过，但此 check 从未上报（疑 workflow 被禁用/改名/卡审批）→ 需人工排查，不重基"
    exit 0
  fi
  if self_heal_already_attempted "$head_number" "$head_sha"; then
    echo "Queue head #${head_number}: ci.yml still has no run on ${head_sha} after one self-heal; escalating."
    alert_queue_blocked "$head_number" "$head_ref" "$unreported_checks" \
      "- 队列影响：其后所有 PR 无法合入；已试过自续重基一次仍无 run → 需人工（GitHub 侧触发未恢复，重基已无法自愈）" \
      "exhausted" "$head_sha"
    exit 0
  fi
  behind="$(gh api "repos/${REPO}/compare/main...${head_ref}" --jq '.behind_by // 0')"
  if [ "$behind" -eq 0 ]; then
    echo "Queue head #${head_number}: no ci.yml run on ${head_sha} but not behind main; no base-change to push."
    alert_queue_blocked "$head_number" "$head_ref" "$unreported_checks" \
      "- 队列影响：其后所有 PR 无法合入；队首未落后 main，无 base-change 可做 → 需人工（补空提交或关闭重开以重触发 CI）"
    exit 0
  fi
  echo "Queue head #${head_number}: ci.yml has no run on ${head_sha}; self-healing with one update-branch."
  update_branch_tolerant "$head_number"
  alert_queue_blocked "$head_number" "$head_ref" "$unreported_checks" \
    "- 队列影响：其后所有 PR 无法合入；已自续重基一次（base-change push 重新触发 CI），等待新 head 的 run" \
    "attempted" "$head_sha"
  exit 0
fi

# ── #2624：**绿但冲突**的队首是告警盲区 ────────────────────────────────────
#
# 上面六个 `alert_queue_blocked` 调用点全部以 `$failed_checks` / `$unreported_checks`
# 为条件 ⇒ **checks 一条不红**的队首从不告警。而 CONFLICTING 的队首正是这种形态：
# 它不红、也**不会**走自愈（`pr-update-branch` 的纪律是「不对红队首自动重基」，
# 而它不红；冲突本身也让重基失败）⇒ 队列整条停摆却**零告警**，只能靠人恰好去看队列
# （实测 #2584 卡住 43 个提交，`ci/queue-blocked` 为空）。
#
# 判据直接取 **GitHub 事实**（`mergeable`/`mergeStateStatus`），与 checks 判定同源同层；
# **刻意不消费 `queue_head_telemetry` 的 `reason_code`**——该工具的设计约束明写
# 「reason_code 是 advisory telemetry：本工具与任何 workflow 都不得据其分支
# （出现 `if reason_code == ...` 即意味着它已悄悄变成控制面契约）」
# （`tools/dev/queue_head_telemetry.py:14-15`）。此处按同一事实各自判读，
# 与那边「一份判读、两个消费面」的意图一致，且不把 advisory 面升格为契约。
#
# 指纹用 `conflicting=<head_sha:0:12>` 与既有 `failed=...` **显式区分**：二者处置不同
# （冲突要人解冲突，红 check 要修 check），共用一个指纹会互相覆盖正文。
mergeable_state="$(jq -r '.mergeable // ""' <<<"$head_json")"
merge_state_status="$(jq -r '.mergeStateStatus // ""' <<<"$head_json")"
if [ "$mergeable_state" = "CONFLICTING" ] || [ "$merge_state_status" = "DIRTY" ]; then
  conflict_sha="$(jq -r '.headRefOid // ""' <<<"$head_json")"
  echo "Queue head #${head_number} is CONFLICTING with main; queue blocked (all checks green)."
  alert_queue_blocked "$head_number" "$head_ref" "" \
    "- 队列影响：队首 required checks **全绿**、但因**与 main 冲突**无法合入 ⇒ 其后所有 PR 停摆；且冲突队首不走自动重基（重基必失败）⇒ **不会自愈**，须人工解冲突" \
    "" "" "conflicting-${conflict_sha:0:12}"
  exit 0
fi

# 队首**确实**通过全部 required checks 且无冲突：恢复关闭存量告警。
# 注意次序：必须放在冲突判定**之后**——否则冲突队首会被「先关闭、再重开」，
# 每轮 reconcile 抖动一次告警（既刷通知，也让去重指纹失去意义）。
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
