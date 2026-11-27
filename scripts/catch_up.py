"""Start the nightly job if yesterday's service day has not been processed.

    python scripts/catch_up.py            # decides, and dispatches with gh
    python scripts/catch_up.py --dry-run  # decides only

GitHub drops scheduled runs when it is busy; on 2026-11-20 the 08:35 UTC
nightly run never started. The watchdog runs this every time it fires, so
any scheduled workflow that does start can make up for a missed nightly run.

Dispatches ``nightly.yml`` when all hold:
- it is at least 00:30 Pacific (yesterday's service day is complete);
- ``quality/days/<yesterday>.json`` is not on the data branch (the last
  output of the nightly chain), or says it is not complete (it was written
  before the day's stop events existed);
- no nightly run was created in the last ``QUIET_HOURS`` hours (a run that
  keeps failing should not be restarted every half hour).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.config import PACIFIC, REPO

QUIET_HOURS = 3
READY_AFTER = timedelta(minutes=30)  # after midnight Pacific


def decide(now: datetime, processed: bool, last_run: datetime | None) -> tuple[bool, str]:
    local = now.astimezone(PACIFIC)
    day = local.date() - timedelta(days=1)
    if local - local.replace(hour=0, minute=0, second=0, microsecond=0) < READY_AFTER:
        return False, f"too early: {day} may still be collecting"
    if processed:
        return False, f"{day} already processed"
    if last_run is not None and now - last_run < timedelta(hours=QUIET_HOURS):
        return False, f"{day} not processed, but a nightly run started at {last_run:%H:%M} UTC"
    return True, f"{day} not processed and no recent nightly run: dispatching"


def is_complete(day_file: str) -> bool:
    """Whether a quality day file counts as processed: it must say it is
    complete. A file written before its stop events existed says
    ``"complete": false`` (since ``quality/0.6.1``); files from before that
    have no such field and count as complete."""
    try:
        return bool(json.loads(day_file).get("complete", True))
    except (ValueError, AttributeError):
        return False


def service_day(now: datetime) -> date:
    return now.astimezone(PACIFIC).date() - timedelta(days=1)


def gh(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["gh", *args], capture_output=True, text=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", REPO))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    now = datetime.now(UTC)
    day = service_day(now)
    have = gh("api", f"repos/{args.repo}/contents/quality/days/{day}.json?ref=data",
              "-H", "Accept: application/vnd.github.raw")  # fmt: skip
    processed = have.returncode == 0 and is_complete(have.stdout)
    runs = gh("run", "list", "--repo", args.repo, "--workflow", "nightly.yml", "--limit", "1",
              "--json", "createdAt")  # fmt: skip
    last = json.loads(runs.stdout or "[]") if runs.returncode == 0 else []
    last_run = datetime.fromisoformat(last[0]["createdAt"].replace("Z", "+00:00")) if last else None

    go, why = decide(now, processed, last_run)
    print(why)
    if go and not args.dry_run:
        run = gh("workflow", "run", "nightly.yml", "--repo", args.repo, "--ref", "main")
        if run.returncode != 0:
            print(f"dispatch failed: {run.stderr.strip()}", file=sys.stderr)
            return 1
        print("nightly.yml dispatched")
    return 0


if __name__ == "__main__":
    sys.exit(main())
