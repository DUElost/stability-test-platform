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
  pr-agent-gate
)

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
gh pr update-branch "$num" --repo "$REPO"
