"""Daily on-time summary at true timepoints (docs/methods.md, section 4).

Validation: docs/methods.md, section 5.

For each scheduled timepoint event (a trip's scheduled time at one of its
true timepoints):

- the measured time is the departure, except at the last stop of a trip,
  where it is the arrival;
- deviation = measured time minus scheduled time;
- on time: from ``EARLY_S`` early to ``LATE_S`` late inclusive; an early
  arrival at the last stop counts as on time;
- not observed: no inferred time. Never counted as late.

Events scheduled before collection began are left out, and so, since
``ontime/0.4.0``, are the events of trips the feed said were canceled
(``pipeline/canceled.py``); those are counted under ``canceled``.

``by_route_stop`` breaks the counts down by route and timepoint stop and
keeps every deviation (whole seconds) so that medians over several days can
be computed exactly (``pipeline/events/routes.py``). Since ``ontime/0.4.1``
``by_hour`` keeps them too, for the hour-by-hour period figures
(``pipeline/periods.py``).

Since ``ontime/0.6.0`` each route and stop carries its ``position`` in the
trips (``first``, ``middle`` or ``last``: the one most of its events have,
``middle`` on a tie), and ``by_position`` counts the events by their own
position, so the gap between a trip's ends and its middle can be shown.

Since ``ontime/0.8.0`` the day counts ``last_stop_early_arrivals``: arrivals
at a trip's last stop more than ``EARLY_S`` early, which are counted on time.
Most agencies count them as early; the comparisons recount our figure that
way (``pipeline/compare.py``), and the difference between the two is exactly
this count (gate rule I13).
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
from typing import Any

from pipeline.common import Row
from pipeline.config import PACIFIC
from pipeline.stats import median

ONTIME_VERSION = "ontime/0.8.0"  # 0.8.0: early arrivals at last stops counted
EARLY_S = -60
LATE_S = 300


def classify(event: Row) -> tuple[str, float | None, datetime | None]:
    """(category, deviation in seconds, scheduled time) for one timepoint event."""
    last = event["is_last_stop"]
    measured = event["arrival_time"] if last else event["departure_time"]
    scheduled = event["scheduled_arrival"] if last else event["scheduled_departure"]
    if measured is None or scheduled is None:
        return "not_observed", None, scheduled
    dev = (measured - scheduled).total_seconds()
    if dev > LATE_S:
        return "late", dev, scheduled
    if dev < EARLY_S and not last:
        return "early", dev, scheduled
    return "on_time", dev, scheduled


POSITIONS = ("first", "middle", "last")


def position(event: Row) -> str:
    """Where in its trip a stop event lies: ``first``, ``middle`` or ``last``."""
    return "first" if event.get("is_first_stop") else "last" if event["is_last_stop"] else "middle"


# Why a departure has no time: the event's status (``pipeline/events/infer.py``),
# the arrival's at a trip's last stop, the departure's elsewhere.
UNTIMED_REASONS = ("gap", "skipped", "not_reached", "no_reports", "past_when_first_seen")


def untimed_reason(event: Row) -> str:
    status = event.get("arrival_status" if event["is_last_stop"] else "departure_status")
    return status or "unknown"


def reason_counts(c: Counter) -> dict[str, int]:
    """Every known reason, zero or not, then any other in name order."""
    return {**{r: c[r] for r in UNTIMED_REASONS},
            **{r: c[r] for r in sorted(set(c) - set(UNTIMED_REASONS))}}  # fmt: skip


# Hour bands of the scheduled time, Pacific, [start, end): the same edges as
# the bands of the gaps model (``pipeline/quality/gaps.py``).
HOUR_BANDS = (
    ("early", 0, 7),
    ("morning", 7, 9),
    ("midday", 9, 15),
    ("afternoon", 15, 18),
    ("evening", 18, 24),
)


def hour_band(hour: int) -> str:
    return next(name for name, lo, hi in HOUR_BANDS if lo <= hour < hi)


def cell_of(route_id: str, hour: int, pos: str) -> str:
    """The weighting cell of a timepoint event: ``route|band|position``."""
    return f"{route_id}|{hour_band(hour)}|{pos}"


def _mode(c: Counter) -> str:
    top = max(c.values())
    tied = [p for p in POSITIONS if c[p] == top]
    return tied[0] if len(tied) == 1 else "middle"


def _block(counts: Counter, devs: list[float]) -> dict[str, Any]:
    scheduled = sum(counts.values())
    observed = scheduled - counts["not_observed"]
    mid = median(devs)
    return {
        "scheduled": scheduled,
        "observed": observed,
        "observed_share": round(observed / scheduled, 4) if scheduled else None,
        "on_time": counts["on_time"],
        "early": counts["early"],
        "late": counts["late"],
        "on_time_share_of_observed": round(counts["on_time"] / observed, 4) if observed else None,
        "deviation_s_median": round(mid, 1) if mid is not None else None,
    }


def summarize(
    events: list[Row],
    collection_start: datetime,
    stop_names: dict[str, str] | None = None,
    canceled: set[str] | frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """``canceled``: trips the feed said were canceled (``pipeline/canceled.py``).
    Since ``ontime/0.4.0`` their timepoint events are left out of every count,
    as a bus that did not run cannot be on time or late, and counted apart."""
    total: Counter = Counter()
    left_out = 0
    devs: list[float] = []
    by_route: dict[str, Counter] = defaultdict(Counter)
    route_devs: dict[str, list[float]] = defaultdict(list)
    by_hour: dict[int, Counter] = defaultdict(Counter)
    hour_devs: dict[int, list[float]] = defaultdict(list)
    by_stop: dict[tuple[str, str], Counter] = defaultdict(Counter)
    stop_devs: dict[tuple[str, str], list[float]] = defaultdict(list)
    stop_seq: dict[tuple[str, str], list[int]] = defaultdict(list)
    stop_pos: dict[tuple[str, str], Counter] = defaultdict(Counter)
    by_position: dict[str, Counter] = {p: Counter() for p in POSITIONS}
    position_devs: dict[str, list[float]] = {p: [] for p in POSITIONS}
    by_trip: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    early_last = 0
    untimed: Counter = Counter()
    untimed_pos: dict[str, Counter] = {p: Counter() for p in POSITIONS}
    by_cell: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])
    by_trip_cell: dict[str, dict[str, list[int]]] = defaultdict(
        lambda: defaultdict(lambda: [0, 0, 0])
    )
    for e in events:
        if not e["is_timepoint"]:
            continue
        cat, dev, scheduled = classify(e)
        if scheduled is None or scheduled < collection_start:
            continue
        if canceled and e["trip_id"] in canceled:
            left_out += 1
            continue
        hour = scheduled.astimezone(PACIFIC).hour
        key = (e["route_id"], e["stop_id"])
        pos = position(e)
        for c in (total, by_route[e["route_id"]], by_hour[hour], by_stop[key], by_position[pos]):
            c[cat] += 1
        stop_seq[key].append(e["stop_sequence"])
        stop_pos[key][pos] += 1
        cell = cell_of(e["route_id"], hour, pos)
        counts = [1, cat != "not_observed", cat == "on_time"]
        cells = [by_cell[cell]]
        if e.get("trip_id") is not None:
            cells.append(by_trip_cell[e["trip_id"]][cell])
        for c3 in cells:
            for i, x in enumerate(counts):
                c3[i] += x
        if cat == "not_observed":
            reason = untimed_reason(e)
            untimed[reason] += 1
            untimed_pos[pos][reason] += 1
        if cat != "not_observed" and e.get("trip_id") is not None:
            by_trip[e["trip_id"]][0] += 1
            by_trip[e["trip_id"]][1] += cat == "on_time"
        early_last += cat == "on_time" and dev is not None and dev < EARLY_S
        if dev is not None:
            devs.append(dev)
            route_devs[e["route_id"]].append(dev)
            hour_devs[hour].append(dev)
            stop_devs[key].append(dev)
            position_devs[pos].append(dev)
    route_stop: dict[str, dict[str, Any]] = defaultdict(dict)
    for (route, stop), c in sorted(by_stop.items()):
        seqs = sorted(stop_seq[(route, stop)])
        route_stop[route][stop] = {
            "name": (stop_names or {}).get(stop, ""),
            "stop_sequence_median": seqs[len(seqs) // 2],
            "position": _mode(stop_pos[(route, stop)]),
            **_block(c, stop_devs[(route, stop)]),
            "deviations_s": sorted(round(d) for d in stop_devs[(route, stop)]),
        }
    return {
        "ontime_version": ONTIME_VERSION,
        "definition": {
            "window_s": [EARLY_S, LATE_S],
            "measured": "departure at timepoints; arrival at the last stop",
            "timepoints": "stops printed in Island Transit's timetables",
        },
        **_block(total, devs),
        # Since ontime/0.8.0: of on_time, the arrivals at a last stop more
        # than a minute early.
        "last_stop_early_arrivals": early_last,
        "canceled": {
            "trip_ids": sorted(canceled),
            "timepoint_events_left_out": left_out,
        },
        "by_route": {r: _block(c, route_devs[r]) for r, c in sorted(by_route.items())},
        "by_hour": {
            str(h): {
                **_block(c, hour_devs[h]),
                "deviations_s": sorted(round(d) for d in hour_devs[h]),
            }
            for h, c in sorted(by_hour.items())
        },
        "by_position": {p: _block(by_position[p], position_devs[p]) for p in POSITIONS},
        # Since ontime/0.7.0: why departures have no time.
        "untimed": {
            "departures": sum(untimed.values()),
            "by_reason": reason_counts(untimed),
            "by_position": {p: reason_counts(untimed_pos[p]) for p in POSITIONS},
        },
        # Since ontime/0.7.0: [scheduled, observed, on time] by cell of route,
        # hour band and position, for the whole day and for each trip.
        "by_cell": dict(sorted(by_cell.items())),
        "by_trip_cell": {t: dict(sorted(c.items())) for t, c in sorted(by_trip_cell.items())},
        "by_route_stop": dict(route_stop),
        # [observed, on time] timepoint departures of each trip with any
        # observed, so that an interval can resample whole trips.
        "by_trip": dict(sorted(by_trip.items())),
    }
