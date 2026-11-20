"""Daily on-time summary at true timepoints (docs/methods.md, section 4).

EXPERIMENTAL until the field validation in docs/methods.md, section 5.

For each scheduled timepoint event (a trip's scheduled time at one of its
true timepoints):

- the measured time is the departure, except at the last stop of a trip,
  where it is the arrival;
- deviation = measured time minus scheduled time;
- on time: from ``EARLY_S`` early to ``LATE_S`` late inclusive; an early
  arrival at the last stop counts as on time;
- not observed: no inferred time. Never counted as late.

Events scheduled before collection began are left out.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
from typing import Any

from pipeline.config import PACIFIC

ONTIME_VERSION = "ontime/0.1.0"
EARLY_S = -60
LATE_S = 300

Row = dict[str, Any]


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


def _block(counts: Counter, devs: list[float]) -> dict[str, Any]:
    scheduled = sum(counts.values())
    observed = scheduled - counts["not_observed"]
    devs = sorted(devs)
    return {
        "scheduled": scheduled,
        "observed": observed,
        "observed_share": round(observed / scheduled, 4) if scheduled else None,
        "on_time": counts["on_time"],
        "early": counts["early"],
        "late": counts["late"],
        "on_time_share_of_observed": round(counts["on_time"] / observed, 4) if observed else None,
        "deviation_s_median": round(devs[len(devs) // 2], 1) if devs else None,
    }


def summarize(events: list[Row], collection_start: datetime) -> dict[str, Any]:
    total: Counter = Counter()
    devs: list[float] = []
    by_route: dict[str, Counter] = defaultdict(Counter)
    route_devs: dict[str, list[float]] = defaultdict(list)
    by_hour: dict[int, Counter] = defaultdict(Counter)
    hour_devs: dict[int, list[float]] = defaultdict(list)
    for e in events:
        if not e["is_timepoint"]:
            continue
        cat, dev, scheduled = classify(e)
        if scheduled is None or scheduled < collection_start:
            continue
        hour = scheduled.astimezone(PACIFIC).hour
        for c in (total, by_route[e["route_id"]], by_hour[hour]):
            c[cat] += 1
        if dev is not None:
            devs.append(dev)
            route_devs[e["route_id"]].append(dev)
            hour_devs[hour].append(dev)
    return {
        "ontime_version": ONTIME_VERSION,
        "experimental": True,
        "definition": {
            "window_s": [EARLY_S, LATE_S],
            "measured": "departure at timepoints; arrival at the last stop",
            "timepoints": "stops printed in Island Transit's timetables",
        },
        **_block(total, devs),
        "by_route": {r: _block(c, route_devs[r]) for r, c in sorted(by_route.items())},
        "by_hour": {str(h): _block(c, hour_devs[h]) for h, c in sorted(by_hour.items())},
    }
