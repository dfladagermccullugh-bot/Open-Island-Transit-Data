"""Class A comparisons: our own method on a sample of another agency's feed.

    python -m pipeline.compare_sample --agency <id> --rt <vehicle positions URL> \\
        --static <GTFS zip URL> --minutes 120 --out compare/<id>/<date>.json

Polls the agency's GTFS-Realtime vehicle positions every ``--every`` seconds
for ``--minutes``, downloads its schedule, and runs the same inference as
for Island Transit (``pipeline/events/infer.py``): the same stop radius, the
same gaps, the same rule for an on-time departure (1 minute early to 5
minutes late). Timepoints are the stops the schedule marks as timepoints
(``timepoint`` 1 in ``stop_times.txt``); a schedule that marks none counts
every stop, and the summary says so.

Only a summary is kept (a few kilobytes): no other agency's raw data is stored.
It holds the share on time with a 95% interval from a bootstrap that
resamples whole trips (departures on one trip are not independent), and the
same share with a stop radius of 30, 40 and 50 meters, to show how much the
figure depends on that choice. Since ``compare-sample/0.3.0`` it also keeps
``[moments, on time]`` for each trip (``by_trip``), so that the compare stage
can pool several samples and resample their trips together, and the
agency's own weekday and clock hours the sample covered (``local_weekday``,
``local_hours``), so that Island Transit's figure can be recounted over the
same hours (``pipeline/compare.py``).

The agency's own time zone (``agency.txt``) sets what its schedule times
mean; the inference code was written for Pacific time, so this module sets
the schedule module's zone for the run.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from collections import defaultdict
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from pipeline.common import Row
from pipeline.compare import local_hours
from pipeline.events import infer
from pipeline.events.ontime import EARLY_S, LATE_S, classify
from pipeline.gtfs import schedule
from pipeline.gtfs.tables import rows as gtfs_rows
from pipeline.gtfs.timepoints import ANY_DIRECTION
from pipeline.gtfs.zipread import ZipContents, read_zip
from pipeline.stats import quantile, share

SAMPLE_VERSION = "compare-sample/0.3.0"  # 0.3.0: counts per trip, local weekday and hours
RADII = (30.0, 40.0, 50.0)
BOOTSTRAP = 1000
USER_AGENT = "OpenIslandTransitData-compare/0.1 (+https://github.com/dfladagermccullugh-bot/Open-Island-Transit-Data)"


def vehicle_rows(feeds: list[bytes]) -> list[Row]:
    """Vehicle positions from several fetches, one row per vehicle and
    timestamp, shaped like the parsed vehicles table."""
    from pipeline.parse.realtime import parse_feed

    seen: dict[tuple[str, int], Row] = {}
    for data in feeds:
        try:
            parsed = parse_feed(data)
        except Exception:  # one bad fetch must not end the sample
            continue
        for v in parsed.vehicles:
            key = v.get("vehicle_id") or f"entity:{v.get('entity_id')}"
            ts = v.get("vehicle_ts")
            if ts is None or v.get("lat") is None:
                continue
            seen.setdefault((key, ts), {
                "vehicle_key": key, "trip_id": v.get("trip_id"),
                "vehicle_ts": datetime.fromtimestamp(ts, UTC), "lat": v["lat"], "lon": v["lon"],
                "bearing": v.get("bearing"), "speed": v.get("speed"),
                "current_stop_sequence": v.get("current_stop_sequence"),
                "trip_schedule_relationship": v.get("trip_schedule_relationship"),
            })  # fmt: skip
    return list(seen.values())


def schedule_problem(contents: ZipContents) -> str | None:
    """Why a schedule cannot be used, or None. Checked before sampling, so a
    download that is not a readable schedule ends the job at once instead of
    giving a sample with no trips after two hours."""
    if not gtfs_rows(contents, "trips.txt") or not gtfs_rows(contents, "stop_times.txt"):
        return "the schedule download has no trips (not a readable GTFS zip?)"
    zone = (gtfs_rows(contents, "agency.txt") or [{}])[0].get("agency_timezone")
    if not zone:
        return "the schedule names no time zone in agency.txt"
    try:
        ZoneInfo(zone)
    except Exception:
        return f"unknown time zone {zone!r} in agency.txt"
    return None


def timepoint_keys(contents: ZipContents) -> tuple[set[tuple[str, str, str]], bool]:
    """(route, any direction, stop) of the schedule's timepoints, and whether
    the schedule marks any; if it marks none, every stop."""
    trips = {t["trip_id"]: t["route_id"] for t in gtfs_rows(contents, "trips.txt")}
    marked, every = set(), set()
    for st in gtfs_rows(contents, "stop_times.txt"):
        route = trips.get(st["trip_id"])
        if route is None:
            continue
        key = (route, ANY_DIRECTION, st["stop_id"])
        every.add(key)
        if (st.get("timepoint") or "").strip() == "1":
            marked.add(key)
    return (marked, True) if marked else (every, False)


def deviations(events: list[Row], start: datetime, end: datetime) -> dict[str, list[float]]:
    """Trip id -> deviations (seconds) of timepoint moments scheduled inside
    the sample."""
    out: dict[str, list[float]] = defaultdict(list)
    for e in events:
        if not e["is_timepoint"]:
            continue
        _, dev, scheduled = classify(e)
        if scheduled is None or not (start <= scheduled <= end) or dev is None:
            continue
        out[e["trip_id"]].append(dev)
    return out


def on_time(devs: list[float]) -> float | None:
    return share(sum(EARLY_S <= d <= LATE_S for d in devs), len(devs), 4)


def bootstrap(by_trip: dict[str, list[float]], seed: int = 0) -> tuple[float | None, float | None]:
    """95% interval of the on-time share, resampling whole trips."""
    trips = list(by_trip.values())
    if len(trips) < 5:
        return None, None
    rng = random.Random(seed)
    shares = []
    for _ in range(BOOTSTRAP):
        pick = [d for _ in trips for d in rng.choice(trips)]
        if pick:
            shares.append(sum(EARLY_S <= d <= LATE_S for d in pick) / len(pick))
    lo, hi = quantile(shares, 0.025), quantile(shares, 0.975)
    return (round(lo, 4) if lo is not None else None, round(hi, 4) if hi is not None else None)


def summarize(contents: ZipContents, vehicles: list[Row], start: datetime, end: datetime,
              agency: str) -> dict[str, Any]:  # fmt: skip
    zone = ZoneInfo(gtfs_rows(contents, "agency.txt")[0]["agency_timezone"])
    day = start.astimezone(zone).date()
    keys, marked = timepoint_keys(contents)
    saved_zone, saved_radius = schedule.PACIFIC, infer.STOP_RADIUS_M
    by_radius: dict[float, dict[str, list[float]]] = {}
    try:
        schedule.PACIFIC = zone  # the agency's own clock for its schedule times
        for r in RADII:
            infer.STOP_RADIUS_M = r
            events, _ = infer_for(day, contents, vehicles, keys)
            by_radius[r] = deviations(events, start, end)
    finally:
        schedule.PACIFIC, infer.STOP_RADIUS_M = saved_zone, saved_radius
    main = by_radius[40.0]
    reported = {v["trip_id"] for v in vehicles if v.get("trip_id")}
    scheduled = {t["trip_id"] for t in gtfs_rows(contents, "trips.txt")}
    devs = [d for ds in main.values() for d in ds]
    lo, hi = bootstrap(main)
    return {
        "sample_version": SAMPLE_VERSION,
        "agency": agency,
        "service_date": day.isoformat(),
        "sample_start": start.isoformat(timespec="seconds"),
        "sample_end": end.isoformat(timespec="seconds"),
        "timepoints": "marked in the schedule"
        if marked
        else "every stop (the schedule marks none)",
        "agency_timezone": zone.key,
        "vehicles_seen": len({v["vehicle_key"] for v in vehicles}),
        # How many trips the buses reported are in the schedule: a low share
        # means the feed and the schedule do not use the same trip ids.
        "trips_reported": len(reported),
        "trips_in_schedule": len(reported & scheduled),
        "position_reports": len(vehicles),
        "trips_measured": len(main),
        "moments_measured": len(devs),
        "moments_on_time": sum(EARLY_S <= d <= LATE_S for d in devs),
        "on_time_share": on_time(devs),
        "on_time_interval_95": [lo, hi],
        "deviation_s_median": quantile(devs, 0.5),
        "by_stop_radius_m": {
            str(int(r)): on_time([d for ds in by_radius[r].values() for d in ds]) for r in RADII
        },
        "local_weekday": start.astimezone(zone).strftime("%A"),
        "local_hours": local_hours(start, end, zone),
        # [moments, on time] of each trip measured, for pooled intervals.
        "by_trip": {
            t: [len(ds), sum(EARLY_S <= d <= LATE_S for d in ds)] for t, ds in sorted(main.items())
        },
    }


def infer_for(
    day: date, contents: ZipContents, vehicles: list[Row], keys: set
) -> tuple[list, list]:
    return infer.infer_day(day, contents, vehicles, keys)


def fetch(url: str) -> bytes:
    import requests

    r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=60)
    r.raise_for_status()
    return r.content


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Run our method on a sample of another agency's feed.")
    ap.add_argument("--agency", required=True)
    ap.add_argument("--rt", required=True, help="GTFS-Realtime vehicle positions URL")
    ap.add_argument("--static", required=True, help="GTFS schedule zip URL")
    ap.add_argument("--minutes", type=float, default=120)
    ap.add_argument("--every", type=float, default=20)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(argv)
    contents = read_zip(fetch(args.static))
    problem = schedule_problem(contents)
    if problem:
        raise SystemExit(f"{args.agency}: {problem}; no sample taken")
    feeds, start = [], datetime.now(UTC)
    stop_at = time.monotonic() + 60 * args.minutes
    while time.monotonic() < stop_at:
        try:
            feeds.append(fetch(args.rt))
        except Exception as exc:  # a failed fetch is a gap, not the end
            print(f"fetch failed: {exc!r}", file=sys.stderr)
        time.sleep(args.every)
    end = datetime.now(UTC)
    out = summarize(contents, vehicle_rows(feeds), start, end, args.agency)
    out["fetches"] = len(feeds)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({k: out[k] for k in ("agency", "trips_measured", "on_time_share")}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
