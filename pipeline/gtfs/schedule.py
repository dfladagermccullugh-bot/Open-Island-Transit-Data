"""Scheduled trips for one service day, from a static GTFS feed.

GTFS times are measured from "noon minus 12 hours" in the agency's time
zone, which is midnight except on the two days a year the clocks change.
"""

from __future__ import annotations

import bisect
import csv
import io
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

from pipeline.config import PACIFIC
from pipeline.gtfs.zipread import ZipContents

DAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


@dataclass(frozen=True)
class Trip:
    trip_id: str
    route_id: str
    service_id: str
    start: datetime  # first scheduled departure, UTC-aware
    end: datetime  # last scheduled arrival


def _rows(contents: ZipContents, name: str) -> list[dict[str, str]]:
    body = contents.files.get(name)
    if body is None:
        return []
    return list(csv.DictReader(io.StringIO(body.decode("utf-8-sig", errors="replace"))))


def active_services(contents: ZipContents, day: date) -> set[str]:
    stamp = day.strftime("%Y%m%d")
    weekday = DAYS[day.weekday()]
    active = {
        c["service_id"]
        for c in _rows(contents, "calendar.txt")
        if c.get(weekday) == "1" and c["start_date"] <= stamp <= c["end_date"]
    }
    for d in _rows(contents, "calendar_dates.txt"):
        if d["date"] == stamp:
            if d["exception_type"] == "1":
                active.add(d["service_id"])
            elif d["exception_type"] == "2":
                active.discard(d["service_id"])
    return active


def removed_services(contents: ZipContents, day: date) -> list[dict[str, str]]:
    """calendar_dates rows that remove service on ``day`` (holidays)."""
    stamp = day.strftime("%Y%m%d")
    return [
        d
        for d in _rows(contents, "calendar_dates.txt")
        if d["date"] == stamp and d["exception_type"] == "2"
    ]


def day_origin(day: date) -> datetime:
    """The instant GTFS time 00:00:00 refers to on ``day``."""
    # Subtract in UTC: arithmetic on a Pacific-aware datetime is wall-clock
    # arithmetic and would ignore a clock change.
    return datetime.combine(day, time(12), tzinfo=PACIFIC).astimezone(UTC) - timedelta(hours=12)


def _seconds(hms: str) -> int:
    h, m, s = (int(x) for x in hms.strip().split(":"))
    return h * 3600 + m * 60 + s


def scheduled_trips(contents: ZipContents, day: date) -> list[Trip]:
    services = active_services(contents, day)
    trips = {t["trip_id"]: t for t in _rows(contents, "trips.txt") if t["service_id"] in services}
    bounds: dict[str, list[int]] = {}
    for st in _rows(contents, "stop_times.txt"):
        if st["trip_id"] not in trips:
            continue
        times = [_seconds(x) for x in (st.get("arrival_time"), st.get("departure_time")) if x]
        if not times:
            continue
        b = bounds.setdefault(st["trip_id"], [min(times), max(times)])
        b[0], b[1] = min(b[0], *times), max(b[1], *times)
    origin = day_origin(day)
    return sorted(
        (
            Trip(
                trip_id=tid,
                route_id=trips[tid]["route_id"],
                service_id=trips[tid]["service_id"],
                start=origin + timedelta(seconds=lo),
                end=origin + timedelta(seconds=hi),
            )
            for tid, (lo, hi) in bounds.items()
        ),
        key=lambda t: (t.start, t.trip_id),
    )


class ServiceWindows:
    """Answers "was at least one trip scheduled at this moment?" """

    def __init__(self, trips: list[Trip]) -> None:
        merged: list[list[datetime]] = []
        for t in sorted(trips, key=lambda t: t.start):
            if merged and t.start <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], t.end)
            else:
                merged.append([t.start, t.end])
        self.starts = [m[0] for m in merged]
        self.ends = [m[1] for m in merged]

    def __contains__(self, moment: datetime) -> bool:
        i = bisect.bisect_right(self.starts, moment) - 1
        return i >= 0 and moment <= self.ends[i]
