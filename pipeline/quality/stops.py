"""Quality metrics that need inferred stop events (docs/methods.md, section 2).

Inputs for one service day:
  events        rows of ``stop-events-<date>.parquet``
  tracks        rows of ``trip-tracks-<date>.parquet``
  predictions   rows of ``trip-update-stops-<date>.parquet``
  vehicles      the parsed vehicles table
  terminals     (lat, lon) of every first and last stop of the day's trips

Stop events are experimental (see docs/methods.md, section 5), so these
metrics inherit that label.
"""

from __future__ import annotations

import bisect
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from typing import Any

from pipeline.config import PACIFIC
from pipeline.events.ontime import classify
from pipeline.quality.metrics import _metres, _quantile, _r, _share

Row = dict[str, Any]

HORIZONS_MIN = (1, 2, 5, 10, 15, 30)
PREDICTION_STALE_S = 30  # a prediction counts until this long after its last fetch
WITHIN_S = 60
STATIONARY_MS = 0.5
TERMINAL_M = 150.0
DWELL_GAP_S = 120.0
DWELL_MIN_REPORTS = 3


# --- stop-level coverage ----------------------------------------------------------


def _not_observed_reason(e: Row) -> str:
    status = e["arrival_status"] if e["is_last_stop"] else e["departure_status"]
    return status or "unknown"


def stop_coverage(events: list[Row], collection_start: datetime) -> dict[str, Any]:
    """Scheduled stop events with an inferred time, divided by scheduled stop
    events: at true timepoints and at every stop. The measured time is the
    departure, or the arrival at a trip's last stop (as for on-time figures)."""

    def block(rows: list[Row]) -> dict[str, Any]:
        counts: Counter[str] = Counter()
        reasons: Counter[str] = Counter()
        by_route: dict[str, Counter[str]] = defaultdict(Counter)
        for e in rows:
            cat, _, _ = classify(e)
            seen = cat != "not_observed"
            counts["scheduled"] += 1
            counts["observed"] += seen
            by_route[e["route_id"]]["scheduled"] += 1
            by_route[e["route_id"]]["observed"] += seen
            if not seen:
                reasons[_not_observed_reason(e)] += 1
        return {
            "scheduled": counts["scheduled"],
            "observed": counts["observed"],
            "coverage": _share(counts["observed"], counts["scheduled"]),
            "not_observed_by_reason": dict(sorted(reasons.items())),
            "by_route": {
                r: {**c, "coverage": _share(c["observed"], c["scheduled"])}
                for r, c in sorted(by_route.items())
            },
        }

    def when(e: Row) -> datetime | None:
        return e["scheduled_arrival"] if e["is_last_stop"] else e["scheduled_departure"]

    rows = [e for e in events if (t := when(e)) is not None and t >= collection_start]
    return {
        "timepoints": block([e for e in rows if e["is_timepoint"]]),
        "all_stops": block(rows),
    }


# --- shape fit and assignment -----------------------------------------------------


def shape_fit(tracks: list[Row]) -> dict[str, Any]:
    """Position reports in each trip's scheduled window that lie more than
    100 m from the trip's shape, or move against it."""
    by_route: dict[str, Counter[str]] = defaultdict(Counter)
    total: Counter[str] = Counter()
    for t in tracks:
        for c in (total, by_route[t["route_id"]]):
            c["position_reports"] += t["position_reports"]
            c["off_shape"] += t["off_shape_reports"]
            c["wrong_direction"] += t["wrong_direction_reports"]

    def block(c: Counter[str]) -> dict[str, Any]:
        return {
            "position_reports": c["position_reports"],
            "off_shape": c["off_shape"],
            "off_shape_share": _share(c["off_shape"], c["position_reports"]),
            "wrong_direction": c["wrong_direction"],
            "wrong_direction_share": _share(c["wrong_direction"], c["position_reports"]),
        }

    return {
        **block(total),
        "threshold_m": 100,
        "by_route": {r: block(c) for r, c in sorted(by_route.items())},
    }


def assignment(
    vehicles: list[Row], tracks: list[Row], static_trip_ids: set[str], todays_trip_ids: set[str]
) -> dict[str, Any]:
    """Observed trip ids that cannot be tied to a scheduled trip that day, and
    scheduled trips whose reports could not be used."""
    seen = {v["trip_id"] for v in vehicles if v["trip_id"] is not None}
    not_in_static = seen - static_trip_ids
    not_today = (seen & static_trip_ids) - todays_trip_ids
    with_reports = [t for t in tracks if t["position_reports"] > 0]
    return {
        "observed_trip_ids": len(seen),
        "unassignable": len(not_in_static) + len(not_today),
        "unassignable_share": _share(len(not_in_static) + len(not_today), len(seen)),
        "trip_id_not_in_static": sorted(not_in_static),
        "trip_not_scheduled_today": sorted(not_today),
        "scheduled_trips_with_reports": len(with_reports),
        "trips_with_no_usable_report": sum(t["reports_used"] == 0 for t in with_reports),
        "trips_with_more_than_one_vehicle": sum(t["vehicles_with_trip_id"] > 1 for t in tracks),
    }


# --- prediction error ---------------------------------------------------------------


def prediction_error(predictions: list[Row], events: list[Row]) -> dict[str, Any]:
    """Predicted minus inferred actual time, by how far ahead the prediction
    was made. Arrival everywhere except a trip's first stop, where it is the
    departure. A positive error means the bus came earlier than predicted.

    The prediction ``h`` minutes ahead is the one being published at the
    actual time minus ``h``: the latest run first fetched at or before then,
    if it was still being fetched within ``PREDICTION_STALE_S``.

    The feed sends no ``start_date``, and some predictions are clock times on
    another Pacific day than the trip's scheduled time (the same trip id runs
    every day). Those are counted as ``other_day`` and left out of the error
    figures."""
    runs: dict[tuple[str, int], list[Row]] = defaultdict(list)
    for p in predictions:
        if p["trip_id"] is not None and p["stop_sequence"] is not None:
            runs[(p["trip_id"], p["stop_sequence"])].append(p)
    for rs in runs.values():
        rs.sort(key=lambda r: r["first_requested_at"])
    starts = {k: [r["first_requested_at"] for r in rs] for k, rs in runs.items()}

    errors: dict[int, list[float]] = {h: [] for h in HORIZONS_MIN}
    other_day: Counter[int] = Counter()
    matched = 0
    for e in events:
        first = e["is_first_stop"]
        actual = e["departure_time"] if first else e["arrival_time"]
        key = (e["trip_id"], e["stop_sequence"])
        if actual is None or key not in runs:
            continue
        matched += 1
        sched = e["scheduled_departure"] if first else e["scheduled_arrival"]
        service_day = (sched or actual).astimezone(PACIFIC).date()
        for h in HORIZONS_MIN:
            at = actual - timedelta(minutes=h)
            i = bisect.bisect_right(starts[key], at) - 1
            if i < 0:
                continue
            run = runs[key][i]
            if (at - run["last_requested_at"]).total_seconds() > PREDICTION_STALE_S:
                continue
            predicted = run["departure_time"] if first else run["arrival_time"]
            if predicted is None:
                predicted = run["arrival_time"] if first else run["departure_time"]
            if predicted is None:
                continue
            if predicted.astimezone(PACIFIC).date() != service_day:
                other_day[h] += 1
                continue
            errors[h].append((predicted - actual).total_seconds())

    def block(xs: list[float], h: int) -> dict[str, Any]:
        return {
            "n": len(xs),
            "other_day": other_day[h],
            "other_day_share": _share(other_day[h], len(xs) + other_day[h]),
            "bias_s": _r(statistics.fmean(xs)) if xs else None,
            "median_s": _r(_quantile(xs, 0.5)),
            "mae_s": _r(statistics.fmean(abs(x) for x in xs)) if xs else None,
            "within_60s_share": _share(sum(abs(x) <= WITHIN_S for x in xs), len(xs)),
        }

    return {
        "observed_events_with_predictions": matched,
        "definition": "predicted minus inferred time; arrival, or departure at a trip's first stop",
        "by_horizon_min": {str(h): block(errors[h], h) for h in HORIZONS_MIN},
    }


# --- jitter -----------------------------------------------------------------------


def jitter(vehicles: list[Row], terminals: list[tuple[float, float]]) -> dict[str, Any]:
    """Spread of reported positions while a bus stands at a terminal.

    A dwell is a run of at least ``DWELL_MIN_REPORTS`` consecutive reports
    from one vehicle, each with speed under ``STATIONARY_MS`` and within
    ``TERMINAL_M`` of a trip's first or last stop, no more than
    ``DWELL_GAP_S`` apart. Jitter is each report's distance from the dwell's
    median position."""

    def near_terminal(lat: float, lon: float) -> bool:
        return any(
            abs(lat - a) < 0.002 and abs(lon - b) < 0.003 and _metres(lat, lon, a, b) <= TERMINAL_M
            for a, b in terminals
        )

    by_vehicle: dict[str, list[Row]] = defaultdict(list)
    for v in vehicles:
        if v["vehicle_ts"] is not None and v["lat"] is not None and v["speed"] is not None:
            by_vehicle[v["vehicle_key"]].append(v)

    dwells: list[list[Row]] = []
    for rows in by_vehicle.values():
        rows.sort(key=lambda r: r["vehicle_ts"])
        cur: list[Row] = []
        for r in rows:
            still = r["speed"] < STATIONARY_MS and near_terminal(r["lat"], r["lon"])
            if (
                still
                and cur
                and (r["vehicle_ts"] - cur[-1]["vehicle_ts"]).total_seconds() <= (DWELL_GAP_S)
            ):
                cur.append(r)
                continue
            if len(cur) >= DWELL_MIN_REPORTS:
                dwells.append(cur)
            cur = [r] if still else []
        if len(cur) >= DWELL_MIN_REPORTS:
            dwells.append(cur)

    spread: list[float] = []
    for d in dwells:
        lat = statistics.median(r["lat"] for r in d)
        lon = statistics.median(r["lon"] for r in d)
        spread += [_metres(lat, lon, r["lat"], r["lon"]) for r in d]
    return {
        "dwells": len(dwells),
        "reports": len(spread),
        "jitter_m_median": _r(_quantile(spread, 0.5)),
        "jitter_m_p95": _r(_quantile(spread, 0.95)),
        "definition": (
            f"distance from the dwell's median position; speed under {STATIONARY_MS} m/s, "
            f"within {TERMINAL_M:.0f} m of a first or last stop, "
            f"at least {DWELL_MIN_REPORTS} reports"
        ),
    }


# --- vehicles against the previous 30 days -----------------------------------------


def missing_vehicles(today: set[str], previous: dict[str, list[str]]) -> dict[str, Any]:
    """Vehicles seen on any of the previous days given (date -> vehicle ids)
    but not today."""
    before: set[str] = set().union(*previous.values()) if previous else set()
    gone = sorted(before - today)
    return {
        "previous_days_available": len(previous),
        "vehicles_seen_previously": len(before),
        "missing_count": len(gone) if previous else None,
        "missing_today": gone,
        "new_today": sorted(today - before) if previous else [],
    }
