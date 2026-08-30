#!/usr/bin/env bash
# Schedule fallback: approve action_required runs on every open same-repo PR branch.
set -euo pipefail

REPO="${GITHUB_REPOSITORY:?}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

mapfile -t branches < <(
  gh pr list --repo "$REPO" --state open --limit 100 \
    --json headRefName,isDraft,isCrossRepository \
    --jq '.[] | select(.isDraft == false) | select(.isCrossRepository == false) | .headRefName'
)

if [ "${#branches[@]}" -eq 0 ]; then
  echo "No open PRs; nothing to approve."
  exit 0
fi

echo "Scanning ${#branches[@]} open PR branch(es) for action_required runs."
for branch in "${branches[@]}"; do
  bash "${SCRIPT_DIR}/approve-pending-workflow-runs.sh" "$branch"
done
