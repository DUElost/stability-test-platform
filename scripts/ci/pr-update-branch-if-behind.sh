#!/usr/bin/env bash
# When auto-merge PR has all required checks green but is behind main, update branch once.
set -euo pipefail

REPO="${GITHUB_REPOSITORY:?}"
BRANCH="${HEAD_BRANCH:?}"
SHA="${HEAD_SHA:?}"

REQUIRED=(
  lint
  CodeQL
  pr-typecheck
  pr-compileall
  pr-agent-tests
  pr-migrate-empty-db
)

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

pr_json="$(
  gh pr list --repo "$REPO" --head "$BRANCH" --state open \
    --json number,autoMergeRequest,statusCheckRollup,headRefOid \
    --jq '.[0] // empty'
)"
if [ -z "$pr_json" ]; then
  echo "No open PR for branch ${BRANCH}; skip."
  exit 0
fi

num="$(jq -r '.number' <<<"$pr_json")"
auto_method="$(jq -r '.autoMergeRequest.mergeMethod // ""' <<<"$pr_json")"
if [ -z "$auto_method" ]; then
  echo "PR #${num} has no auto-merge; skip."
  exit 0
fi

head_oid="$(jq -r '.headRefOid' <<<"$pr_json")"
if [ "$head_oid" != "$SHA" ]; then
  echo "workflow_run head ${SHA} != PR head ${head_oid}; skip stale run."
  exit 0
fi

for check in "${REQUIRED[@]}"; do
  conclusion="$(
    jq -r --arg name "$check" '
      [.statusCheckRollup[]? | select(.name == $name)] | first | .conclusion // ""
    ' <<<"$pr_json"
  )"
  if [ "$conclusion" != "SUCCESS" ]; then
    echo "Required check ${check} is not SUCCESS (${conclusion:-missing}); skip."
    exit 0
  fi
done

behind="$(
  gh api "repos/${REPO}/compare/main...${BRANCH}" --jq '.behind_by // 0'
)"
if [ "$behind" -eq 0 ]; then
  echo "PR #${num} is up to date with main."
  exit 0
fi

echo "PR #${num} is ${behind} commit(s) behind main; updating branch."
update_branch_tolerant "$num"
