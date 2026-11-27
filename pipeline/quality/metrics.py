"""Daily feed-quality metrics, as defined in docs/methods.md, section 2.

Inputs for one service day:
  fetches    the fetch log (rows of the raw file without bodies)
  vehicles   the parsed vehicles table (one row per vehicle per run)
  trips      scheduled trips from the pinned Passio static version
  static_ids trip, route and stop ids in that version

Metrics that need inferred stop events or trip shapes are in
``pipeline/quality/stops.py``.
"""

from __future__ import annotations

import itertools
import math
from collections import Counter, defaultdict
from collections.abc import Container, Iterable
from datetime import date, datetime, timedelta
from typing import Any

from pipeline import glitch
from pipeline.archive.days import day_bounds
from pipeline.common import Row, stamp
from pipeline.config import PACIFIC
from pipeline.geo import meters_between
from pipeline.gtfs.schedule import ServiceWindows, Trip
from pipeline.stats import mean, quantile, rounded, share

QUALITY_VERSION = "quality/0.6.1"  # 0.6.1: complete


SLOT_SECONDS = {"vehiclePositions": 20, "tripUpdates": 20, "serviceAlerts": 300}
STALE_SECONDS = 120
GAP_SECONDS = 90
MAX_KMH = 130
WINDOW_MIN = 30  # a trip is observed if a report carries its id this close to its schedule
VEHICLE_FIELDS = (
    "vehicle_id",
    "vehicle_label",
    "vehicle_ts",
    "trip_id",
    "route_id",
    "direction_id",
    "start_date",
    "start_time",
    "trip_schedule_relationship",
    "lat",
    "lon",
    "bearing",
    "speed",
    "odometer",
    "current_stop_sequence",
    "stop_id",
    "current_status",
    "congestion_level",
    "occupancy_status",
    "occupancy_percentage",
)


def _ok(f: Row) -> bool:
    return f.get("http_status") == 200 and not f.get("error_class")


def _hour(moment: datetime) -> int:
    return moment.astimezone(PACIFIC).hour


def distinct_reports(vehicles: list[Row]) -> list[Row]:
    """One row per position report. The vehicles table has one row per run of
    unchanged values, and a run also ends when a fetch fails or the vehicle
    is briefly absent, so the same report (vehicle and vehicle timestamp) can
    appear in several rows. The row first fetched is kept. Rows without a
    vehicle timestamp cannot be told apart and are all kept."""
    first: dict[tuple[str, datetime], Row] = {}
    out: list[Row] = []
    for v in sorted(vehicles, key=lambda r: r["first_fetch_id"]):
        if v["vehicle_ts"] is None:
            out.append(v)
        elif (key := (v["vehicle_key"], v["vehicle_ts"])) not in first:
            first[key] = v
            out.append(v)
    return out


# --- paradata ---------------------------------------------------------------


def _slots(times: list[datetime], step: int, phase: float) -> set[int]:
    """Slot numbers of scheduler ticks. Slots are ``step`` seconds wide and
    centred on the usual tick moment (``phase`` seconds into each step), so
    a tick a little early or late still falls in its own slot."""
    return {math.floor((t.timestamp() - phase) / step + 0.5) for t in times}


def _phase(times: list[datetime], step: int) -> float:
    """The usual moment within each ``step`` at which the scheduler fires:
    the circular mean of the tick times modulo ``step``."""
    if not times:
        return 0.0
    angles = [(t.timestamp() % step) / step * 2 * math.pi for t in times]
    mean = math.atan2(sum(map(math.sin, angles)), sum(map(math.cos, angles)))
    return (mean / (2 * math.pi) * step) % step


def paradata(fetches: list[Row], day: date, collection_start: datetime) -> dict[str, Any]:
    """Collector uptime by feed: the share of scheduler slots in the service
    day that hold a successful fetch. A fetch belongs to the day its slot
    falls in, whichever side of midnight it was logged."""
    start, end = day_bounds(day)
    observed_from = max(start, collection_start)
    out: dict[str, Any] = {"observed_from": observed_from.isoformat()}
    for feed, step in SLOT_SECONDS.items():
        mine = [f for f in fetches if f["feed"] == feed and f["scheduled_at"]]
        phase = _phase([f["scheduled_at"] for f in mine], step)
        lo = math.ceil((observed_from.timestamp() - phase) / step)
        hi = math.ceil((end.timestamp() - phase) / step)  # slots lo .. hi-1 are centred in the day
        in_day = set(range(lo, hi))
        slots = _slots([f["scheduled_at"] for f in mine], step, phase) & in_day
        ok_slots = _slots([f["scheduled_at"] for f in mine if _ok(f)], step, phase) & in_day
        latency = [f["latency_ms"] for f in mine if f.get("latency_ms") is not None]
        expected = len(in_day)
        out[feed] = {
            "slots_expected": expected,
            "slots_logged": len(slots),
            "slots_ok": len(ok_slots),
            "uptime": share(len(ok_slots), expected),
            "not_logged": expected - len(slots),
            "logged_not_ok": len(slots - ok_slots),
            "tick_phase_s": round(phase, 2),
            "latency_ms_median": rounded(quantile(latency, 0.5)),
            "latency_ms_p95": rounded(quantile(latency, 0.95)),
            "collector_versions": sorted(
                {f["collector_version"] for f in mine if f.get("collector_version")}
            ),
        }
    return out


def refresh(runs: Iterable[Row], poll_s: int = SLOT_SECONDS["tripUpdates"]) -> dict[str, Any]:
    """How often the feed's trip updates really change, from the runs of the
    trip-updates table (one row per trip and stretch of fetches in which
    nothing about its update changed).

    A *pair* is two consecutive fetches that both listed a trip. The pair
    *changed* when the trip's update differed between them, that is, when one
    run ends and the next begins at the following fetch. A trip that drops
    out of the feed and comes back later is not a pair. The interval is the
    time between the feed's own timestamps of two successive updates of a
    trip. (The response body cannot be used for this: its header timestamp
    is the request time, so every body differs.)"""
    by_trip: dict[str, list[Row]] = defaultdict(list)
    for r in runs:
        by_trip[r["trip_key"]].append(r)
    repeats = changed = 0
    intervals: list[float] = []
    for mine in by_trip.values():
        mine.sort(key=lambda r: r["first_requested_at"])
        repeats += sum(r["times_seen"] - 1 for r in mine)
        for a, b in itertools.pairwise(mine):
            gap = (b["first_requested_at"] - a["last_requested_at"]).total_seconds()
            if gap > 1.5 * poll_s:
                continue
            changed += 1
            if a["trip_update_ts"] and b["trip_update_ts"]:
                step = (b["trip_update_ts"] - a["trip_update_ts"]).total_seconds()
                if step > 0:
                    intervals.append(step)
    pairs = repeats + changed
    return {
        "pairs": pairs,
        "changed": changed,
        "changed_share": share(changed, pairs),
        "update_interval_s_median": rounded(quantile(intervals, 0.5)),
        "update_interval_s_p90": rounded(quantile(intervals, 0.9)),
        "poll_interval_s": poll_s,
    }


# --- nonresponse --------------------------------------------------------------


def fetch_nonresponse(fetches: list[Row]) -> dict[str, Any]:
    out = {}
    for feed in SLOT_SECONDS:
        mine = [f for f in fetches if f["feed"] == feed]
        bad = [f for f in mine if not _ok(f)]
        out[feed] = {
            "attempted": len(mine),
            "failed": len(bad),
            "rate": share(len(bad), len(mine)),
            "by_error": dict(Counter(f.get("error_class") or "http" for f in bad)),
        }
    return out


def feed_emptiness(fetches: list[Row], windows: ServiceWindows) -> dict[str, Any]:
    """Empty and stale vehicle-position fetches while service is scheduled,
    overall and by Pacific hour. Both rates are shares of successful fetches
    in service. A fetch is empty if it lists no vehicle, and stale if it
    lists vehicles and the newest vehicle timestamp is more than
    ``STALE_SECONDS`` old; an empty fetch is not also counted as stale."""
    by_hour: dict[int, Counter[str]] = defaultdict(Counter)
    total: Counter[str] = Counter()
    for f in fetches:
        if f["feed"] != "vehiclePositions" or not f.get("requested_at"):
            continue
        h = by_hour[_hour(f["requested_at"])]
        h["attempted"] += 1
        in_service = f["requested_at"] in windows
        if not in_service:
            continue
        for c in (h, total):
            c["in_service"] += 1
            if not _ok(f):
                c["failed_in_service"] += 1
                continue
            c["ok_in_service"] += 1
            if not f.get("entity_count"):
                c["empty_in_service"] += 1
                continue  # empty is counted once, not also as stale
            newest = f.get("newest_entity_ts")
            if newest is None:
                c["no_timestamp_in_service"] += 1
            elif (f["requested_at"] - newest).total_seconds() > STALE_SECONDS:
                c["stale_in_service"] += 1
    return {
        "in_service_fetches": total["in_service"],
        "ok_in_service_fetches": total["ok_in_service"],
        "empty_in_service": total["empty_in_service"],
        "empty_in_service_rate": share(total["empty_in_service"], total["ok_in_service"]),
        "stale_in_service": total["stale_in_service"],
        "stale_in_service_rate": share(total["stale_in_service"], total["ok_in_service"]),
        "no_timestamp_in_service": total["no_timestamp_in_service"],
        "by_hour": {str(k): dict(v) for k, v in sorted(by_hour.items())},
    }


def item_nonresponse(vehicles: list[Row]) -> dict[str, Any]:
    """Share of position reports with each field empty (``vehicles`` should
    be distinct reports)."""
    n = len(vehicles)
    return {
        "position_reports": n,
        "missing_share": {k: share(sum(v[k] is None for v in vehicles), n) for k in VEHICLE_FIELDS},
    }


def _reports_by_vehicle(vehicles: list[Row]) -> dict[str, list[Row]]:
    by: dict[str, list[Row]] = defaultdict(list)
    for v in vehicles:
        by[v["vehicle_key"]].append(v)
    for rows in by.values():
        rows.sort(key=lambda r: r["first_fetch_id"])
    return by


def reports_in_window(
    vehicles: list[Row], trips: list[Trip]
) -> dict[str, dict[str, set[datetime]]]:
    """Scheduled trip id -> vehicle -> vehicle timestamps of the reports that
    carry the trip's id within ``WINDOW_MIN`` minutes of its schedule. A bus
    can show a trip id hours before or after the trip (the same ids run
    every day, and a bus at a terminal shows its next trip), so reports
    outside the window say nothing about whether the trip was tracked."""
    margin = timedelta(minutes=WINDOW_MIN)
    window = {t.trip_id: (t.start - margin, t.end + margin) for t in trips}
    out: dict[str, dict[str, set]] = defaultdict(lambda: defaultdict(set))
    for v in vehicles:
        w = window.get(v["trip_id"])
        if w and v["vehicle_ts"] is not None and w[0] <= v["vehicle_ts"] <= w[1]:
            out[v["trip_id"]][v["vehicle_key"]].add(v["vehicle_ts"])
    return out


def trip_gaps(vehicles: list[Row], trips: list[Trip]) -> dict[str, Any]:
    """For each observed scheduled trip: longest interval between successive
    position reports, and the share of the observed trip spent in gaps over
    GAP_SECONDS. Reports are those of the vehicle with the most reports for
    the trip, inside the trip's window (``reports_in_window``)."""
    longest, gap_share = [], []
    for by_vehicle in reports_in_window(vehicles, trips).values():
        # The vehicle that sent the most reports for the trip.
        most = max(by_vehicle.values(), key=len)
        s = sorted(most)
        if len(s) < 2:
            continue
        steps = [(b - a).total_seconds() for a, b in itertools.pairwise(s)]
        span = (s[-1] - s[0]).total_seconds()
        longest.append(max(steps))
        gap_share.append(sum(x for x in steps if x > GAP_SECONDS) / span if span else 0.0)
    return {
        "trips_with_two_or_more_reports": len(longest),
        "longest_gap_s_median": rounded(quantile(longest, 0.5)),
        "longest_gap_s_p95": rounded(quantile(longest, 0.95)),
        "trips_with_gap_over_90s": sum(x > GAP_SECONDS for x in longest),
        "share_of_trip_time_in_gaps_mean": rounded(mean(gap_share), 4) if gap_share else None,
    }


# --- coverage -----------------------------------------------------------------

TRIP_COLUMNS = (
    "trip_id",
    "route_id",
    "start_hour",
    "scheduled_in_service",
    "observed",
    "canceled",
)


def trip_records(
    trips: list[Trip], seen: set[str], canceled: Container[str] = ()
) -> list[list[Any]]:
    """One row per scheduled trip: Pacific hour of its first departure, how
    many scheduled trips (itself included) are in service at that moment,
    whether any position carried its id within its window, and whether the
    feed said it was canceled. Input to the gap model
    (``pipeline/quality/gaps.py``)."""
    rows = []
    for t in sorted(trips, key=lambda t: (t.start, t.trip_id)):
        busy = sum(o.start <= t.start < o.end or o is t for o in trips)
        rows.append(
            [t.trip_id, t.route_id, _hour(t.start), busy, t.trip_id in seen, t.trip_id in canceled]
        )
    return rows


def coverage(
    vehicles: list[Row],
    trips: list[Trip],
    static_trip_ids: set[str],
    todays_trip_ids: set[str] | None = None,
    reports: list[Row] | None = None,
    canceled: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Trip coverage over ``trips`` (those observable): a trip is observed
    when at least one report carries its id within ``WINDOW_MIN`` minutes of
    its schedule. Over-coverage is counted over distinct position reports
    (``reports``, default ``vehicles``) against every trip scheduled that
    day (``todays_trip_ids``, default ``trips``).

    ``canceled`` (``pipeline.canceled.canceled_trips``) names the trips the
    feed said did not run. ``trip_coverage`` counts them like any other
    trip; ``trip_coverage_excluding_canceled`` takes those that were also
    not observed out of the scheduled count."""
    reports = vehicles if reports is None else reports
    seen = set(reports_in_window(vehicles, trips))
    anytime = {v["trip_id"] for v in vehicles if v["trip_id"] is not None}
    scheduled = {t.trip_id for t in trips}
    canceled = {t: how for t, how in (canceled or {}).items() if t in scheduled}
    not_run = set(canceled) - seen
    by_route: dict[str, Counter[str]] = defaultdict(Counter)
    for t in trips:
        by_route[t.route_id]["scheduled"] += 1
        by_route[t.route_id]["observed"] += t.trip_id in seen
        by_route[t.route_id]["canceled"] += t.trip_id in canceled
    ids = [v["trip_id"] for v in reports]
    no_trip = sum(t is None for t in ids)
    unknown = sum(t is not None and t not in static_trip_ids for t in ids)
    today = todays_trip_ids if todays_trip_ids is not None else scheduled
    off_day = sum(t is not None and t in static_trip_ids and t not in today for t in ids)
    return {
        "scheduled_trips": len(scheduled),
        "observed_scheduled_trips": len(scheduled & seen),
        "trip_coverage": share(len(scheduled & seen), len(scheduled)),
        "canceled_trips": len(canceled),
        "canceled_by": {how: sum(v == how for v in canceled.values()) for how in ("flag", "alert")},
        "canceled_trip_ids": sorted(canceled),
        "canceled_but_observed": len(set(canceled) & seen),
        "trip_coverage_excluding_canceled": share(
            len(scheduled & seen), len(scheduled) - len(not_run)
        ),
        "window_min": WINDOW_MIN,
        "trips_seen_only_outside_window": len((scheduled & anytime) - seen),
        "routes_never_reporting": sorted(r for r, c in by_route.items() if not c["observed"]),
        "by_route": {
            r: {**c, "coverage": share(c["observed"], c["scheduled"])}
            for r, c in sorted(by_route.items())
        },
        "trips": {
            "columns": list(TRIP_COLUMNS),
            "rows": trip_records(trips, seen, canceled),
        },
        "vehicles_seen": len({v["vehicle_key"] for v in vehicles}),
        "over_coverage": {
            "position_reports": len(reports),
            "no_trip_id": no_trip,
            "trip_id_not_in_static": unknown,
            "trip_not_scheduled_today": off_day,
            "share": share(no_trip + unknown + off_day, len(reports)),
        },
    }


# --- measurement error ----------------------------------------------------------


def measurement(vehicles: list[Row]) -> dict[str, Any]:
    """``vehicles`` should be distinct reports, so that a report's age is
    measured at the first fetch that returned it."""
    ages = [
        (v["first_requested_at"] - v["vehicle_ts"]).total_seconds()
        for v in vehicles
        if v["vehicle_ts"] is not None
    ]
    future = sum(a < 0 for a in ages)
    backwards = 0
    jumps = 0
    pairs = 0
    for rows in _reports_by_vehicle(vehicles).values():
        prev = None
        for r in rows:
            if r["vehicle_ts"] is None:
                continue
            if prev is not None:
                dt = (r["vehicle_ts"] - prev["vehicle_ts"]).total_seconds()
                if dt < 0:
                    backwards += 1
                elif dt > 0 and None not in (r["lat"], r["lon"], prev["lat"], prev["lon"]):
                    pairs += 1
                    kmh = meters_between(prev["lat"], prev["lon"], r["lat"], r["lon"]) / dt * 3.6
                    jumps += kmh > MAX_KMH
            prev = r
    return {
        "position_age_s_median": rounded(quantile(ages, 0.5)),
        "position_age_s_p95": rounded(quantile(ages, 0.95)),
        "position_age_definition": "first fetch request time minus vehicle timestamp",
        "timestamps_in_future": future,
        "timestamps_going_backwards": backwards,
        "consecutive_report_pairs": pairs,
        "impossible_jumps_over_130_kmh": jumps,
        "glitches": glitch.flag(vehicles).summary(),
    }


# --- processing error -------------------------------------------------------------


def id_match(vehicles: list[Row], static_ids: dict[str, set[str]]) -> dict[str, Any]:
    out = {}
    for field, ids in (
        ("trip_id", static_ids["trips"]),
        ("route_id", static_ids["routes"]),
        ("stop_id", static_ids["stops"]),
    ):
        present = [v[field] for v in vehicles if v[field] is not None]
        out[field] = {
            "present": len(present),
            "in_static": sum(x in ids for x in present),
            "match_rate": share(sum(x in ids for x in present), len(present)),
        }
    return out


# --- the day ------------------------------------------------------------------------


def day_metrics(
    day: date,
    fetches: Iterable[Row],
    vehicles: Iterable[Row],
    trips: list[Trip],
    static_ids: dict[str, set[str]],
    collection_start: datetime,
    extra: dict[str, Any] | None = None,
    canceled: dict[str, str] | None = None,
) -> dict[str, Any]:
    fetches = list(fetches)
    vehicles = list(vehicles)
    reports = distinct_reports(vehicles)
    # Only trips that had not ended before collection began can be observed.
    observed_from = max(day_bounds(day)[0], collection_start)
    all_trips, trips = trips, [t for t in trips if t.end >= observed_from]
    windows = ServiceWindows(trips)
    return {
        "service_date": day.isoformat(),
        "quality_version": QUALITY_VERSION,
        "computed_at": stamp(),
        "scheduled_service": {
            "trips": len(all_trips),
            "trips_ended_before_collection": len(all_trips) - len(trips),
            "first_departure": min((t.start for t in trips), default=None),
            "last_arrival": max((t.end for t in trips), default=None),
            "scheduled_hours": round(
                sum(
                    (e - s for s, e in zip(windows.starts, windows.ends, strict=True)), timedelta()
                ).total_seconds()
                / 3600,
                2,
            ),
        },
        "paradata": paradata(fetches, day, collection_start),
        "nonresponse": {
            "fetch": fetch_nonresponse(fetches),
            "vehicle_feed": feed_emptiness(fetches, windows),
            "items": item_nonresponse(reports),
            "trip_gaps": trip_gaps(vehicles, trips),
        },
        "coverage": coverage(
            vehicles, trips, static_ids["trips"], {t.trip_id for t in all_trips}, reports, canceled
        ),
        "measurement": measurement(reports),
        "processing": {
            "id_match": id_match(reports, static_ids),
            "vehicle_table_rows": len(vehicles),
            "distinct_position_reports": len(reports),
            **(extra or {}),
        },
    }
