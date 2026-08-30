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
  # 按 PR 状态判定而非再堆一条报错文案匹配：合入与 update-branch 的竞态
  # 不只有一种报错形态，而「PR 已不在 open 态」是唯一稳定的判据。
  state="$(gh pr view "$num" --repo "$REPO" --json state --jq .state 2>/dev/null || echo UNKNOWN)"
  if [ "$state" = "MERGED" ] || [ "$state" = "CLOSED" ]; then
    echo "PR #${num} is already ${state}; update-branch no longer applies."
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
