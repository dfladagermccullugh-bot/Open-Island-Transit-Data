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

import math
import statistics
from collections import Counter, defaultdict
from collections.abc import Iterable
from datetime import UTC, date, datetime, timedelta
from typing import Any

from pipeline.archive.days import day_bounds
from pipeline.config import PACIFIC
from pipeline.gtfs.schedule import ServiceWindows, Trip

QUALITY_VERSION = "quality/0.3.0"

Row = dict[str, Any]

SLOT_SECONDS = {"vehiclePositions": 20, "tripUpdates": 20, "serviceAlerts": 300}
STALE_SECONDS = 120
GAP_SECONDS = 90
MAX_KMH = 130
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


def _quantile(values: list[float], q: float) -> float | None:
    """Linear-interpolation quantile (same as numpy's default)."""
    if not values:
        return None
    v = sorted(values)
    pos = (len(v) - 1) * q
    lo, hi = math.floor(pos), math.ceil(pos)
    return v[lo] + (v[hi] - v[lo]) * (pos - lo)


def _share(num: int, den: int) -> float | None:
    return round(num / den, 6) if den else None


def _r(x: float | None, nd: int = 1) -> float | None:
    return round(x, nd) if x is not None else None


def _hour(moment: datetime) -> int:
    return moment.astimezone(PACIFIC).hour


def _metres(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6_371_000 * math.asin(math.sqrt(a))


# --- paradata ---------------------------------------------------------------


def paradata(fetches: list[Row], day: date, collection_start: datetime) -> dict[str, Any]:
    start, end = day_bounds(day)
    observed_from = max(start, collection_start)
    out: dict[str, Any] = {"observed_from": observed_from.isoformat()}
    for feed, step in SLOT_SECONDS.items():
        mine = [f for f in fetches if f["feed"] == feed]
        expected = max(0, math.ceil((end - observed_from).total_seconds() / step))
        slots = {int(f["scheduled_at"].timestamp()) // step for f in mine if f["scheduled_at"]}
        ok_slots = {
            int(f["scheduled_at"].timestamp()) // step for f in mine if f["scheduled_at"] and _ok(f)
        }
        latency = [f["latency_ms"] for f in mine if f.get("latency_ms") is not None]
        out[feed] = {
            "slots_expected": expected,
            "slots_logged": len(slots),
            "slots_ok": len(ok_slots),
            "uptime": _share(len(ok_slots), expected),
            "not_logged": max(0, expected - len(slots)),
            "logged_not_ok": len(slots - ok_slots),
            "latency_ms_median": _r(_quantile(latency, 0.5)),
            "latency_ms_p95": _r(_quantile(latency, 0.95)),
            "collector_versions": sorted(
                {f["collector_version"] for f in mine if f.get("collector_version")}
            ),
        }
    return out


# --- nonresponse --------------------------------------------------------------


def fetch_nonresponse(fetches: list[Row]) -> dict[str, Any]:
    out = {}
    for feed in SLOT_SECONDS:
        mine = [f for f in fetches if f["feed"] == feed]
        bad = [f for f in mine if not _ok(f)]
        out[feed] = {
            "attempted": len(mine),
            "failed": len(bad),
            "rate": _share(len(bad), len(mine)),
            "by_error": dict(Counter(f.get("error_class") or "http" for f in bad)),
        }
    return out


def feed_emptiness(fetches: list[Row], windows: ServiceWindows) -> dict[str, Any]:
    """Empty and stale vehicle-position fetches while service is scheduled,
    overall and by Pacific hour."""
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
            newest = f.get("newest_entity_ts")
            if newest is None or (f["requested_at"] - newest).total_seconds() > STALE_SECONDS:
                c["stale_in_service"] += 1
    return {
        "in_service_fetches": total["in_service"],
        "ok_in_service_fetches": total["ok_in_service"],
        "empty_in_service": total["empty_in_service"],
        "empty_in_service_rate": _share(total["empty_in_service"], total["ok_in_service"]),
        "stale_in_service": total["stale_in_service"],
        "stale_in_service_rate": _share(total["stale_in_service"], total["ok_in_service"]),
        "by_hour": {str(k): dict(v) for k, v in sorted(by_hour.items())},
    }


def item_nonresponse(vehicles: list[Row]) -> dict[str, Any]:
    n = len(vehicles)
    return {
        "position_reports": n,
        "missing_share": {
            k: _share(sum(v[k] is None for v in vehicles), n) for k in VEHICLE_FIELDS
        },
    }


def _reports_by_vehicle(vehicles: list[Row]) -> dict[str, list[Row]]:
    by: dict[str, list[Row]] = defaultdict(list)
    for v in vehicles:
        by[v["vehicle_key"]].append(v)
    for rows in by.values():
        rows.sort(key=lambda r: r["first_fetch_id"])
    return by


def trip_gaps(vehicles: list[Row]) -> dict[str, Any]:
    """For each observed trip: longest interval between successive new
    position reports, and the share of the observed trip spent in gaps over
    GAP_SECONDS."""
    by_trip: dict[str, set[datetime]] = defaultdict(set)
    for v in vehicles:
        if v["trip_id"] is not None and v["vehicle_ts"] is not None:
            by_trip[v["trip_id"]].add(v["vehicle_ts"])
    longest, gap_share = [], []
    for stamps in by_trip.values():
        s = sorted(stamps)
        if len(s) < 2:
            continue
        steps = [(b - a).total_seconds() for a, b in zip(s, s[1:], strict=False)]
        span = (s[-1] - s[0]).total_seconds()
        longest.append(max(steps))
        gap_share.append(sum(x for x in steps if x > GAP_SECONDS) / span if span else 0.0)
    return {
        "trips_with_two_or_more_reports": len(longest),
        "longest_gap_s_median": _r(_quantile(longest, 0.5)),
        "longest_gap_s_p95": _r(_quantile(longest, 0.95)),
        "trips_with_gap_over_90s": sum(x > GAP_SECONDS for x in longest),
        "share_of_trip_time_in_gaps_mean": _r(statistics.fmean(gap_share), 4)
        if gap_share
        else None,
    }


# --- coverage -----------------------------------------------------------------

TRIP_COLUMNS = ("trip_id", "route_id", "start_hour", "scheduled_in_service", "observed")


def trip_records(trips: list[Trip], seen: set[str]) -> list[list[Any]]:
    """One row per scheduled trip: Pacific hour of its first departure, how
    many scheduled trips (itself included) are in service at that moment,
    and whether any position carried its id. Input to the gap model
    (``pipeline/quality/gaps.py``)."""
    rows = []
    for t in sorted(trips, key=lambda t: (t.start, t.trip_id)):
        busy = sum(o.start <= t.start < o.end or o is t for o in trips)
        rows.append([t.trip_id, t.route_id, _hour(t.start), busy, t.trip_id in seen])
    return rows


def coverage(
    vehicles: list[Row],
    trips: list[Trip],
    static_trip_ids: set[str],
    todays_trip_ids: set[str] | None = None,
) -> dict[str, Any]:
    """Trip coverage over ``trips`` (those observable); over-coverage against
    every trip scheduled that day (``todays_trip_ids``, default ``trips``)."""
    seen = {v["trip_id"] for v in vehicles if v["trip_id"] is not None}
    scheduled = {t.trip_id for t in trips}
    by_route: dict[str, Counter[str]] = defaultdict(Counter)
    for t in trips:
        by_route[t.route_id]["scheduled"] += 1
        by_route[t.route_id]["observed"] += t.trip_id in seen
    ids = [v["trip_id"] for v in vehicles]
    no_trip = sum(t is None for t in ids)
    unknown = sum(t is not None and t not in static_trip_ids for t in ids)
    today = todays_trip_ids if todays_trip_ids is not None else scheduled
    off_day = sum(t is not None and t in static_trip_ids and t not in today for t in ids)
    return {
        "scheduled_trips": len(scheduled),
        "observed_scheduled_trips": len(scheduled & seen),
        "trip_coverage": _share(len(scheduled & seen), len(scheduled)),
        "routes_never_reporting": sorted(r for r, c in by_route.items() if not c["observed"]),
        "by_route": {
            r: {**c, "coverage": _share(c["observed"], c["scheduled"])}
            for r, c in sorted(by_route.items())
        },
        "trips": {
            "columns": list(TRIP_COLUMNS),
            "rows": trip_records(trips, seen),
        },
        "vehicles_seen": len({v["vehicle_key"] for v in vehicles}),
        "over_coverage": {
            "position_reports": len(vehicles),
            "no_trip_id": no_trip,
            "trip_id_not_in_static": unknown,
            "trip_not_scheduled_today": off_day,
            "share": _share(no_trip + unknown + off_day, len(vehicles)),
        },
    }


# --- measurement error ----------------------------------------------------------


def measurement(vehicles: list[Row]) -> dict[str, Any]:
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
                    kmh = _metres(prev["lat"], prev["lon"], r["lat"], r["lon"]) / dt * 3.6
                    jumps += kmh > MAX_KMH
            prev = r
    return {
        "position_age_s_median": _r(_quantile(ages, 0.5)),
        "position_age_s_p95": _r(_quantile(ages, 0.95)),
        "position_age_definition": "first fetch request time minus vehicle timestamp",
        "timestamps_in_future": future,
        "timestamps_going_backwards": backwards,
        "consecutive_report_pairs": pairs,
        "impossible_jumps_over_130_kmh": jumps,
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
            "match_rate": _share(sum(x in ids for x in present), len(present)),
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
) -> dict[str, Any]:
    fetches = list(fetches)
    vehicles = list(vehicles)
    # Only trips that had not ended before collection began can be observed.
    observed_from = max(day_bounds(day)[0], collection_start)
    all_trips, trips = trips, [t for t in trips if t.end >= observed_from]
    windows = ServiceWindows(trips)
    return {
        "service_date": day.isoformat(),
        "quality_version": QUALITY_VERSION,
        "computed_at": datetime.now(UTC).isoformat(timespec="seconds"),
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
            "items": item_nonresponse(vehicles),
            "trip_gaps": trip_gaps(vehicles),
        },
        "coverage": coverage(vehicles, trips, static_ids["trips"], {t.trip_id for t in all_trips}),
        "measurement": measurement(vehicles),
        "processing": {"id_match": id_match(vehicles, static_ids), **(extra or {})},
    }
