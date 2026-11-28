#!/usr/bin/env bash
# Move `data` to the commit the publishing gate has just passed.
#
#   scripts/promote_data.sh [REPORT]
#
# Reads the gate's report in ./data-branch, `checks/gate.json` by default
# (the nightly job's full gate) or `checks/gate-light.json` (the light gate
# that compare.yml and validators.yml run), which `python -m
# pipeline.invariants --report` wrote. The checkout's HEAD may be later than
# the commit the gate checked (`checked_commit`) only by the commit that
# records the report. In order, this script:
#
# 1. refuses unless the report says the gate passed, and HEAD's files equal
#    the checked commit's, apart from checks/;
# 2. refuses unless `data`, as it is now, is an ancestor of HEAD: `data` is
#    only ever fast-forwarded, never rewritten;
# 3. pushes HEAD to `data-staging` without pulling first (if another writer
#    moved it since the check, nothing is promoted and the next run checks
#    again);
# 4. pushes HEAD to `data`, and records PROMOTED=1 in $GITHUB_ENV.
#
# Exits 0 when `data` is already at HEAD. DATA_BRANCH and STAGING_BRANCH
# override the branch names (the tests use them).
set -euo pipefail
report=${1:-checks/gate.json}
data=${DATA_BRANCH:-data}
staging=${STAGING_BRANCH:-data-staging}
cd data-branch
head=$(git rev-parse HEAD)
if [ ! -f "$report" ]; then
  echo "refusing to promote: no $report" >&2
  exit 1
fi
read -r passed checked < <(python3 -c '
import json, sys
g = json.load(open(sys.argv[1], encoding="utf-8"))
print("yes" if g.get("passed") is True else "no", g.get("checked_commit") or "none")
' "$report")
if [ "$passed" != "yes" ]; then
  echo "refusing to promote: the gate did not pass ($report)" >&2
  exit 1
fi
if ! git cat-file -e "$checked^{commit}" 2>/dev/null; then
  echo "refusing to promote: the checked commit $checked is not in this checkout" >&2
  exit 1
fi

if ! git diff --quiet "$checked" "$head" -- . ':(exclude)checks'; then
  echo "refusing to promote: $head differs from the checked commit $checked" >&2
  exit 1
fi
if git ls-remote --exit-code --heads origin "$data" >/dev/null 2>&1; then
  git fetch -q --filter=tree:0 origin "$data:refs/remotes/origin/$data" 2>/dev/null
  current=$(git rev-parse "refs/remotes/origin/$data")
  if [ "$current" = "$head" ]; then
    echo "$data is already at $head"
    exit 0
  fi
  if ! git merge-base --is-ancestor "$current" "$head"; then
    echo "refusing to promote: $data ($current) is not an ancestor of $head" >&2
    exit 1
  fi
fi
if ! git push -q origin "HEAD:refs/heads/$staging"; then
  echo "not promoting: $staging moved since the check; the next run checks again" >&2
  exit 1
fi
git push -q origin "HEAD:refs/heads/$data"
echo "$data moved to $head"
if [ -n "${GITHUB_ENV:-}" ]; then echo "PROMOTED=1" >> "$GITHUB_ENV"; fi
