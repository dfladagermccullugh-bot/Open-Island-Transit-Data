"""Follow the physical bus, not just the trip id (docs/methods.md, section 3).

The feed names the trip a bus is on, but the name changes at the wrong
moments. A bus often shows its next trip's id before it reaches the last
stop of the one it is on, so the end of that trip has no reports under its
own id. It may keep the previous trip's id while it waits at the first stop
of the next, or send no trip id at all for a stretch after a driver logs
off. Buses leave the depot between Greenbank and Coupeville and drive to
their first stop, change routes during the day (21 of 26 buses served two
or more routes on November 24, 2026), and get new drivers mid-route.

So each trip also uses the reports its bus sent under any other trip id, or
none, in a window around it:

- from the later of 30 minutes before its scheduled start and the last
  report the bus's previous trip used (``borrowed_before``);
- between its own first and last report (``borrowed_within``);
- after its own last report, up to 30 minutes after its scheduled end,
  but never past the moment the bus left the first stop of its next trip,
  as that trip's own reports show (``borrowed_after``). Without that limit
  a trip the bus did not drive could borrow the bus's next run of the same
  route.

Relays (since ``events/0.6.0``): when a driver is swapped by a change of
bus, two vehicles carry one trip id one after the other. If another
vehicle's reports under the trip's id end at most ``RELAY_S`` before the
chosen vehicle's begin, within ``RELAY_M`` of where they begin (or begin as
soon after the chosen vehicle's end, as close), the trip also uses them
(``relay``), and the chosen bus's own reports from that side of the
handover are not borrowed. A second vehicle that overlaps the chosen one in
time is another bus showing the wrong id, and is left out as before.

Tracking (``pipeline/events/infer.py``) still starts only near the start of
the trip's shape and stops at its end, so the drive from the depot and the
next trip's route are not mistaken for this trip. One report can time both
one trip's arrival at a terminal and the next trip's departure from it; the
publishing rule I10 checks that a bus never seems to be on two trips at
once.
"""

from __future__ import annotations

import itertools
from collections import defaultdict
from collections.abc import Iterable
from datetime import datetime, timedelta
from typing import NamedTuple

from pipeline.common import Row
from pipeline.geo import meters_between
from pipeline.gtfs.schedule import Trip

Report = tuple[float, float, float, float | None, float | None]  # epoch, lat, lon, bearing, speed

RELAY_S = 300.0  # 5 minutes
RELAY_M = 300.0  # about 1,000 feet


class Relay(NamedTuple):
    """The other vehicle of a relay, its reports under the trip's id, and
    whether it ran the trip ``before`` or ``after`` the chosen vehicle."""

    vehicle_key: str
    reports: dict[float, Row]
    side: str


def relay(per_vehicle: dict[str, dict[float, Row]], chosen: str) -> Relay | None:
    """The vehicle that ran the trip just before or after ``chosen``, if
    any: its reports under the trip's id end at most ``RELAY_S`` before the
    chosen vehicle's begin and within ``RELAY_M`` of that place, or begin as
    soon and as close after the chosen vehicle's end. Of several, the one
    nearest in time."""
    own = per_vehicle.get(chosen) or {}
    if not own:
        return None
    first, last = min(own), max(own)
    best: tuple[tuple[float, str], Relay] | None = None
    for key, rs in per_vehicle.items():
        if key == chosen or not rs:
            continue
        a0, a1 = min(rs), max(rs)
        if a1 < first and first - a1 <= RELAY_S:
            here, there, side, gap = rs[a1], own[first], "before", first - a1
        elif a0 > last and a0 - last <= RELAY_S:
            here, there, side, gap = own[last], rs[a0], "after", a0 - last
        else:
            continue
        if meters_between(here["lat"], here["lon"], there["lat"], there["lon"]) > RELAY_M:
            continue
        if best is None or (gap, key) < best[0]:
            best = ((gap, key), Relay(key, rs, side))
    return best[1] if best else None


def timelines(
    vehicles: Iterable[Row], removed: set[tuple[str, datetime]]
) -> dict[str, list[Report]]:
    """Vehicle key -> its reports in time order, one per timestamp, whatever
    trip id they carry, without the GPS glitches in ``removed``."""
    seen: dict[str, dict[float, Report]] = defaultdict(dict)
    for v in vehicles:
        ts = v["vehicle_ts"]
        if ts is None or v["lat"] is None or v["lon"] is None:
            continue
        if (v["vehicle_key"], ts) in removed:
            continue
        t = ts.timestamp()
        seen[v["vehicle_key"]].setdefault(t, (t, v["lat"], v["lon"], v["bearing"], v["speed"]))
    return {k: [d[t] for t in sorted(d)] for k, d in seen.items()}


def vehicle_order(
    trips: list[Trip], keys: list[str | None], own: list[dict[float, Row]]
) -> dict[str | None, list[int]]:
    """Trip indexes grouped by the vehicle that ran them, each group in the
    order the bus ran them: by its first own report, else by schedule."""
    groups: dict[str | None, list[int]] = defaultdict(list)
    for i, key in enumerate(keys):
        groups[key].append(i)
    for idx in groups.values():
        idx.sort(key=lambda i: (min(own[i]) if own[i] else trips[i].start.timestamp(), i))
    return dict(groups)


def trip_window(
    trip: Trip, margin: timedelta, prev_end: float | None, next_leaves: float | None = None
) -> tuple[float, float]:
    """(first, last) epoch seconds from which a trip may borrow reports:
    from the later of ``margin`` before its start and the last report its
    bus's previous trip used, to the earlier of ``margin`` after its end and
    the moment the bus left its next trip's first stop."""
    lo = (trip.start - margin).timestamp()
    if prev_end is not None:
        lo = max(lo, prev_end)
    hi = (trip.end + margin).timestamp()
    if next_leaves is not None:
        hi = min(hi, next_leaves - 0.001)  # the report leaving that stop is not borrowed
    return lo, hi


def reports_for(
    own: dict[float, Row],
    line: list[Report],
    window: tuple[float, float],
    relay: Relay | None = None,
) -> tuple[list[Report], dict[float, str]]:
    """A trip's reports, its own, the borrowed ones and those of a relay
    vehicle, in time order, and where each came from."""
    sources: dict[float, str] = {}
    out: dict[float, Report] = {}
    for t, v in own.items():
        out[t] = (t, v["lat"], v["lon"], v["bearing"], v["speed"])
        sources[t] = "own"
    if own:
        first, last = min(own), max(own)
        lo, hi = window
        for r in line:
            t = r[0]
            if t in out or t < lo or t > hi:
                continue
            out[t] = r
            sources[t] = (
                "borrowed_before"
                if t < first
                else "borrowed_after"
                if t > last
                else "borrowed_within"
            )
    if own and relay is not None:
        # Before the handover (or after it) the chosen bus was elsewhere.
        cut, before = (min(own), True) if relay.side == "before" else (max(own), False)
        for t in [t for t in out if sources[t] != "own" and (t < cut if before else t > cut)]:
            del out[t], sources[t]
        for t, v in relay.reports.items():
            if t not in out:
                out[t] = (t, v["lat"], v["lon"], v["bearing"], v["speed"])
                sources[t] = "relay"
    return [out[t] for t in sorted(out)], sources


def summary(tracks: list[Row], block_of: dict[str, str]) -> dict[str, object]:
    """How the day's trips were followed across trip ids, for the on-time
    day file. ``block_disagreements``: consecutive trips of one bus that
    Passio's schedule puts in different blocks (``block_id``, the sequence
    of trips one bus is planned to run). The feed shows what the bus did,
    so these are reported, never corrected; buses are often swapped."""
    by_vehicle: dict[str, list[Row]] = defaultdict(list)
    for t in tracks:
        if t["vehicle_key"] and t["position_reports"]:
            by_vehicle[t["vehicle_key"]].append(t)
    pairs = disagree = 0
    for trips in by_vehicle.values():
        trips.sort(key=lambda t: t["scheduled_start"])
        for a, b in itertools.pairwise(trips):
            pairs += 1
            ba, bb = block_of.get(a["trip_id"]), block_of.get(b["trip_id"])
            disagree += bool(ba and bb and ba != bb)
    routes = [len({t["route_id"] for t in trips}) for trips in by_vehicle.values()]
    return {
        "vehicles": len(by_vehicle),
        "vehicles_on_two_or_more_routes": sum(n >= 2 for n in routes),
        "consecutive_trip_pairs": pairs,
        "block_disagreements": disagree,
        "relays": sum(bool(t.get("relay_vehicle_key")) for t in tracks),
        "trips_with_borrowed_reports": sum(
            bool(
                t.get("reports_borrowed_before")
                or t.get("reports_borrowed_after")
                or t.get("reports_borrowed_within")
            )
            for t in tracks
        ),
    }
