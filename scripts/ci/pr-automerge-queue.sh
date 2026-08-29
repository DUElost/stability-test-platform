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
