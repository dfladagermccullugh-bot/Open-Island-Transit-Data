"""Automatic checks of inferred stop events (docs/methods.md, section 5,
checks 2 and 3). They need no field data.

Check 2, agreement with the feed: each vehicle report carries
``current_stop_sequence``, the stop the bus is heading for. When the bus
passes stop k the feed moves on to k + 1. The change happened between the
last report still showing k or less (``t_prev``) and the first showing more
than k (``t_next``). An inferred departure from stop k agrees with the feed
if it lies in [t_prev - TOLERANCE_S, t_next + TOLERANCE_S]. First and last
stops are left out: at the first stop the feed already shows the next stop
while the bus waits, and the last stop has no departure.

Check 3, travel times: consecutive observed times along a trip (departure
from one stop, arrival at the next) must not go backwards, and the speed
between them must not exceed ``MAX_SEGMENT_MS``. Segments across a stop
whose part of the shape the bus left out (status ``skipped``) are not
measured for speed: that distance was not driven.

Trip offsets (``trip_offsets``): a route whose trips mostly run a steady few
minutes off the schedule, which is what a wrong schedule looks like.
"""

from __future__ import annotations

import itertools
from collections import Counter, defaultdict
from datetime import datetime
from functools import partial
from typing import Any

from pipeline.common import Row
from pipeline.stats import quantile, share

VALIDATE_VERSION = "validate/0.3.0"  # 0.3.0: trip offsets
TOLERANCE_S = 30.0
MAX_SEGMENT_MS = 35.0  # 126 km/h
OFFSET_MIN_STOPS = 4
OFFSET_IQR_S = 60.0
OFFSET_MEDIAN_S = 240.0
OFFSET_ROUTE_SHARE = 0.5


def _q(values: list[float], q: float) -> float | None:
    x = quantile(values, q)
    return round(x, 1) if x is not None else None


# Shares in the on-time files are published to four decimal places.
_share = partial(share, digits=4)


def feed_agreement(events: list[Row], vehicles: list[Row]) -> dict[str, Any]:
    series: dict[tuple[str, str], list[tuple[datetime, int]]] = defaultdict(list)
    for v in vehicles:
        if None in (v["trip_id"], v["vehicle_ts"], v["current_stop_sequence"]):
            continue
        series[(v["trip_id"], v["vehicle_key"])].append(
            (v["vehicle_ts"], v["current_stop_sequence"])
        )
    for key, unsorted in series.items():
        series[key] = sorted(set(unsorted))

    lead: list[float] = []  # t_next minus inferred departure
    inside = within = 0
    backwards = 0
    by_route: dict[str, Counter[str]] = defaultdict(Counter)
    for reports in series.values():
        backwards += sum(b[1] < a[1] for a, b in itertools.pairwise(reports))
    for e in events:
        dep = e["departure_time"]
        if dep is None or e["is_first_stop"] or e["is_last_stop"]:
            continue
        s = series.get((e["trip_id"], e["vehicle_key"]))
        if not s:
            continue
        k = e["stop_sequence"]
        nxt = next((i for i, (_, seq) in enumerate(s) if seq > k), None)
        if nxt is None or nxt == 0 or s[nxt - 1][1] > k:
            continue
        t_prev, t_next = s[nxt - 1][0], s[nxt][0]
        lead.append((t_next - dep).total_seconds())
        a = (t_prev - dep).total_seconds()
        b = lead[-1]
        ok_in = a <= 0 <= b
        ok_tol = a - TOLERANCE_S <= 0 <= b + TOLERANCE_S
        inside += ok_in
        within += ok_tol
        c = by_route[e["route_id"]]
        c["compared"] += 1
        c["within_tolerance"] += ok_tol
    n = len(lead)
    return {
        "compared": n,
        "inside_change_interval_share": _share(inside, n),
        "within_tolerance_share": _share(within, n),
        "tolerance_s": TOLERANCE_S,
        "feed_change_after_departure_s": {
            "p5": _q(lead, 0.05),
            "median": _q(lead, 0.5),
            "p95": _q(lead, 0.95),
        },
        "feed_sequence_going_backwards": backwards,
        "by_route": {
            r: {**c, "share": _share(c["within_tolerance"], c["compared"])}
            for r, c in sorted(by_route.items())
        },
    }


def travel_times(events: list[Row]) -> dict[str, Any]:
    by_trip: dict[str, list[Row]] = defaultdict(list)
    for e in events:
        by_trip[e["trip_id"]].append(e)
    segments = negative = fast = across_skip = 0
    examples: list[Row] = []
    worst: float | None = None
    for rows in by_trip.values():
        rows.sort(key=lambda r: r["stop_sequence"])
        # (time, distance, crosses a skipped stretch since the previous time, row, kind)
        times: list[tuple[datetime, float, bool, Row, str]] = []
        skip = False
        for r in rows:
            skip = skip or "skipped" in (r["arrival_status"], r["departure_status"])
            for kind, offset in (("arrival", -40.0), ("departure", 40.0)):
                t = r[f"{kind}_time"]
                if t is not None and r["stop_distance_m"] is not None:
                    times.append((t, r["stop_distance_m"] + offset, skip, r, kind))
                    skip = False
        for (t0, d0, _, r0, k0), (t1, d1, crossed, r1, k1) in itertools.pairwise(times):
            segments += 1
            dt = (t1 - t0).total_seconds()
            if dt < 0:
                negative += 1
            elif crossed:
                across_skip += 1
            elif dt > 0:
                speed = (d1 - d0) / dt
                worst = speed if worst is None else max(worst, speed)
                if speed > MAX_SEGMENT_MS:
                    fast += 1
                    examples.append(
                        {
                            "trip_id": r0["trip_id"],
                            "route_id": r0["route_id"],
                            "from": f"{k0} {r0['stop_sequence']}",
                            "to": f"{k1} {r1['stop_sequence']}",
                            "meters": round(d1 - d0),
                            "seconds": round(dt, 1),
                            "speed_ms": round(speed, 1),
                            "report_gap_s": r1[f"{k1}_gap_s"],
                        }
                    )
    return {
        "segments": segments,
        "negative": negative,
        "over_max_speed": fast,
        "across_skipped_stretch": across_skip,
        "max_speed_ms": round(worst, 1) if worst is not None else None,
        "max_allowed_ms": MAX_SEGMENT_MS,
        "fastest": sorted(examples, key=lambda x: -x["speed_ms"])[:10],
    }


def trip_offsets(events: list[Row]) -> dict[str, Any]:
    """Whole trips running a steady few minutes off the schedule.

    For each trip with at least ``OFFSET_MIN_STOPS`` observed moments (the
    departure, or the arrival at the last stop), the median and the spread
    (interquartile range) of measured minus scheduled time. A trip is
    shifted when its spread is under ``OFFSET_IQR_S`` and its median beyond
    ``OFFSET_MEDIAN_S`` either way: the bus kept to the timetable's pace but
    at another clock time, which is what a wrong schedule looks like (say,
    a day pinned to the wrong Passio version). A route is flagged when at
    least ``OFFSET_ROUTE_SHARE`` of its measured trips are shifted. Since
    ``validate/0.3.0``."""
    devs: dict[str, list[float]] = defaultdict(list)
    route_of: dict[str, str] = {}
    for e in events:
        last = e["is_last_stop"]
        measured = e["arrival_time"] if last else e["departure_time"]
        scheduled = e["scheduled_arrival"] if last else e["scheduled_departure"]
        if measured is None or scheduled is None:
            continue
        devs[e["trip_id"]].append((measured - scheduled).total_seconds())
        route_of[e["trip_id"]] = e["route_id"]
    routes: dict[str, Counter] = defaultdict(Counter)
    shifted_trips: list[dict[str, Any]] = []
    for trip, d in devs.items():
        if len(d) < OFFSET_MIN_STOPS:
            continue
        mid, q1, q3 = quantile(d, 0.5), quantile(d, 0.25), quantile(d, 0.75)
        assert mid is not None and q1 is not None and q3 is not None
        shifted = q3 - q1 < OFFSET_IQR_S and abs(mid) > OFFSET_MEDIAN_S
        routes[route_of[trip]]["trips"] += 1
        routes[route_of[trip]]["shifted"] += shifted
        if shifted:
            shifted_trips.append(
                {"trip_id": trip, "route_id": route_of[trip], "median_s": round(mid, 1),
                 "iqr_s": round(q3 - q1, 1)}
            )  # fmt: skip
    flagged = sorted(
        r
        for r, c in routes.items()
        if c["trips"] and c["shifted"] / c["trips"] >= OFFSET_ROUTE_SHARE
    )
    return {
        "trips_measured": sum(c["trips"] for c in routes.values()),
        "shifted_trips": len(shifted_trips),
        "flagged_routes": flagged,
        "rule": {
            "min_stops": OFFSET_MIN_STOPS,
            "iqr_under_s": OFFSET_IQR_S,
            "median_beyond_s": OFFSET_MEDIAN_S,
            "route_share": OFFSET_ROUTE_SHARE,
        },
        "by_route": {r: dict(c) for r, c in sorted(routes.items())},
        "shifted": sorted(shifted_trips, key=lambda x: -abs(x["median_s"]))[:20],
    }


def validate(events: list[Row], vehicles: list[Row]) -> dict[str, Any]:
    return {
        "validate_version": VALIDATE_VERSION,
        "feed_agreement": feed_agreement(events, vehicles),
        "travel_times": travel_times(events),
        "trip_offsets": trip_offsets(events),
    }
