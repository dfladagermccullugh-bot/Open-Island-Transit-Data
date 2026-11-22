"""Crosswalk from the Passio static GTFS to the Trillium static GTFS.

The real-time feed uses Passio's ids, and Passio marks every stop as a
timepoint with interpolated times. Trillium marks timepoints but uses
different ids. This module links the two:

Stops
    By ``stop_code`` when both feeds have it and the two stops are within
    ``CODE_MAX_M`` metres (the nearest one if several share the code);
    otherwise the nearest Trillium stop within ``DISTANCE_MAX_M`` metres;
    otherwise unmatched. The nearest candidate and its distance are kept
    either way, so every decision can be checked.

Routes
    By ``route_short_name``. Routes with no short name are matched when each
    feed has routes without one and Trillium has exactly one.

Trillium timepoints
    The two feeds do not carry the same schedule (trip counts and times
    differ), so timepoints cannot be matched trip by trip. A Passio stop is a
    Trillium timepoint on a route when Trillium marks the matched stop as a
    timepoint on at least one trip of the matched route. The counts behind
    that decision are kept. Trillium's schedule is out of date, so these
    flags are kept for comparison only; the timepoints used for on-time
    figures come from the printed timetables (``pipeline/gtfs/timepoints.py``).

Schedule agreement
    For every Passio stop time at a Trillium timepoint, the nearest Trillium
    timepoint time at the same stop and route, on a day of the week both
    serve. This measures how far the two published schedules agree; it is
    not used to choose timepoints.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from typing import Any

from pipeline.common import Row
from pipeline.gtfs.schedule import DAYS
from pipeline.gtfs.tables import rows as _rows
from pipeline.gtfs.tables import seconds as _seconds
from pipeline.gtfs.zipread import ZipContents

CODE_MAX_M = 150.0
DISTANCE_MAX_M = 60.0
CROSSWALK_VERSION = "crosswalk/0.2.0"  # 0.2.0: true and false in lowercase in the CSV files


Tables = dict[str, list[Row]]


NEEDED = ("stops", "routes", "trips", "stop_times", "calendar")


def tables(contents: ZipContents) -> Tables:
    out: Tables = {}
    for name in NEEDED:
        out[name] = _rows(contents, f"{name}.txt")
    return out


def metres(a: Row, b: Row) -> float:
    """Equirectangular distance between two stops; accurate to well under a
    metre at the distances that matter here."""
    lat1, lon1, lat2, lon2 = map(
        math.radians,
        (float(a["stop_lat"]), float(a["stop_lon"]), float(b["stop_lat"]), float(b["stop_lon"])),
    )
    x = (lon2 - lon1) * math.cos((lat1 + lat2) / 2)
    return 6_371_000 * math.hypot(x, lat2 - lat1)


def _boardable(stops: list[Row]) -> list[Row]:
    return [s for s in stops if s.get("location_type", "") in ("", "0") and s.get("stop_lat")]


def stop_crosswalk(passio: list[Row], trillium: list[Row]) -> list[Row]:
    trillium = _boardable(trillium)
    by_code: dict[str, list[Row]] = defaultdict(list)
    for t in trillium:
        if t.get("stop_code"):
            by_code[t["stop_code"]].append(t)
    out = []
    for p in _boardable(passio):
        nearest = min(trillium, key=lambda t: metres(p, t))
        match, method = None, None
        code = p.get("stop_code") or ""
        if code and by_code.get(code):
            cand = min(by_code[code], key=lambda t: metres(p, t))
            if metres(p, cand) <= CODE_MAX_M:
                match, method = cand, "stop_code"
        if match is None and metres(p, nearest) <= DISTANCE_MAX_M:
            match, method = nearest, "distance"
        shown = match or nearest
        out.append(
            {
                "passio_stop_id": p["stop_id"],
                "passio_stop_code": code,
                "passio_stop_name": p.get("stop_name", ""),
                "trillium_stop_id": match["stop_id"] if match else "",
                "trillium_stop_code": match.get("stop_code", "") if match else "",
                "trillium_stop_name": match.get("stop_name", "") if match else "",
                "method": method or "",
                "distance_m": round(metres(p, shown), 1),
                "nearest_trillium_stop_id": nearest["stop_id"],
                "nearest_trillium_stop_name": nearest.get("stop_name", ""),
                "nearest_distance_m": round(metres(p, nearest), 1),
            }
        )
    return out


def route_crosswalk(passio: list[Row], trillium: list[Row]) -> list[Row]:
    t_by_name: dict[str, list[Row]] = defaultdict(list)
    for t in trillium:
        t_by_name[t.get("route_short_name", "").strip()].append(t)
    out = []
    for p in passio:
        name = p.get("route_short_name", "").strip()
        cands = t_by_name.get(name, [])
        match = cands[0] if len(cands) == 1 else None
        method = ("route_short_name" if name else "no short name in either feed") if match else ""
        out.append(
            {
                "passio_route_id": p["route_id"],
                "passio_route_short_name": name,
                "passio_route_long_name": p.get("route_long_name", ""),
                "trillium_route_id": match["route_id"] if match else "",
                "trillium_route_long_name": match.get("route_long_name", "") if match else "",
                "method": method,
            }
        )
    return out


def _weekdays(calendar: list[Row]) -> dict[str, set[int]]:
    return {c["service_id"]: {i for i, d in enumerate(DAYS) if c.get(d) == "1"} for c in calendar}


def timepoints(
    p: Tables, t: Tables, stops: list[Row], routes: list[Row]
) -> tuple[list[Row], list[Row]]:
    """Return (timepoint_stops, agreement) rows. See module docstring."""
    stop_map = {r["passio_stop_id"]: r["trillium_stop_id"] for r in stops if r["trillium_stop_id"]}
    route_map = {
        r["passio_route_id"]: r["trillium_route_id"] for r in routes if r["trillium_route_id"]
    }
    t_trips = {r["trip_id"]: r for r in t["trips"]}
    p_trips = {r["trip_id"]: r for r in p["trips"]}
    t_days = _weekdays(t["calendar"])
    p_days = _weekdays(p["calendar"])

    # Trillium stop times by (route, stop): counts and timepoint departures.
    events: Counter[tuple[str, str]] = Counter()
    tp_times: dict[tuple[str, str], list[tuple[int, set[int]]]] = defaultdict(list)
    for st in t["stop_times"]:
        trip = t_trips.get(st["trip_id"])
        if trip is None:
            continue
        key = (trip["route_id"], st["stop_id"])
        events[key] += 1
        when = _seconds(st.get("departure_time") or st.get("arrival_time", ""))
        if st.get("timepoint") == "1" and when is not None:
            tp_times[key].append((when, t_days.get(trip["service_id"], set(range(7)))))

    # Passio stop times grouped by (route, stop).
    p_events: dict[tuple[str, str], list[Row]] = defaultdict(list)
    for st in p["stop_times"]:
        trip = p_trips.get(st["trip_id"])
        if trip is not None:
            p_events[(trip["route_id"], st["stop_id"])].append({**st, "_trip": trip})

    tp_rows, agreement = [], []
    for (p_route, p_stop), sts in sorted(p_events.items()):
        t_route, t_stop = route_map.get(p_route, ""), stop_map.get(p_stop, "")
        key = (t_route, t_stop)
        n_tp = len(tp_times.get(key, []))
        is_tp = bool(t_route and t_stop and n_tp)
        tp_rows.append(
            {
                "passio_route_id": p_route,
                "passio_stop_id": p_stop,
                "trillium_route_id": t_route,
                "trillium_stop_id": t_stop,
                "passio_stop_times": len(sts),
                "trillium_stop_times": events.get(key, 0) if t_route and t_stop else 0,
                "trillium_timepoint_stop_times": n_tp if t_route and t_stop else 0,
                "trillium_timepoint": is_tp,
            }
        )
        if not is_tp:
            continue
        for st in sts:
            when = _seconds(st.get("departure_time") or st.get("arrival_time", ""))
            days = p_days.get(st["_trip"]["service_id"], set(range(7)))
            diffs = [w - when for w, d in tp_times[key] if d & days] if when is not None else []
            nearest = min(diffs, key=abs) if diffs else None
            agreement.append(
                {
                    "passio_trip_id": st["trip_id"],
                    "passio_route_id": p_route,
                    "passio_stop_id": p_stop,
                    "stop_sequence": st.get("stop_sequence", ""),
                    "passio_time": st.get("departure_time") or st.get("arrival_time", ""),
                    "nearest_trillium_minus_passio_s": nearest,
                }
            )
    return tp_rows, agreement


def summarize(
    stops: list[Row], routes: list[Row], tp_rows: list[Row], agreement: list[Row]
) -> dict[str, Any]:
    diffs = [a["nearest_trillium_minus_passio_s"] for a in agreement]
    known = [abs(d) for d in diffs if d is not None]
    n = len(diffs)
    return {
        "stops": {
            "passio": len(stops),
            "by_method": dict(Counter(r["method"] or "unmatched" for r in stops)),
        },
        "routes": {
            "passio": len(routes),
            "unmatched": [r["passio_route_id"] for r in routes if not r["trillium_route_id"]],
        },
        "timepoints": {
            "passio_route_stops": len(tp_rows),
            "trillium_timepoint_route_stops": sum(r["trillium_timepoint"] for r in tp_rows),
            "mixed_route_stops": sum(
                0 < r["trillium_timepoint_stop_times"] < r["trillium_stop_times"] for r in tp_rows
            ),
        },
        "schedule_agreement": {
            "passio_stop_times_at_timepoints": n,
            "same_time": sum(d == 0 for d in known),
            "within_60_s": sum(d <= 60 for d in known),
            "within_300_s": sum(d <= 300 for d in known),
            "no_trillium_time_same_weekday": n - len(known),
        },
    }


def build(passio: ZipContents, trillium: ZipContents) -> dict[str, Any]:
    p, t = tables(passio), tables(trillium)
    stops = stop_crosswalk(p["stops"], t["stops"])
    routes = route_crosswalk(p["routes"], t["routes"])
    tp_rows, agreement = timepoints(p, t, stops, routes)
    return {
        "stops": stops,
        "routes": routes,
        "timepoint_stops": tp_rows,
        "schedule_agreement": agreement,
        "summary": summarize(stops, routes, tp_rows, agreement),
    }
