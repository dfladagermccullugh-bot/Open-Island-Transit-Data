"""Service days.

A service day is a calendar date in America/Los_Angeles. Island Transit
schedules no trips across midnight, and collector rows are assigned to days
by the time they were logged. Days are 23 or 25 hours long when daylight
saving time changes.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

from pipeline.config import PACIFIC


def day_bounds(day: date) -> tuple[datetime, datetime]:
    """UTC start (inclusive) and end (exclusive) of a Pacific calendar date."""
    start = datetime.combine(day, time.min, tzinfo=PACIFIC)
    end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=PACIFIC)
    return start.astimezone(UTC), end.astimezone(UTC)


def pacific_date(moment: datetime) -> date:
    return moment.astimezone(PACIFIC).date()


def complete_days(first: date, now: datetime) -> list[date]:
    """Every Pacific date from ``first`` up to yesterday (Pacific), in order.
    Today is never complete."""
    today = pacific_date(now)
    out = []
    day = first
    while day < today:
        out.append(day)
        day += timedelta(days=1)
    return out
