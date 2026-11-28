#!/usr/bin/env bash
# Commit and push the data branch checkout in ./data-branch, if it changed.
#
#   scripts/publish_data.sh BRANCH "message"
#
# BRANCH is `data-staging` for every writer: `data` itself moves only when
# the publishing gate passes (scripts/promote_data.sh). Called after every
# stage of the nightly job, so that work a stage finished is kept even if a
# later stage fails or the job runs out of time. If the push is refused
# because the branch moved, the new commits are pulled in (rebase) and the
# push tried again, up to four times.
set -euo pipefail
branch=$1
message=$2
cd data-branch
git add -A
if git diff --cached --quiet; then
  echo "data branch unchanged"
  exit 0
fi
git -c user.name="github-actions[bot]" \
    -c user.email="41898282+github-actions[bot]@users.noreply.github.com" \
    commit -q -m "$message $(date -u +%Y-%m-%dT%H:%MZ)"
for wait in 0 5 20 60; do
  sleep "$wait"
  if git push -q origin "HEAD:refs/heads/$branch"; then
    echo "$branch updated"
    if [ -n "${GITHUB_ENV:-}" ]; then echo "DATA_CHANGED=1" >> "$GITHUB_ENV"; fi
    exit 0
  fi
  git -c user.name="github-actions[bot]" \
      -c user.email="41898282+github-actions[bot]@users.noreply.github.com" \
      pull -q --rebase origin "$branch" || true
done
echo "could not push $branch" >&2
exit 1
