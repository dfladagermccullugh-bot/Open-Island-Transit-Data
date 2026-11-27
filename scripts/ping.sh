#!/usr/bin/env bash
# Tell an outside monitor (Healthchecks.io) that a job started, finished or
# failed, so a run that never happens is noticed from outside GitHub.
#
#   scripts/ping.sh start|success|fail
#
# Does nothing until the HC_PING_URL secret exists (Davin sets it up later).
# A failed ping never fails the job.
set -uo pipefail
kind="${1:-success}"
if [ -z "${HC_PING_URL:-}" ]; then
  echo "ping $kind skipped: no HC_PING_URL"
  exit 0
fi
case "$kind" in
  start) url="$HC_PING_URL/start" ;;
  fail) url="$HC_PING_URL/fail" ;;
  *) url="$HC_PING_URL" ;;
esac
curl -fsS -m 10 --retry 3 -o /dev/null "$url" && echo "ping $kind sent" || echo "ping $kind failed"
exit 0
