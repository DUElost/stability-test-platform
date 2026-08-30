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
# （GraphQL: head sha didn't match the current head ref，见 run 33269782605）。
# 该错误意味着别的 run 已经把 main 合进来了 —— 本轮目标已达成，按无害处理，
# 别把竞态刷成红 X。其余失败原样返回非零，交给 set -e 让 job 变红。
update_branch_tolerant() {
  local num="$1" out rc=0
  out="$(gh pr update-branch "$num" --repo "$REPO" 2>&1)" || rc=$?
  printf '%s\n' "$out"
  if [ "$rc" -ne 0 ] && printf '%s' "$out" | grep -qi "didn't match the current head ref"; then
    echo "update-branch on #${num} lost the head-sha race; another run already updated it."
    return 0
  fi
  return "$rc"
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
  exit 0
fi

echo "Queue head: PR #${head_number}"

for row in "${filtered[@]}"; do
  num="$(jq -r '.number' <<<"$row")"
  url="$(jq -r '.url' <<<"$row")"
  has_auto="$(jq -r 'if .autoMergeRequest then "yes" else "no" end' <<<"$row")"

  if [ "$num" = "$head_number" ]; then
    method="$(
      gh api graphql \
        -f query='query($owner:String!,$repo:String!,$number:Int!){repository(owner:$owner,name:$repo){pullRequest(number:$number){autoMergeRequest{mergeMethod}}}}' \
        -F owner="$owner" -F repo="$name" -F number="$num" \
        --jq '.data.repository.pullRequest.autoMergeRequest.mergeMethod // ""'
    )"
    if [ "$method" != "MERGE" ]; then
      gh pr merge "$url" --auto --merge
      echo "Enabled auto-merge on #${num}"
    else
      echo "Auto-merge already enabled on #${num}"
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

head_ref=""
for row in "${filtered[@]}"; do
  if [ "$(jq -r '.number' <<<"$row")" = "$head_number" ]; then
    head_ref="$(jq -r '.headRefName' <<<"$row")"
    break
  fi
done
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

for check in "${REQUIRED[@]}"; do
  conclusion="$(
    jq -r --arg name "$check" '
      [.statusCheckRollup[]? | select(.name == $name)] | first | .conclusion // ""
    ' <<<"$head_json"
  )"
  if [ "$conclusion" != "SUCCESS" ]; then
    echo "Queue head #${head_number}: ${check} not SUCCESS (${conclusion:-missing}); skip head update."
    exit 0
  fi
done

behind="$(
  gh api "repos/${REPO}/compare/main...${head_ref}" --jq '.behind_by // 0'
)"
if [ "$behind" -eq 0 ]; then
  echo "Queue head #${head_number} is up to date with main."
  exit 0
fi

echo "Queue head #${head_number} is ${behind} commit(s) behind main; updating branch."
update_branch_tolerant "$head_number"
