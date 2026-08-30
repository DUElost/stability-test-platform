#!/usr/bin/env bash
# Approve action_required workflow runs for one branch (no checkout of PR code).
set -euo pipefail

REPO="${GITHUB_REPOSITORY:?}"
BRANCH="${1:?branch name required}"

is_excluded_ref() {
  local ref="$1"
  [[ "$ref" == dependabot/npm_and_yarn/frontend/frontend-major-* ]] && return 0
  [[ "$ref" == dependabot/github_actions/* ]] && return 0
  return 1
}

if is_excluded_ref "$BRANCH"; then
  echo "Skipping excluded branch ${BRANCH}."
  exit 0
fi

encoded_branch="$(printf '%s' "$BRANCH" | jq -sRr @uri)"
runs_json="$(
  gh api "repos/${REPO}/actions/runs?status=action_required&head_branch=${encoded_branch}&per_page=30" \
    2>/dev/null || echo '{"workflow_runs":[]}'
)"
count="$(jq '(.workflow_runs // []) | length' <<<"$runs_json")"
if ! [[ "$count" =~ ^[0-9]+$ ]] || [ "$count" -eq 0 ]; then
  echo "No workflow runs awaiting approval on branch ${BRANCH}."
  exit 0
fi

echo "Found ${count} workflow run(s) awaiting approval on ${BRANCH}."
while IFS=$'\t' read -r run_id run_name; do
  [ -z "$run_id" ] && continue
  if gh api "repos/${REPO}/actions/runs/${run_id}/approve" -X POST >/dev/null 2>&1; then
    echo "Approved workflow run ${run_id} (${run_name})."
  else
    echo "Failed to approve workflow run ${run_id} (${run_name}); check actions:write on GITHUB_TOKEN."
  fi
done < <(jq -r '(.workflow_runs // [])[] | "\(.id)\t\(.name)"' <<<"$runs_json")
