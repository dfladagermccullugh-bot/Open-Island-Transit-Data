"""Does the latest service day need a person's attention? Three tiers.

    python scripts/anomaly.py --data data-branch                 # print, exit by tier
    python scripts/anomaly.py --data data-branch --write-notes   # also quality/notes.json

Each figure of the latest service day is compared with the same kind of day
(weekday, Saturday, Sunday) over the 28 service days before it: the median
and the median absolute deviation (MAD), with a floor so a run of identical
days does not make every small change look huge. Its distance from usual is
z = (value - median) / (1.4826 * MAD). With fewer than 5 such days, fixed
limits are used instead.

- Tier 1, problem (exit 4; the watchdog fails its run): a publishing rule
  fails (``pipeline/invariants.py``, light set); a service day without stop
  events; no scheduled trips; trip ids that stop matching the schedule
  (below 95%); trip coverage far below usual (z < -4, or 15 points); a day
  still unprocessed 36 hours after it ended; a scheduled workflow that
  GitHub turned off for inactivity; the dispatch token expiring within 7
  days.
- Tier 2, look (exit 1; a comment on the "Data check" issue): many more
  impossible jumps than usual (z > 4 and more than 20); position age (95th
  percentile) more than twice usual; off-shape share well above usual
  (z > 3); timepoint coverage well below usual (z < -3); on-time share far
  from usual (|z| > 4); a route whose trips run a steady few minutes off the
  timetable; feed agreement (check 2) below 90%; fewer printed timepoint
  names tied to a stop than the day before; the token expiring within 21
  days.
- Tier 3, information (exit 3; nothing fails): a new Passio schedule
  version, schedule downloads that disagreed, canceled trips, the holiday
  list running out.

Exit 0: nothing to say; 2: the figures could not be read. ``--write-notes``
writes every finding to ``quality/notes.json`` for the site. Standard
library only (``tests/test_light_imports.py``).
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.common import utcnow
from pipeline.config import PACIFIC
from pipeline.stats import median

NOTES_VERSION = "notes/0.1.0"
BASELINE_DAYS = 28
MIN_HISTORY = 5
MAD_SCALE = 1.4826
UNPROCESSED_AFTER = timedelta(hours=36)
TOKEN_PROBLEM_DAYS = 7
TOKEN_LOOK_DAYS = 21

# Metric: (daily.csv column, MAD floor, the check, fixed limit with little history).
# Each check names the tier and the direction that is a finding.
CHECKS = [
    ("trip_coverage", 0.02, ("problem", "low", 4.0), 0.80),
    ("impossible_jumps", 2.0, ("look", "high", 4.0), 20),
    ("off_shape_share", 0.005, ("look", "high", 3.0), 0.08),
    ("timepoint_coverage", 0.02, ("look", "low", 3.0), 0.60),
]
ON_TIME_FLOOR = 0.03
ON_TIME_Z = 4.0
JUMPS_MIN = 20
AGE_P95_RATIO = 2.0
AGE_P95_FIXED = 30.0
FEED_AGREEMENT_MIN = 0.90
MATCH_RATE_MIN = 0.95
COVERAGE_DROP = 0.15
RESOLVED_DROP = 0.005


@dataclass
class Findings:
    problems: list[str] = field(default_factory=list)
    looks: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def add(self, tier: str, text: str) -> None:
        {"problem": self.problems, "look": self.looks, "note": self.notes}[tier].append(text)

    def exit_code(self) -> int:
        if self.problems:
            return 4
        if self.looks:
            return 1
        return 3 if self.notes else 0


def day_type(d: date) -> str:
    """As ``pipeline.quality.gaps.day_type``, without numpy."""
    return {5: "saturday", 6: "sunday"}.get(d.weekday(), "weekday")


def _num(row: dict[str, str] | None, key: str) -> float | None:
    try:
        return float((row or {}).get(key) or "")
    except ValueError:
        return None


def _yes(value: str | None) -> bool:
    return (value or "").strip().lower() == "true"


def service_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    return [r for r in rows if _yes(r.get("service_expected", "true"))]


def baseline(
    history: list[dict[str, str]], key: str, floor: float
) -> tuple[float, float, int] | None:
    """(median, scaled MAD with its floor, days) of ``key`` over ``history``."""
    values = [v for r in history if (v := _num(r, key)) is not None]
    if not values:
        return None
    mid = median(values)
    assert mid is not None
    mad = median([abs(v - mid) for v in values])
    assert mad is not None
    return mid, max(MAD_SCALE * mad, floor), len(values)


def history_of(service: list[dict[str, str]], last: dict[str, str]) -> list[dict[str, str]]:
    """The same kind of day as ``last`` among the service days before it."""
    kind = day_type(date.fromisoformat(last["service_date"]))
    same = [r for r in service[:-1] if day_type(date.fromisoformat(r["service_date"])) == kind]
    return same[-BASELINE_DAYS:]


def check_figures(
    f: Findings, quality: list[dict[str, str]], ontime: list[dict[str, str]],
    ontime_day: dict[str, Any] | None,
) -> None:  # fmt: skip
    """The figures of the latest service day against usual."""
    service = service_rows(quality)
    if not service:
        return
    last, before = service[-1], history_of(service, service[-1])
    day = last["service_date"]
    if last.get("complete", "true").lower() == "false" or (
        last.get("timepoint_coverage", "x") == "" and "timepoint_coverage" in last
    ):
        f.add("problem", f"{day}: the quality file has no stop-event figures (stop events missing)")
    trips, match = _num(last, "scheduled_trips"), _num(last, "trip_id_match_rate")
    if trips == 0:
        f.add("problem", f"{day}: the schedule lists no trips, on a day when service is expected")
    if match is not None and match < MATCH_RATE_MIN:
        f.add("problem", f"{day}: only {match:.0%} of position reports carry a trip id found in "
              f"the schedule version used ({last.get('passio_version')}); if the schedule "
              "changed that day, add a row to reference/static-pin-overrides.csv")  # fmt: skip
    for key, floor, (tier, way, limit), fixed in CHECKS:
        x = _num(last, key)
        if x is None:
            continue
        base = baseline(before, key, floor)
        if base is None or base[2] < MIN_HISTORY:
            bad = x < fixed if way == "low" else x > fixed
            if bad:
                f.add(tier, f"{day}: {key} {x:g} is past the fixed limit {fixed:g} "
                      "(fewer than 5 days of the same kind to compare with)")  # fmt: skip
            continue
        mid, spread, n = base
        z = (x - mid) / spread
        bad = z < -limit if way == "low" else z > limit
        if key == "impossible_jumps":
            bad = bad and x > JUMPS_MIN
        if key == "trip_coverage":
            bad = bad or x < mid - COVERAGE_DROP
        if bad:
            f.add(tier, f"{day}: {key} {x:g}, usual {mid:g} over {n} days of the same kind "
                  f"(z = {z:+.1f})")  # fmt: skip
    age, base = _num(last, "position_age_s_p95"), baseline(before, "position_age_s_p95", 0.5)
    if age is not None:
        usual = base[0] if base and base[2] >= MIN_HISTORY else None
        if (usual is not None and age > AGE_P95_RATIO * usual) or (
            usual is None and age > AGE_P95_FIXED
        ):
            f.add(
                "look", f"{day}: position age (95th percentile) {age:g} s, usual {usual or 'n/a'}"
            )
    resolved = _num(last, "timepoint_names_resolved_share")
    prev = _num(service[-2], "timepoint_names_resolved_share") if len(service) > 1 else None
    if resolved is not None and prev is not None and resolved < prev - RESOLVED_DROP:
        f.add("look", f"{day}: {resolved:.1%} of printed timepoint names are tied to a stop, down "
              f"from {prev:.1%}; check the timetable list and its overrides")  # fmt: skip
    if len(service) > 1 and last.get("passio_version") != service[-2].get("passio_version"):
        f.add("note", f"{day} is the first day on a new Passio schedule version "
              f"{last.get('passio_version')}")  # fmt: skip
    if _yes(last.get("passio_changed_during_day")):
        f.add("note", f"{day}: the schedule downloads disagreed that day; pinned as "
              f"{last.get('passio_basis')}")  # fmt: skip
    if _num(last, "canceled_trips"):
        f.add(
            "note", f"{day}: the feed marked {_num(last, 'canceled_trips'):.0f} trips as canceled"
        )

    o_service = service_rows(ontime)
    if o_service:
        o_last = o_service[-1]
        o_before = history_of(o_service, o_last)
        share = _num(o_last, "on_time_share_of_observed")
        base = baseline(o_before, "on_time_share_of_observed", ON_TIME_FLOOR)
        if share is not None and base and base[2] >= MIN_HISTORY:
            z = (share - base[0]) / base[1]
            if abs(z) > ON_TIME_Z:
                f.add("look", f"{o_last['service_date']}: on time {share:.1%}, usual "
                      f"{base[0]:.1%} (z = {z:+.1f})")  # fmt: skip
        agree = _num(o_last, "feed_agreement_share")
        if agree is not None and agree < FEED_AGREEMENT_MIN:
            f.add("look", f"{o_last['service_date']}: only {agree:.1%} of inferred departures "
                  "agree with the feed (check 2)")  # fmt: skip
    flagged = (
        ((ontime_day or {}).get("validation") or {}).get("trip_offsets", {}).get("flagged_routes")
    )
    if flagged and ontime_day:
        f.add("look", f"{ontime_day['service_date']}: trips of routes {', '.join(flagged)} run a "
              "steady few minutes off the timetable; is the right schedule pinned?")  # fmt: skip


def check_processed(
    f: Findings, quality: list[dict[str, str]], first: str | None, now: datetime
) -> None:
    """A service day still not processed 36 hours after it ended."""
    if not first:
        return
    done = {r["service_date"] for r in quality if r.get("complete", "true").lower() != "false"}
    day = date.fromisoformat(first)
    today = now.astimezone(PACIFIC).date()
    while day < today:
        ended = datetime.combine(day + timedelta(days=1), time.min, tzinfo=PACIFIC)
        if now - ended > UNPROCESSED_AFTER and day.isoformat() not in done:
            f.add("problem", f"service day {day} is still not processed, "
                  f"{(now - ended).total_seconds() / 3600:.0f} hours after it ended")  # fmt: skip
        day += timedelta(days=1)


def check_token(f: Findings, expires: datetime | None, now: datetime) -> None:
    if expires is None:
        return
    days = (expires - now).total_seconds() / 86400
    if days <= TOKEN_PROBLEM_DAYS:
        f.add("problem", f"the dispatch token expires {expires:%B %d, %Y} ({days:.0f} days); "
              "make a new one and store it as github_dispatch_token")  # fmt: skip
    elif days <= TOKEN_LOOK_DAYS:
        f.add("look", f"the dispatch token expires {expires:%B %d, %Y} ({days:.0f} days)")


def check_workflows(f: Findings, states: dict[str, str]) -> None:
    for name, state in sorted(states.items()):
        if state == "disabled_inactivity":
            f.add("problem", f"GitHub turned off the scheduled workflow {name} for inactivity; "
                  "turn it on again in the Actions tab")  # fmt: skip


def check_holidays(
    f: Findings, last_holiday: date | None, today: date, warn_days: int = 60
) -> None:
    if last_holiday is not None and (last_holiday - today).days <= warn_days:
        f.add("note", f"the list of no-service holidays ends on {last_holiday.isoformat()}: "
              "add next year's dates to reference/no-service-days.csv")  # fmt: skip


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def evaluate(data: Path, now: datetime, last_holiday: date | None = None,
             token_expires: datetime | None = None, workflows: dict[str, str] | None = None,
             gate: bool = True) -> Findings:  # fmt: skip
    """Every finding for a data branch checkout."""
    f = Findings()
    quality = read_rows(data / "quality" / "daily.csv")
    ontime_path = data / "ontime" / "daily.csv"
    ontime = read_rows(ontime_path) if ontime_path.exists() else []
    latest = ontime[-1]["service_date"] if ontime else None
    day_path = data / "ontime" / "days" / f"{latest}.json"
    ontime_day = json.loads(day_path.read_text()) if latest and day_path.exists() else None
    if gate:
        from pipeline import invariants

        res = invariants.run(data, light_only=True)
        for p in res.problems:
            f.add("problem", f"publishing rule {p}")
    check_figures(f, quality, ontime, ontime_day)
    raw = json.loads((data / "manifest" / "raw.json").read_text()) if (
        data / "manifest" / "raw.json").exists() else {"days": {}}  # fmt: skip
    check_processed(f, quality, min(raw["days"], default=None), now)
    check_token(f, token_expires, now)
    check_workflows(f, workflows or {})
    check_holidays(f, last_holiday, now.astimezone(PACIFIC).date())
    return f


def notes_file(f: Findings, quality_last: str | None) -> dict[str, Any]:
    """The findings for the site. No time stamp: the file changes only when
    a finding does, so a nightly run with nothing new publishes nothing."""
    tier = "problem" if f.problems else "look" if f.looks else "note" if f.notes else "ok"
    return {
        "notes_version": NOTES_VERSION,
        "latest_service_date": quality_last,
        "status": tier,
        "problems": f.problems,
        "looks": f.looks,
        "notes": f.notes,
    }


def report(f: Findings, now: datetime) -> str:
    lines = [f"Data check at {now.isoformat(timespec='seconds')}"]
    for title, items in (("Problem", f.problems), ("Look", f.looks), ("Note", f.notes)):
        lines += [f"- {title}: {x}" for x in items]
    if len(lines) == 1:
        lines.append("data OK")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Tiered checks of the latest service day.")
    ap.add_argument("--data", type=Path, default=Path("data-branch"))
    ap.add_argument("--write-notes", action="store_true", help="write quality/notes.json")
    args = ap.parse_args(argv)
    now = utcnow()
    if not (args.data / "quality" / "daily.csv").exists():
        print("quality/daily.csv could not be read")
        return 2
    from pipeline.gtfs.holidays import last_listed

    f = evaluate(args.data, now, last_listed())
    print(report(f, now))
    if args.write_notes:
        rows = read_rows(args.data / "quality" / "daily.csv")
        out = notes_file(f, rows[-1]["service_date"] if rows else None)
        path = args.data / "quality" / "notes.json"
        path.write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")
    return f.exit_code()


if __name__ == "__main__":
    sys.exit(main())
