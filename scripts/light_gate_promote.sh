#!/usr/bin/env bash
# The light publishing gate on ./data-branch, then `data` moves to it.
#
#   scripts/light_gate_promote.sh
#
# For the writers other than the nightly job (compare.yml, validators.yml),
# when data-staging held nothing the nightly job's full gate had not passed.
# Runs rules I1 to I8 (`python -m pipeline.invariants --light`), records
# their report as checks/gate-light.json on data-staging whether they passed
# or not, and promotes (scripts/promote_data.sh) only if they passed. The
# full gate's report, checks/gate.json, is left as the nightly job wrote it.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
set +e
python -m pipeline.invariants --light --data data-branch \
  --report data-branch/checks/gate-light.json --commit "$(git -C data-branch rev-parse HEAD)"
code=$?
set -e
"$here/publish_data.sh" data-staging "Light gate:"
if [ "$code" != "0" ]; then
  echo "the light gate failed: data stays where it is" >&2
  exit 1
fi
"$here/promote_data.sh" checks/gate-light.json
