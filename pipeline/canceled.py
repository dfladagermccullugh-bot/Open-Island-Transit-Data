"""Trips the feed itself says were canceled.

A scheduled trip that no bus reports looks the same in the vehicle positions
whether the bus ran without reporting or did not run at all. The feed has two
ways to say a trip did not run, and this module is the one definition of
both:

- a trip update or a vehicle position whose trip carries the schedule
  relationship ``CANCELED`` or ``DELETED``; or
- a service alert with the effect ``NO_SERVICE`` that names the trip, while
  the alert is active at some moment of the trip's scheduled time. An alert
  with no active period applies for as long as it is shown. An alert that
  names only a route or a stop is too coarse to cancel a trip and is not
  used.

Only trips scheduled on the day are returned. Up to November 2026 Island
Transit's feed has sent neither signal; the counts are published all the
same, so that a first cancellation shows up.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from pipeline.common import Row
from pipeline.gtfs.schedule import Trip

CANCELED_FLAGS = frozenset({"CANCELED", "DELETED"})
CANCELING_EFFECT = "NO_SERVICE"


def _active_during(periods: Iterable[Any] | None, trip: Trip) -> bool:
    periods = list(periods or [])
    if not periods:
        return True
    for p in periods:
        start, end = (p.get("start"), p.get("end")) if isinstance(p, dict) else p
        if (start is None or start <= trip.end) and (end is None or end >= trip.start):
            return True
    return False


def canceled_trips(
    trip_updates: Iterable[Row],
    alerts: Iterable[Row],
    trips: list[Trip],
    vehicles: Iterable[Row] = (),
) -> dict[str, str]:
    """Scheduled trip id -> ``"flag"`` or ``"alert"``, whichever said the trip
    was canceled. A flag outranks an alert."""
    scheduled = {t.trip_id: t for t in trips}
    out: dict[str, str] = {}
    for a in alerts:
        if a.get("effect") != CANCELING_EFFECT:
            continue
        for trip_id in a.get("trip_ids") or []:
            trip = scheduled.get(trip_id)
            if trip is not None and _active_during(a.get("active_periods"), trip):
                out[trip_id] = "alert"
    for rows in (trip_updates, vehicles):
        for r in rows:
            if r.get("trip_schedule_relationship") in CANCELED_FLAGS and r["trip_id"] in scheduled:
                out[r["trip_id"]] = "flag"
    return out
