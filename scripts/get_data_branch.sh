#!/usr/bin/env bash
# Check out the staging data branch in ./data-branch, for a workflow that
# writes data.
#
#   scripts/get_data_branch.sh
#
# Every writer (nightly.yml, compare.yml, validators.yml) publishes to
# `data-staging` (scripts/publish_data.sh); only a passing publishing gate
# moves `data` to what was written (scripts/promote_data.sh). So `data`, the
# branch the site, the examples and readers use, only ever holds checked
# figures.
#
# The first time, `data-staging` is created at the commit `data` is at (the
# bootstrap); when neither branch exists, an empty one is started. The clone
# has the whole commit history but fetches file contents only as needed,
# which is enough to tell whether `data` can be fast-forwarded.
#
# Records DATA_BASE and STAGING_BASE, the commits the two branches were at,
# in $GITHUB_ENV when it is set. DATA_REMOTE, DATA_BRANCH and STAGING_BRANCH
# override the remote and branch names (the tests use them).
set -euo pipefail
remote=${DATA_REMOTE:-"https://x-access-token:${GH_TOKEN:-}@github.com/${GITHUB_REPOSITORY:-}.git"}
data=${DATA_BRANCH:-data}
staging=${STAGING_BRANCH:-data-staging}

has() { git ls-remote --exit-code --heads "$remote" "$1" >/dev/null 2>&1; }

data_base=""
staging_base=""
if has "$staging"; then
  git clone -q --filter=tree:0 --single-branch --branch "$staging" "$remote" data-branch 2>/dev/null
  cd data-branch
  staging_base=$(git rev-parse HEAD)
  if has "$data"; then
    git fetch -q --filter=tree:0 origin "$data:refs/remotes/origin/$data" 2>/dev/null
    data_base=$(git rev-parse "refs/remotes/origin/$data")
  fi
elif has "$data"; then
  git clone -q --filter=tree:0 --single-branch --branch "$data" "$remote" data-branch 2>/dev/null
  cd data-branch
  data_base=$(git rev-parse HEAD)
  staging_base=$data_base
  git checkout -q -b "$staging"
  git update-ref "refs/remotes/origin/$data" "$data_base"
  git push -q origin "HEAD:refs/heads/$staging"
  echo "created $staging at $data ($data_base)"
else
  git init -q data-branch
  cd data-branch
  git remote add origin "$remote"
  git checkout -q --orphan "$staging"
  echo "neither $data nor $staging exists: starting an empty branch"
fi
git config user.name "github-actions[bot]"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
echo "$staging at ${staging_base:-none}, $data at ${data_base:-none}"
if [ -n "${GITHUB_ENV:-}" ]; then
  { echo "DATA_BASE=$data_base"; echo "STAGING_BASE=$staging_base"; } >> "$GITHUB_ENV"
fi
