"""Infer when each bus reached and left each scheduled stop.

Implements docs/methods.md, section 3. For one service day and the Passio
static version pinned to it:

1. Every stop of a trip is placed on the trip's shape, in stop order.
2. The trip's position reports (from the parsed vehicles table) are those
   carrying its ``trip_id`` with a vehicle timestamp within the scheduled
   window +/- ``WINDOW_MIN``. If several vehicles carry the same trip id, the
   one with the most reports is used and the count is kept.
3. Tracking starts at the first report near the start of the shape (a bus
   often shows its next trip id while finishing the previous trip) and stops
   when the bus reaches the end. Each report is projected onto the shape,
   searching only a window ahead of the last matched distance (this handles
   loops and repeated stops). Reports more than ``OFF_SHAPE_M`` from the
   shape, or moving against its direction, are counted and not used.
   Distance along the shape is kept non-decreasing, so GPS jitter cannot
   create a second crossing. If two consecutive reports lie on a later part
   of the shape, heading its way, tracking moves there and the stretch in
   between is recorded as not driven (the published shape can include a
   branch a trip does not take).
4. Arrival at a stop is the moment the bus first comes within
   ``STOP_RADIUS_M`` of it along the shape; departure is the moment it moves
   beyond that distance. Each is interpolated linearly between the two
   reports either side. If those reports are more than ``MAX_GAP_S`` apart
   the event is recorded as not observed. The first stop has a departure
   only; the last stop an arrival only.

Every scheduled stop of every scheduled trip gets a row, observed or not.
"""

from __future__ import annotations

import csv
import io
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

from pipeline.events.geometry import Shape
from pipeline.gtfs.schedule import day_origin, scheduled_trips
from pipeline.gtfs.zipread import ZipContents

EVENTS_VERSION = "events/0.2.1"

WINDOW_MIN = 30
OFF_SHAPE_M = 100.0
STOP_RADIUS_M = 40.0
MAX_GAP_S = 120.0
MAX_SPEED_MS = 35.0  # 126 km/h: the search window ahead of the last position
BACKTRACK_M = 50.0
START_ZONE_M = 300.0  # a report this close to the start of the shape starts tracking
END_ZONE_M = 50.0  # tracking stops this close to the end of the shape
LATE_JOIN_S = 120.0  # without a report near the start, track from 2 min before departure
MOVING_MS = 3.0  # above this speed, the bearing is trusted
WRONG_WAY_DEG = 120.0  # bearing this far from the shape's heading: moving the other way
REANCHOR_M = 30.0  # a report this close to a later part of the shape may re-anchor there
REANCHOR_DEG = 45.0  # ... if the bus's bearing is this close to the shape's heading
STOP_PASS_M = 150.0  # a shape passing within this distance of a stop may serve it
UNPLACED_COST_M = 10_000.0  # cost of leaving a stop at the previous stop's distance

Row = dict[str, Any]


def _rows(contents: ZipContents, name: str) -> list[Row]:
    body = contents.files.get(name, b"")
    return list(csv.DictReader(io.StringIO(body.decode("utf-8-sig", errors="replace"))))


def _seconds(hms: str) -> int | None:
    if not hms:
        return None
    h, m, s = (int(x) for x in hms.strip().split(":"))
    return h * 3600 + m * 60 + s


@dataclass
class StaticData:
    shapes: dict[str, Shape]
    trips: dict[str, Row]
    stop_times: dict[str, list[Row]]  # trip id -> rows in stop order
    stops: dict[str, Row]
    _placed: dict[tuple, list[float]] = field(default_factory=dict)

    @classmethod
    def load(cls, contents: ZipContents) -> StaticData:
        pts: dict[str, list[tuple[int, float, float]]] = defaultdict(list)
        for r in _rows(contents, "shapes.txt"):
            pts[r["shape_id"]].append(
                (int(r["shape_pt_sequence"]), float(r["shape_pt_lat"]), float(r["shape_pt_lon"]))
            )
        shapes = {}
        for sid, p in pts.items():
            p.sort()
            shapes[sid] = Shape.from_latlon([a[1] for a in p], [a[2] for a in p])
        stop_times: dict[str, list[Row]] = defaultdict(list)
        for r in _rows(contents, "stop_times.txt"):
            stop_times[r["trip_id"]].append(r)
        for rows in stop_times.values():
            rows.sort(key=lambda r: int(r["stop_sequence"]))
        return cls(
            shapes=shapes,
            trips={t["trip_id"]: t for t in _rows(contents, "trips.txt")},
            stop_times=dict(stop_times),
            stops={s["stop_id"]: s for s in _rows(contents, "stops.txt")},
        )

    def place_stops(self, trip_id: str) -> list[float] | None:
        """Distance along the shape of each stop of the trip, in order.

        A shape can pass a stop more than once (loops, out-and-back
        segments). Each stop's candidates are every pass within
        ``STOP_PASS_M`` plus the nearest point; the placement keeps stops in
        order along the shape and minimises the sum of their offsets
        (dynamic programming). A stop with no candidate in order is put at
        the previous stop's distance, at a cost of ``UNPLACED_COST_M``."""
        trip = self.trips[trip_id]
        shape = self.shapes.get(trip.get("shape_id", ""))
        if shape is None:
            return None
        ids = tuple(r["stop_id"] for r in self.stop_times.get(trip_id, []))
        key = (trip["shape_id"], ids)
        if key not in self._placed:
            cands = [
                shape.passes(float(self.stops[s]["stop_lat"]), float(self.stops[s]["stop_lon"]),
                             STOP_PASS_M)
                for s in ids
            ]  # fmt: skip
            self._placed[key] = order_stops(cands)
        return self._placed[key]


def order_stops(cands: list[list[tuple[float, float]]]) -> list[float]:
    """Choose one (distance, offset) candidate per stop so that distances
    never decrease and the summed offset is smallest. See ``place_stops``."""
    if not cands:
        return []
    # Each state: (cost, distance, back-pointer index into the previous row).
    rows: list[list[tuple[float, float, int]]] = []
    prev: list[tuple[float, float, int]] = [(0.0, 0.0, -1)]
    for opts in cands:
        row = []
        for along, off in opts:
            best = min(
                ((c + off, k) for k, (c, d, _) in enumerate(prev) if d <= along + 1e-6),
                default=None,
            )
            if best is not None:
                row.append((best[0], along, best[1]))
        # Unplaced: stay at the cheapest previous state's distance.
        k = min(range(len(prev)), key=lambda k: prev[k][0])
        row.append((prev[k][0] + UNPLACED_COST_M, prev[k][1], k))
        rows.append(row)
        prev = row
    j = min(range(len(prev)), key=lambda k: prev[k][0])
    out = []
    for row in reversed(rows):
        out.append(row[j][1])
        j = row[j][2]
    return out[::-1]


@dataclass
class Track:
    """A vehicle's progress along one trip's shape."""

    times: list[float]  # epoch seconds
    dists: list[float]  # non-decreasing metres along the shape
    reports: int = 0
    before_start: int = 0  # dropped: before the bus was seen at the start of the trip
    after_end: int = 0  # dropped: after the bus reached the end of the shape
    off_shape: int = 0  # dropped: more than OFF_SHAPE_M from the shape
    wrong_direction: int = 0  # dropped: moving against the shape's direction
    skips: list[tuple[float, float]] = field(default_factory=list)  # stretches not driven


def _angle(a: float, b: float) -> float:
    return abs((a - b + 180) % 360 - 180)


def track(shape: Shape, reports: list[tuple], scheduled_start: float) -> Track:
    """Follow one vehicle along a trip's shape.

    ``reports``: (epoch seconds, lat, lon, bearing, speed), sorted by time,
    one per timestamp. Tracking starts at the first report within
    ``START_ZONE_M`` of the start of the shape (a bus waiting at the terminal
    often already carries the next trip's id while finishing the previous
    one). If there is none, it starts at the first report from
    ``scheduled_start`` minus ``LATE_JOIN_S``, wherever it is. It ends when
    the bus reaches the end of the shape.

    If the bus leaves out part of the shape (the published shape has a
    branch this trip did not drive), reports stop matching the search
    window. Tracking then moves forward to a later part of the shape when two
    consecutive reports lie on it (see ``_forward_pass``), and records the
    stretch left out in ``skips``; stops in it get no time."""
    tr = Track([], [], reports=len(reports))
    start = next(
        (
            k
            for k, r in enumerate(reports)
            if (hit := shape.project(r[1], r[2], 0.0, START_ZONE_M)) and hit[1] <= OFF_SHAPE_M
        ),
        None,
    )
    if start is None:
        late = scheduled_start - LATE_JOIN_S
        start = next((k for k, r in enumerate(reports) if r[0] >= late), len(reports))
    tr.before_start = start
    pending: tuple[float, float, str] | None = None  # a report that may re-anchor tracking
    for k in range(start, len(reports)):
        ts, lat, lon, bearing, speed = reports[k]
        if tr.dists and tr.dists[-1] >= shape.length - END_ZONE_M:
            tr.after_end = len(reports) - k
            break
        if not tr.dists:
            hit = shape.project(lat, lon)
            # On a loop the start and end coincide; prefer the start.
            if hit and hit[0] > 0.9 * shape.length:
                early = shape.project(lat, lon, 0.0, 0.1 * shape.length)
                if early and early[1] <= OFF_SHAPE_M:
                    hit = early
        else:
            last, dt = tr.dists[-1], max(ts - tr.times[-1], 0.0)
            hit = shape.project(
                lat, lon, max(last - BACKTRACK_M, 0.0), last + BACKTRACK_M + MAX_SPEED_MS * dt
            )
        if hit is None or hit[1] > OFF_SHAPE_M:
            kind = "off_shape"
        elif _against(shape, hit[0], bearing, speed):
            kind = "wrong_direction"
        else:
            pending = None
            tr.times.append(ts)
            tr.dists.append(max(hit[0], tr.dists[-1]) if tr.dists else hit[0])
            continue
        setattr(tr, kind, getattr(tr, kind) + 1)
        if not tr.dists:
            continue
        jump = _forward_pass(shape, lat, lon, bearing, speed, tr.dists[-1])
        if (
            jump is not None
            and pending is not None
            and pending[1] <= jump <= pending[1] + BACKTRACK_M + MAX_SPEED_MS * (ts - pending[0])
        ):
            # Two reports in a row agree: the bus left out part of the shape.
            setattr(tr, pending[2], getattr(tr, pending[2]) - 1)
            setattr(tr, kind, getattr(tr, kind) - 1)
            tr.skips.append((tr.dists[-1], pending[1]))
            tr.times += [pending[0], ts]
            tr.dists += [pending[1], jump]
            pending = None
        else:
            pending = (ts, jump, kind) if jump is not None else None
    return tr


def _against(shape: Shape, along: float, bearing: float | None, speed: float | None) -> bool:
    """Moving against the shape's direction at that distance along it."""
    return (
        bearing is not None
        and (speed or 0.0) >= MOVING_MS
        and _angle(bearing, shape.heading(along)) > WRONG_WAY_DEG
    )


def _forward_pass(
    shape: Shape, lat: float, lon: float, bearing: float | None, speed: float | None, last: float
) -> float | None:
    """The first later part of the shape the report lies on, heading the same
    way as the bus: within ``REANCHOR_M``, bearing within ``REANCHOR_DEG``.
    None if the bus is not clearly moving."""
    if bearing is None or (speed or 0.0) < MOVING_MS:
        return None
    for along, off in shape.passes(lat, lon, REANCHOR_M):
        if (
            along > last + BACKTRACK_M
            and off <= REANCHOR_M
            and _angle(bearing, shape.heading(along)) <= REANCHOR_DEG
        ):
            return along
    return None


def crossing(tr: Track, boundary: float) -> tuple[float | None, float | None, str]:
    """When the track first reaches ``boundary``: (epoch seconds or None,
    seconds between the two reports used, status)."""
    if not tr.dists:
        return None, None, "no_reports"
    if any(a < boundary < b for a, b in tr.skips):
        return None, None, "skipped"
    for k, d in enumerate(tr.dists):
        if d >= boundary:
            if k == 0:
                return None, None, "past_when_first_seen"
            d0, t0, t1 = tr.dists[k - 1], tr.times[k - 1], tr.times[k]
            gap = t1 - t0
            if gap > MAX_GAP_S:
                return None, gap, "gap"
            return t0 + (boundary - d0) / (d - d0) * gap, gap, "observed"
    return None, None, "not_reached"


def _epoch(dt: datetime) -> float:
    return dt.timestamp()


def _at(origin: datetime, seconds: int | None) -> datetime | None:
    return origin + timedelta(seconds=seconds) if seconds is not None else None


def _dt(x: float | None) -> datetime | None:
    return datetime.fromtimestamp(x, UTC) if x is not None else None


def infer_day(
    day: date,
    contents: ZipContents,
    vehicles: list[Row],
    timepoints: set[tuple[str, str]] | None = None,
) -> tuple[list[Row], list[Row]]:
    """Return (stop events, trip summaries) for every scheduled trip."""
    static = StaticData.load(contents)
    timepoints = timepoints or set()
    origin = day_origin(day)

    by_trip: dict[str, list[Row]] = defaultdict(list)
    for v in vehicles:
        if v["trip_id"] is not None and v["vehicle_ts"] is not None and v["lat"] is not None:
            by_trip[v["trip_id"]].append(v)

    events: list[Row] = []
    trips_out: list[Row] = []
    for trip in scheduled_trips(contents, day):
        meta = static.trips[trip.trip_id]
        shape = static.shapes.get(meta.get("shape_id", ""))
        placed = static.place_stops(trip.trip_id)
        margin = timedelta(minutes=WINDOW_MIN)
        lo, hi = trip.start - margin, trip.end + margin
        mine = [v for v in by_trip.get(trip.trip_id, []) if lo <= v["vehicle_ts"] <= hi]
        per_vehicle: dict[str, dict[float, Row]] = defaultdict(dict)
        for v in mine:
            per_vehicle[v["vehicle_key"]].setdefault(_epoch(v["vehicle_ts"]), v)
        vehicle_key = max(per_vehicle, key=lambda k: len(per_vehicle[k]), default=None)
        reports = (
            sorted(
                (ts, v["lat"], v["lon"], v["bearing"], v["speed"])
                for ts, v in per_vehicle[vehicle_key].items()
            )
            if vehicle_key
            else []
        )
        tr = (
            track(shape, reports, _epoch(trip.start))
            if shape is not None
            else Track([], [], reports=len(reports))
        )

        stop_rows = static.stop_times.get(trip.trip_id, [])
        n = len(stop_rows)
        for i, st in enumerate(stop_rows):
            sd = placed[i] if placed else None
            arr_s, dep_s = _seconds(st["arrival_time"]), _seconds(st["departure_time"])
            row: Row = {
                "trip_id": trip.trip_id,
                "route_id": trip.route_id,
                "vehicle_key": vehicle_key,
                "stop_sequence": int(st["stop_sequence"]),
                "stop_id": st["stop_id"],
                "is_first_stop": i == 0,
                "is_last_stop": i == n - 1,
                "is_timepoint": (trip.route_id, st["stop_id"]) in timepoints,
                "stop_distance_m": round(sd, 1) if sd is not None else None,
                "scheduled_arrival": _at(origin, arr_s),
                "scheduled_departure": _at(origin, dep_s),
            }
            for kind, boundary, skip in (
                ("arrival", (sd or 0.0) - STOP_RADIUS_M, i == 0),
                ("departure", (sd or 0.0) + STOP_RADIUS_M, i == n - 1),
            ):
                if skip:
                    t, gap, status = None, None, "not_applicable"
                elif sd is None:
                    t, gap, status = None, None, "no_shape"
                else:
                    t, gap, status = crossing(tr, boundary)
                row[f"{kind}_time"] = _dt(t)
                row[f"{kind}_gap_s"] = round(gap, 1) if gap is not None else None
                row[f"{kind}_status"] = status
            events.append(row)

        trips_out.append(
            {
                "trip_id": trip.trip_id,
                "route_id": trip.route_id,
                "scheduled_start": trip.start,
                "scheduled_end": trip.end,
                "vehicles_with_trip_id": len(per_vehicle),
                "vehicle_key": vehicle_key,
                "position_reports": tr.reports,
                "reports_used": len(tr.dists),
                "reports_before_start": tr.before_start,
                "reports_after_end": tr.after_end,
                "off_shape_reports": tr.off_shape,
                "wrong_direction_reports": tr.wrong_direction,
                "shape_id": meta.get("shape_id"),
                "shape_length_m": round(shape.length, 1) if shape is not None else None,
                "max_distance_m": round(tr.dists[-1], 1) if tr.dists else None,
                "shape_skips": len(tr.skips),
                "skipped_m": round(sum(b - a for a, b in tr.skips), 1),
            }
        )
    return events, trips_out
