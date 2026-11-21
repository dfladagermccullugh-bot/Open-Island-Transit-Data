#!/usr/bin/env bash
# Commit and push the data branch checkout in ./data-branch, if it changed.
#
#   scripts/publish_data.sh "message"
#
# Called after every stage of the nightly job, so that work a stage finished
# is published even if a later stage fails or the job runs out of time. If
# the push is refused because the branch moved, the new commits are pulled
# in (rebase) and the push tried again, up to four times.
set -euo pipefail
cd data-branch
git add -A
if git diff --cached --quiet; then
  echo "data branch unchanged"
  exit 0
fi
git -c user.name="github-actions[bot]" \
    -c user.email="41898282+github-actions[bot]@users.noreply.github.com" \
    commit -q -m "$1 $(date -u +%Y-%m-%dT%H:%MZ)"
for wait in 0 5 20 60; do
  sleep "$wait"
  if git push -q origin HEAD:data; then
    echo "data branch updated"
    if [ -n "${GITHUB_ENV:-}" ]; then echo "DATA_CHANGED=1" >> "$GITHUB_ENV"; fi
    exit 0
  fi
  git -c user.name="github-actions[bot]" \
      -c user.email="41898282+github-actions[bot]@users.noreply.github.com" \
      pull -q --rebase origin data || true
done
echo "could not push the data branch" >&2
exit 1
