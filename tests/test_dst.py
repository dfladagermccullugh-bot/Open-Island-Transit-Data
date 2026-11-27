"""Clock changes: the service day, GTFS times and hour-by-hour figures stay
right on the days Pacific time springs forward and falls back.

Service runs about 3:35 AM to 8:25 PM, so no trip is scheduled in the hour
the clock changes; these tests make sure the arithmetic around it holds."""

from datetime import UTC, date, datetime

from pipeline.archive.days import day_bounds
from pipeline.config import PACIFIC
from pipeline.events.ontime import summarize
from pipeline.gtfs.schedule import day_origin

SPRING = date(2027, 3, 14)  # 2 AM PST becomes 3 AM PDT
FALL = date(2027, 11, 7)  # 2 AM PDT becomes 1 AM PST


def test_the_service_day_is_23_and_25_hours_long():
    for day, hours in ((SPRING, 23), (FALL, 25), (date(2027, 3, 15), 24)):
        start, end = day_bounds(day)
        assert (end - start).total_seconds() == hours * 3600


def test_gtfs_times_count_from_noon_minus_12_hours():
    # GTFS: times are measured from noon minus 12 hours, so 08:00:00 is
    # 8 AM on the wall clock even on a day the clock changes.
    for day in (SPRING, FALL):
        origin = day_origin(day)
        eight = origin.timestamp() + 8 * 3600
        assert datetime.fromtimestamp(eight, UTC).astimezone(PACIFIC).hour == 8
    assert day_origin(SPRING) == datetime(2027, 3, 14, 7, 0, tzinfo=UTC)  # 11 PM PST before
    assert day_origin(FALL) == datetime(2027, 11, 7, 8, 0, tzinfo=UTC)  # 1 AM PDT


def event(trip, hour_utc, minute, late_s, day):
    scheduled = datetime(day.year, day.month, day.day, hour_utc, minute, tzinfo=UTC)
    return {"trip_id": trip, "route_id": "R", "stop_id": "S", "stop_sequence": 2,
            "is_timepoint": True, "is_last_stop": False, "scheduled_arrival": scheduled,
            "scheduled_departure": scheduled, "arrival_time": None,
            "departure_time": datetime.fromtimestamp(scheduled.timestamp() + late_s, UTC),
            }  # fmt: skip


def test_hour_buckets_follow_the_wall_clock():
    start = datetime(2026, 11, 19, tzinfo=UTC)
    # 6 AM wall clock: 13:00 UTC in PDT (spring day), 14:00 UTC in PST (fall day).
    spring = summarize([event("A", 13, 0, 30, SPRING)], start)
    fall = summarize([event("B", 14, 0, 30, FALL)], start)
    assert list(spring["by_hour"]) == ["6"]
    assert list(fall["by_hour"]) == ["6"]
