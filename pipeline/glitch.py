"""GPS glitches: position reports a bus could not have made.

Implements docs/methods.md, section 3 ("GPS glitches"). Each vehicle's
reports are put in time order (one report per vehicle timestamp, whatever
trip id it carries), and the speed between two reports is the straight-line
distance over the time between them. A speed above ``MAX_KMH`` (130 km/h,
about 80 mph) is impossible for these buses.

- Spike: one report the bus jumps to (an impossible speed into it) where
  the reports on either side agree with each other, and a bus going from
  the one before to the one after could not have passed through it: the
  path through it, over the time between them, is faster than ``MAX_KMH``.
  When the speed out of it is impossible too, that always holds. The
  report is removed.
- Plateau: a run of reports lasting ``PLATEAU_S`` (120 seconds) or less
  that the bus jumps to and back from (impossible speeds into it and out
  of it), where the reports on either side of the run agree with each
  other and the run does not carry on smoothly past the report that seems
  to jump back (then that report is the glitch). The run is removed.
- A jump with no jump back (the bus is simply somewhere else from then on),
  or one the bus could have made by driving fast between the reports on
  either side (often a report whose timestamp is a few seconds off), is not
  removed: we cannot tell which side is wrong. It is counted as an
  unresolved jump.

A removed report within ``CROSS_M`` (30 meters) and ``CROSS_S`` (10 seconds)
of a report of another bus is also counted on its own: the feed may have
sent one bus's position under another's id.
"""

from __future__ import annotations

import itertools
from bisect import bisect_left, bisect_right
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from pipeline.common import Row
from pipeline.geo import meters_between

GLITCH_VERSION = "glitch/0.1.0"
MAX_KMH = 130.0
PLATEAU_S = 120.0
CROSS_M = 30.0
CROSS_S = 10.0


@dataclass
class Glitches:
    """The reports to drop, as (vehicle key, vehicle timestamp), and counts."""

    removed: set[tuple[str, datetime]] = field(default_factory=set)
    spikes: int = 0
    plateaus: int = 0
    plateau_reports: int = 0
    unresolved_jumps: int = 0
    cross_vehicle: int = 0
    by_vehicle: dict[str, int] = field(default_factory=dict)

    def summary(self) -> dict[str, Any]:
        return {
            "glitch_version": GLITCH_VERSION,
            "removed_reports": len(self.removed),
            "spikes": self.spikes,
            "plateaus": self.plateaus,
            "plateau_reports": self.plateau_reports,
            "unresolved_jumps": self.unresolved_jumps,
            "removed_near_another_bus": self.cross_vehicle,
            "vehicles_with_removed_reports": len(self.by_vehicle),
        }


Point = tuple[datetime, float, float]


def timelines(vehicles: Iterable[Row]) -> dict[str, list[Point]]:
    """Vehicle key -> (timestamp, lat, lon) in time order, one per timestamp
    (the first report seen with it)."""
    seen: dict[str, dict[datetime, Point]] = defaultdict(dict)
    for v in vehicles:
        ts, lat, lon = v["vehicle_ts"], v["lat"], v["lon"]
        if ts is None or lat is None or lon is None:
            continue
        seen[v["vehicle_key"]].setdefault(ts, (ts, lat, lon))
    return {k: [d[t] for t in sorted(d)] for k, d in seen.items()}


def _kmh(a: Point, b: Point) -> float:
    dt = (b[0] - a[0]).total_seconds()
    return meters_between(a[1], a[2], b[1], b[2]) / dt * 3.6 if dt > 0 else 0.0


def _fast(a: Point, b: Point) -> bool:
    return _kmh(a, b) > MAX_KMH


def _impossible_detour(before: Point, run: list[Point], after: Point) -> bool:
    """Whether a bus going from ``before`` to ``after`` could not also have
    been at every point of ``run`` on the way: the path through them, over
    the time from ``before`` to ``after``, is faster than ``MAX_KMH``."""
    path = [before, *run, after]
    meters = sum(meters_between(a[1], a[2], b[1], b[2]) for a, b in itertools.pairwise(path))
    seconds = (after[0] - before[0]).total_seconds()
    return seconds > 0 and meters / seconds * 3.6 > MAX_KMH


def flag_one(points: list[Point]) -> tuple[list[tuple[int, int]], int]:
    """Runs of one vehicle's reports to remove, as (first, last) indexes, and
    the number of unresolved jumps. Each report is compared with the last
    report kept, so a removed run never becomes the reference."""
    runs: list[tuple[int, int]] = []
    unresolved = 0
    kept: Point | None = None
    i, n = 0, len(points)
    while i < n:
        if kept is not None and _fast(kept, points[i]):
            end = None
            for j in range(i, n - 1):
                if (points[j][0] - points[i][0]).total_seconds() > PLATEAU_S:
                    break
                back = points[j + 1]
                # A run that carries on smoothly to the report after ``back``
                # is the bus's real path; ``back`` is the odd one, and is
                # judged on its own when the loop reaches it.
                returns = not _fast(kept, back) and (
                    _fast(points[j], back)
                    or (j == i and _impossible_detour(kept, points[i : j + 1], back))
                )
                if not returns:
                    continue
                if j > i and j + 2 < n and not _fast(points[j], points[j + 2]):
                    break  # the run carries on past ``back``: ``back`` is the glitch
                end = j
                break
            if end is not None:
                runs.append((i, end))
                i = end + 1
                continue
            unresolved += 1
        kept = points[i]
        i += 1
    return runs, unresolved


def flag(vehicles: Iterable[Row]) -> Glitches:
    """Every glitch in a day's position reports."""
    lines = timelines(vehicles)
    out = Glitches()
    for key, points in lines.items():
        runs, unresolved = flag_one(points)
        out.unresolved_jumps += unresolved
        for a, b in runs:
            if a == b:
                out.spikes += 1
            else:
                out.plateaus += 1
                out.plateau_reports += b - a + 1
            out.removed |= {(key, points[k][0]) for k in range(a, b + 1)}
            out.by_vehicle[key] = out.by_vehicle.get(key, 0) + b - a + 1
    out.cross_vehicle = _near_another_bus(out.removed, lines)
    return out


def _near_another_bus(removed: set[tuple[str, datetime]], lines: dict[str, list[Point]]) -> int:
    """Removed reports within ``CROSS_M`` and ``CROSS_S`` of another bus's report."""
    times = {k: [p[0] for p in pts] for k, pts in lines.items()}
    where = {(k, p[0]): p for k, pts in lines.items() for p in pts}
    count = 0
    for key, ts in removed:
        _, lat, lon = where[(key, ts)]
        near = False
        for other, pts in lines.items():
            if other == key:
                continue
            lo = bisect_left(times[other], ts - timedelta(seconds=CROSS_S))
            hi = bisect_right(times[other], ts + timedelta(seconds=CROSS_S))
            if any(meters_between(lat, lon, p[1], p[2]) <= CROSS_M for p in pts[lo:hi]):
                near = True
                break
        count += near
    return count


def keep(vehicles: list[Row], glitches: Glitches) -> list[Row]:
    """The reports that are not glitches."""
    if not glitches.removed:
        return vehicles
    return [v for v in vehicles if (v["vehicle_key"], v["vehicle_ts"]) not in glitches.removed]
