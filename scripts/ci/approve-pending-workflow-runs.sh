#!/usr/bin/env bash
# Approve action_required workflow runs for one branch (no checkout of PR code).
# Optional env: APPROVE_POLL_ATTEMPTS (default 1), APPROVE_POLL_INTERVAL_SEC (default 15).
set -euo pipefail

REPO="${GITHUB_REPOSITORY:?}"
BRANCH="${1:?branch name required}"
POLL_ATTEMPTS="${APPROVE_POLL_ATTEMPTS:-1}"
POLL_INTERVAL="${APPROVE_POLL_INTERVAL_SEC:-15}"

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

approve_branch_once() {
  local encoded_branch runs_json count
  encoded_branch="$(printf '%s' "$BRANCH" | jq -sRr @uri)"
  runs_json="$(
    gh api "repos/${REPO}/actions/runs?status=action_required&head_branch=${encoded_branch}&per_page=30" \
      2>/dev/null || echo '{"workflow_runs":[]}'
  )"
  count="$(jq '(.workflow_runs // []) | length' <<<"$runs_json")"
  if ! [[ "$count" =~ ^[0-9]+$ ]] || [ "$count" -eq 0 ]; then
    return 1
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
  return 0
}

attempt=1
while [ "$attempt" -le "$POLL_ATTEMPTS" ]; do
  if approve_branch_once; then
    exit 0
  fi
  if [ "$attempt" -lt "$POLL_ATTEMPTS" ]; then
    echo "No workflow runs awaiting approval on ${BRANCH} (attempt ${attempt}/${POLL_ATTEMPTS}); retry in ${POLL_INTERVAL}s."
    sleep "$POLL_INTERVAL"
  fi
  attempt=$((attempt + 1))
done

echo "No workflow runs awaiting approval on ${BRANCH} after ${POLL_ATTEMPTS} attempt(s)."
