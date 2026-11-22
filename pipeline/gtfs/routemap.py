"""Route map: each route's path and its printed-timetable stops, for the site.

Built once per timepoints folder (a Passio version plus the printed
timetable in effect) and written to ``maps/<timepoints key>.json`` on the
data branch. Paths come from the Passio ``shapes.txt``: segments drawn
forth, back and forth again are collapsed (as for stop events, see
``pipeline/events/geometry.py``), then each path is simplified to within
``SIMPLIFY_M`` metres and rounded to 5 decimal places (about 1 m).
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

import numpy as np

from pipeline.events.geometry import drop_retraced, to_xy
from pipeline.gtfs.tables import rows as _rows
from pipeline.gtfs.zipread import ZipContents

MAP_VERSION = "map/0.1.0"
SIMPLIFY_M = 5.0

Row = dict[str, str]


def simplify(lats: list[float], lons: list[float], tol_m: float = SIMPLIFY_M) -> list[int]:
    """Indices kept by Douglas-Peucker with tolerance ``tol_m`` metres."""
    n = len(lats)
    if n < 3:
        return list(range(n))
    x, y = to_xy(lats, lons)
    keep = np.zeros(n, dtype=bool)
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    while stack:
        a, b = stack.pop()
        if b - a < 2:
            continue
        dx, dy = x[b] - x[a], y[b] - y[a]
        seg2 = dx * dx + dy * dy
        px, py = x[a + 1 : b] - x[a], y[a + 1 : b] - y[a]
        if seg2 == 0:
            d = np.hypot(px, py)
        else:
            t = np.clip((px * dx + py * dy) / seg2, 0, 1)
            d = np.hypot(px - t * dx, py - t * dy)
        i = int(np.argmax(d))
        if d[i] > tol_m:
            keep[a + 1 + i] = True
            stack += [(a, a + 1 + i), (a + 1 + i, b)]
    return [int(i) for i in np.flatnonzero(keep)]


def _name(route: Row) -> str:
    return route.get("route_short_name") or route.get("route_long_name") or route["route_id"]


def build(passio: ZipContents, timepoints: list[Row]) -> dict[str, Any]:
    routes = {r["route_id"]: r for r in _rows(passio, "routes.txt")}
    trips = _rows(passio, "trips.txt")
    shape_trips: dict[str, Counter] = defaultdict(Counter)
    for t in trips:
        if t.get("shape_id"):
            shape_trips[t["route_id"]][t["shape_id"]] += 1

    points: dict[str, list[tuple[int, float, float]]] = defaultdict(list)
    for p in _rows(passio, "shapes.txt"):
        points[p["shape_id"]].append(
            (int(p["shape_pt_sequence"]), float(p["shape_pt_lat"]), float(p["shape_pt_lon"]))
        )

    out_routes = []
    lat_all: list[float] = []
    lon_all: list[float] = []
    for route_id, counts in sorted(shape_trips.items(), key=lambda kv: _name(routes[kv[0]])):
        r = routes.get(route_id, {"route_id": route_id})
        paths = []
        for shape_id in sorted(counts):
            pts = sorted(points.get(shape_id, []))
            if len(pts) < 2:
                continue
            lats, lons, _ = drop_retraced([p[1] for p in pts], [p[2] for p in pts])
            idx = simplify(lats, lons)
            paths.append([[round(lons[i], 5), round(lats[i], 5)] for i in idx])
            lat_all += lats
            lon_all += lons
        out_routes.append(
            {
                "route_id": route_id,
                "short_name": r.get("route_short_name", ""),
                "long_name": r.get("route_long_name", ""),
                "name": _name(r),
                "color": "#" + (r.get("route_color") or "777777"),
                "trips": sum(counts.values()),
                "paths": paths,
            }
        )

    stops = {s["stop_id"]: s for s in _rows(passio, "stops.txt")}
    tp: dict[str, dict[str, Any]] = {}
    for row in timepoints:
        sid = row.get("passio_stop_id")
        if not sid or sid not in stops:
            continue
        s = stops[sid]
        entry = tp.setdefault(
            sid,
            {
                "stop_id": sid,
                "name": row.get("printed_stop") or s.get("stop_name", ""),
                "lon": round(float(s["stop_lon"]), 5),
                "lat": round(float(s["stop_lat"]), 5),
                "routes": [],
            },
        )
        if row["passio_route_id"] not in entry["routes"]:
            entry["routes"].append(row["passio_route_id"])

    bbox = (
        [
            round(min(lon_all), 4),
            round(min(lat_all), 4),
            round(max(lon_all), 4),
            round(max(lat_all), 4),
        ]
        if lat_all
        else None
    )
    return {
        "map_version": MAP_VERSION,
        "bbox": bbox,
        "routes": out_routes,
        "timepoint_stops": sorted(tp.values(), key=lambda s: s["name"]),
    }
