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

With ``--data`` it checks the published figures instead (``quality/daily.csv``
on the data branch), for signs that the numbers of the latest service day
cannot be trusted or that something needs a person's attention:
  - trip ids in the live feed no longer match the schedule the day was
    measured against (the mark of a schedule change pinned to the wrong
    version)
  - the schedule lists no trips on a day when service is expected
  - trip coverage far below recent days
  - fewer printed timepoint names tied to a stop than the day before
  - notes: a new Passio schedule version, a day whose schedule downloads
    disagreed, trips the feed marked as canceled, and the list of no-service
    holidays about to run out
It exits 1 with the list, 0 if there is nothing to say, and 2 if the figures
could not be read (then the workflow leaves its issue as it is).

With ``--data --data-dir <data branch checkout>`` (how the workflow runs it
since November 27, 2026) it runs the tiered checks of ``scripts/anomaly.py``
instead, adding the dispatch token's expiry and the state of the scheduled
workflows: exit 4 for a problem (the run fails), 1 for something to look
at, 3 for notes only, 0 for nothing, 2 if the figures could not be read.
"""

from __future__ import annotations

import csv
import io
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import requests

from pipeline.archive.store import Collector
from pipeline.config import PACIFIC, REPO, USER_AGENT
from pipeline.gtfs.holidays import last_listed, service_expected
from pipeline.stats import median

MAX_AGE_S = {"vehiclePositions": 600, "tripUpdates": 600, "serviceAlerts": 1800}
DATABASE_WARN_BYTES = 300 * 1024 * 1024  # free tier allows 500 MB
OLDEST_RAW_WARN = timedelta(days=10)
EMPTY_RUN = 9  # successful fetches in a row (about 3 minutes) before an empty feed is reported
FROZEN_AFTER = timedelta(minutes=10)  # newest vehicle timestamp this old: positions have stopped
STATIC_MAX_AGE = timedelta(hours=36)  # Passio's schedule is downloaded twice a day
PRUNE_MAX_AGE = timedelta(hours=36)  # the prune job runs once a day
DAILY_CSV = f"https://raw.githubusercontent.com/{REPO}/data/quality/daily.csv"

# Checks on the published figures (``--data``).
MATCH_RATE_MIN = 0.95  # share of position reports whose trip id is in the day's schedule
COVERAGE_DROP = 0.15  # trip coverage this far below the recent median is reported
MEDIAN_DAYS = 14  # service days the recent median is taken over
MIN_HISTORY = 5  # earlier service days needed before coverage is compared
RESOLVED_DROP = 0.005  # fall in the share of printed timepoint names tied to a stop
HOLIDAY_LIST_WARN_DAYS = 60
PIN_OVERRIDES = "reference/static-pin-overrides.csv"

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
        age = row["last_ok_age_s"] if row else None
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


def daily_rows() -> list[dict[str, str]] | None:
    """The rows of quality/daily.csv on the data branch, or None if it could
    not be read."""
    try:
        r = requests.get(DAILY_CSV, headers={"User-Agent": USER_AGENT}, timeout=30)
        if r.status_code != 200:
            return None
        return list(csv.DictReader(io.StringIO(r.text)))
    except requests.RequestException:
        return None


def processed_days() -> set[str] | None:
    """Days in quality/daily.csv whose quality file is complete (a day
    computed before its stop events existed is not processed yet; rows from
    before the ``complete`` column count as complete)."""
    rows = daily_rows()
    return None if rows is None else complete_days(rows)


def complete_days(rows: list[dict[str, str]]) -> set[str]:
    return {r["service_date"] for r in rows if (r.get("complete") or "true").lower() != "false"}


def _yes(value: str | None) -> bool:
    """True for "True" (the first daily tables) and "true" (written since)."""
    return (value or "").strip().lower() == "true"


def _num(row: dict[str, str], key: str) -> float | None:
    try:
        return float(row.get(key) or "")
    except ValueError:
        return None


def data_problems(
    rows: list[dict[str, str]], today: date, last_holiday: date | None = None
) -> list[str]:
    """What the published figures say needs attention. ``rows``: daily.csv
    in date order. The checks look at the latest day on which service was
    expected; days without service are neither checked nor used for
    comparison. A column that an older row lacks is skipped."""
    out: list[str] = []
    service = [r for r in rows if _yes(r.get("service_expected", "true"))]
    if service:
        last, earlier = service[-1], service[:-1]
        day = last["service_date"]
        match, trips = _num(last, "trip_id_match_rate"), _num(last, "scheduled_trips")
        coverage = _num(last, "trip_coverage")
        if trips == 0:
            out.append(f"{day}: the schedule lists no trips, on a day when service is expected")
        if match is not None and match < MATCH_RATE_MIN:
            out.append(
                f"{day}: only {match:.0%} of position reports carry a trip id found in the "
                f"schedule version used ({last.get('passio_version')}). If the schedule changed "
                f"that day, add a row for the right version to {PIN_OVERRIDES}"
            )
        recent = [c for r in earlier[-MEDIAN_DAYS:] if (c := _num(r, "trip_coverage")) is not None]
        if coverage is not None and len(recent) >= MIN_HISTORY:
            usual = median(recent)
            if usual is not None and coverage < usual - COVERAGE_DROP:
                out.append(
                    f"{day}: trip coverage was {coverage:.1%}, against a median of {usual:.1%} "
                    f"over the {len(recent)} service days before"
                )
        resolved = _num(last, "timepoint_names_resolved_share")
        before = _num(earlier[-1], "timepoint_names_resolved_share") if earlier else None
        if resolved is not None and before is not None and resolved < before - RESOLVED_DROP:
            out.append(
                f"{day}: {resolved:.1%} of printed timepoint names are tied to a stop, down from "
                f"{before:.1%} the day before. Check the timetable list and its overrides"
            )
        if earlier and last.get("passio_version") != earlier[-1].get("passio_version"):
            out.append(
                f"note: {day} is the first day on a new Passio schedule version "
                f"{last.get('passio_version')}"
            )
        if _yes(last.get("passio_changed_during_day")):
            out.append(
                f"note: {day}: the schedule downloads disagreed that day, and the version was "
                f"pinned as: {last.get('passio_basis')}"
            )
        canceled = _num(last, "canceled_trips")
        if canceled:
            out.append(f"note: {day}: the feed marked {canceled:.0f} trips as canceled")
    if last_holiday is not None and (last_holiday - today).days <= HOLIDAY_LIST_WARN_DAYS:
        out.append(
            f"the list of no-service holidays ends on {last_holiday.isoformat()}: "
            "add next year's dates to reference/no-service-days.csv"
        )
    return out


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


def outside_checks(collector: Collector | None = None) -> tuple[datetime | None, date | None]:
    """(When the dispatch token expires, the day the live site's figures run
    through), as the collector last saw them (``status_now``, migration
    0007); None for what is not known. GitHub sends no expiry for a token
    that has none."""
    try:
        rows = (collector or Collector()).status()
    except Exception:  # unreadable status is reported by the collector check
        return None, None
    expires = next((_when(r["dispatch_token_expires_at"]) for r in rows
                    if r.get("dispatch_token_expires_at")), None)  # fmt: skip
    through = next((date.fromisoformat(r["site_data_through"]) for r in rows
                    if r.get("site_data_through")), None)  # fmt: skip
    return expires, through


def workflow_states(repo: str = REPO) -> dict[str, str]:
    """Workflow file -> state on GitHub, or nothing if gh cannot say."""
    import json
    import subprocess

    try:
        done = subprocess.run(
            ["gh", "api", f"repos/{repo}/actions/workflows", "--jq", ".workflows"],
            capture_output=True, text=True, timeout=60,
        )  # fmt: skip
        if done.returncode != 0:
            return {}
        return {w["path"].rsplit("/", 1)[-1]: w["state"] for w in json.loads(done.stdout)}
    except (OSError, ValueError, subprocess.SubprocessError):
        return {}


def data_dir_main(now: datetime, data: Path) -> int:
    """The tiered checks of ``scripts/anomaly.py`` on a data branch checkout.
    Exit 4: a problem; 1: something to look at; 3: notes only; 0: nothing;
    2: the figures could not be read."""
    import anomaly

    if not (data / "quality" / "daily.csv").exists():
        print("data check: quality/daily.csv could not be read at " + now.isoformat())
        return 2
    expires, site_through = outside_checks()
    found = anomaly.evaluate(
        data, now, last_listed(), expires, workflow_states(), site_through=site_through
    )
    print(anomaly.report(found, now))
    return found.exit_code()


def data_main(now: datetime) -> int:
    rows = daily_rows()
    stamp = now.isoformat(timespec="seconds")
    if rows is None:
        print("data check: quality/daily.csv could not be read at " + stamp)
        return 2
    found = data_problems(rows, now.astimezone(PACIFIC).date(), last_listed())
    if found:
        print("Data check at " + stamp)
        for p in found:
            print(f"- {p}")
        return 1
    print("data OK at " + stamp)
    return 0


def main() -> int:
    now = datetime.now(UTC)
    if "--data" in sys.argv[1:]:
        if "--data-dir" in sys.argv[1:]:
            return data_dir_main(now, Path(sys.argv[sys.argv.index("--data-dir") + 1]))
        return data_main(now)
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
