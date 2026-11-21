"""Watchdog: is the collector still collecting, and is the archive keeping up?

Exits 1 and prints the reasons if anything is wrong, so the scheduled
workflow fails (GitHub emails the repository owner) and an issue is opened.

Checks:
  - each real-time feed has a successful fetch recently
  - in service hours the positions feed is not empty for several fetches in
    a row, and its vehicle timestamps are still moving
  - the collector is not backing off
  - the latest Passio schedule download worked
  - the database is not filling up, and the prune job is running cleanly
  - the nightly job has processed the service day before yesterday
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests  # noqa: E402

from pipeline.archive.store import Collector  # noqa: E402
from pipeline.config import PACIFIC, REPO, USER_AGENT  # noqa: E402
from pipeline.gtfs.holidays import service_expected  # noqa: E402

MAX_AGE_S = {"vehiclePositions": 600, "tripUpdates": 600, "serviceAlerts": 1800}
DATABASE_WARN_BYTES = 300 * 1024 * 1024  # free tier allows 500 MB
OLDEST_RAW_WARN = timedelta(days=10)
EMPTY_RUN = 9  # successful fetches in a row (about 3 minutes) before an empty feed is reported
FROZEN_AFTER = timedelta(minutes=10)  # newest vehicle timestamp this old: positions have stopped
STATIC_MAX_AGE = timedelta(hours=36)  # Passio's schedule is downloaded twice a day
PRUNE_MAX_AGE = timedelta(hours=36)  # the prune job runs once a day
DAILY_CSV = f"https://raw.githubusercontent.com/{REPO}/data/quality/daily.csv"

Row = dict[str, Any]


def in_service_hours(now: datetime) -> bool:
    """Hours when buses are certainly scheduled: 8 AM to 5 PM Pacific on
    every day of the week, except the holidays on which Island Transit runs
    no service (``reference/no-service-days.csv``)."""
    local = now.astimezone(PACIFIC)
    return 8 <= local.hour < 17 and service_expected(local.date())


def _when(text: str) -> datetime:
    t = datetime.fromisoformat(text)
    return t if t.tzinfo else t.replace(tzinfo=UTC)


def _ok(row: Row) -> bool:
    return row.get("http_status") == 200 and not row.get("error_class")


def feed_problems(status: dict[str, Row]) -> list[str]:
    out = []
    for feed, limit in MAX_AGE_S.items():
        row = status.get(feed)
        age = row and row["last_ok_age_s"]
        if age is None:
            out.append(f"{feed}: no successful fetch in the last 2 days")
        elif age > limit:
            out.append(f"{feed}: last successful fetch was {age // 60} minutes ago")
    return out


def position_problems(recent: list[Row], now: datetime) -> list[str]:
    """``recent``: the latest vehicle-position fetches, newest first. One
    empty reply between two buses' reports is normal, so the feed is called
    empty only when ``EMPTY_RUN`` successful fetches in a row had no vehicle."""
    if not in_service_hours(now):
        return []
    ok = [r for r in recent if _ok(r)][:EMPTY_RUN]
    if len(ok) < EMPTY_RUN:
        return []  # too few recent fetches to judge; the feed-age check covers that
    if all(not r.get("entity_count") for r in ok):
        return [f"vehiclePositions: feed empty in service hours for {EMPTY_RUN} fetches in a row"]
    newest = max(
        (_when(r["newest_entity_ts"]) for r in ok if r.get("newest_entity_ts")), default=None
    )
    if newest is not None and now - newest > FROZEN_AFTER:
        minutes = int((now - newest).total_seconds() // 60)
        return [
            f"vehiclePositions: newest vehicle timestamp is {minutes} minutes old (frozen feed)"
        ]
    return []


def static_problems(downloads: list[Row], now: datetime) -> list[str]:
    passio = [d for d in downloads if d["source"] == "passio"]
    if not passio:
        return []
    latest = passio[0]
    if now - _when(latest["logged_at"]) > STATIC_MAX_AGE:
        return [
            f"static GTFS: no Passio download since {_when(latest['logged_at']):%Y-%m-%d %H:%M} UTC"
        ]
    if len(passio) >= 2 and not any(_ok(d) for d in passio[:2]):
        return ["static GTFS: the last two Passio downloads failed"]
    return []


def storage_problems(health: Row, prunes: list[Row], now: datetime) -> list[str]:
    out = []
    if health["database_bytes"] > DATABASE_WARN_BYTES:
        out.append(f"database is {health['database_bytes'] / 1e6:.0f} MB (limit 500 MB)")
    oldest = _when(health["oldest_raw_at"]) if health["oldest_raw_at"] else None
    if oldest and now - oldest > OLDEST_RAW_WARN:
        out.append(
            f"oldest unarchived response is from {oldest:%Y-%m-%d}: "
            "the nightly archive or the prune job is not keeping up"
        )
    if prunes:
        last_run = _when(prunes[0]["ran_at"])
        if now - last_run > PRUNE_MAX_AGE:
            out.append(f"prune job has not run since {last_run:%Y-%m-%d %H:%M} UTC")
        # Rows of the latest run: those written within a minute of the newest.
        latest = [p for p in prunes if last_run - _when(p["ran_at"]) < timedelta(minutes=1)]
        errors = [p for p in latest if p.get("error")]
        if errors:
            e = errors[-1]
            out.append(f"prune job: {e.get('service_date') or 'run'} failed: {e['error']}")
    return out


def nightly_problems(processed: set[str] | None, first_day: str | None, now: datetime) -> list[str]:
    """``processed``: service dates in quality/daily.csv on the data branch,
    or None if it could not be read (then nothing is reported: GitHub being
    unreachable is not a collector problem). The day before yesterday has
    had a full day of nightly attempts."""
    if processed is None or first_day is None:
        return []
    due = (now.astimezone(PACIFIC).date() - timedelta(days=2)).isoformat()
    if due >= first_day and due not in processed:
        return [f"nightly job: service day {due} is still not processed"]
    return []


def processed_days() -> set[str] | None:
    try:
        r = requests.get(DAILY_CSV, headers={"User-Agent": USER_AGENT}, timeout=30)
        if r.status_code != 200:
            return None
        return {line.split(",", 1)[0] for line in r.text.splitlines()[1:] if line}
    except requests.RequestException:
        return None


def problems(collector: Collector, now: datetime, processed: set[str] | None = None) -> list[str]:
    status = {row["feed"]: row for row in collector.status()}
    out = feed_problems(status)
    out += position_problems(collector.recent_fetches("vehiclePositions", 3 * EMPTY_RUN), now)
    health = collector.health()
    if health["backoff_until"]:
        out.append(f"collector is backing off after {health['consecutive_failures']} failing ticks")
    out += static_problems(collector.recent_static(), now)
    out += storage_problems(health, collector.recent_prunes(), now)
    since = next(
        (r["collecting_since"] for r in status.values() if r.get("collecting_since")), None
    )
    first = _when(since).astimezone(PACIFIC).date().isoformat() if since else None
    out += nightly_problems(processed, first, now)
    return out


def main() -> int:
    now = datetime.now(UTC)
    try:
        found = problems(Collector(), now, processed_days())
    except Exception as exc:
        found = [f"could not read collector status: {exc}"]
    if found:
        print("Collector problems at " + now.isoformat(timespec="seconds"))
        for p in found:
            print(f"- {p}")
        return 1
    print("collector OK at " + now.isoformat(timespec="seconds"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
